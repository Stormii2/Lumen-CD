from lumen import compile_source, fix_to_fixpoint, apply_suggestions
from lumen.span import MACHINE, MAYBE


def test_machine_fix_inserts_semicolon():
    fixed, n, res = fix_to_fixpoint("int x = 5\nprint(x);\n")
    assert fixed == "int x = 5;\nprint(x);\n" and n == 1 and res.ok


def test_safe_mode_never_applies_maybe_fixes():
    src = 'string s = "oops;\nprint(s);\n'      # E0002: closing-quote guess is MAYBE
    fixed, n, res = fix_to_fixpoint(src)
    assert fixed == src and n == 0
    fixed2, n2, res2 = fix_to_fixpoint(src, unsafe=True)
    assert n2 >= 1 and fixed2 != src


def test_typo_fix_applies_only_when_unambiguous():
    fixed, n, res = fix_to_fixpoint("int count = 1;\nprint(cuont);\n")
    assert "print(count);" in fixed and res.ok


def test_fix_is_idempotent():
    src = "int a = 1\nitn b = 2;\nprint(a + b);\n"
    once, _, _ = fix_to_fixpoint(src)
    twice, n2, _ = fix_to_fixpoint(once)
    assert once == twice and n2 == 0


def test_valid_program_is_untouched():
    src = "int x = 1;\nprint(x);\n"
    fixed, n, res = fix_to_fixpoint(src)
    assert fixed == src and n == 0 and res.ok


def test_overlapping_edits_are_not_both_applied():
    src = "int x = 5 @ 3;\n"
    res = compile_source(src)
    new, applied = apply_suggestions(src, res.diagnostics, unsafe=True)
    # whatever was chosen, the result must not be corrupted text
    assert new.count("int x") == 1


def test_fix_reaches_a_program_that_runs():
    from lumen import TACInterpreter
    src = "int total = 5;\nitn count = 10;\ntotal = total + count\nprint(total);\n"
    fixed, n, res = fix_to_fixpoint(src)
    assert res.ok and n == 2
    r = compile_source(fixed)
    assert TACInterpreter(r.functions).run() == ["15"]


def test_every_suggestion_has_valid_edit_spans():
    srcs = ["int x = 5\nprint(x);", "whlie (true) { }", "int x = 1 @ 2;", 'string s = "a;', "itn x = 1;"]
    for src in srcs:
        for d in compile_source(src).diagnostics:
            if d.suggestion:
                assert d.suggestion.applicability in (MACHINE, MAYBE, "info")
                for e in d.suggestion.edits:
                    assert 0 <= e.span.start <= e.span.end <= len(src)
