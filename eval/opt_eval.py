#!/usr/bin/env python3
"""
Evaluation of the SSA optimiser: does it preserve behaviour, and does it help?

  1. Equivalence campaign - thousands of random programs, each run unoptimised
     and optimised; output and run-time error must be identical.
  2. Effect on real programs - static instruction count, dynamic TAC steps and
     wall-clock run time, before and after.
  3. Contribution of each optimisation - the same measurements with one
     optimisation switched off at a time.
  4. Cost - how long the optimiser itself takes, and how that grows.

Writes docs/results/OPTIMIZER.md, results_opt.json and fig_opt.png.

    python3 eval/opt_eval.py [--programs-only] [--fuzz N]
"""

import argparse
import glob
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

from lumen import compile_source, TACInterpreter, LumenRuntimeError   # noqa: E402
from lumen.optimizer import OptConfig, optimize_program                # noqa: E402
from tests.fuzzgen import generate                                     # noqa: E402
from perf import gen_program, best_of                                  # noqa: E402

BENCH = {
    "loop 200k": "int i = 0; int s = 0;\nwhile (i < 200000) { s = s + i % 7; i = i + 1; }\nprint(s);\n",
    "fib(20)": "func int fib(int n) { if (n < 2) { return n; } return fib(n - 1) + fib(n - 2); }\nprint(fib(20));\n",
    "array 50k": "int[] a = [1,2,3,4,5,6,7,8];\nint i = 0; int s = 0;\nwhile (i < 50000) { s = s + a[i % 8]; i = i + 1; }\nprint(s);\n",
    "constants": "int scale = 2 * 3 + 4;\nint total = 0;\nint i = 0;\nwhile (i < 20000) { int k = scale * 2; total = total + k + (i * 3) + (i * 3); i = i + 1; }\nprint(total);\n",
}


def run(functions, max_steps=50_000_000):
    interp = TACInterpreter(functions, max_steps=max_steps)
    try:
        interp.run()
        return ("ok", interp.output, None), interp.steps
    except LumenRuntimeError as e:
        return ("err", list(interp.output), e.code), interp.steps


def static_count(functions):
    return sum(1 for f in functions.values() for i in f.instrs if i.op != "LABEL")


def load_programs():
    progs = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "eval", "programs", "*.lum"))):
        progs[os.path.basename(p)[:-4]] = open(p, encoding="utf-8").read()
    progs.update(BENCH)
    progs["optimize_demo"] = open(os.path.join(ROOT, "examples", "optimize_demo.lum")).read()
    return progs


def equivalence(n):
    out = {}
    for label, risky in (("safe", False), ("risky", True)):
        ran = mism = errs = skipped = 0
        before = after = 0
        for seed in range(n):
            res = compile_source(generate(seed, risky), optimize=True)
            if not res.ok:
                skipped += 1
                continue
            a, sa = run(res.unoptimised, 3_000_000)
            if a[2] == "R0006":
                skipped += 1
                continue
            b, sb = run(res.functions, 3_000_000)
            ran += 1
            errs += a[0] == "err"
            before += sa
            after += sb
            if a != b:
                mism += 1
        out[label] = {"programs": ran, "mismatches": mism, "runtime_error_programs": errs,
                      "skipped": skipped, "steps_before": before, "steps_after": after}
        print(label, out[label], flush=True)
    return out


def effect(progs):
    rows = []
    for name, src in progs.items():
        res = compile_source(src, optimize=True)
        if not res.ok:
            continue
        (a, sa), (b, sb) = run(res.unoptimised), run(res.functions)

        def timed(f):
            return best_of(lambda: run(f), 3) * 1e3
        ta, tb = timed(res.unoptimised), timed(res.functions)
        rows.append({"program": name, "static_before": static_count(res.unoptimised),
                     "static_after": static_count(res.functions), "steps_before": sa, "steps_after": sb,
                     "ms_before": ta, "ms_after": tb, "same": a == b})
        print(rows[-1], flush=True)
    return rows


def ablation(progs):
    variants = {"all on": OptConfig(), "no constants": OptConfig(constants=False),
                "no copies": OptConfig(copies=False), "no cse": OptConfig(cse=False),
                "no dce": OptConfig(dce=False), "none": OptConfig(False, False, False, False)}
    compiled = {n: compile_source(s) for n, s in progs.items()}
    compiled = {n: r for n, r in compiled.items() if r.ok}
    base = {n: run(r.functions)[1] for n, r in compiled.items()}
    rows = []
    for vname, cfg in variants.items():
        steps = static = 0
        ok = True
        for n, r in compiled.items():
            opt, _ = optimize_program(r.functions, cfg)
            res, st = run(opt)
            ok &= res == run(r.functions)[0]
            steps += st
            static += static_count(opt)
        rows.append({"variant": vname, "steps": steps, "static": static, "same": ok,
                     "steps_before": sum(base.values()),
                     "static_before": sum(static_count(r.functions) for r in compiled.values())})
        print(rows[-1], flush=True)
    return rows


