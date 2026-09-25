# Lumen

**A diagnostics-first compiler front-end with multi-phase error recovery and machine-applicable repair suggestions.**

Most compilers halt at the first error, or recover so crudely that one mistake cascades into a dozen spurious messages. Lumen inverts that priority: it's a compiler for a small statically-typed imperative language where **diagnostic quality is the primary design axis**, not an afterthought. It recovers at every phase instead of aborting, poisons error types instead of cascading, and — as of this build — type-checks, lowers to three-address code, and executes real programs end to end.

---

## Status: core implementation

The full front-end through execution is implemented and tested: lexer, parser, symbol table, type checker with an error-poisoned type lattice, a three-address-code generator, and a TAC interpreter. 23 tests pass. Repair ranking, `--fix`, and the seeded-defect evaluation harness are next (see [Roadmap](#roadmap)).

| Component | Status |
|---|---|
| Source manager & span model | ✅ |
| Lexer with recovery | ✅ |
| Full parser (functions, if/while, arrays, recursion) | ✅ |
| Phrase-level & panic-mode recovery | ✅ |
| Scoped symbol table | ✅ |
| Type checker + error-poisoned type lattice | ✅ |
| Return-path validation | ✅ |
| Three-address code generator | ✅ |
| TAC interpreter (recursion, arrays, functions) | ✅ |
| Caret diagnostic renderer | ✅ |
| Keyword-confusion "did you mean" | ✅ |
| Test suite (pytest) | ✅ 23 passing |
| Repair ranking engine & `--fix` | 🔜 |
| Suppression precision / cascade-ratio harness | 🔜 |

---

## Example: type poisoning stops a cascade

```
$ lumenc examples/semantic_errors.lum
error[E0412]: cannot find variable 'totl' in this scope
 --> examples/semantic_errors.lum:1:13
...
error[E0300]: variable 'total' is already declared in this scope
...
error[E0302]: cannot initialise 'int' variable 'score' with a value of type 'string'
...
error[E0307]: while-condition must be 'bool', found 'int'
...
error[E0311]: 'add' expects 2 argument(s), found 3
5 errors generated.
```

Five independent seeded defects, five diagnostics — a cascade ratio of 1.0. The first defect (`totl` undeclared) poisons `total`'s type; every later statement that touches `total` is silently suppressed instead of re-reporting the same root cause.

## Example: recursion and arrays actually execute

```
$ lumenc examples/factorial.lum --quiet-run=false
compilation succeeded — 0 diagnostics.
--- program output ---
720
55
```

`factorial(6)` and `fibonacci(10)`, computed by walking real three-address code through the interpreter — not simulated.

---

## Running it

Requires Python 3.8+ and pytest (`pip install pytest`), nothing else.

```bash
git clone <this-repo-url>
cd lumen
python3 lumenc.py examples/factorial.lum          # recursion, functions
python3 lumenc.py examples/arrays.lum              # arrays, while loops
python3 lumenc.py examples/semantic_errors.lum     # poisoning / cascade demo
python3 lumenc.py examples/demo.lum                # lexer/parser recovery demo
python3 lumenc.py examples/valid.lum --emit-tac    # see the generated TAC

python3 -m pytest tests/ -v                        # run the test suite
```

Flags: `--no-color` (plain output), `--emit-tac` (print the generated three-address code), `--quiet-run` (type-check only, skip execution).

### Language

```
func int gcd(int a, int b) {
    if (b == 0) { return a; }
    return gcd(b, a - (a / b) * b);
}

int[] nums = [4, 8, 15, 16, 23, 42];
int i = 0;
int total = 0;
while (i < 6) {
    total = total + nums[i];
    i = i + 1;
}
print(total);
print(gcd(48, 18));
```

Types: `int`, `float`, `bool`, `string`, `void`, and `T[]` arrays. Functions, recursion, `if`/`else`, `while`, arithmetic/comparison/logical operators, array indexing, and a `print(...)` built-in for observable output.

---

## Project layout

```
lumenc.py            entry point / CLI
lumen/
  span.py            SourceMap, Span, Diagnostic dataclasses
  tokens.py          Token kinds and keyword table
  lexer.py           Hand-written lexer with recovery
  suggest.py         Levenshtein edit distance — keyword & identifier "did you mean"
  ast_nodes.py       AST node dataclasses
  parser.py          Full recursive-descent parser, Pratt expressions,
                     phrase-level + panic-mode recovery
  symbol_table.py    Scope-chained symbol table
  checker.py         Static type checker, error-poisoned type lattice,
                     return-path validation
  tac.py             Three-address code generator
  interpreter.py     TAC interpreter (activation records, recursion)
  renderer.py        Caret diagnostic renderer
examples/
  demo.lum                Lexer/parser recovery showcase
  valid.lum                Clean baseline
  factorial.lum             Recursion (factorial, fibonacci)
  arrays.lum                 Arrays + while loops
  semantic_errors.lum         Seeded semantic defects — poisoning demo
tests/
  test_lumen.py       23 pytest cases across every phase
```

---

## Design

| Module | Owns | Status |
|---|---|---|
| **1 — Front-End Ingestion** | Source manager, lexer, full parser, span-annotated AST | ✅ |
| **2 — Semantic Analysis** | Scoped symbol table, type checker, error-poisoned type lattice | ✅ |
| **3 — Diagnostics Engine** | Diagnostic collection, recovery selection | ✅ (ranking/suppression-metrics next) |
| **4 — Output & Evaluation** | Caret renderer, TAC generator, TAC interpreter | ✅ (`--fix`, metrics harness next) |

Every phase emits structured `Diagnostic` objects; rendering happens once, after every phase has run, so a single defect's diagnostics stay together and poisoned downstream effects stay silent.

---

## Roadmap

- **v0.2 — Semantics & execution** *(current)*: full parser, symbol table, type checker, type poisoning, TAC generation + interpreter, 23-test suite
- **v0.3 — Repair & measurement**: edit-distance repair ranking, `--fix` / `--explain`, suppression-precision + cascade-ratio evaluation harness on a seeded-defect corpus, published results

---

## References

- Aho, Lam, Sethi, Ullman — *Compilers: Principles, Techniques, and Tools*
- Burke & Fisher (1987); Diekmann & Tratt (2020) — practical LL/LR recovery algorithms
- Clang, Elm, Rust — caret diagnostics, applicability-classified fixes (`rustfix`)

---

Built by [Srijan Sengupta](https://github.com/Stormii2).
