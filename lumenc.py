#!/usr/bin/env python3
"""
lumenc - the Lumen compiler.

    python3 lumenc.py FILE.lum              compile, report diagnostics, run
    python3 lumenc.py FILE.lum --check      compile only (no execution)
    python3 lumenc.py FILE.lum --fix        apply machine-applicable fixes (rewrites FILE)
    python3 lumenc.py FILE.lum --fix --diff show the fixes as a diff, do not write
    python3 lumenc.py --explain E0412       explain an error code
    python3 lumenc.py --grammar             print FIRST/FOLLOW tables
    python3 lumenc.py FILE.lum -O           optimise (SSA-based) before running
    python3 lumenc.py FILE.lum --emit-ssa   show the SSA form

Exit status: 0 success, 1 compile errors, 2 run-time error, 3 usage / I/O error.
See USER_GUIDE.md for every option.
"""

import argparse
import difflib
import json
import sys
import time

from lumen import (RecoveryConfig, compile_source, render_all, render_diagnostic, summary_line,
                   TACInterpreter, LumenRuntimeError, format_program, fix_to_fixpoint, explain,
                   SourceMap, Diagnostic, __version__)
from lumen.explain import all_codes
from lumen.interpreter import MAX_STEPS
from lumen.span import Span


def build_parser():
    p = argparse.ArgumentParser(prog="lumenc", description="Lumen: a diagnostics-first compiler.",
                                add_help=True)
    p.add_argument("file", nargs="?", help="source file (.lum)")
    p.add_argument("--version", action="version", version=f"lumenc {__version__}")
    p.add_argument("--no-color", action="store_true", help="plain-text diagnostics")
    p.add_argument("--color", action="store_true", help="force ANSI colour even when not a terminal")
    p.add_argument("--check", "--quiet-run", dest="check", action="store_true",
                   help="compile only; do not execute")
    p.add_argument("--emit-tac", action="store_true", help="print the three-address code")
    p.add_argument("-O", "--optimize", action="store_true",
                   help="optimise the three-address code (SSA-based) before running")
    p.add_argument("--emit-ssa", action="store_true",
                   help="print the SSA form with phi functions (after optimisation with -O)")
    p.add_argument("--opt-disable", metavar="LIST", default="",
                   help="with -O: comma-separated optimisations to switch off "
                        "(constants, copies, cse, dce)")
    p.add_argument("--opt-stats", action="store_true", help="with -O: print what the optimiser did")
    p.add_argument("--emit-tokens", action="store_true", help="print the token stream")
    p.add_argument("--json", action="store_true", help="machine-readable diagnostics on stdout")
    p.add_argument("--fix", action="store_true", help="apply machine-applicable fixes")
    p.add_argument("--unsafe-fixes", action="store_true",
                   help="with --fix: also apply 'maybe-incorrect' guesses")
    p.add_argument("--diff", action="store_true", help="with --fix: print a diff, do not write")
    p.add_argument("--explain", metavar="CODE", help="explain an error code and exit")
    p.add_argument("--list-codes", action="store_true", help="list all diagnostic codes")
    p.add_argument("--grammar", action="store_true", help="print grammar FIRST/FOLLOW tables")
    p.add_argument("--time", action="store_true", help="print per-phase timings")
    p.add_argument("--max-steps", type=int, default=MAX_STEPS, help="run-time instruction limit")
    g = p.add_argument_group("recovery ablation switches (for the evaluation)")
    g.add_argument("--fail-fast", action="store_true", help="stop at the first error")
    g.add_argument("--no-phrase-level", action="store_true")
    g.add_argument("--sync", choices=("first-follow", "semicolon"), default="first-follow")
    g.add_argument("--poison", choices=("expr", "full", "none"), default="expr")
    g.add_argument("--no-token-poison", action="store_true")
    g.add_argument("--no-suppress", action="store_true")
    g.add_argument("--no-mute-damaged", action="store_true")
    g.add_argument("--no-infer-lost-decl", action="store_true")
    g.add_argument("--error-gap", type=int, default=3, help="tokens of silence after a parse error (0 = off)")
    return p


def make_config(a) -> RecoveryConfig:
    return RecoveryConfig(phrase_level=not a.no_phrase_level, sync=a.sync, poison=a.poison,
                          token_poison=not a.no_token_poison, suppress=not a.no_suppress,
                          fail_fast=a.fail_fast, error_gap=a.error_gap,
                          mute_damaged=not a.no_mute_damaged,
                          infer_lost_decl=not a.no_infer_lost_decl)