def cost():
    rows = []
    for n in (400, 800, 1600, 3200, 6400):
        src = gen_program(n)
        res = compile_source(src)
        t_opt = best_of(lambda: optimize_program(res.functions), 3)
        t_all = best_of(lambda: compile_source(src), 3)
        rows.append({"loc": src.count("\n"), "compile_ms": t_all * 1e3, "optimise_ms": t_opt * 1e3})
        print(rows[-1], flush=True)
    return rows


def slope(xs, ys):
    lx, ly = [math.log(x) for x in xs], [math.log(max(y, 1e-9)) for y in ys]
    mx, my = sum(lx) / len(lx), sum(ly) / len(ly)
    return sum((a - mx) * (b - my) for a, b in zip(lx, ly)) / sum((a - mx) ** 2 for a in lx)


def write_report(out):
    L = ["# Optimiser results\n",
         "Produced by `python3 eval/opt_eval.py`. Every number is measured, not estimated.\n"]
    eq = out.get("equivalence")
    if eq:
        L += ["## 1. Does optimisation preserve behaviour?\n",
              "Each random program (`tests/fuzzgen.py`) is run unoptimised and optimised; the output, and the "
              "run-time error if any, must be identical. *risky* programs include overflow, division by zero "
              "and out-of-range indexes; *safe* programs mostly run to completion.\n",
              "| generator | programs compared | with a run-time error | mismatches | dynamic steps before | after |",
              "|---|---|---|---|---|---|"]
        for k in ("safe", "risky"):
            e = eq[k]
            L.append(f"| {k} | {e['programs']} | {e['runtime_error_programs']} | **{e['mismatches']}** | "
                     f"{e['steps_before']} | {e['steps_after']} ({100 * (1 - e['steps_after'] / e['steps_before']):.0f}% fewer) |")
        L.append("")
    rows = out["effect"]
    L += ["## 2. Effect on real programs\n",
          "| program | instructions before | after | TAC steps before | after | steps saved | run ms before | after | same output |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['program']} | {r['static_before']} | {r['static_after']} | {r['steps_before']} | "
                 f"{r['steps_after']} | {100 * (1 - r['steps_after'] / max(r['steps_before'], 1)):.0f}% | "
                 f"{r['ms_before']:.1f} | {r['ms_after']:.1f} | {r['same']} |")
    tot_b = sum(r["steps_before"] for r in rows)
    tot_a = sum(r["steps_after"] for r in rows)
    L += [f"\nAcross all programs: {tot_b} steps -> {tot_a} ({100 * (1 - tot_a / tot_b):.0f}% fewer).\n"]
    L += ["## 3. Contribution of each optimisation\n",
          "All programs above, optimised with one optimisation switched off at a time.\n",
          "| configuration | static instructions | dynamic steps | steps vs. unoptimised | output unchanged |",
          "|---|---|---|---|---|"]
    for r in out["ablation"]:
        L.append(f"| {r['variant']} | {r['static']} | {r['steps']} | "
                 f"{abs(100 * (1 - r['steps'] / r['steps_before'])):.0f}% {'fewer' if r['steps'] <= r['steps_before'] else 'more'} | {r['same']} |")
    L += ["\n`none` runs the SSA round trip with every pass off: it shows that converting to SSA and back "
          "itself adds steps (extra copies), so the gains come from the passes.\n"]
    c = out["cost"]
    L += ["## 4. Cost of optimising\n", "| lines | whole compile ms | of which optimiser ms |", "|---|---|---|"]
    for r in c:
        L.append(f"| {r['loc']} | {r['compile_ms']:.0f} | {r['optimise_ms']:.0f} |")
    L.append(f"\nLog-log slope of optimiser time against program size: **{out['cost_slope']:.2f}** "
             "(1.00 = linear).\n")
    with open(os.path.join(ROOT, "docs", "results", "OPTIMIZER.md"), "w") as f:
        f.write("\n".join(L))


def figure(out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    rows = out["effect"]
    names = [r["program"] for r in rows]
    saved = [100 * (1 - r["steps_after"] / max(r["steps_before"], 1)) for r in rows]
    order = sorted(range(len(rows)), key=lambda i: saved[i])
    fig, ax = plt.subplots(figsize=(8, 0.38 * len(rows) + 1.2))
    ax.barh([names[i] for i in order], [saved[i] for i in order], color="#18855F")
    ax.set_xlabel("TAC steps removed by the optimiser (%)")
    ax.set_title("Dynamic instruction count saved per program")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(ROOT, "docs", "results", "fig_opt.png"), dpi=140)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fuzz", type=int, default=2500, help="random programs per generator")
    ap.add_argument("--programs-only", action="store_true")
    a = ap.parse_args()
    progs = load_programs()
    out = {}
    if not a.programs_only:
        out["equivalence"] = equivalence(a.fuzz)
    out["effect"] = effect(progs)
    out["ablation"] = ablation(progs)
    out["cost"] = cost()
    out["cost_slope"] = slope([r["loc"] for r in out["cost"]], [r["optimise_ms"] for r in out["cost"]])
    with open(os.path.join(ROOT, "docs", "results", "results_opt.json"), "w") as f:
        json.dump(out, f, indent=1)
    write_report(out)
    figure(out)
    print("wrote docs/results/OPTIMIZER.md")


if __name__ == "__main__":
    main()
