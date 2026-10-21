#!/usr/bin/env python3
"""
Seeded-defect evaluation harness.

    python3 eval/run_eval.py [--out docs/results] [--seed 2024]

Compiles every mutant of eval/corpus.py under a set of compiler
configurations and scores them against the known ground truth. The
configurations are the Phase 2 compiler (frozen snapshot), the "naive" and
"fail-fast" baselines, the full Phase 3 compiler, and Phase 3 with each
recovery technique switched off one at a time (ablation).

Metrics (single-defect set, 1 injected defect per program):
  detected   >= 1 error reported
  located    an error whose line lies in [defect line, defect line + tol]
  exact      a located error whose code is one of the acceptable codes
  clean      exactly one error and it is located (no cascade, no noise)
  extra      mean number of diagnostics beyond the first (cascade size)
Metrics (multi-defect set, 3 injected defects per program):
  recall     fraction of defects with a located error
  precision  fraction of reported errors that fall in a defect window
Fix metrics (Phase 3 only): see fix_eval().
"""

import argparse
import json
import math
import os
import signal
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, "baselines"))

from eval.corpus import build_single, build_multi, build_fuzz   # noqa: E402
from lumen import (compile_source, RecoveryConfig, DEFAULT, NAIVE, FAIL_FAST,   # noqa: E402
                   fix_to_fixpoint, TACInterpreter, LumenRuntimeError)

TIMEOUT = 1


class _Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise _Timeout()


def with_timeout(fn, *a):
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(TIMEOUT)
    try:
        return fn(*a)
    finally:
        signal.alarm(0)


# ---- configurations ------------------------------------------------------------
CONFIGS = {
    "phase2":        None,        # frozen snapshot, special-cased below
    "failfast":      FAIL_FAST,
    "naive":         NAIVE,
    "phase3":        DEFAULT,
    "-phrase":       DEFAULT.with_(phrase_level=False),
    "-sync(FF)":     DEFAULT.with_(sync="semicolon"),
    "poison=full":   DEFAULT.with_(poison="full"),
    "-poison":       DEFAULT.with_(poison="none"),
    "-tokpoison":    DEFAULT.with_(token_poison=False),
    "-gap":          DEFAULT.with_(error_gap=0),
    "-mute":         DEFAULT.with_(mute_damaged=False),
    "-lostdecl":     DEFAULT.with_(infer_lost_decl=False),
    "-suppress":     DEFAULT.with_(suppress=False),
}
MAIN_CONFIGS = ["failfast", "naive", "phase2", "phase3"]
ABLATIONS = ["phase3", "-phrase", "-sync(FF)", "-poison", "poison=full", "-tokpoison", "-gap", "-mute", "-lostdecl", "-suppress"]


def _phase2_diag(source):
    import lumen_phase2 as p2
    toks, ld = p2.Lexer(source).scan()
    ps = p2.Parser(toks, source)
    items = ps.parse_program()
    ck = p2.TypeChecker(source)
    ck.check_program(items)
    ds = ld + ps.diagnostics + ck.diagnostics
    smap = p2.SourceMap(source)
    return [(d.code, "error", d.span.line_col(smap)[0]) for d in ds]


def diagnose(cfg_name, source):
    """-> (list of (code, severity, line), status) ; status in ok|hang|crash"""
    try:
        if cfg_name == "phase2":
            return with_timeout(_phase2_diag, source), "ok"
        res = with_timeout(compile_source, source, CONFIGS[cfg_name], False)
        from lumen.span import SourceMap
        sm = SourceMap(source)
        return [(d.code, d.severity, d.span.line_col(sm)[0]) for d in res.diagnostics], "ok"
    except _Timeout:
        return [], "hang"
    except RecursionError:
        return [], "crash"
    except Exception as e:                       # noqa: BLE001
        return [], "crash"


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


# ---- single-defect scoring -----------------------------------------------------------
def score_single(cfg_name, mutants):
    rows = []
    for m in mutants:
        diags, status = diagnose(cfg_name, m.source)
        errs = [d for d in diags if d[1] == "error"]
        located = [d for d in errs if m.line - m.pre <= d[2] <= m.line + m.tol]
        exact = [d for d in located if (not m.expected) or d[0] in m.expected]
        rows.append({
            "id": m.id, "op": m.op, "cat": m.category, "status": status,
            "n_err": len(errs), "detected": bool(errs), "located": bool(located),
            "exact": bool(exact), "clean": len(errs) == 1 and bool(located),
            "extra": max(0, len(errs) - 1),
            "codes": [d[0] for d in errs][:8],
        })
    return rows


