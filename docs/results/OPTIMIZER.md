# Optimiser results

Produced by `python3 eval/opt_eval.py`. Every number is measured, not estimated.

## 1. Does optimisation preserve behaviour?

Each random program (`tests/fuzzgen.py`) is run unoptimised and optimised; the output, and the run-time error if any, must be identical. *risky* programs include overflow, division by zero and out-of-range indexes; *safe* programs mostly run to completion.

| generator | programs compared | with a run-time error | mismatches | dynamic steps before | after |
|---|---|---|---|---|---|
| safe | 2500 | 203 | **0** | 1515792 | 877937 (42% fewer) |
| risky | 2500 | 1269 | **0** | 774319 | 439110 (43% fewer) |

## 2. Effect on real programs

| program | instructions before | after | TAC steps before | after | steps saved | run ms before | after | same output |
|---|---|---|---|---|---|---|---|---|
| p01_primes | 46 | 35 | 2335 | 1895 | 19% | 0.5 | 0.4 | True |
| p02_sort | 66 | 44 | 725 | 506 | 30% | 0.2 | 0.2 | True |
| p03_gcd | 29 | 26 | 68 | 59 | 13% | 0.1 | 0.1 | True |
| p04_collatz | 50 | 42 | 8585 | 6723 | 22% | 1.3 | 1.1 | True |
| p05_stats | 56 | 46 | 145 | 132 | 9% | 0.1 | 0.1 | True |
| p06_logic | 68 | 49 | 128 | 98 | 23% | 0.1 | 0.1 | True |
| p07_scope | 28 | 19 | 32 | 22 | 31% | 0.0 | 0.0 | True |
| p08_fibtable | 68 | 47 | 2830 | 2100 | 26% | 0.9 | 0.7 | True |
| p09_digits | 54 | 44 | 330 | 248 | 25% | 0.1 | 0.1 | True |
| p10_power | 40 | 30 | 180 | 154 | 14% | 0.1 | 0.1 | True |
| p11_factorial | 39 | 28 | 1748 | 1287 | 26% | 0.5 | 0.6 | True |
| p12_arrays | 33 | 23 | 94 | 73 | 22% | 0.1 | 0.1 | True |
| loop 200k | 17 | 13 | 2400011 | 1800009 | 25% | 350.4 | 352.9 | True |
| fib(20) | 20 | 16 | 207964 | 153238 | 26% | 72.6 | 71.3 | True |
| array 50k | 35 | 23 | 650028 | 500018 | 23% | 103.9 | 79.4 | True |
| constants | 30 | 15 | 380017 | 220009 | 42% | 95.3 | 33.1 | True |
| optimize_demo | 47 | 15 | 124 | 64 | 48% | 0.1 | 0.0 | True |

Across all programs: 3655344 steps -> 2686635 (27% fewer).

## 3. Contribution of each optimisation

All programs above, optimised with one optimisation switched off at a time.

| configuration | static instructions | dynamic steps | steps vs. unoptimised | output unchanged |
|---|---|---|---|---|
| all on | 515 | 2686635 | 27% fewer | True |
| no constants | 730 | 3624608 | 1% fewer | True |
| no copies | 567 | 3247970 | 11% fewer | True |
| no cse | 520 | 2706689 | 26% fewer | True |
| no dce | 807 | 4186011 | 15% more | True |
| none | 818 | 4186018 | 15% more | True |

`none` runs the SSA round trip with every pass off: it shows that converting to SSA and back itself adds steps (extra copies), so the gains come from the passes.

## 4. Cost of optimising

| lines | whole compile ms | of which optimiser ms |
|---|---|---|
| 401 | 28 | 41 |
| 801 | 60 | 86 |
| 1601 | 155 | 193 |
| 3201 | 283 | 491 |
| 6401 | 752 | 1377 |

Log-log slope of optimiser time against program size: **1.27** (1.00 = linear).
