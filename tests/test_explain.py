import pytest
from lumen import compile_source, explain
from lumen.explain import CATALOG


def test_unknown_code():
    assert explain("E9999") is None


def test_case_insensitive():
    assert explain("e0412").startswith("E0412")


@pytest.mark.parametrize("code", sorted(CATALOG))
def test_catalogue_example_triggers_its_code(code):
    summary, text, bad, fixed = CATALOG[code]
    assert summary and text
    if code.startswith("R"):
        if bad is None:
            return
        from lumen import TACInterpreter, LumenRuntimeError
        res = compile_source(bad)
        assert res.ok, res.codes
        with pytest.raises(LumenRuntimeError) as ei:
            TACInterpreter(res.functions).run()
        assert ei.value.code == code
        return
    if bad is None:
        return
    res = compile_source(bad)
    assert code in res.codes, f"{code}: example gave {res.codes}"
    if fixed is not None:
        assert compile_source(fixed).ok, f"{code}: corrected example still fails"


def test_every_emitted_code_is_documented():
    import re, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "lumen"
    emitted = set()
    for f in root.glob("*.py"):
        emitted |= set(re.findall(r'"([EWR]\d{4})"', f.read_text()))
    emitted -= {"R0099", "R0000"}       # internal assertion / default codes
    missing = sorted(emitted - set(CATALOG))
    assert not missing, f"undocumented diagnostic codes: {missing}"
