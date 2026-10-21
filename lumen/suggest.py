"""
Edit-distance machinery behind Lumen's "did you mean ...?" suggestions.

Two distances are provided:

  * levenshtein()          - classic insert / delete / substitute.
  * damerau_levenshtein()  - also counts a swap of two adjacent characters
                             as ONE edit (the unrestricted Lowrance-Wagner
                             algorithm). Transposition is the most common
                             typing slip (`itn`, `lenght`, `whiel`), so under
                             plain Levenshtein those typos cost 2 and fall
                             outside a tight threshold; under Damerau they
                             cost 1.

rank_candidates() turns a distance into an ordered list of repair
candidates, with a length-aware threshold and deterministic tie-breaking.
"""

from typing import Dict, Iterable, List, Optional, Tuple

KNOWN_KEYWORDS = ["int", "float", "bool", "string", "void", "true", "false",
                   "if", "else", "while", "return", "func"]


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def damerau_levenshtein(a: str, b: str) -> int:
    """Unrestricted Damerau-Levenshtein distance (Lowrance & Wagner, 1975)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if not la:
        return lb
    if not lb:
        return la
    inf = la + lb
    last_row: Dict[str, int] = {}
    # d is (la+2) x (lb+2); row/col 0 hold the sentinel `inf`
    d = [[0] * (lb + 2) for _ in range(la + 2)]
    d[0][0] = inf
    for i in range(la + 1):
        d[i + 1][0] = inf
        d[i + 1][1] = i
    for j in range(lb + 1):
        d[0][j + 1] = inf
        d[1][j + 1] = j
    for i in range(1, la + 1):
        last_match_col = 0
        for j in range(1, lb + 1):
            i1 = last_row.get(b[j - 1], 0)
            j1 = last_match_col
            if a[i - 1] == b[j - 1]:
                cost = 0
                last_match_col = j
            else:
                cost = 1
            d[i + 1][j + 1] = min(
                d[i][j] + cost,                                   # substitute
                d[i + 1][j] + 1,                                  # insert
                d[i][j + 1] + 1,                                  # delete
                d[i1][j1] + (i - i1 - 1) + 1 + (j - j1 - 1),      # transpose
            )
        last_row[a[i - 1]] = i
    return d[la + 1][lb + 1]


def max_edit_distance(word_len: int) -> int:
    """Length-aware threshold: short words tolerate only 1 edit (otherwise
    almost any two short names are 'close'), longer words up to 3."""
    return max(1, min(3, word_len // 3))


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def rank_candidates(word: str, candidates: Iterable[str],
                     preferred: Optional[Dict[str, bool]] = None,
                     max_distance: Optional[int] = None) -> List[Tuple[str, int]]:
    """Return [(candidate, distance)] best-first.

    Ordering key: edit distance (case-insensitive, so `Total` ~ `total` is
    distance 0), then whether the candidate is `preferred` (e.g. its type
    fits the context), then longer shared prefix, then closeness in length,
    then alphabetical so the result is fully deterministic.
    """
    limit = max_edit_distance(len(word)) if max_distance is None else max_distance
    ranked = []
    wl = word.lower()
    for cand in set(candidates):
        if cand == word:
            continue
        # Cheap lower bound: edit distance >= length difference, so most
        # candidates in a large scope are rejected without running the DP.
        if abs(len(cand) - len(word)) > limit:
            continue
        dist = damerau_levenshtein(wl, cand.lower())
        if dist > limit:
            continue
        pref = 0 if (preferred and preferred.get(cand)) else 1
        ranked.append((dist, pref, -_common_prefix(word, cand),
                       abs(len(word) - len(cand)), cand))
    ranked.sort()
    return [(r[-1], r[0]) for r in ranked]


def closest_keyword(word: str, candidates: Iterable[str] = KNOWN_KEYWORDS,
                     max_distance: Optional[int] = None) -> Optional[str]:
    ranked = rank_candidates(word, candidates, max_distance=max_distance)
    return ranked[0][0] if ranked else None


def closest_identifier(word: str, candidates: Iterable[str],
                        max_distance: Optional[int] = None) -> Optional[str]:
    """Best scope-visible identifier for an undeclared name, or None."""
    ranked = rank_candidates(word, candidates, max_distance=max_distance)
    return ranked[0][0] if ranked else None


def unique_best(ranked: List[Tuple[str, int]]) -> bool:
    """True when there is exactly one candidate at the minimum distance."""
    return bool(ranked) and (len(ranked) == 1 or ranked[1][1] > ranked[0][1])
