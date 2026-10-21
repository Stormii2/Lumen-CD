"""
Table-driven functional test cases for the whole compiler (lex -> parse ->
check -> TAC -> run).

Each case is a dict:
    id      short unique id (category prefix: V valid, I invalid, B boundary,
            E error-recovery, C complex)
    desc    one-line purpose
    src     Lumen source text
    out     expected printed lines (valid programs)          -- or --
    codes   expected diagnostic codes, in source order       -- or --
    rt      expected run-time error code (program compiles, then fails)
    warn    optional: expected warning codes (compile still succeeds)

`tools/gen_test_report.py` runs the same table and renders docs/TEST_CASES.md.
"""

CASES = []


def case(id, cat, desc, src, **expect):
    CASES.append(dict(id=id, cat=cat, desc=desc, src=src, **expect))


def prog(path):
    import os
    with open(os.path.join(os.path.dirname(__file__), "..", "eval", "programs", path)) as f:
        return f.read()


# ============================== VALID ======================================
case("V01", "valid", "arithmetic precedence and parentheses",
     "print(2 + 3 * 4);\nprint((2 + 3) * 4);\nprint(10 - 4 - 3);", out=["14", "20", "3"])
case("V02", "valid", "integer division truncates toward zero",
     "print(7 / 2);\nprint(-7 / 2);\nprint(7 / -2);", out=["3", "-3", "-3"])
case("V03", "valid", "modulo takes the sign of the dividend",
     "print(7 % 3);\nprint(-7 % 3);\nprint(7 % -3);", out=["1", "-1", "1"])
case("V04", "valid", "int widens to float in mixed arithmetic",
     "float f = 5;\nprint(f / 2);\nprint(1 + 2.5);", out=["2.5", "3.5"])
case("V05", "valid", "booleans print as true/false",
     "print(true);\nprint(false);\nprint(3 < 4);", out=["true", "false", "true"])
case("V06", "valid", "string literals and comparison",
     'string a = "hi";\nprint(a);\nprint(a == "hi");\nprint("a" != "b");', out=["hi", "true", "true"])
case("V07", "valid", "&& short-circuits (right side not evaluated)",
     "int z = 0;\nprint(false && (1 / z == 0));", out=["false"])
case("V08", "valid", "|| short-circuits (right side not evaluated)",
     "int z = 0;\nprint(true || (1 / z == 0));", out=["true"])
case("V09", "valid", "inner-scope shadowing does not clobber the outer variable",
     "int x = 1;\nif (true) {\n  int x = 2;\n  print(x);\n}\nprint(x);", out=["2", "1"])
case("V10", "valid", "functions read and write globals",
     "int g = 10;\nfunc int bump() {\n  g = g + 1;\n  return g;\n}\nprint(bump());\nprint(bump());\nprint(g);",
     out=["11", "12", "12"])
case("V11", "valid", "recursion",
     "func int fact(int n) {\n  if (n <= 1) { return 1; }\n  return n * fact(n - 1);\n}\nprint(fact(10));",
     out=["3628800"])
case("V12", "valid", "forward reference to a later function",
     "print(twice(4));\nfunc int twice(int v) { return v * 2; }", out=["8"])
case("V13", "valid", "arrays: read, write, pass by reference",
     "func void zero(int[] a) { a[1] = 0; }\nint[] v = [5, 6, 7];\nzero(v);\nprint(v[0]);\nprint(v[1]);\nprint(v[2]);",
     out=["5", "0", "7"])
case("V14", "valid", "else-if chain",
     "func string sign(int n) {\n  if (n < 0) { return \"neg\"; } else if (n == 0) { return \"zero\"; } else { return \"pos\"; }\n}\nprint(sign(-1));\nprint(sign(0));\nprint(sign(9));",
     out=["neg", "zero", "pos"])
case("V15", "valid", "void function with side effect",
     "func void hello() { print(\"hello\"); }\nhello();", out=["hello"])
case("V16", "valid", "unary minus and not",
     "int a = 5;\nprint(-a);\nprint(--a);\nprint(!false);", out=["-5", "5", "true"])
case("V17", "valid", "comments are ignored",
     "// full line\nint x = 1; // trailing\nprint(x);", out=["1"])
case("V18", "valid", "CRLF line endings", "int x = 1;\r\nprint(x);\r\n", out=["1"])
case("V19", "valid", "float array literal accepts int elements",
     "float[] a = [1, 2.5];\nprint(a[0]);\nprint(a[1]);", out=["1.0", "2.5"])
case("V20", "valid", "nested loops", 
     "int s = 0;\nint i = 0;\nwhile (i < 3) {\n  int j = 0;\n  while (j < 3) { s = s + 1; j = j + 1; }\n  i = i + 1;\n}\nprint(s);",
     out=["9"])
