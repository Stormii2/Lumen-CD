import pytest
from lumen.suggest import (levenshtein, damerau_levenshtein, max_edit_distance,
                           rank_candidates, unique_best)


@pytest.mark.parametrize("a,b,d", [
    ("", "", 0), ("abc", "abc", 0), ("abc", "", 3), ("", "abc", 3),
    ("kitten", "sitting", 3), ("ab", "ba", 1), ("whlie", "while", 1),
    ("ca", "abc", 2),            # unrestricted DL (Lowrance-Wagner); optimal-string-alignment would give 3
    ("count", "cuont", 1), ("total", "totl", 1),
])
def test_damerau_levenshtein(a, b, d):
    assert damerau_levenshtein(a, b) == d
    assert damerau_levenshtein(b, a) == d               # symmetric


def test_transposition_is_cheaper_than_plain_levenshtein():
    assert levenshtein("cuont", "count") == 2
    assert damerau_levenshtein("cuont", "count") == 1


def test_triangle_inequality_sample():
    words = ["abc", "acb", "bca", "cab", "abcd", "xyz", ""]
    for a in words:
        for b in words:
            for c in words:
                assert damerau_levenshtein(a, c) <= damerau_levenshtein(a, b) + damerau_levenshtein(b, c)


def test_threshold_grows_with_length_and_is_capped():
    assert max_edit_distance(1) == 1 and max_edit_distance(3) == 1
    assert max_edit_distance(6) == 2 and max_edit_distance(30) == 3


def test_rank_prefers_closest_and_is_deterministic():
    r = rank_candidates("totl", ["total", "tool", "count"])
    assert r[0][0] == "total"
    assert r == rank_candidates("totl", ["count", "tool", "total"])    # order-independent


def test_unique_best_detects_ties():
    assert unique_best(rank_candidates("totl", ["total", "count"]))
    assert not unique_best(rank_candidates("aa", ["ab", "ac"]))


def test_far_words_give_no_candidates():
    assert rank_candidates("zzzzzz", ["total", "count"]) == []
