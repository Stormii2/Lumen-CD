**Lumen**

A diagnostics-first compiler front-end with multi-phase error recovery and machine-applicable repair suggestions.

Most compilers halt at the first error, or recover so crudely that one mistake cascades into a dozen spurious messages. Lumen inverts that priority: it's a compiler front-end for a small statically-typed imperative language where diagnostic quality is the primary design axis, not an afterthought. It recovers at every phase instead of aborting, so a single run reports every genuine defect — rendered Rust/Clang-style, with caret underlines and "did you mean" suggestions for common mistakes.

**Why**

Premature termination — a fail-fast compiler forces one edit-compile cycle per bug. Eight independent defects need eight compilations to discover.
Error cascades — naive recovery (discard tokens, hope for the best) turns one missing semicolon into a wall of unrelated-looking errors.
Uninformative messages — most compiler errors describe the parser's internal confusion, not what the programmer actually meant.

Lumen addresses all three: it recovers instead of aborting, suppresses cascades instead of printing them, and proposes concrete fixes instead of just naming the problem.

**Design**

Module	Owns
1 — Front-End Ingestion	Source manager, lexer, recursive-descent + Pratt parser, span-annotated AST
2 — Semantic Analysis	Scoped symbol table, static type checker, error-poisoned type lattice
3 — Diagnostics Engine	Diagnostic bus, recovery strategy selector, suppression engine, repair & ranking
4 — Output & Evaluation	Caret renderer, --fix source patcher, TAC generator + interpreter, metrics harness

Every phase emits structured Diagnostic objects onto a shared bus; rendering happens once, after suppression and ranking — which is what makes cascade suppression possible in the first place.

**References**

Aho, Lam, Sethi, Ullman — Compilers: Principles, Techniques, and Tools (recovery strategy taxonomy)
Burke & Fisher (1987); Diekmann & Tratt (2020) — practical LL/LR recovery algorithms
Clang, Elm, Rust — caret diagnostics, programmer-facing messages, applicability-classified fixes (rustfix)
Barik et al.; Denny et al.; Becker et al. — empirical studies on error-message effectiveness
