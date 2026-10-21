"""Tests for CFG construction, SSA form and the optimiser.

The central property is *semantic equivalence*: for every program, the
optimised code must print the same output and fail with the same run-time
error (or none) as the unoptimised code. It is checked on every catalogued
test program, on the evaluation programs, and on thousands of random programs
(tests/fuzzgen.py). The remaining tests pin individual transformations and the
rules that stop the optimiser from changing behaviour.
"""

import glob
import os

import pytest

from lumen import compile_source, TACInterpreter, LumenRuntimeError, format_program
from lumen.cfg import build_cfg, compute_dominators, dominance_frontiers, format_cfg
from lumen.optimizer import (OptStats, _FunctionOptimiser, _const_text, _escaping_globals, fold,
                             optimize_program, ssa_text)
from lumen.ssa import to_ssa, verify_ssa
from tests.cases import CASES
from tests.fuzzgen import generate
from tests.test_cli import lumenc, write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_tac(functions, max_steps=3_000_000):
    interp = TACInterpreter(functions, max_steps=max_steps)
    try:
        interp.run()
        return ("ok", interp.output, None)
    except LumenRuntimeError as e:
        return ("err", list(interp.output), e.code)


def both(src):
    res = compile_source(src, optimize=True)
    assert res.ok, res.codes
    return run_tac(res.unoptimised), run_tac(res.functions), res


def main_ops(res):
    return [i.op for i in res.functions["__main__"].instrs]


# ---------------------------------------------------------------------------
# equivalence

CASE_SOURCES = [(c["id"], c["src"]) for c in CASES if compile_source(c["src"]).ok]
EVAL_PROGRAMS = [p for p in sorted(glob.glob(os.path.join(ROOT, "eval", "programs", "*.lum"))
                                   + glob.glob(os.path.join(ROOT, "examples", "*.lum")))
                 if compile_source(open(p, encoding="utf-8").read()).ok]


@pytest.mark.parametrize("cid,src", CASE_SOURCES, ids=[c[0] for c in CASE_SOURCES])
def test_catalogued_programs_behave_identically(cid, src):
    plain, opt, _ = both(src)
    assert plain == opt


@pytest.mark.parametrize("path", EVAL_PROGRAMS, ids=[os.path.basename(p) for p in EVAL_PROGRAMS])
def test_example_and_eval_programs_behave_identically(path):
    src = open(path, encoding="utf-8").read()
    plain, opt, _ = both(src)
    assert plain == opt


@pytest.mark.parametrize("risky", [True, False])
def test_random_programs_behave_identically(risky):
    ran = 0
    for seed in range(250):
        src = generate(seed, risky)
        res = compile_source(src, optimize=True)
        assert res.ok, (seed, res.codes)
        plain = run_tac(res.unoptimised)
        if plain[2] == "R0006":
            continue
        assert run_tac(res.functions) == plain, f"seed {seed}\n{src}"
        ran += 1
    assert ran > 200


def test_random_programs_exercise_errors_and_successes():
    kinds = set()
    for seed in range(120):
        res = compile_source(generate(seed, True))
        kinds.add(run_tac(res.functions)[0])
    assert kinds == {"ok", "err"}


def test_optimising_twice_is_stable():
    for seed in range(60):
        res = compile_source(generate(seed, False), optimize=True)
        again, _ = optimize_program(res.functions)
        assert run_tac(again) == run_tac(res.unoptimised)
        n1 = sum(len(f.instrs) for f in res.functions.values())
        n2 = sum(len(f.instrs) for f in again.values())
        assert n2 <= n1


# ---------------------------------------------------------------------------
# CFG and SSA structure

def test_ssa_invariants_hold_before_and_after_optimisation():
    checked = 0
    for seed in range(120):
        res = compile_source(generate(seed))
        globals_ = _escaping_globals(res.functions)
        for fn in res.functions.values():
            cfg, names = to_ssa(fn, globals_)
            assert verify_ssa(cfg, fn.params, names) == [], (seed, fn.name)
            _FunctionOptimiser(cfg, names, OptStats()).run()
            assert verify_ssa(cfg, fn.params, names) == [], (seed, fn.name, "after")
            checked += 1
    assert checked > 100


