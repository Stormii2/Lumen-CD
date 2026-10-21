# Performance results

Measured on the machine that built this report (CPython 3.11.15). Times are best-of-N wall-clock; memory is the `tracemalloc` peak.

## Compile time vs program size (error-free programs)

| lines | Phase 3 total ms | Phase 2 total ms | lex | parse | check | tac |
|---|---|---|---|---|---|---|
| 101 | 6.5 | 7.1 | 2.4 | 7.6 | 1.1 | 1.2 |
| 201 | 13.1 | 8.8 | 2.6 | 8.8 | 1.0 | 1.1 |
| 401 | 25.3 | 18.6 | 5.3 | 16.7 | 2.5 | 2.3 |
| 801 | 52.8 | 38.8 | 11.2 | 34.5 | 4.2 | 5.2 |
| 1601 | 112.8 | 79.4 | 21.2 | 96.4 | 8.8 | 11.7 |
| 3201 | 248.9 | 169.7 | 51.1 | 160.0 | 19.2 | 46.7 |
| 6401 | 543.1 | 378.1 | 103.1 | 313.2 | 37.9 | 94.8 |

Log-log slope of time vs lines: Phase 3 = **1.07**, Phase 2 = 1.00 (1.00 = linear). Phase 3 does more per token (token poisoning, annotations, uid allocation, FIRST/FOLLOW-guided recovery) so its constant is higher; the growth rate is what matters.

## Error-recovery cost (a defect every ~10 statements)

| lines | errors reported | compile ms (with defects) | compile ms (same size, clean) | slowdown |
|---|---|---|---|---|
| 401 | 70 | 23.1 | 24.6 | 0.9x |
| 1602 | 290 | 115.2 | 106.8 | 1.1x |
| 3201 | 580 | 320.3 | 260.1 | 1.2x |

Recovery adds little cost. A first version of this benchmark exposed a quadratic cost: every undeclared name was compared (Damerau-Levenshtein) with every visible name, and this program has hundreds of variables in scope (1.9 s at 1,600 lines). Rejecting candidates whose length differs from the word by more than the edit threshold, an exact lower bound that changes no result, made it linear (0.15 s).


## Peak memory

| lines | compiler | peak KiB |
|---|---|---|
| 801 | phase3 | 4562 |
| 801 | phase2 | 4011 |
| 3201 | phase3 | 18322 |
| 3201 | phase2 | 16115 |

## Interpreter throughput

| benchmark | TAC steps | Phase 3 ms | Phase 2 ms | speed-up | M steps/s (P3) | same output |
|---|---|---|---|---|---|---|
| loop 200k | 2400011 | 902 | 2965 | 3.29x | 2.66 | True |
| fib(20) | 207964 | 53 | 208 | 3.94x | 3.94 | True |
| array 50k | 650028 | 92 | 625 | 6.78x | 7.05 | True |

The Phase 3 interpreter decodes each function once into tuples with pre-resolved operands and jump targets; Phase 2 re-parsed operand strings on every instruction.