case("V21", "valid", "print with several arguments", "print(1, 2, \"x\");", out=["1 2 x"])
case("V22", "valid", "parameters shadow globals",
     "int n = 5;\nfunc int f(int n) { return n + 1; }\nprint(f(1));\nprint(n);", out=["2", "5"])
case("V23", "valid", "int returned from float function widens",
     "func float half() { return 1; }\nprint(half());", out=["1.0"])
case("V24", "valid", "comparison chain through variables",
     "int a = 3;\nint b = 4;\nbool ok = a < b && b <= 4 && a != b;\nprint(ok);", out=["true"])

# ============================== INVALID ====================================
case("I01", "invalid", "undeclared variable (typo)", "int count = 1;\nprint(cuont);", codes=["E0412"])
case("I02", "invalid", "undeclared function", "prnt(1);", codes=["E0413"])
case("I03", "invalid", "type mismatch in initialiser", 'int x = "hi";', codes=["E0302"])
case("I04", "invalid", "type mismatch in assignment", 'int x = 1;\nx = 2.5;', codes=["E0302"])
case("I05", "invalid", "string + int", 'int x = 1 + "a";', codes=["E0304"])
case("I06", "invalid", "int used as a condition", "int n = 1;\nif (n) { print(n); }", codes=["E0307"])
case("I07", "invalid", "duplicate declaration in one scope", "int x = 1;\nint x = 2;", codes=["E0300"])
case("I08", "invalid", "too many call arguments",
     "func int f(int a) { return a; }\nprint(f(1, 2));", codes=["E0311"])
case("I09", "invalid", "wrong argument type (E0305 is the type-mismatch code)",
     "func int f(int a) { return a; }\nprint(f(\"s\"));", codes=["E0305"])
case("I10", "invalid", "return type mismatch", 'func int f() { return "a"; }', codes=["E0308"])
case("I11", "invalid", "missing return on one path",
     "func int f(int a) { if (a > 0) { return 1; } }", codes=["E0310"])
case("I12", "invalid", "return outside a function", "return 1;", codes=["E0309"])
case("I13", "invalid", "indexing a non-array", "int x = 1;\nprint(x[0]);", codes=["E0306"])
case("I14", "invalid", "non-int array index", "int[] a = [1];\nprint(a[true]);", codes=["E0303"])
case("I15", "invalid", "inconsistent array literal", 'int[] a = [1, "two"];', codes=["E0305"])
case("I16", "invalid", "void variable", "void x = 1;", codes=["E0312"])
case("I17", "invalid", "void call used as a value", "func void f() { }\nprint(f());", codes=["E0313"])
case("I18", "invalid", "calling a variable", "int x = 1;\nx(2);", codes=["E0414"])
case("I19", "invalid", "function used as a value", "func int f() { return 1; }\nint x = f;", codes=["E0415"])
case("I20", "invalid", "duplicate parameter", "func int f(int a, int a) { return a; }", codes=["E0301"])
case("I21", "invalid", "missing semicolon", "int x = 5\nprint(x);", codes=["E0201"])
case("I22", "invalid", "missing closing paren", "print((1 + 2);", codes=["E0202"])
case("I23", "invalid", "missing expression", "int x = ;", codes=["E0203"])
case("I24", "invalid", "missing initialiser", "int x;", codes=["E0102"])
case("I25", "invalid", "unterminated string", 'string s = "hello;', codes=["E0002"])
case("I26", "invalid", "invalid character", "int x = 5 @ 3;", codes=["E0001"])
case("I27", "invalid", "malformed number", "float f = 1.2.3;", codes=["E0003"])
case("I28", "invalid", "unknown type name", "integer x = 1;", codes=["E0410"])
case("I29", "invalid", "misspelled while", "whlie (true) { }", codes=["E0411"])
case("I30", "invalid", "missing close brace", "if (true) { print(1);", codes=["E0121"])
case("I31", "invalid", "unary - on bool", "int x = -true;", codes=["E0303"])
case("I32", "invalid", "missing function return type", "func f() { return 1; }", codes=["E0110"])
case("I33", "invalid", "nested function (balanced braces)", "func void f() { func void g() { } }", codes=["E0126"])
case("I34", "invalid", "bool arithmetic", "bool b = true + false;", codes=["E0304"])

# ============================== BOUNDARY ===================================
case("B01", "boundary", "empty file", "", out=[])
case("B02", "boundary", "only comments and blank lines", "// nothing\n\n   \n// more\n", out=[])
case("B03", "boundary", "file without trailing newline", "print(1);", out=["1"])
case("B04", "boundary", "largest int literal", "print(9223372036854775807);", out=["9223372036854775807"])
case("B05", "boundary", "int overflow on add is a run-time error",
     "int m = 9223372036854775807;\nprint(m + 1);", rt="R0004")
