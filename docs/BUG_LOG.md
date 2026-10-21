# Defects found by adversarial testing

Testing began by attacking the first working version of the compiler with boundary, error-condition and adversarial inputs (`/tests/test_robustness.py`, `tests/test_regressions.py`). Everything below was **reproduced on the frozen snapshot of that version** (`eval/baselines/lumen_phase2/`) before being fixed; each fix is pinned by a regression test of the same name.

| # | Input | Early behaviour | Current behaviour |
|---|---|---|---|
| 1 | a stray `}` at top level | **parser loops forever** | E0124 with removal suggestion |
| 2 | `else { }` with no `if` | **parser loops forever** | E0125 |
| 3 | `int z = 0; print(1 / z);` | prints `0` | R0001 division by zero, caret on `1 / z` |
| 4 | `5 % z` | prints `0` | R0002 |
| 5 | `a[10]` on a 3-element array | silently yields `0` | R0003 with index and length |
| 6 | `a[10] = 1` | silently **grows the array** | R0003 |
| 7 | `int x = 1; if (true) { int x = 2; } print(x);` | prints `2` (inner write clobbers outer) | prints `1` (unique storage names per declaration) |
| 8 | `float x = 5; print(x / 2);` | prints `2` (no int->float at run time) | prints `2.5` (explicit `itof` in the TAC) |
| 9 | `print(true)` | prints `1` | prints `true` |
| 10 | `-7 / 2`, `-7 % 2` | `-4`, `1` (floor semantics) | `-3`, `-1` (truncation, as in C/Java) |
| 11 | functions that read a global | return `0` (frames had no access to globals) | read the real global |
| 12 | `false && (1 / z == 0)` | evaluates both sides | short-circuits |
| 13 | 3000 nested parentheses; a 5000-term sum | uncaught Python `RecursionError` | E0204, depth limit |
| 14 | `f(n) = f(n+1)` recursion | uncaught `RecursionError` | R0005 stack-overflow error |
| 15 | `int x = 9223372036854775807; x + 1` | unbounded integer | R0004 overflow |
| 16 | `int x;` | **two** diagnostics (E0102 + spurious E0412) | one diagnostic; `x` stays declared |
| 17 | `int[] a = [];` | message about type `Error[]` | accepted; empty literal fits any array type |
| 18 | `void x = 1;` | confusing E0302 | E0312 |
| 19 | calling a variable `x(2)`; using a function name as a value `int x = f;` | misleading E0413 "cannot find function 'x'"; the second is **accepted silently** | E0414 / E0415 |
| 20 | `string s = "abc;` | E0002 **plus** a second error (E0201) from the damaged token | one diagnostic |
| 21 | `whlie (x) { }` | E0201 + E0413 | one diagnostic (E0411) with a fix |
| 22 | `func void f(int[ a)` | missing `]` silently accepted | E0123 |
| 23 | a misspelled return type, `func brol f(int n) {...}` | **parser loops forever** | one E0410 with a fix |

Items 1, 2 and 23 were the most serious: the first version could be made to hang by a one-character input or a single typo.
