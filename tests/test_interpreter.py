import pytest
from lumen import compile_source, TACInterpreter, LumenRuntimeError, format_value


def run(src, **kw):
    res = compile_source(src)
    assert res.ok, res.codes
    return TACInterpreter(res.functions, **kw).run()


def test_runtime_error_carries_a_span_pointing_at_the_operation():
    src = "int z = 0;\nprint(5 / z);\n"
    res = compile_source(src)
    with pytest.raises(LumenRuntimeError) as ei:
        TACInterpreter(res.functions).run()
    e = ei.value
    assert e.code == "R0001"
    assert src[e.span.start:e.span.end] == "5 / z"


def test_print_fn_streams_output_before_a_failure():
    seen = []
    res = compile_source("print(1);\nprint(2);\nint z = 0;\nprint(1 / z);\n")
    with pytest.raises(LumenRuntimeError):
        TACInterpreter(res.functions, print_fn=seen.append).run()
    assert seen == ["1", "2"]


def test_step_limit_is_configurable():
    res = compile_source("int i = 0;\nwhile (i < 1000) { i = i + 1; }\n")
    with pytest.raises(LumenRuntimeError) as ei:
        TACInterpreter(res.functions, max_steps=100).run()
    assert ei.value.code == "R0006"
    assert TACInterpreter(res.functions, max_steps=100000).run() == []


def test_call_depth_limit_is_configurable():
    src = "func int d(int n) { if (n == 0) { return 0; } return 1 + d(n - 1); }\nprint(d(50));"
    res = compile_source(src)
    with pytest.raises(LumenRuntimeError) as ei:
        TACInterpreter(res.functions, max_depth=20).run()
    assert ei.value.code == "R0005"
    assert TACInterpreter(res.functions, max_depth=100).run() == ["50"]


def test_format_value():
    assert format_value(True) == "true" and format_value(False) == "false"
    assert format_value([1, True, 2.5]) == "[1, true, 2.5]"
    assert format_value("s") == "s" and format_value(3) == "3"


def test_printing_an_array():
    assert run("int[] a = [1, 2, 3];\nprint(a);") == ["[1, 2, 3]"]


def test_globals_visible_in_functions_and_main_frame_is_globals():
    assert run("int g = 1;\nfunc void inc() { g = g + 1; }\ninc();\ninc();\nprint(g);") == ["3"]


def test_recursion_limit_restored_after_run():
    import sys
    before = sys.getrecursionlimit()
    run("print(1);")
    assert sys.getrecursionlimit() == before


def test_interpreters_are_independent():
    a = compile_source("int x = 1;\nprint(x);").functions
    b = compile_source("int x = 2;\nprint(x);").functions
    assert TACInterpreter(a).run() == ["1"]
    assert TACInterpreter(b).run() == ["2"]
    assert TACInterpreter(a).run() == ["1"]
