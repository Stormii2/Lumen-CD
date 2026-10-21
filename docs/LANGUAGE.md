# The Lumen language

Lumen is a small, statically typed, imperative language designed for one purpose: to be a *good subject for a compiler whose main feature is its diagnostics*. It has just enough features (functions, recursion, arrays, scopes, four value types) to make type checking, scope resolution and error recovery non-trivial, and nothing more.

## 1. Lexical structure

| Element | Rule |
|---|---|
| Whitespace | space, tab, CR, LF - ignored |
| Comment | `//` to end of line |
| Identifier | letter or `_`, then letters, digits, `_` (non-ASCII letters allowed) |
| Integer literal | decimal digits, e.g. `42` (must fit in 64 bits when used) |
| Float literal | digits `.` digits, e.g. `3.14` |
| String literal | `"..."` on one line; no escape sequences |
| Boolean literal | `true`, `false` |
| Keywords | `int float bool string void if else while return func true false` |
| Operators | `+ - * / % == != < > <= >= && \|\| ! =` |
| Punctuation | `( ) { } [ ] , ;` |

`print` is a built-in function, not a keyword.

## 2. Types

| Type | Values | Notes |
|---|---|---|
| `int` | 64-bit signed | overflow is a **run-time error** (R0004), not wrap-around |
| `float` | IEEE double | |
| `bool` | `true` / `false` | no implicit conversion from or to any other type |
| `string` | text | `+` concatenates, `==` / `!=` compare |
| `T[]` | fixed-length array of `T` | length fixed when the literal is evaluated; arrays are passed **by reference** |
| `void` | - | function return type only |

Implicit conversions: **`int` -> `float` only** (in initialisers, assignments, arguments, returns, mixed arithmetic and array literals such as `float[] a = [1, 2.5];`). Everything else needs to be written explicitly - in particular `if (n)` with an `int` is an error (E0307); write `if (n != 0)`.

## 3. Grammar (LL(1))

The same grammar is stored as data in `lumen/grammar.py`, where FIRST/FOLLOW are computed and the LL(1) property is checked by the test-suite (`lumenc --grammar` prints the tables).

```
program     -> { item }
item        -> func_decl | statement
func_decl   -> "func" type IDENT "(" [ param { "," param } ] ")" block
param       -> type [ "[" "]" ] IDENT
type        -> "int" | "float" | "bool" | "string" | "void"

block       -> "{" { statement } "}"
statement   -> block | if_stmt | while_stmt | return_stmt | var_decl | ident_stmt
if_stmt     -> "if" "(" expr ")" block [ "else" ( block | if_stmt ) ]
while_stmt  -> "while" "(" expr ")" block
return_stmt -> "return" [ expr ] ";"
var_decl    -> type [ "[" "]" ] IDENT "=" expr ";"
ident_stmt  -> IDENT ( "=" expr ";" | "[" expr "]" "=" expr ";" | "(" args ")" ";" )

expr        -> or_e                       (loosest binding first)
or_e        -> and_e   { "||" and_e }
and_e       -> eq_e    { "&&" eq_e }
eq_e        -> rel_e   { ("==" | "!=") rel_e }
rel_e       -> add_e   { ("<" | ">" | "<=" | ">=") add_e }
add_e       -> mul_e   { ("+" | "-") mul_e }
mul_e       -> unary   { ("*" | "/" | "%") unary }
unary       -> ("-" | "!") unary | postfix
postfix     -> primary { "[" expr "]" }
primary     -> NUMBER | STRING | "true" | "false" | IDENT [ "(" args ")" ]
             | "(" expr ")" | "[" args "]"
args        -> [ expr { "," expr } ]
```

All binary operators are left-associative. (The recursive-descent parser implements the expression levels with a Pratt loop; the grammar above is the equivalent specification.)

## 4. Semantics

**Programs.** Top-level statements run in order; function declarations are hoisted, so a function may be called before its declaration appears in the file. Functions are declared at the top level only.

**Scope.** Every `{ }` block opens a scope. Declaring a name that already exists *in the same scope* is an error (E0300); declaring it in an inner scope shadows the outer one for the rest of the inner block. Top-level variables are global and visible inside functions. A parameter shadows a global of the same name.

**Initialisation.** Every variable must be initialised where it is declared (`int x;` is E0102).

**Functions.** A non-`void` function must return a value on every control-flow path (E0310). `void` functions return nothing and cannot be used as values (E0313). Recursion is allowed; the call depth is limited to 2000 (R0005).

**Operators.**

| Operator | Operands | Result |
|---|---|---|
| `+ - * / %` | numeric (`+` also `string`,`string`) | `int` if both `int`, else `float` |
| `/` on `int` | | truncates toward zero (`-7 / 2 == -3`) |
| `%` on `int` | | sign follows the dividend (`-7 % 2 == -1`) |
| `< > <= >=` | numeric | `bool` |
| `== !=` | same type, or both numeric | `bool` |
| `&& \|\|` | `bool` | `bool`, **short-circuit** |
| `!` | `bool` | `bool` |
| unary `-` | numeric | same type |

**Arrays.** `a[i]` is bounds-checked on read **and** write (R0003); negative indices are out of range. An empty literal `[]` is accepted wherever an array of any element type is expected.

**Output.** `print(a, b, ...)` writes its arguments separated by single spaces and a newline. Booleans print as `true`/`false`; floats print with Python's `repr` (so `10.0`, `2.5`); arrays print as `[1, 2, 3]`.

## 5. Run-time errors

A program that compiled can still fail while running. Each failure is reported with a source caret, like a compile error:

| Code | Cause |
|---|---|
| R0001 | division by zero (int or float) |
| R0002 | modulo by zero |
| R0003 | array index out of bounds |
| R0004 | 64-bit integer overflow |
| R0005 | call depth exceeded (runaway recursion) |
| R0006 | step limit exceeded (probable infinite loop; default 5,000,000 TAC instructions) |
| R0007 | variable read before it was assigned |

## 6. Deliberately not in the language

Floating-point `%` aside, no `for`/`break`/`continue`, no structs, no first-class functions, no dynamic array resizing, no string indexing, no input. The project's focus is diagnostic quality and a clean middle end, not language size. Native code generation is not implemented; programs run through three-address code in an interpreter.
