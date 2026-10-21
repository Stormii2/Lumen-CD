"""
Each test here is a defect that boundary / adversarial testing found in the
first working version (see docs/BUG_LOG.md). They pin the current behaviour.
"""
import pytest
from lumen import compile_source, TACInterpreter, LumenRuntimeError


def out(src):
    res = compile_source(src)
    assert res.ok, res.codes
    return TACInterpreter(res.functions).run()


def test_stray_close_brace_terminates():            # early version: infinite loop
    assert compile_source("}\n").codes == ["E0124"]


def test_else_without_if_terminates():              # early version: infinite loop
    assert compile_source("else { }\n").codes == ["E0125"]


def test_division_by_zero_is_an_error_not_zero():   # early version: printed 0
    with pytest.raises(LumenRuntimeError) as e:
        out("int z = 0;\nprint(1 / z);")
    assert e.value.code == "R0001"


def test_array_out_of_bounds_is_an_error_not_zero():  # early version: read gave 0, write grew array
    with pytest.raises(LumenRuntimeError):
        out("int[] a = [1];\nprint(a[4]);")
    with pytest.raises(LumenRuntimeError):
        out("int[] a = [1];\na[4] = 2;")


def test_shadowing_does_not_overwrite_outer():       # early version: printed 2
    assert out("int x = 1;\nif (true) { int x = 2; }\nprint(x);") == ["1"]


def test_int_to_float_widening_at_runtime():         # early version: float x = 5; x/2 -> 2
    assert out("float x = 5;\nprint(x / 2);") == ["2.5"]


def test_bool_prints_as_word():                      # early version: printed 1
    assert out("print(true);") == ["true"]


def test_truncating_division():                      # early version: floor division, -7/2 == -4
    assert out("print(-7 / 2);\nprint(-7 % 2);") == ["-3", "-1"]


def test_uninitialised_declaration_single_diagnostic():   # early version: 2 diagnostics
    assert compile_source("int x;\nprint(x);").codes == ["E0102"]


def test_empty_array_literal_has_sensible_type():    # early version: "type 'Error[]'"
    assert compile_source("int[] a = [];").ok


def test_void_variable_has_a_specific_error():       # early version: odd E0302
    assert compile_source("void x = 1;").codes == ["E0312"]


def test_functions_see_globals():                    # early version: returned 0
    assert out("int g = 7;\nfunc int f() { return g; }\nprint(f());") == ["7"]


def test_logical_operators_short_circuit():          # early version: both sides always evaluated
    assert out("int z = 0;\nprint(false && 1 / z == 0);") == ["false"]


def test_unterminated_string_single_diagnostic():    # early version: E0002 + E0201
    assert compile_source('string s = "abc;\n').codes == ["E0002"]


def test_keyword_typo_single_diagnostic():           # early version: E0201 + E0413
    assert compile_source("whlie (true) { }").codes == ["E0411"]


def test_integer_overflow_detected():                # early version: unbounded Python ints
    with pytest.raises(LumenRuntimeError) as e:
        out("int m = 9223372036854775807;\nprint(m + 1);")
    assert e.value.code == "R0004"


def test_deep_nesting_does_not_crash_the_compiler():   # early version: uncaught RecursionError
    assert compile_source("print(" + "(" * 5000 + "1" + ")" * 5000 + ");").codes == ["E0204"]


def test_runaway_recursion_does_not_crash_the_interpreter():   # early version: uncaught RecursionError
    with pytest.raises(LumenRuntimeError) as e:
        out("func int f(int n) { return f(n + 1); }\nprint(f(0));")
    assert e.value.code == "R0005"


def test_missing_bracket_in_type_is_reported():      # early version: silently accepted
    assert compile_source("func void f(int[ a) { }").codes == ["E0123"]