def test_phi_is_placed_where_branches_join():
    src = ("func int pick(int c) { int x = 0; if (c > 0) { x = 1; } else { x = 2; } return x; }\n"
           "print(pick(1)); print(pick(-1));\n")
    res = compile_source(src)
    cfg, names = to_ssa(res.functions["pick"], set())
    phis = [p for b in cfg.blocks.values() for p in b.phis]
    assert len(phis) == 1 and phis[0].var == "x" and len(phis[0].args) == 2
    assert "phi(" in ssa_text(res.functions)


def test_loop_header_gets_phis_for_loop_carried_variables():
    src = "int i = 0; int s = 0;\nwhile (i < 4) { s = s + i; i = i + 1; }\nprint(s);\n"
    res = compile_source(src)
    cfg, _ = to_ssa(res.functions["__main__"], set())
    carried = {p.var for b in cfg.blocks.values() for p in b.phis}
    assert {"i", "s"} <= carried


def test_phi_is_pruned_when_the_variable_is_dead():
    # x is assigned in both branches but never read afterwards: no phi needed
    src = ("func void f(int c) { int x = 0; if (c > 0) { x = 1; } else { x = 2; } print(c); }\n"
           "f(1);\n")
    res = compile_source(src)
    cfg, _ = to_ssa(res.functions["f"], set())
    assert [p for b in cfg.blocks.values() for p in b.phis if p.var == "x"] == []


def test_dominators_and_frontiers_on_a_diamond():
    src = "func int f(int c) { int x = 0; if (c > 0) { x = 1; } else { x = 2; } return x; }\nprint(f(1));\n"
    res = compile_source(src)
    cfg = build_cfg(res.functions["f"])
    idom, children, rpo = compute_dominators(cfg)
    df = dominance_frontiers(cfg, idom)
    joins = [b for b in cfg.blocks.values() if len(b.preds) == 2]
    assert len(joins) == 1
    join = joins[0].id
    for p in joins[0].preds:                      # each branch has the join in its frontier
        assert join in df[p]
    assert all(idom[b] == cfg.entry or b == cfg.entry or idom[b] in idom for b in idom)


def test_critical_edges_are_split():
    src = "int i = 0;\nwhile (i < 3) { if (i == 1) { print(i); } i = i + 1; }\n"
    res = compile_source(src)
    cfg = build_cfg(res.functions["__main__"])
    for b in cfg.blocks.values():
        if len(b.succs()) > 1:
            assert all(len(cfg.blocks[s].preds) == 1 for s in b.succs())


# ---------------------------------------------------------------------------
# individual transformations

def test_constant_folding():
    _, _, res = both("print(2 * 3 + 4);\n")
    ops = main_ops(res)
    assert "*" not in ops and "+" not in ops
    assert res.opt_stats.folded >= 2


def test_float_and_bool_and_string_folding_matches_the_interpreter():
    src = ('print(1.5 * 2.0); print(7 / 2); print(-7 % 3); print(1 < 2 && 3 > 2);\n'
           'print("ab" + "cd"); print("x" == "x"); print(10 / 4.0);\n')
    plain, opt, res = both(src)
    assert plain == opt and plain[1][0] == "3.0"
    assert not {"*", "/", "%", "+", "<", ">", "=="} & set(main_ops(res))


def test_branch_on_constant_is_folded_and_dead_arm_removed():
    plain, opt, res = both("if (1 > 2) { print(1); } else { print(2); }\n")
    assert opt == ("ok", ["2"], None) == plain
    assert "IF_FALSE" not in main_ops(res)
    assert res.opt_stats.branches_folded >= 1 and res.opt_stats.blocks_removed >= 1


def test_constants_propagate_through_variables_and_loops():
    src = "int a = 5;\nint b = a * 2;\nint c = b + a;\nprint(c);\n"
    plain, opt, res = both(src)
    assert opt[1] == ["15"]
    assert "*" not in main_ops(res)