case("B06", "boundary", "int overflow on multiply",
     "int m = 4611686018427387904;\nprint(m * 2);", rt="R0004")
case("B07", "boundary", "first array element", "int[] a = [7, 8, 9];\nprint(a[0]);", out=["7"])
case("B08", "boundary", "last array element", "int[] a = [7, 8, 9];\nprint(a[2]);", out=["9"])
case("B09", "boundary", "index == length is out of bounds", "int[] a = [7, 8, 9];\nprint(a[3]);", rt="R0003")
case("B10", "boundary", "negative index is out of bounds", "int[] a = [7, 8, 9];\nprint(a[0 - 1]);", rt="R0003")
case("B11", "boundary", "array write out of bounds", "int[] a = [1];\na[5] = 2;", rt="R0003")
case("B12", "boundary", "empty array literal can be declared, indexing fails",
     "int[] a = [];\nprint(a[0]);", rt="R0003")
case("B13", "boundary", "single-element array", "int[] a = [42];\nprint(a[0]);", out=["42"])
case("B14", "boundary", "loop that runs zero times",
     "int i = 5;\nwhile (i < 3) { print(i); }\nprint(\"done\");", out=["done"])
case("B15", "boundary", "division by zero", "int z = 0;\nprint(5 / z);", rt="R0001")
case("B16", "boundary", "modulo by zero", "int z = 0;\nprint(5 % z);", rt="R0002")
case("B17", "boundary", "float division by zero is also an error",
     "float z = 0.0;\nprint(1.0 / z);", rt="R0001")
case("B18", "boundary", "constant division by zero warns, then fails at run time",
     "print(1 / 0);", warn=["W0001"], rt="R0001")
case("B19", "boundary", "recursion depth 1500 is fine",
     "func int d(int n) { if (n == 0) { return 0; } return 1 + d(n - 1); }\nprint(d(1500));", out=["1500"])
case("B20", "boundary", "runaway recursion is a clean stack-overflow error",
     "func int f(int n) { return f(n + 1); }\nprint(f(0));", rt="R0005")
case("B21", "boundary", "infinite loop hits the step limit", "while (true) { }", rt="R0006")
case("B22", "boundary", "nesting depth 60 parses", "print(" + "(" * 60 + "1" + ")" * 60 + ");", out=["1"])
case("B23", "boundary", "nesting depth 3000 is rejected, not a crash",
     "print(" + "(" * 3000 + "1" + ")" * 3000 + ");", codes=["E0204"])
case("B24", "boundary", "5000-term expression chain is rejected, not a crash",
     "int x = " + " + ".join(["1"] * 5000) + ";", codes=["E0204"])
case("B25", "boundary", "very long identifier", "int " + "a" * 500 + " = 3;\nprint(" + "a" * 500 + ");", out=["3"])
case("B26", "boundary", "2000-statement program",
     "int s = 0;\n" + "s = s + 1;\n" * 2000 + "print(s);", out=["2000"])
case("B27", "boundary", "unterminated string at end of file, no newline", 'print("abc', codes=["E0002", "E0202"])
case("B28", "boundary", "INT_MIN reachable through subtraction",
     "int m = 0 - 9223372036854775807 - 1;\nprint(m);", out=["-9223372036854775808"])
case("B29", "boundary", "negating INT_MIN overflows",
     "int m = 0 - 9223372036854775807 - 1;\nprint(-m);", rt="R0004")
case("B30", "boundary", "stray closing brace does not hang the parser", "int x = 1;\n}\nprint(x);", codes=["E0124"])
case("B31", "boundary", "else without if does not hang the parser", "else { print(1); }", codes=["E0125"])
case("B32", "boundary", "unicode in a string literal", 'print("héllo ✓");', out=["héllo ✓"])

# ============================== ERROR RECOVERY ==============================
case("E01", "recovery", "three independent defects are all reported",
     "int a = 1\nint b = zzz;\nstring s = 5;", codes=["E0201", "E0412", "E0302"])
case("E02", "recovery", "poisoning: a failed initialiser does not cascade",
     "int x = totl;\nint y = x + 1;\nprint(y * 2);", codes=["E0412"])
case("E03", "recovery", "poisoning: unterminated string yields one diagnostic",
     'string s = "oops;\nprint(s);', codes=["E0002"])
case("E04", "recovery", "keyword typo yields one diagnostic, body still checked",
     "int n = 3;\nwhlie (n > 0) {\n  n = n - 1;\n}\nint q = nn;", codes=["E0411", "E0412"])
case("E05", "recovery", "misspelled return is repaired",
     "func int f() {\n  retrun 1;\n}", codes=["E0411"])
case("E06", "recovery", "missing '=' assumed: later use of the variable is silent",
     "int x 5;\nprint(x);", codes=["E0102"])
