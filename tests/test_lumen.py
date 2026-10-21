"""
PyTest suite covering the lexer, parser, type checker (poisoning), and
the TAC generator + interpreter. Run with: pytest -q
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lumen.lexer import Lexer
from lumen.parser import Parser
from lumen.checker import TypeChecker
from lumen.tac import TACGenerator
from lumen.interpreter import TACInterpreter


def compile_source(src: str):
    tokens, lex_diags = Lexer(src).scan()
    parser = Parser(tokens, src)
    items = parser.parse_program()
    checker = TypeChecker(src)
    checker.check_program(items)
    diags = lex_diags + parser.diagnostics + checker.diagnostics
    return items, diags


def run_source(src: str):
    items, diags = compile_source(src)
    assert not diags, f"expected clean compile, got: {[d.code for d in diags]}"
    functions = TACGenerator().generate(items)
    return TACInterpreter(functions).run()


# ---- lexer ----------------------------------------------------------

def test_lexer_invalid_character_recovers():
    _, diags = compile_source("int x = 1;\nweird@ = 2;\n")
    codes = [d.code for d in diags]
    assert "E0001" in codes


def test_lexer_unterminated_string_recovers():
    tokens, diags = Lexer('string s = "abc;\n').scan()
    assert any(d.code == "E0002" for d in diags)
    assert tokens[-1].kind.name == "EOF"  # lexer still reached EOF


def test_lexer_malformed_number():
    _, diags = Lexer("float x = 1.2.3;").scan()
    assert any(d.code == "E0003" for d in diags)


# ---- parser recovery --------------------------------------------------

def test_missing_semicolon_is_phrase_level_recovered():
    _, diags = compile_source("int a = 1\nint b = 2;\n")
    codes = [d.code for d in diags]
    assert "E0201" in codes
    # crucially: b's declaration itself parses cleanly, no extra parser noise
    assert codes.count("E0201") == 1


def test_keyword_typo_suggests_correction():
    _, diags = compile_source("itn x = 5;\n")
    e0410 = [d for d in diags if d.code == "E0410"]
    assert len(e0410) == 1
    assert "int" in e0410[0].help


def test_panic_mode_recovers_and_keeps_parsing():
    _, diags = compile_source("@@@ ;\nint x = 1;\n")
    # the garbage line is reported once and doesn't stop x from parsing
    assert any(d.code == "E0001" for d in diags)
    codes = [d.code for d in diags]
    assert "E0100" not in codes or codes.count("E0100") <= 1


# ---- type checker / poisoning -----------------------------------------

def test_undeclared_variable_reported_once_per_use():
    _, diags = compile_source("int x = totl + 1;\n")
    assert [d.code for d in diags] == ["E0412"]


def test_poisoning_suppresses_downstream_cascade():
    src = "int x = totl + 1;\nx = x * 2;\nint y = x + 3;\n"
    _, diags = compile_source(src)
    # exactly one diagnostic for the whole file: the one real defect.
    # x and y both become type Error and every further use is silent.
    assert [d.code for d in diags] == ["E0412"]


def test_redeclaration_reports_with_label():
    _, diags = compile_source("int x = 1;\nint x = 2;\n")
    assert [d.code for d in diags] == ["E0300"]
    assert diags[0].labels, "expected a label pointing at the first declaration"


def test_type_mismatch_on_initialiser():
    _, diags = compile_source('int x = "hello";\n')
    assert [d.code for d in diags] == ["E0302"]


def test_int_widens_to_float_without_error():
    _, diags = compile_source("float x = 5;\n")
    assert diags == []


def test_condition_must_be_bool():
    _, diags = compile_source("int x = 1;\nwhile (x) { x = x - 1; }\n")
    assert [d.code for d in diags] == ["E0307"]


def test_function_arity_mismatch():
    src = "func int add(int a, int b) { return a + b; }\nint s = add(1, 2, 3);\n"
    _, diags = compile_source(src)
    assert [d.code for d in diags] == ["E0311"]


def test_return_path_validation_catches_missing_return():
    src = "func int f(int a) { if (a > 0) { return a; } }\n"
    _, diags = compile_source(src)
    assert [d.code for d in diags] == ["E0310"]


def test_return_path_validation_accepts_if_else():
    src = "func int f(int a) { if (a > 0) { return a; } else { return 0; } }\nint x = f(3);\n"
    _, diags = compile_source(src)
    assert diags == []


def test_clean_program_has_zero_diagnostics():
    src = "int a = 5;\nint b = a + 2;\n"
    _, diags = compile_source(src)
    assert diags == []


# ---- TAC generation + interpreter -------------------------------------

def test_arithmetic_executes_correctly():
    out = run_source("int x = 2 + 3 * 4;\nprint(x);\n")
    assert out == ["14"]


def test_if_else_branches_correctly():
    src = 'int x = 5;\nif (x > 3) { print("big"); } else { print("small"); }\n'
    assert run_source(src) == ["big"]


def test_while_loop_executes():
    src = "int i = 0;\nint total = 0;\nwhile (i < 5) { total = total + i; i = i + 1; }\nprint(total);\n"
    assert run_source(src) == ["10"]  # 0+1+2+3+4


def test_recursive_factorial():
    src = "func int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }\nprint(fact(5));\n"
    assert run_source(src) == ["120"]


def test_array_indexing_and_mutation():
    src = "int[] a = [1, 2, 3];\na[1] = 99;\nprint(a[0]);\nprint(a[1]);\nprint(a[2]);\n"
    assert run_source(src) == ["1", "99", "3"]


def test_string_concatenation():
    assert run_source('print("a" + "b");\n') == ["ab"]


def test_function_with_multiple_params_and_recursion():
    src = ("func int gcd(int a, int b) { if (b == 0) { return a; } return gcd(b, a - (a / b) * b); }\n"
           "print(gcd(48, 18));\n")
    assert run_source(src) == ["6"]