def test_copy_propagation_removes_copies():
    src = "int[] v = [3, 1, 2];\nint i = 1;\nint j = i;\nint k = j;\nprint(v[k]);\n"
    plain, opt, res = both(src)
    assert opt == plain
    assert main_ops(res).count("COPY") == 0


def test_common_subexpression_elimination():
    src = ("func int f(int a, int b) { int x = a * b + 1; int y = a * b + 2; return x + y; }\n"
           "print(f(3, 4));\n")
    res = compile_source(src, optimize=True)
    assert res.opt_stats.cse >= 1
    assert [i.op for i in res.functions["f"].instrs].count("*") == 1
    assert run_tac(res.functions) == run_tac(res.unoptimised)


def test_cse_works_across_a_dominating_branch_but_not_across_siblings():
    src = ("func int f(int a, int b, bool c) {\n"
           "  int x = a + b;\n"
           "  int r = 0;\n"
           "  if (c) { r = a + b; } else { r = (a + b) * 2; }\n"
           "  return r + x + (a + b);\n}\n"
           "print(f(1, 2, true)); print(f(1, 2, false));\n")
    plain, opt, res = both(src)
    assert opt == plain == ("ok", ["9", "12"], None)
    assert [i.op for i in res.functions["f"].instrs].count("+") <= 3


def test_dead_code_is_removed():
    src = ("func int f(int a) { bool unused = a < 3; bool unused2 = !unused; return a; }\nprint(f(4));\n")
    res = compile_source(src, optimize=True)
    ops = [i.op for i in res.functions["f"].instrs]
    assert "<" not in ops and "not" not in ops
    assert res.opt_stats.dead_removed >= 2


def test_loop_invariant_constant_is_not_recomputed():
    plain, opt, res = both("int s = 0; int i = 0;\nwhile (i < 3) { s = s + 10 * 2; i = i + 1; }\nprint(s);\n")
    assert opt[1] == ["60"] and "*" not in main_ops(res)


def test_swap_in_a_loop_survives_phi_destruction():
    src = ("int a = 1; int b = 2; int i = 0;\n"
           "while (i < 3) { int t = a; a = b; b = t; i = i + 1; }\n"
           "print(a); print(b);\n")
    plain, opt, _ = both(src)
    assert opt == plain == ("ok", ["2", "1"], None)


def test_parallel_phis_with_a_cycle():
    src = ("func int f(int n) { int a = 1; int b = 2; int i = 0;\n"
           "  while (i < n) { int t = a; a = b; b = t; i = i + 1; }\n"
           "  return a * 10 + b; }\n"
           "print(f(0)); print(f(1)); print(f(2)); print(f(3));\n")
    plain, opt, _ = both(src)
    assert opt == plain == ("ok", ["12", "21", "12", "21"], None)


def test_nested_loops_and_early_return():
    src = ("func int find(int[] a, int n, int x) { int i = 0;\n"
           "  while (i < n) { if (a[i] == x) { return i; } i = i + 1; }\n"
           "  return -1; }\n"
           "int[] v = [5, 8, 13, 21];\n"
           "print(find(v, 4, 13)); print(find(v, 4, 99));\n")
    plain, opt, _ = both(src)
    assert opt == plain == ("ok", ["2", "-1"], None)


# ---------------------------------------------------------------------------
# the optimiser must not change what a program does

def test_division_by_zero_is_not_folded_away():
    plain, opt, _ = both("int z = 0;\nprint(1);\nint u = 1 / z;\nprint(2);\n")
    assert plain == opt == ("err", ["1"], "R0001")


def test_unused_division_is_not_deleted():
    plain, opt, res = both("int z = 0; int unused = 10 / z; print(5);\n")
    assert plain == opt == ("err", [], "R0001")
    assert "/" in main_ops(res)


def test_overflow_is_not_folded_away():
    plain, opt, _ = both("int big = 9223372036854775807;\nprint(1);\nint u = big + 1;\nprint(2);\n")
    assert plain == opt == ("err", ["1"], "R0004")


def test_unused_overflowing_multiplication_is_not_deleted():
    plain, opt, _ = both("int a = 4611686018427387904; int u = a * 4; print(0);\n")
    assert plain == opt == ("err", [], "R0004")


