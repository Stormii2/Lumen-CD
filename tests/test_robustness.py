"""
Robustness: the compiler must terminate and never raise on ANY input.
The first version hung forever on a stray '}' and on 'else' without 'if'; these tests
exist so that class of defect cannot return.
"""
import os
import random
import signal

import pytest

from lumen import compile_source, RecoveryConfig, NAIVE, FAIL_FAST, DEFAULT, TACInterpreter, LumenRuntimeError
from lumen.explain import CATALOG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG_DIR = os.path.join(ROOT, "eval", "programs")
PROGRAMS = [open(os.path.join(PROG_DIR, f)).read() for f in sorted(os.listdir(PROG_DIR)) if f.endswith(".lum")]

CONFIGS = {
    "default": DEFAULT, "naive": NAIVE, "failfast": FAIL_FAST,
    "no-phrase": DEFAULT.with_(phrase_level=False),
    "semicolon-sync": DEFAULT.with_(sync="semicolon"),
    "no-poison": DEFAULT.with_(poison="none"),
    "expr-poison": DEFAULT.with_(poison="expr"),
}
SNIPPETS = [")", "(", "}", "{", ";", "+", "=", "else", "5", "x", "]", "[", ",", "&&", "int", "return",
            "while", "func", "\"", "@", "1.2.3", "9z", "//", "!", "-", "if", "true", "void"]


class Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise Timeout()


def bounded(fn, *a, seconds=5):
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(seconds)
    try:
        return fn(*a)
    finally:
        signal.alarm(0)


def mutate(src, rng, n=1):
    for _ in range(n):
        pos = rng.randrange(len(src) + 1)
        kind = rng.choice(["ins", "del", "rep"])
        junk = rng.choice(SNIPPETS)
        if kind == "ins":
            src = src[:pos] + junk + " " + src[pos:]
        elif kind == "del":
            src = src[:pos] + src[pos + rng.randrange(1, 6):]
        else:
            src = src[:pos] + junk + src[pos + 1:]
    return src


@pytest.mark.parametrize("cfg_name", sorted(CONFIGS))
def test_token_fuzz_terminates_without_exceptions(cfg_name):
    rng = random.Random(1234)
    cfg = CONFIGS[cfg_name]
    for i in range(250):
        src = mutate(rng.choice(PROGRAMS), rng, n=rng.choice([1, 1, 2, 3]))
        res = bounded(compile_source, src, cfg, False)
        for d in res.diagnostics:
            assert 0 <= d.span.start <= d.span.end <= len(src) + 1, (src, d)
            assert d.code in CATALOG, d.code


def test_random_garbage_text_terminates():
    rng = random.Random(7)
    alphabet = "abcdefint whilefunc(){}[];=+-*/%<>!&|\"'.,0123456789\n\t@#$~`\\"
    for _ in range(300):
        src = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 200)))
        bounded(compile_source, src, DEFAULT, False)


def test_random_bytes_as_text_terminates():
    rng = random.Random(8)
    for _ in range(100):
        src = bytes(rng.randrange(0, 256) for _ in range(rng.randrange(0, 300))).decode("latin-1")
        bounded(compile_source, src, DEFAULT, False)


@pytest.mark.parametrize("src", [
    "}" * 500, "{" * 500, "(" * 500, ")" * 500, "[" * 500, "]" * 500, ";" * 1000,
    "else " * 200, "func " * 200, "int " * 200, "\"" * 99, "@" * 500, "1.2.3." * 100,
    "if (" * 300, "while (true) " * 100, "return " * 200, "int x = " * 200,
    "func int f(" + "int a, " * 400 + "int z) { return 1; }",
    "print(" + "f(" * 200 + "1" + ")" * 200 + ");",
    "int[] a = [" + "[" * 200 + "1" + "]" * 200 + "];",
    "\n" * 5000, " " * 5000,
])
def test_pathological_inputs(src):
    bounded(compile_source, src, DEFAULT, False)


def test_valid_programs_have_no_diagnostics_and_run():
    for src in PROGRAMS:
        res = compile_source(src)
        assert res.diagnostics == []
        bounded(lambda: TACInterpreter(res.functions).run())


def test_every_mutated_program_that_compiles_also_runs_or_fails_cleanly():
    rng = random.Random(99)
    for _ in range(300):
        src = mutate(rng.choice(PROGRAMS), rng)
        res = bounded(compile_source, src)
        if res.ok:
            try:
                bounded(lambda: TACInterpreter(res.functions, max_steps=200_000).run())
            except LumenRuntimeError as e:
                assert e.code.startswith("R")


def test_deterministic_output():
    src = "int a = 1\nint b = zzz;\nwhlie (a) { }\n"
    r1 = [d.to_dict() for d in compile_source(src).diagnostics]
    r2 = [d.to_dict() for d in compile_source(src).diagnostics]
    assert r1 == r2
