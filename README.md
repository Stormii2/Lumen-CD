# Lumen

**A diagnostics-first compiler for a small statically-typed language - lexer, recursive-descent parser, type checker, three-address-code generator and interpreter, built around multi-layer error recovery and machine-applicable repair suggestions.**



Most compilers either stop at the first error or recover so crudely that one mistake produces a dozen spurious messages. Lumen treats **diagnostic quality as the primary design axis**: it keeps going after an error, suppresses the cascade that error would normally cause, explains what is wrong with a source caret, and offers a repair - which `--fix` can apply.

```
$ python3 lumenc.py examples/demo.lum --no-color
error[E0410]: unknown type 'itn'
 --> examples/demo.lum:2:1
  |
2 | itn count = 10;
  | ^^^
  |
help: did you mean 'int'?
fix (machine-applicable): replace 'itn' with 'int'
  | int count = 10;
...
7 errors generated.          <- seven defects in the file, seven diagnostics
```

## Status: complete

| Area | Delivered |
|---|---|
| Front end | lexer with recovery; LL(1) grammar as data with computed FIRST/FOLLOW; recursive-descent + Pratt parser |
| Recovery | token poisoning, phrase-level repair, FIRST/FOLLOW-derived panic mode, error-distance rule, damaged-statement muting, lost-declaration inference, error-type poisoning, containment suppression - each independently switchable |
| Semantics | scoped symbol table, type checker with error-poisoned lattice, int->float widening, return-path analysis, warnings |
| Repair | Damerau-Levenshtein ranking, applicability classes (machine-applicable / maybe-incorrect), `--fix`, `--fix --diff`, `--unsafe-fixes`, `--explain`, `--json` |
| Back end | three-address code, fast pre-decoded interpreter, located run-time errors |
| Verification | ~320 pytest tests (124 table-driven functional cases), fuzz/robustness tests, seeded-defect evaluation with ablation, performance study |

## Quick start

```
python3 lumenc.py examples/valid.lum            # compile and run
python3 lumenc.py examples/typos.lum            # see diagnostics and repairs
python3 lumenc.py examples/demo.lum --fix --diff   # preview automatic repairs
python3 lumenc.py --explain E0412               # explain an error code
python3 -m pytest tests -q                      # run the test-suite
```

Python 3.8+; no dependencies to compile and run. See **[USER_GUIDE.md](USER_GUIDE.md)** for every option.

## Repository map

| Path | Contents |
|---|---|
| `lumenc.py` | command-line driver |
| `lumen/` | the compiler package (`lexer`, `parser`, `grammar`, `checker`, `suggest`, `repair`, `diagnostics`, `fixer`, `tac`, `interpreter`, `renderer`, `explain`, ...) |
| `examples/` | sample programs |
| `tests/` | pytest suite; `tests/cases.py` is the functional test table |
| `eval/` | seeded-defect corpus generator, evaluation harness, performance benchmarks, frozen snapshot of the first working version, used as a baseline (`eval/baselines/`) |
| `docs/LANGUAGE.md` | language reference and grammar |
| `docs/ARCHITECTURE.md` | design of the pipeline and the recovery layers |
| `docs/ERROR_CODES.md` | every diagnostic code with an example |
| `docs/TEST_CASES.md`, `docs/test_results.txt` | test cases and their results |
| `docs/results/` | evaluation and performance results (markdown, JSON, charts) |
| `docs/evidence/` | captured compiler output for key scenarios (`.txt` + rendered `.png`) |
| `docs/OPTIMIZER.md` | SSA construction and the optimiser: design, rules, results |
| `docs/BUG_LOG.md` | bugs found in the first working version by adversarial testing, with before and after |

## Results at a glance

Measured numbers live in `docs/results/EVAL_heldout.md` (held-out corpus), `EVAL_dev.md` (development corpus) and `PERFORMANCE.md`; they are produced by `eval/run_eval.py` and `eval/perf.py`, never typed in by hand.

## Scope

Included: everything above, plus an SSA-based optimiser (`-O`). Not included: native code generation (programs run through three-address code in an interpreter) and a language larger than needed to exercise the compiler.