def test_out_of_bounds_read_survives_even_if_unused():
    plain, opt, _ = both("int[] a = [1, 2, 3];\nint i = 3;\nint u = a[i];\nprint(0);\n")
    assert plain == opt == ("err", [], "R0003")


def test_dead_branch_hides_its_error_exactly_as_before():
    src = "bool debug = false;\nint z = 0;\nif (debug) { print(1 / z); }\nprint(7);\n"
    plain, opt, res = both(src)
    assert plain == opt == ("ok", ["7"], None)
    assert "/" not in main_ops(res)


def test_division_by_safe_constant_may_be_deleted():
    src = "func int f(int a) { int unused = a / 2; return a; }\nprint(f(9));\n"
    res = compile_source(src, optimize=True)
    assert "/" not in [i.op for i in res.functions["f"].instrs]


def test_division_by_minus_one_is_kept_because_it_can_overflow():
    src = "func int f(int a) { int unused = a / -1; return 1; }\nprint(f(-9223372036854775807 - 1));\n"
    plain, opt, _ = both(src)
    assert plain == opt and plain[0] == "err" and plain[2] == "R0004"


def test_calls_and_prints_are_never_removed():
    src = "func int noisy(int x) { print(x); return x; }\nint unused = noisy(1);\nnoisy(2);\nprint(3);\n"
    plain, opt, _ = both(src)
    assert plain == opt == ("ok", ["1", "2", "3"], None)


def test_global_written_by_a_function_is_not_assumed_constant():
    src = ("int g = 0;\nfunc void bump() { g = g + 1; }\n"
           "bump(); bump();\nint a = g + 1;\nbump();\nint b = g + 1;\nprint(a); print(b);\n")
    plain, opt, _ = both(src)
    assert plain == opt == ("ok", ["3", "4"], None)


def test_expression_over_a_global_is_not_reused_across_a_call():
    src = ("int g = 1;\nfunc void set() { g = 5; }\n"
           "int a = g + 1;\nset();\nint b = g + 1;\nprint(a); print(b);\n")
    plain, opt, _ = both(src)
    assert plain == opt == ("ok", ["2", "6"], None)


def test_global_only_used_by_main_is_optimised():
    res = compile_source("int x = 4;\nint y = x * 5;\nprint(y);\n", optimize=True)
    assert "*" not in main_ops(res)


def test_array_aliasing_through_a_parameter():
    src = ("func void put(int[] a, int i, int v) { a[i] = v; }\n"
           "int[] xs = [0, 0, 0];\nint[] ys = xs;\nput(ys, 1, 9);\nprint(xs[1]); print(ys[1]);\n")
    plain, opt, _ = both(src)
    assert plain == opt == ("ok", ["9", "9"], None)


def test_short_circuit_side_effects_are_preserved():
    src = ("func bool loud(bool v) { print(9); return v; }\n"
           "print(false && loud(true)); print(true || loud(false)); print(true && loud(true));\n")
    plain, opt, _ = both(src)
    assert plain == opt == ("ok", ["false", "true", "9", "true"], None)


def test_recursion_and_step_limit_behave():
    src = "func int f(int n) { return f(n + 1); }\nprint(f(0));\n"
    plain, opt, _ = both(src)
    assert plain[0] == opt[0] == "err" and plain[2] == opt[2] == "R0005"
    plain, opt, _ = both("while (true) { }\n")
    assert plain[2] == opt[2] == "R0006"


def test_run_time_error_keeps_its_source_location_after_optimisation():
    src = "int z = 0;\nint a = 6;\nprint(a / z);\n"
    res = compile_source(src, optimize=True)
    with pytest.raises(LumenRuntimeError) as ei:
        TACInterpreter(res.functions).run()
    assert src[ei.value.span.start:ei.value.span.end] == "a / z"


def test_optimiser_never_increases_instruction_count_on_the_demo():
    src = open(os.path.join(ROOT, "examples", "optimize_demo.lum")).read()
    res = compile_source(src, optimize=True)
    assert res.opt_stats.instr_after < res.opt_stats.instr_before * 0.5
    assert run_tac(res.functions) == run_tac(res.unoptimised) == ("ok", ["160"], None)


