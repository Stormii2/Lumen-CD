# SSA and the optimiser

Lumen can optimise a program before running it: `lumenc prog.lum -O`. The optimiser is off by default so that unoptimised code stays available for comparison; with it on, every program prints exactly what it printed before and fails with exactly the same run-time error, in the same place.

```
three-address code  ->  control-flow graph  ->  SSA  ->  passes  ->  out of SSA  ->  three-address code
     tac.py               cfg.py              ssa.py   optimizer.py      ssa.py          cfg.py
```

The result is ordinary three-address code, so the interpreter runs optimised and unoptimised programs identically. `--emit-tac -O` shows the optimised code, `--emit-ssa` shows the SSA form with its phi functions, and `--opt-stats` reports what each pass did.

## Control-flow graph (`cfg.py`)

A function's flat instruction list is split into *basic blocks* - straight-line runs with one entry and one exit - joined by edges. Each block keeps its jump separately (`goto`, `br`, `ret`), so no pass has to look for jumps inside the instruction list. Two preparations:

* a synthetic empty *entry* block, so nothing jumps to the entry and phi functions never need to sit "before" it;
* *critical-edge splitting*: an edge from a block with two successors to a block with two predecessors gets a block of its own, which gives the copies that replace phi functions a place that belongs to exactly that edge.

Analyses: reverse post-order, immediate dominators (Cooper-Harvey-Kennedy iterative algorithm), the dominator tree, and dominance frontiers (Cytron et al.).

## SSA form (`ssa.py`)

In SSA every variable is assigned once: the n-th assignment to `x` defines `x$n`. Where control flow joins, a *phi function* chooses by incoming edge:

```
L1:    ; preds: B1, B3
    i$2 = phi(B1: i$1, B3: i$3)
    t9$1 = i$2 < 5
```

* **Placement.** Phi functions go on the iterated dominance frontier of each variable's definitions, but only where the variable is *live* (pruned SSA), so no phi has an undefined operand.
* **Renaming.** One walk of the dominator tree (iterative, so long programs cannot overflow the stack) gives each definition a fresh name and rewrites each use to the nearest dominating definition.
* **What is in SSA.** Function parameters and local variables and temporaries. Global variables that some function reads or writes stay as ordinary memory: any call may change them. A global used only by the top-level code is not visible to any function, so it *is* treated as a value. Arrays are references; `a[i] = v` changes the object, not the name, so it is a *use* of `a`.
* **Leaving SSA.** Each phi becomes copies at the end of its predecessors. The copies for one edge happen "at once", so they are sequenced so no copy overwrites a value another still needs; a cycle (`a <- b`, `b <- a`) is broken with one temporary. Then empty blocks are bypassed and straight-line block pairs merged.

`verify_ssa` checks the defining properties - every name defined once, every use dominated by its definition, one phi operand per predecessor - and the test-suite runs it before and after optimisation on over a hundred random functions.

## Optimisations (`optimizer.py`)

All passes run on SSA and repeat until nothing changes.

| Pass | What it does |
|---|---|
| Constant folding and propagation | evaluates operations on known constants with the interpreter's exact arithmetic and carries the results into later uses, across blocks and around loops |
| Branch folding | a conditional on a known constant becomes a jump; the unreachable arm and its blocks disappear |
| Copy propagation | `x = y` is removed by using `y` directly; a phi whose operands are all equal is a copy |
| Global value numbering | an expression already computed in a dominating block is reused instead of recomputed (common-subexpression elimination) |
| Dead-code elimination | unused definitions with no side effect are deleted, cascading |
| Jump threading, block merging | tidy the graph after leaving SSA |

Each of the first four groups can be switched off (`--opt-disable constants,copies,cse,dce`), which is how `eval/opt_eval.py` measures what each contributes.

## What the optimiser must not change

In Lumen, "what a program does" includes *which run-time error it raises, and where*. So:

1. **Operations that can fail are not folded away and not deleted.** `+ - *` on integers can overflow (R0004), `/` and `%` can divide by zero (R0001, R0002), an array read can be out of bounds (R0003). `int unused = 1 / z;` must still stop the program with R0001. Folding is refused whenever the evaluation would fail, so the failure stays for run time. An unused `a / 2` *can* be deleted: a constant divisor other than 0 and -1 cannot fail.
2. **Side effects stay.** Calls, `print`, array writes and stores to globals are never removed or reordered.
3. **Globals are memory.** A global visible to a function is never assumed constant, and an expression that reads one is never reused across a call or a store.
4. **Dead branches hide their errors, exactly as before.** `if (false) { print(1 / z); }` never fails, and still does not after the branch is removed.
5. **Source locations survive.** Instructions keep their source spans, so a run-time error after optimisation points at the same expression.
6. **The optimiser cannot make compilation fail.** If SSA construction meets a shape it does not handle, that function is left unchanged.

## Testing

`tests/test_optimizer.py` (141 tests):

* **Equivalence**: every catalogued valid program, every evaluation program and example, and random programs (`tests/fuzzgen.py`) are run both ways; output and run-time error must match. The random generator produces typed, scoped programs with shadowing, globals written by functions, short-circuit operators whose right-hand side prints, aliased arrays, values near the 64-bit limit and divisions by zero. Two generators - *risky* (about half the programs end in a run-time error) and *safe* (most run to completion) - each supply thousands of programs in the evaluation.
* **SSA structure**: invariants, phi placement at joins and loop headers, pruning, dominators and frontiers, critical-edge splitting.
* **Each transformation**, and **each rule above**, with a test that fails if the optimiser changes the behaviour.
* **Command line**: `-O`, `--emit-ssa`, `--opt-stats`.

## Results

Measured by `python3 eval/opt_eval.py`; tables in `docs/results/OPTIMIZER.md`, chart `docs/results/fig_opt.png`.

* **Equivalence:** 5,000 random programs (2,500 safe, 2,500 risky; 1,472 of them end in a run-time error) behave identically optimised and unoptimised. The campaign caught one real bug during development - common-subexpression elimination treated `+` as commutative, which is wrong for string concatenation - now fixed and covered by a regression test.
* **Effect:** across the 17 evaluation programs, dynamic TAC steps fall 27% (3,655,344 to 2,686,635); the demo falls 48%, the constant-heavy benchmark 42%. Wall-clock gains are real where steps fall a lot (constants 95 ms to 33 ms) and within noise elsewhere.
* **Contribution:** dead-code elimination and constant propagation matter most; copy propagation next; CSE is small on these programs.
* **Cost:** optimiser time grows slightly faster than linearly (log-log slope 1.27); 1.4 s for a 6,400-line program.

## Limits

* Optimisation is per function. There is no inlining, no loop-invariant code motion and no strength reduction, and arithmetic that can overflow is never deleted, so an unused `i = i + 1` stays.
* Leaving SSA gives every SSA name its own variable, so the optimised code can contain more copies than the original. With every pass off, the SSA round trip alone adds about 15% more steps; the passes more than pay for it.
* Gains are modest on the evaluation programs because they were not written to be optimised; the demo program (`examples/optimize_demo.lum`) shows what the passes can do when there is something to find.
* There is no native code generation. Programs run through three-address code in an interpreter.