case("E07", "recovery", "missing initialiser: later use of the variable is silent",
     "int x;\nprint(x);", codes=["E0102"])
case("E08", "recovery", "unknown type name: variable is still declared",
     "itn x = 5;\nprint(x);", codes=["E0410"])
case("E09", "recovery", "bad character does not poison the whole statement",
     "int x = 1 @ + 2;\nprint(x);", codes=["E0001"])
case("E10", "recovery", "two missing semicolons, both reported",
     "int a = 1\nint b = 2\nprint(a + b);", codes=["E0201", "E0201"])
case("E11", "recovery", "error in one function does not hide errors in the next",
     "func int f() { return nope; }\nfunc int g() { return nada; }", codes=["E0412", "E0412"])
case("E12", "recovery", "an error inside a function body does not break global code",
     "func int f() { return nope; }\nprint(1);\nprint(oops);", codes=["E0412", "E0412"])
case("E13", "recovery", "missing '{' of a block is assumed",
     "int i = 0;\nwhile (i < 3)\n  i = i + 1;\n  print(i);\n", codes=["E0120"])
case("E14", "recovery", "junk before the block: skipped, block parsed normally",
     "int n = 1;\nif (n > 0 ,) {\n  print(n);\n}", codes=["E0202"])
case("E15", "recovery", "missing '}' before the next function is detected there",
     "func int f() {\n  return 1;\n\nfunc int g() {\n  return 2;\n}\nprint(g());", codes=["E0121"])
case("E16", "recovery", "misspelled parameter type is repaired",
     "func int f(itn a) { return a; }\nprint(f(2));", codes=["E0410"])
case("E17", "recovery", "misspelled return type is repaired",
     "func itn f() { return 1; }\nprint(f());", codes=["E0410"])
case("E18", "recovery", "warnings do not stop execution",
     "func int f() { return 1; print(2); }\nprint(f());", warn=["W0002"], out=["1"])
case("E19", "recovery", "warning and error reported together (source order)",
     "func int f() { return 1; print(2); }\nint x = nope;", codes=["W0002", "E0412"])
case("E20", "recovery", "missing ']' in array type is reported once",
     "func void f(int[ a) { }", codes=["E0123"])
case("E21", "recovery", "a lost variable name does not cascade to every use",
     "int  = 84;\nint y = x + 1;\nprint(y + x);", codes=["E0101"])
case("E22", "recovery", "semantic fallout inside a syntactically damaged statement is muted",
     "int total = 1;\nint[] nums = [1, 2];\ntotal = total + nums]0];", codes=["E0201"])

# ============================== COMPLEX ====================================
case("C01", "complex", "primes below 50", prog("p01_primes.lum"),
     out=["2", "3", "5", "7", "11", "13", "17", "19", "23", "29", "31", "37", "41", "43", "47", "15"])
case("C02", "complex", "bubble sort on an array parameter", prog("p02_sort.lum"),
     out=["11", "12", "22", "25", "34", "64", "90"])
case("C03", "complex", "gcd / lcm", prog("p03_gcd.lum"), out=["12", "252"])
case("C04", "complex", "longest Collatz chain under 30", prog("p04_collatz.lum"), out=["27", "111"])
case("C05", "complex", "float statistics", prog("p05_stats.lum"), out=["84.45", "95.25"])
case("C06", "complex", "boolean logic and strings", prog("p06_logic.lum"),
     out=["child", "teen", "adult", "other", "true"])
case("C07", "complex", "scopes and globals", prog("p07_scope.lum"), out=["20", "10", "6", "8", "8"])
case("C08", "complex", "fibonacci table in an array", prog("p08_fibtable.lum"), out=["88", "34"])
case("C09", "complex", "digit manipulation", prog("p09_digits.lum"), out=["35", "54321", "true", "false"])
case("C10", "complex", "int/float mixing in calls", prog("p10_power.lum"), out=["1024", "243", "10.0", "done"])
case("C11", "complex", "fib(18) recursion",
     "func int fib(int n) { if (n < 2) { return n; } return fib(n - 1) + fib(n - 2); }\nprint(fib(18));",
     out=["2584"])
case("C12", "complex", "sieve-style counting with array",
     "int[] flags = [1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1];\n"
     "int i = 2;\nwhile (i < 30) {\n  if (flags[i] == 1) {\n    int j = i * 2;\n    while (j < 30) { flags[j] = 0; j = j + i; }\n  }\n  i = i + 1;\n}\n"
     "int count = 0;\nint k = 2;\nwhile (k < 30) { if (flags[k] == 1) { count = count + 1; } k = k + 1; }\nprint(count);",
     out=["10"])
case("E23", "recovery", "an independent type error is still found after a failed initialiser",
     "int x = totl;\nstring s = x;", codes=["E0412", "E0302"])