def test_unoptimised_code_is_untouched_by_the_optimiser():
    res = compile_source("int a = 2 * 3;\nprint(a);\n", optimize=True)
    assert "t1 = 2" in format_program(res.unoptimised)
    plain = compile_source("int a = 2 * 3;\nprint(a);\n")
    assert format_program(plain.functions) == format_program(res.unoptimised)


# ---------------------------------------------------------------------------
# constant evaluation helpers

def test_fold_refuses_anything_that_could_fail():
    assert fold("+", 2 ** 63 - 1, 1) is None
    assert fold("*", 2 ** 62, 4) is None
    assert fold("/", 1, 0) is None and fold("%", 1, 0) is None
    assert fold("/", -(2 ** 63), -1) is None
    assert fold("neg", -(2 ** 63)) is None
    assert fold("/", 1.0, 0.0) is None


def test_fold_matches_interpreter_arithmetic():
    assert fold("/", -7, 2) == -3 and fold("%", -7, 2) == -1
    assert fold("/", 7, -2) == -3 and fold("%", 7, -2) == 1
    assert fold("+", 1, 2) == 3 and fold("<", 1, 2) is True
    assert fold("itof", 3) == 3.0 and fold("not", True) is False


@pytest.mark.parametrize("v", [0, 5, -5, 2 ** 63 - 1, 1.5, -0.0, 1e300, True, False, "abc", ""])
def test_const_text_round_trips_through_the_interpreter_parser(v):
    from lumen.interpreter import _parse_const
    t = _const_text(v)
    ok, back = _parse_const(t)
    assert ok and back == v and back.__class__ is v.__class__


def test_const_text_refuses_unrepresentable_values():
    assert _const_text(float("inf")) is None
    assert _const_text(float("nan")) is None
    assert _const_text('he said "hi"') is None


# ---------------------------------------------------------------------------
# command line

def test_cli_optimise_flag_runs_the_optimised_program(tmp_path):
    f = write(tmp_path, "o.lum", "int a = 2 * 3;\nprint(a + 1);\n")
    rc, out, _ = lumenc(f, "--no-color", "-O", "--opt-stats")
    assert rc == 0 and out.strip().splitlines()[-1] == "7"
    assert "optimiser:" in out and "folded" in out


def test_cli_emit_ssa_shows_phi_functions(tmp_path):
    f = write(tmp_path, "s.lum", "int i = 0;\nwhile (i < 3) { i = i + 1; }\nprint(i);\n")
    rc, out, _ = lumenc(f, "--no-color", "--emit-ssa", "--check")
    assert rc == 0 and "phi(" in out and "i$2" in out


def test_cli_emit_tac_with_O_shows_optimised_code(tmp_path):
    f = write(tmp_path, "t.lum", "print(2 * 3);\n")
    rc, out, _ = lumenc(f, "--no-color", "-O", "--emit-tac", "--check")
    assert "(optimised)" in out and "* " not in out.split("--- three-address")[1]


def test_cli_without_O_is_unchanged(tmp_path):
    f = write(tmp_path, "u.lum", "print(2 * 3);\n")
    rc, out, _ = lumenc(f, "--no-color", "--emit-tac", "--check")
    assert "optimised" not in out and "t3 = t1 * t2" in out


def test_cli_runtime_error_still_reported_under_O(tmp_path):
    f = write(tmp_path, "r.lum", "int z = 0;\nprint(4 / z);\n")
    rc, out, _ = lumenc(f, "--no-color", "-O")
    assert rc == 2 and "error[R0001]" in out


def test_string_concat_is_not_commuted_by_cse():
    src = ('string a = "ab";\nstring b = "c";\n'
           'print(a + b);\nprint(b + a);\nprint(a + b);\n')
    res = compile_source(src, optimize=True)
    assert res.ok
    from lumen import TACInterpreter
    i1 = TACInterpreter(res.unoptimised); i1.run()
    i2 = TACInterpreter(res.functions); i2.run()
    assert i1.output == i2.output == ["abc", "cab", "abc"]
