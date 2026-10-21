# Diagnostic code reference

Generated from `lumen/explain.py`; `lumenc --explain CODE` prints the same text. The test-suite compiles every example below and asserts it raises exactly that code.


## Lexical errors (E00xx)

### E0001 - unexpected character

The lexer met a character that is not part of Lumen's alphabet. It is turned into a damaged ERROR token so later phases do not report fallout.

```
int x = 5 @ 3;
```

Corrected:

```
int x = 5 + 3;
```

### E0002 - unterminated string literal

A string started with '"' but the line ended before the closing quote.

```
string s = "hello;
```

Corrected:

```
string s = "hello";
```

### E0003 - malformed number

A numeric literal has an invalid shape (for example two decimal points).

```
float f = 1.2.3;
```

Corrected:

```
float f = 1.23;
```


## Syntax errors - declarations and blocks (E01xx)

### E0100 - unexpected token at start of statement

The token cannot begin any statement; the parser skips to the next synchronisation token.

```
int x = 1;
) print(x);
```

### E0101 - expected an identifier after a type keyword

A type was written but no variable name followed.

```
int = 5;
```

Corrected:

```
int x = 5;
```

### E0102 - malformed variable declaration

Either the '=' is missing, or the variable has no initialiser. Lumen requires every variable to be initialised where it is declared.

```
int x;
```

Corrected:

```
int x = 0;
```

### E0103 - expected '=' in an assignment

An identifier started a statement but was not followed by '=' (or '[').

```
int x = 1;
x 5;
```

Corrected:

```
int x = 1;
x = 5;
```

### E0110 - expected a return type after 'func'

Every function declares a return type: func int f() {...}.

```
func f() { return 1; }
```

Corrected:

```
func int f() { return 1; }
```

### E0111 - expected a function name

'func' and the return type must be followed by a name.

```
func int () { return 1; }
```

Corrected:

```
func int f() { return 1; }
```

### E0112 - expected ')' after parameter list

The parameter list was not closed.

```
func int f(int a { return a; }
```

Corrected:

```
func int f(int a) { return a; }
```

### E0113 - expected '(' after function name

A function name must be followed by its parameter list.

```
func int f { return 1; }
```

Corrected:

```
func int f() { return 1; }
```

### E0114 - expected a parameter type

Each parameter is written 'type name'.

```
func int f(a) { return 1; }
```

Corrected:

```
func int f(int a) { return 1; }
```

### E0120 - expected '{' to start a block

if/while/func bodies must be braced blocks.

```
if (true) print(1);
```

Corrected:

```
if (true) { print(1); }
```

### E0121 - expected '}' to close a block

A block was opened with '{' and never closed.

```
if (true) { print(1);
```

Corrected:

```
if (true) { print(1); }
```

### E0122 - expected '(' after if/while

The condition must be parenthesised.

```
while true { }
```

Corrected:

```
while (true) { }
```

### E0123 - expected ']'

An index or array literal was not closed with ']'.

```
int[] a = [1, 2;
```

Corrected:

```
int[] a = [1, 2];
```

### E0124 - unmatched '}'

There is a closing brace with no matching '{'. (An early version looped forever on this input; it is a regression test.)

```
int x = 1;
}

```

Corrected:

```
int x = 1;

```

### E0125 - 'else' without a matching 'if'

An 'else' must directly follow the block of an 'if'.

```
else { print(1); }
```

### E0126 - nested function declaration

Functions may only be declared at the top level.

```
func void f() { func void g() { } }
```


## Syntax errors - statements and expressions (E02xx)

### E0201 - expected ';' after statement

Statements end with ';'. A missing one where the next token clearly starts a new statement is machine-fixable.

```
int x = 5
print(x);
```

Corrected:

```
int x = 5;
print(x);
```

### E0202 - expected ')'

A parenthesis or argument list was not closed.

```
print((1 + 2);
```

Corrected:

```
print((1 + 2));
```

### E0203 - expected an expression

An expression was required but another token was found.

```
int x = ;
```

Corrected:

```
int x = 0;
```

### E0204 - expression or block nested too deeply

Nesting deeper than the compiler's safety limit is rejected instead of crashing the parser.

```
int x = ((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((1))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))));
```


## Type and semantic errors (E03xx)

### E0300 - duplicate declaration

A name was declared twice in the same scope. (Declaring it in an inner scope is allowed and shadows.)

```
int x = 1;
int x = 2;
```

Corrected:

```
int x = 1;
x = 2;
```

### E0301 - duplicate parameter name

Two parameters of one function share a name.

```
func int f(int a, int a) { return a; }
```

Corrected:

```
func int f(int a, int b) { return a; }
```