def summarize_single(rows):
    n = len(rows)
    s = {"n": n}
    for k in ("detected", "located", "exact", "clean"):
        c = sum(r[k] for r in rows)
        lo, hi = wilson(c, n)
        s[k] = c / n
        s[k + "_ci"] = [lo, hi]
    s["mean_diags"] = sum(r["n_err"] for r in rows) / n
    s["mean_extra"] = sum(r["extra"] for r in rows) / n
    s["max_diags"] = max(r["n_err"] for r in rows)
    s["hang"] = sum(r["status"] == "hang" for r in rows)
    s["crash"] = sum(r["status"] == "crash" for r in rows)
    return s


# ---- multi-defect scoring -----------------------------------------------------------------
def score_multi(cfg_name, cases):
    rec_hit = rec_tot = prec_hit = prec_tot = 0
    exact_hit = 0
    per_case_extra = []
    for comps in cases:
        src = comps[0].source
        diags, status = diagnose(cfg_name, src)
        errs = [d for d in diags if d[1] == "error"]
        in_window = 0
        for m in comps:
            rec_tot += 1
            hits = [d for d in errs if m.line - m.pre <= d[2] <= m.line + m.tol]
            if hits:
                rec_hit += 1
            if any(d[0] in m.expected for d in hits):
                exact_hit += 1
        for d in errs:
            prec_tot += 1
            if any(m.line - m.pre <= d[2] <= m.line + m.tol for m in comps):
                prec_hit += 1
        per_case_extra.append(max(0, len(errs) - len(comps)))
    return {
        "cases": len(cases), "defects": rec_tot,
        "recall": rec_hit / rec_tot, "recall_ci": list(wilson(rec_hit, rec_tot)),
        "exact_recall": exact_hit / rec_tot,
        "precision": prec_hit / prec_tot if prec_tot else 0.0,
        "diags_per_defect": prec_tot / rec_tot,
    }


# ---- fix scoring --------------------------------------------------------------------------
def _run_output(source):
    res = compile_source(source)
    if not res.ok:
        return None
    try:
        return TACInterpreter(res.functions, max_steps=500_000).run()
    except LumenRuntimeError:
        return "RUNTIME_ERROR"


def fix_eval(mutants, unsafe):
    per_op = defaultdict(lambda: defaultdict(int))
    tot = defaultdict(int)
    for m in mutants:
        orig_out = _run_output(m.original)
        fixed, n, res = fix_to_fixpoint(m.source, DEFAULT, unsafe=unsafe)
        rec = per_op[m.op]
        for d in (rec, tot):
            d["n"] += 1
            if n:
                d["attempted"] += 1
            if res.ok:
                d["compiles"] += 1
            if fixed == m.original:
                d["restored"] += 1
            if res.ok and _run_output(fixed) == orig_out:
                d["same_output"] += 1
            if n and not (res.ok and _run_output(fixed) == orig_out):
                d["wrong_fix"] += 1
    return {"total": dict(tot), "per_op": {k: dict(v) for k, v in per_op.items()}}


# ---- reporting ----------------------------------------------------------------------------
def pct(x):
    return f"{100 * x:.1f}%"


