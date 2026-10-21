# Architecture

```
source text
   |
   v
 Lexer ───────────────► tokens (+ damaged ERROR tokens) + lexical diagnostics        lumen/lexer.py
   |
   v
 Parser (recursive descent + Pratt expressions) ──► AST + syntax diagnostics         lumen/parser.py
   |      uses: grammar.py  (FIRST/FOLLOW -> panic-mode sync set)
   |            suggest.py  (Damerau-Levenshtein ranking)   repair.py (suggestions)
   v
 TypeChecker (scoped symbol table, error-poisoned types) ──► annotated AST + semantic diagnostics
   |                                                                                lumen/checker.py
   v
 diagnostics.finalize  (sort, de-duplicate, span-containment suppression)            lumen/diagnostics.py
   |
   |── errors?  yes ► renderer (caret display / --json) ► optional fixer (--fix)     lumen/renderer.py, fixer.py
   |
   no
   v
 TACGenerator ──► flat three-address code per function                                lumen/tac.py
   |
   |── -O ► CFG ► SSA ► optimise (fold, propagate, CSE, DCE) ► out of SSA ► TAC     lumen/cfg.py, ssa.py, optimizer.py
   v
 TACInterpreter ──► program output / run-time errors (R-codes) with source spans     lumen/interpreter.py
```

`lumen/driver.py::compile_source` wires the front half together; the CLI (`lumenc.py`), the test-suite and the evaluation harness all call it, so they exercise exactly the same code.

## Error handling - the layered design

The project's central idea is that every phase **recovers** instead of aborting, and that each layer removes a *different* kind of noise. Every layer can be switched off independently via `RecoveryConfig` (`lumen/config.py`), which is what makes the evaluation an honest ablation.

| Layer | Where | What it does | Config switch |
|---|---|---|---|
| Lexical token poisoning | lexer + parser | An invalid character / malformed literal becomes a *damaged* token. The parser skips lone bad characters (exactly what the suggested fix does) and silences any diagnostic anchored on a neighbour of damaged text. | `token_poison` |
| Phrase-level repair | parser | Local, single-token repairs: assume a missing `;` / `)` / `=` / `]`; repair misspelled keywords and type names; recover a missing return type; assume a missing `{` when braces are unbalanced the other way. Each repair attaches a `Suggestion`. | `phrase_level` |
| Panic-mode sync set | parser | When a statement is unrecoverable, discard tokens up to a synchronisation token. The set is **derived from the grammar**: `FIRST(statement)` minus the weak starters `IDENT`/`{`, plus `;` and `}`. | `sync` |
| Error-distance rule | parser | After an error, silence further *parse* errors for the next 3 tokens (the classic yacc rule): the leftovers of one bad statement are not separate defects. | `error_gap` |
| Damaged-statement muting | parser + checker | A statement the parser flagged is still checked (so declarations take effect) but its semantic diagnostics are dropped. | `mute_damaged` |
| Lost-declaration inference | checker | If a declaration lost its name (`int  = 5;`), the first unknown, unlike-anything-in-scope name used later is bound to it instead of being reported. | `infer_lost_decl` |
| Error-type poisoning | checker | `ERROR` is the bottom of the type lattice: any operation involving it yields `ERROR` and reports nothing. | `poison` (`expr` / `full` / `none`) |
| Containment suppression | diagnostics | Drop exact duplicates, and a later-phase error whose span lies strictly inside an earlier-phase error's span. | `suppress` |

### Repairs and `--fix`

A `Diagnostic` may carry a `Suggestion`: a list of `Edit(span, replacement)` plus an *applicability* in the style of rustc:

* **machine-applicable** - the unique sensible reading (e.g. insert `;` before a token that starts a new statement; replace `whlie` by `while`; replace a misspelled name when exactly one candidate is within distance, the word is at least 4 letters, and distance <= 1).
* **maybe-incorrect** - a guess (e.g. close a string at end of line, insert a `}` whose position is unknown, replace when several candidates tie).

`lumenc --fix` applies only machine-applicable edits, back-to-front, skipping overlaps, and repeats to a fixpoint (one repair can unmask the next defect). `--unsafe-fixes` also applies guesses. The evaluation measures how often each mode restores the original program.

Candidate ranking (`suggest.py`) uses the *unrestricted* Damerau-Levenshtein distance (Lowrance-Wagner; `ca -> abc` is 2) with a length-aware threshold `max(1, min(3, len // 3))`, a type-compatibility preference and deterministic tie-breaking.

## Type checker

* Scoped symbol table (`symbol_table.py`); functions are registered in a first pass so forward references and recursion work.
* Every identifier gets a unique storage name (`uid`) so shadowed variables never share a TAC slot; the checker leaves `ty`, `uid`, `target_ty`, `ret_ty`, `param_tys`, `elem_ty` annotations on the AST and the code generator reads them instead of repeating inference.
* Warnings (W0001 constant division by zero, W0002 unreachable code) never block compilation.

## Code generation and execution

* TAC: one operator and at most two operands per instruction; `itof` is inserted exactly where the checker accepted an int-to-float widening; `&&` / `||` lower to conditional jumps (short-circuit); each instruction keeps the source span it came from.
* The interpreter decodes each function once into tuples (operands pre-resolved, labels pre-indexed), then runs a flat dispatch loop. Globals live in the `__main__` frame. Truncating `/` and `%`; 64-bit overflow, bounds, division, depth and step limits are checked and reported as R-codes with a caret.

## Optimisation (`-O`)

Optional, off by default. Each function becomes a control-flow graph, is converted to SSA (phi functions on dominance frontiers, pruned by liveness), optimised, converted back and flattened to TAC again, so the interpreter runs optimised and unoptimised code identically. See `docs/OPTIMIZER.md` for the passes and the rules that keep run-time errors intact.

## Robustness guarantees

* Every token-consuming loop in the parser has a progress guarantee (an early hang is a regression test).
* Nesting is limited (`MAX_NESTING`, `MAX_AST_DEPTH`) and reported as E0204 instead of overflowing Python's stack; the interpreter has an explicit call-depth limit.
* `tests/test_robustness.py` fuzzes every configuration with thousands of mutated and random inputs, asserting termination, no exceptions, in-range spans and documented diagnostic codes.