### E0302 - type mismatch in initialiser/assignment

The value's type cannot be assigned to the target. Only int -> float widening is implicit.

```
int x = "hi";
```

Corrected:

```
int x = 5;
```

### E0303 - operand/index has the wrong type

A unary operator or an array index got a type it does not accept.

```
bool b = !5;
```

Corrected:

```
bool b = !true;
```

### E0304 - operator not applicable to these types

Binary operators need compatible operand types (arithmetic: numeric; &&/||: bool; comparison: same family).

```
int x = 1 + "a";
```

Corrected:

```
int x = 1 + 2;
```

### E0305 - inconsistent array element types

All elements of an array literal must share a type (int/float mix is promoted to float).

```
int[] a = [1, "two"];
```

Corrected:

```
int[] a = [1, 2];
```

### E0306 - type cannot be indexed

Only arrays can be indexed.

```
int x = 1;
print(x[0]);
```

Corrected:

```
int[] x = [1];
print(x[0]);
```

### E0307 - condition is not a bool

if/while need a bool. Lumen has no truthiness: write the comparison explicitly.

```
int n = 1;
if (n) { print(n); }
```

Corrected:

```
int n = 1;
if (n != 0) { print(n); }
```

### E0308 - return type mismatch

The returned value does not match the declared return type.

```
func int f() { return "a"; }
```

Corrected:

```
func int f() { return 1; }
```

### E0309 - 'return' outside a function

return is only meaningful inside a function body.

```
return 1;
```

### E0310 - missing return on some path

A non-void function must return a value on every control-flow path.

```
func int f(int a) { if (a > 0) { return 1; } }
```

Corrected:

```
func int f(int a) { if (a > 0) { return 1; } return 0; }
```

### E0311 - wrong number or type of call arguments

The call does not match the function's signature.

```
func int f(int a) { return a; }
print(f(1, 2));
```

Corrected:

```
func int f(int a) { return a; }
print(f(1));
```

### E0312 - 'void' used as a variable/parameter type

void is only valid as a function return type.

```
void x = 1;
```

### E0313 - void call used as a value

A void function returns nothing, so it cannot be an argument or operand.

```
func void f() { }
print(f());
```

Corrected:

```
func void f() { }
f();
```


## Name and spelling errors (E04xx)

### E0410 - unknown type name

The type is not one of int, float, bool, string, void (or an array of them). A near-miss is suggested.

```
integer x = 1;
```

Corrected:

```
int x = 1;
```

### E0411 - misspelled keyword

An identifier is within edit distance of a control keyword and used where that keyword belongs.

```
whlie (true) { }
```

Corrected:

```
while (true) { }
```

### E0412 - undeclared variable

The name is not visible here. The closest visible name (Damerau-Levenshtein, type-aware) is suggested.

```
int count = 1;
print(cuont);
```

Corrected:

```
int count = 1;
print(count);
```

### E0413 - undeclared function

No function of that name exists.

```
prnt(1);
```

Corrected:

```
print(1);
```

### E0414 - variable called like a function

Only functions can be called.

```
int x = 1;
x(2);
```

### E0415 - function used as a value

A function name was used as a variable or assigned to.

```
func int f() { return 1; }
int x = f;
```

Corrected:

```
func int f() { return 1; }
int x = f();
```


## Warnings (W00xx)

### W0001 - division by constant zero (warning)

The divisor is the literal 0, so the program will fail at run time with R0001.

```
print(1 / 0);
```

### W0002 - unreachable code (warning)

Statements after 'return' in the same block never run.

```
func int f() { return 1; print(2); }
```


## Run-time errors (R00xx)

### R0001 - run-time: division by zero

An integer or float division had a zero divisor.

```
int z = 0;
print(1 / z);
```

### R0002 - run-time: modulo by zero

A % operation had a zero divisor.

```
int z = 0;
print(1 % z);
```

### R0003 - run-time: array index out of bounds

Arrays are checked on both read and write; An early version silently returned 0 / grew the array.

```
int[] a = [1, 2];
print(a[5]);
```

### R0004 - run-time: integer overflow

int is 64-bit signed; overflow is an error rather than wrap-around or bignum growth.

```
int x = 9223372036854775807;
print(x + 1);
```

### R0005 - run-time: stack overflow

Call depth exceeded 2000 (runaway recursion).

```
func int f(int n) { return f(n + 1); }
print(f(0));
```

### R0006 - run-time: step limit exceeded

The program executed more than 5,000,000 TAC instructions (probable infinite loop).

```
while (true) { }
```

### R0007 - run-time: variable read before assignment

A variable was read on a path where it was never assigned.