def write_markdown(path, single, multi, fixes, per_op_tbl, meta, fuzz=None, title="Evaluation results"):
    L = []
    L.append(f"# {title}\n")
    L.append(f"Generated by `eval/run_eval.py` (seed {meta['seed']}). "
             f"{meta['n_single']} single-defect mutants from {meta['n_prog']} base programs; "
             f"{meta['n_multi']} three-defect programs. All figures are measured, not estimated.\n")
    L.append("## 1. Single-defect set - Phase 2 vs Phase 3 vs baselines\n")
    L.append("| config | detected | located | exact code | clean (1 error, right place) | mean diagnostics | max | hangs | crashes |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for c in MAIN_CONFIGS:
        s = single[c]
        L.append(f"| {c} | {pct(s['detected'])} | {pct(s['located'])} | {pct(s['exact'])} | "
                 f"{pct(s['clean'])} [{pct(s['clean_ci'][0])}-{pct(s['clean_ci'][1])}] | "
                 f"{s['mean_diags']:.2f} | {s['max_diags']} | {s['hang']} | {s['crash']} |")
    L.append("\n`clean` is the headline metric: a mutant has one defect, so the ideal report is exactly one diagnostic pointing at it. 95% Wilson intervals in brackets.\n")
    L.append("## 2. Multi-defect set (three defects per program)\n")
    L.append("| config | recall | exact-code recall | precision | diagnostics per defect |")
    L.append("|---|---|---|---|---|")
    for c in MAIN_CONFIGS:
        s = multi[c]
        L.append(f"| {c} | {pct(s['recall'])} | {pct(s['exact_recall'])} | {pct(s['precision'])} | {s['diags_per_defect']:.2f} |")
    L.append("\nFail-fast can never exceed 1/3 recall here by construction; it is included as the classic-compiler reference point.\n")
    L.append("## 3. Ablation - what each recovery technique contributes\n")
    L.append("| config | exact | clean | mean diagnostics | multi recall | multi precision | diags/defect |")
    L.append("|---|---|---|---|---|---|---|")
    for c in ABLATIONS:
        s, m = single[c], multi[c]
        L.append(f"| {c} | {pct(s['exact'])} | {pct(s['clean'])} | {s['mean_diags']:.2f} | "
                 f"{pct(m['recall'])} | {pct(m['precision'])} | {m['diags_per_defect']:.2f} |")
    L.append("\nRows starting with `-` switch that single technique off; `poison=full` additionally poisons the variable itself (the Phase 2 policy).\n")
    L.append("## 4. Per-operator breakdown (clean rate: Phase 2 -> Phase 3)\n")
    L.append("| defect type | n | category | phase2 clean | phase3 clean | phase2 mean diags | phase3 mean diags |")
    L.append("|---|---|---|---|---|---|---|")
    for op, r in per_op_tbl.items():
        L.append(f"| {op} | {r['n']} | {r['cat']} | {pct(r['p2_clean'])} | {pct(r['p3_clean'])} | {r['p2_mean']:.2f} | {r['p3_mean']:.2f} |")
    if fuzz:
        L.append("\n## 5. Token-level fuzz set (harder, untargeted)\n")
        L.append(f"{meta['n_fuzz']} mutants made by deleting, inserting or replacing ONE random token (mutants the compiler accepts as valid programs are discarded). "
                 "Any error near the mutation counts as `located`; `clean` = exactly one error, located. No expected error code is assumed.\n")
        L.append("| config | located | clean | mean diagnostics | max | hangs | crashes |")
        L.append("|---|---|---|---|---|---|---|")
        for c in MAIN_CONFIGS + [x for x in ABLATIONS if x != "phase3"]:
            f = fuzz[c]
            L.append(f"| {c} | {pct(f['located'])} | {pct(f['clean'])} [{pct(f['clean_ci'][0])}-{pct(f['clean_ci'][1])}] | {f['mean_diags']:.2f} | {f['max_diags']} | {f['hang']} | {f['crash']} |")
        L.append("\nThis is where the individual recovery techniques separate: on the targeted set the phrase-level repairs already isolate most defects, but random token damage forces panic-mode and cascade suppression to do real work.\n")
    L.append("\n## 6. Auto-fix (`--fix`) quality\n")
    L.append("Each mutant is repaired to a fixpoint and compared with the original program.\n")
    L.append("| mode | n | fix attempted | compiles after | restores original text | same program output | wrong fix (applied but not equivalent) |")
    L.append("|---|---|---|---|---|---|---|")
    for mode in ("safe", "unsafe"):
        t = fixes[mode]["total"]
        n = t["n"]
        L.append(f"| {mode} | {n} | {pct(t.get('attempted',0)/n)} | {pct(t.get('compiles',0)/n)} | "
                 f"{pct(t.get('restored',0)/n)} | {pct(t.get('same_output',0)/n)} | "
                 f"{t.get('wrong_fix',0)} ({pct(t.get('wrong_fix',0)/max(1,t.get('attempted',0)))} of attempted) |")
    L.append("\n`safe` = machine-applicable edits only (default `--fix`); `unsafe` also applies maybe-incorrect guesses (`--unsafe-fixes`).\n")
    L.append("Per defect type (safe mode):\n")
    L.append("| defect type | n | attempted | restores original | wrong fix |")
    L.append("|---|---|---|---|---|")
    for op, v in sorted(fixes["safe"]["per_op"].items()):
        L.append(f"| {op} | {v['n']} | {v.get('attempted',0)} | {v.get('restored',0)} | {v.get('wrong_fix',0)} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def make_charts(outdir, single, multi, fixes, name="EVALUATION"):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    pal = {"failfast": "#8a8f98", "naive": "#c97b4a", "phase2": "#5b8def", "phase3": "#1f9d6b"}
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    names = MAIN_CONFIGS
    ax[0].bar(names, [single[c]["mean_diags"] for c in names], color=[pal[c] for c in names])
    ax[0].axhline(1, ls="--", c="k", lw=1); ax[0].set_title("Diagnostics reported per injected defect\n(ideal = 1.0, single-defect set)")
    ax[0].set_ylabel("mean diagnostics")
    for i, c in enumerate(names):
        ax[0].text(i, single[c]["mean_diags"] + 0.03, f"{single[c]['mean_diags']:.2f}", ha="center")
    ax[1].bar(names, [100 * single[c]["clean"] for c in names], color=[pal[c] for c in names])
    ax[1].set_title("Clean reports\n(exactly 1 error, at the defect)"); ax[1].set_ylabel("% of mutants")
    ax[1].set_ylim(0, 100)
    for i, c in enumerate(names):
        ax[1].text(i, 100 * single[c]["clean"] + 1.5, f"{100*single[c]['clean']:.0f}%", ha="center")
    for a in ax:
        a.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(os.path.join(outdir, f"fig_cascade_{name}.png"), dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    x = range(len(ABLATIONS))
    ax.bar([i - 0.2 for i in x], [100 * single[c]["clean"] for c in ABLATIONS], 0.4, label="single: clean %", color="#1f9d6b")
    ax.bar([i + 0.2 for i in x], [100 * multi[c]["precision"] for c in ABLATIONS], 0.4, label="multi: precision %", color="#5b8def")
    ax.set_xticks(list(x)); ax.set_xticklabels(ABLATIONS, rotation=25, ha="right")
    ax.set_title("Ablation: effect of switching off one recovery technique")
    ax.legend(frameon=False); ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(os.path.join(outdir, f"fig_ablation_{name}.png"), dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    modes = ["safe", "unsafe"]
    keys = [("compiles", "compiles after fix"), ("restored", "restores original"), ("wrong_fix", "wrong fix (of all)")]
    w = 0.25
    for j, (k, lab) in enumerate(keys):
        vals = [100 * fixes[m]["total"].get(k, 0) / fixes[m]["total"]["n"] for m in modes]
        ax.bar([i + (j - 1) * w for i in range(2)], vals, w, label=lab)
    ax.set_xticks(range(2)); ax.set_xticklabels(["--fix (safe)", "--fix --unsafe-fixes"])
    ax.set_ylabel("% of mutants"); ax.set_title("Auto-fix outcomes"); ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(os.path.join(outdir, f"fig_fix_{name}.png"), dpi=160); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "results"))
    ap.add_argument("--seed", type=int, default=2024)
    ap.add_argument("--name", default="EVALUATION")
    ap.add_argument("--title", default="Evaluation results")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    t0 = time.time()
    singles = build_single(seed=a.seed)
    multis = build_multi(seed=a.seed + 1)
    fuzzes = build_fuzz(seed=a.seed + 2)
    print(f"{len(singles)} single mutants, {len(multis)} multi cases, {len(fuzzes)} fuzz mutants")

    single_rows, single, multi, fuzz = {}, {}, {}, {}
    for c in CONFIGS:
        single_rows[c] = score_single(c, singles)
        single[c] = summarize_single(single_rows[c])
        multi[c] = score_multi(c, multis)
        fuzz[c] = summarize_single(score_single(c, fuzzes))
        print(f"{c:12s} clean={pct(single[c]['clean']):>6} exact={pct(single[c]['exact']):>6} "
              f"mean={single[c]['mean_diags']:.2f} hang={single[c]['hang']} crash={single[c]['crash']} "
              f"| multi recall={pct(multi[c]['recall'])} prec={pct(multi[c]['precision'])}")

    per_op = {}
    for op in sorted({m.op for m in singles}):
        idx = [i for i, m in enumerate(singles) if m.op == op]
        p2 = [single_rows["phase2"][i] for i in idx]
        p3 = [single_rows["phase3"][i] for i in idx]
        per_op[op] = {"n": len(idx), "cat": singles[idx[0]].category,
                      "p2_clean": sum(r["clean"] for r in p2) / len(idx),
                      "p3_clean": sum(r["clean"] for r in p3) / len(idx),
                      "p2_mean": sum(r["n_err"] for r in p2) / len(idx),
                      "p3_mean": sum(r["n_err"] for r in p3) / len(idx)}

    fixes = {"safe": fix_eval(singles, False), "unsafe": fix_eval(singles, True)}
    for k in fixes:
        print(k, fixes[k]["total"])

    meta = {"seed": a.seed, "n_single": len(singles), "n_multi": len(multis),
            "n_prog": len({m.program for m in singles}), "n_fuzz": len(fuzzes), "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(a.out, a.name + ".json"), "w") as f:
        json.dump({"meta": meta, "single": single, "multi": multi, "fuzz": fuzz, "per_op": per_op,
                   "fixes": fixes, "rows": single_rows}, f, indent=1)
    write_markdown(os.path.join(a.out, a.name + ".md"), single, multi, fixes, per_op, meta, fuzz, a.title)
    make_charts(a.out, single, multi, fixes, a.name)
    print("done in", meta["seconds"], "s ->", a.out)


if __name__ == "__main__":
    main()