def render_runtime_error(e: LumenRuntimeError, source: str, path: str, color: bool) -> str:
    span = e.span or Span(0, 0)
    d = Diagnostic(e.code, e.message, span, phase="runtime")
    out = render_diagnostic(d, SourceMap(source), path, color) if e.span else \
        f"error[{e.code}]: {e.message}\n"
    return out.rstrip("\n")


def main(argv=None):
    a = build_parser().parse_args(argv)
    color = False if a.no_color else (a.color or sys.stdout.isatty())

    if a.list_codes:
        for c in all_codes():
            print(f"{c}  {explain(c).splitlines()[0].split(': ', 1)[1]}")
        return 0
    if a.explain:
        text = explain(a.explain)
        if text is None:
            print(f"lumenc: unknown code '{a.explain}' (try --list-codes)", file=sys.stderr)
            return 3
        print(text)
        return 0
    if a.grammar:
        from lumen.grammar import format_tables
        print(format_tables())
        return 0
    if not a.file:
        build_parser().print_usage(sys.stderr)
        return 3

    try:
        with open(a.file, "r", encoding="utf-8") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"lumenc: cannot read '{a.file}': {e}", file=sys.stderr)
        return 3

    cfg = make_config(a)

    if a.fix:
        fixed, n, res = fix_to_fixpoint(source, cfg, unsafe=a.unsafe_fixes)
        if a.diff:
            sys.stdout.writelines(difflib.unified_diff(
                source.splitlines(True), fixed.splitlines(True), a.file, a.file + " (fixed)"))
        elif n and fixed != source:
            with open(a.file, "w", encoding="utf-8", newline="") as f:
                f.write(fixed)
        print(f"lumenc: applied {n} fix{'es' if n != 1 else ''}"
              f"{' (dry run)' if a.diff else ''}; {len(res.errors)} error(s) remain.")
        if res.diagnostics:
            print(render_all(res.diagnostics, fixed, a.file, color))
        source = fixed
        return 0 if res.ok else 1

    opt = False
    if a.optimize:
        from lumen.optimizer import OptConfig
        off = {x.strip() for x in a.opt_disable.split(",") if x.strip()}
        bad = off - {"constants", "copies", "cse", "dce"}
        if bad:
            print(f"lumenc: unknown optimisation(s) {', '.join(sorted(bad))} "
                  f"(choose from constants, copies, cse, dce)", file=sys.stderr)
            return 3
        opt = OptConfig(**{k: k not in off for k in ("constants", "copies", "cse", "dce")})
    res = compile_source(source, cfg, optimize=opt)

    if a.emit_tokens:
        for t in res.tokens:
            print(f"{t.kind.name:<10} {t.text!r}")
    if a.time:
        print("timings: " + ", ".join(f"{k}={v*1000:.2f}ms" for k, v in res.timings.items()))

    if a.json:
        smap = SourceMap(source)
        print(json.dumps({"file": a.file, "ok": res.ok,
                          "diagnostics": [d.to_dict(smap) for d in res.diagnostics]}, indent=2))
        return 0 if res.ok else 1

    if res.diagnostics:
        print(render_all(res.diagnostics, source, a.file, color))
    if not res.ok:
        print("(codegen and execution skipped)")
        return 1
    if not res.diagnostics:
        print("compilation succeeded - 0 diagnostics.\n")
    else:
        print()

    if a.emit_ssa:
        from lumen.optimizer import ssa_text
        print("--- SSA form" + (" (optimised)" if a.optimize else "") + " ---")
        print(ssa_text(res.unoptimised or res.functions, optimized=a.optimize))
        print("--- end SSA ---\n")
    if a.emit_tac:
        print("--- three-address code" + (" (optimised)" if a.optimize else "") + " ---")
        print(format_program(res.functions))
        print("--- end TAC ---\n")
    if a.opt_stats and res.opt_stats is not None:
        print("optimiser: " + res.opt_stats.summary() + "\n")
    if a.check:
        return 0

    t0 = time.perf_counter()
    interp = TACInterpreter(res.functions, max_steps=a.max_steps, print_fn=print)
    try:
        interp.run()
    except LumenRuntimeError as e:
        sys.stdout.flush()
        print(render_runtime_error(e, source, a.file, color))
        return 2
    if a.time:
        print(f"run: {interp.steps} TAC steps in {(time.perf_counter()-t0)*1000:.2f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
