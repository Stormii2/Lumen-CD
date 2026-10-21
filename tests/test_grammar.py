from lumen import grammar as G
from lumen.tokens import T


def test_grammar_is_ll1():
    assert G.is_ll1(), G.predict_conflicts()
    assert G.predict_conflicts() == []


def test_first_of_statement_is_the_expected_set():
    first = G.FIRST["statement"]
    assert first == {"BOOL", "FLOAT", "IDENT", "IF", "INT", "LBRACE", "RETURN",
                     "STRING_KW", "VOID", "WHILE"}


def test_follow_of_statement_contains_rbrace_and_starters():
    follow = G.FOLLOW["statement"]
    assert "RBRACE" in follow and "EOF" in follow
    assert "WHILE" in follow and "IF" in follow


def test_sync_set_is_strong_starters_plus_semi_rbrace():
    names = {k.name if hasattr(k, "name") else k for k in G.SYNC_SET}
    assert {"SEMI", "RBRACE", "WHILE", "IF", "RETURN", "INT"} <= names
    assert "IDENT" not in names and "LBRACE" not in names     # weak starters excluded


def test_nullable_computation():
    nul = G.compute_nullable()
    assert "program" in nul            # an empty file is a valid program
    assert "statement" not in nul      # a statement always consumes input


def test_tables_render():
    text = G.format_tables()
    assert "FIRST" in text and "FOLLOW" in text
