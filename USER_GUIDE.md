# Lumen user guide

## 1. Requirements and installation

* Python 3.8 or newer (developed and tested on 3.11). No third-party packages are needed to **compile and run** programs.
* To run the tests: `pip install pytest`. To regenerate the evaluation charts / evidence images: `pip install matplotlib pillow`.

```
git clone https://github.com/Stormii2/Lumen-CD.git
cd Lumen-CD
python3 lumenc.py examples/valid.lum        # on Windows: py lumenc.py examples\valid.lum
```

## 2. Writing and running a program

Create `hello.lum`:

```
func int square(int n) {
    return n * n;
}
print(square(12));
```

```
python3 lumenc.py hello.lum
```

```
compilation succeeded - 0 diagnostics.

144
```

Exit status: `0` success - `1` compile errors - `2` run-time error - `3` usage / file error. The language is described in `docs/LANGUAGE.md`.

## 3. Reading a diagnostic

```
error[E0412]: cannot find variable 'totl' in this scope
 --> examples/cascade_demo.lum:2:13
  |
2 | int total = totl + 1;
  |             ^^^^
  |
help: did you mean 'total'? ...
fix (machine-applicable): replace 'totl' with 'total'
  | int total = total + 1;
```

* `error[E0412]` - severity and a stable code. `lumenc --explain E0412` explains it with an example; `lumenc --list-codes` lists all of them (also in `docs/ERROR_CODES.md`).
* The caret line marks the exact source span. Secondary lines (`-----`) point at related code, such as the earlier declaration.
* `fix (machine-applicable)` is a repair the compiler is confident in; `fix? (maybe-incorrect)` is a guess.

## 4. Command-line options

| Option | Effect |
|---|---|
| `FILE` | compile; if there are no errors, run |
| `--check` | compile only |
| `--emit-tac` | print the three-address code |
| `-O`, `--optimize` | optimise the code (SSA-based) before running; output and run-time errors are unchanged |
| `--emit-ssa` | print the SSA form with phi functions (after optimisation with `-O`) |
| `--opt-stats` | with `-O`: what the optimiser did |
| `--opt-disable LIST` | with `-O`: switch off `constants`, `copies`, `cse` and/or `dce` |
| `--emit-tokens` | print the token stream |
| `--json` | diagnostics as JSON (for editors / scripts) |
| `--fix` | apply machine-applicable repairs **in place**, repeating until none remain |
| `--fix --diff` | show the repairs as a unified diff; do not write |
| `--fix --unsafe-fixes` | also apply maybe-incorrect guesses |
| `--explain CODE`, `--list-codes` | documentation for diagnostic codes |
| `--grammar` | print the grammar's FIRST / FOLLOW tables |
| `--time` | per-phase compile timings and interpreter step count |
| `--max-steps N` | run-time instruction limit (default 5,000,000) |
| `--color` / `--no-color` | force / disable ANSI colour (default: colour on a terminal) |
| `--version` | print version |

**Ablation switches** (used by the evaluation; handy for seeing what each technique contributes): `--fail-fast`, `--no-phrase-level`, `--sync {first-follow,semicolon}`, `--poison {expr,full,none}`, `--no-token-poison`, `--error-gap N`, `--no-mute-damaged`, `--no-infer-lost-decl`, `--no-suppress`.

Try it:

```
python3 lumenc.py examples/demo.lum                                   # 7 defects -> 7 diagnostics
python3 lumenc.py examples/demo.lum --check --no-phrase-level --sync semicolon --poison none \
        --no-token-poison --error-gap 0 --no-mute-damaged --no-infer-lost-decl --no-suppress   # same file, techniques off
python3 lumenc.py examples/demo.lum --fix --diff
python3 lumenc.py examples/optimize_demo.lum -O --opt-stats --emit-tac   # 47 -> 15 instructions
python3 lumenc.py examples/optimize_demo.lum --emit-ssa --check          # SSA with phi functions
```

## 5. Examples

| File | Shows |
|---|---|
| `examples/valid.lum`, `arrays.lum`, `factorial.lum` | normal programs |
| `examples/demo.lum` | seven different syntax / lexical defects |
| `examples/semantic_errors.lum` | type and scope errors with secondary labels |
| `examples/typos.lum` | keyword, type and variable-name repairs |
| `examples/cascade_demo.lum` | one defect, one diagnostic |
| `examples/warnings.lum` | warnings do not stop compilation |
| `examples/runtime_error.lum` | compiles, then fails with R0003 and a caret |

## 6. Running the tests and the evaluation

```
python3 -m pytest tests -q                       # ~320 tests, a few seconds
python3 tools/gen_test_report.py                 # regenerate docs/TEST_CASES.md
python3 eval/run_eval.py --seed 2024 --name EVAL_dev      # seeded-defect evaluation (~2-4 min)
python3 eval/perf.py                             # performance measurements
python3 tools/make_evidence.py                   # regenerate docs/evidence/*.txt|png
```

## 7. Troubleshooting

* `lumenc: cannot read ...` - wrong path or the file is not UTF-8 text.
* On Windows PowerShell use `py` or `python` instead of `python3`; colour needs a modern terminal (Windows Terminal) - otherwise add `--no-color`.
* A program that "runs forever" stops with R0006 after 5,000,000 instructions; raise the limit with `--max-steps`.
