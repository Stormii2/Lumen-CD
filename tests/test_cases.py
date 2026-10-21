import pytest
from lumen import compile_source, TACInterpreter, LumenRuntimeError
from cases import CASES


def run_case(c):
    res = compile_source(c["src"])
    errors = [d.code for d in res.diagnostics if d.severity == "error"]
    warns = [d.code for d in res.diagnostics if d.severity == "warning"]
    if "codes" in c:
        return "compile", res.diagnostics, errors + [w for w in warns if w in c["codes"]], None
    return res, errors, warns


@pytest.mark.parametrize("c", CASES, ids=[c["id"] for c in CASES])
def test_case(c):
    res = compile_source(c["src"])
    all_codes = [d.code for d in res.diagnostics]

    if "codes" in c:
        assert all_codes == c["codes"], f"{c['id']}: expected {c['codes']}, got {all_codes}"
        return

    assert res.ok, f"{c['id']}: unexpected errors {all_codes}"
    if "warn" in c:
        assert all_codes == c["warn"]
    else:
        assert all_codes == []
    interp = TACInterpreter(res.functions)
    if "rt" in c:
        with pytest.raises(LumenRuntimeError) as ei:
            interp.run()
        assert ei.value.code == c["rt"]
        assert ei.value.span is not None or c["rt"] in ("R0005", "R0006")
    else:
        assert interp.run() == c["out"]


def test_case_ids_unique_and_categories_present():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    cats = {c["cat"] for c in CASES}
    assert cats == {"valid", "invalid", "boundary", "recovery", "complex"}
