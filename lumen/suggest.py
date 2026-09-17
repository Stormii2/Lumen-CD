"""
Keyword confusion table: ranks known keywords by edit distance to an
unrecognised identifier, for the "did you mean ...?" suggestion path.
"""

from typing import Iterable, Optional

KNOWN_KEYWORDS = ["int", "float", "bool", "string", "true", "false"]


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
            cur[j] = min(
                prev[j] + 1,        # deletion
                cur[j - 1] + 1,     # insertion
                prev[j - 1] + cost  # substitution
            )
        prev = cur
    return prev[-1]


def closest_keyword(word: str, candidates: Iterable[str] = KNOWN_KEYWORDS,
                     max_distance: int = 2) -> Optional[str]:
    best, best_dist = None, max_distance + 1
    for cand in candidates:
        d = levenshtein(word, cand)
        if d < best_dist:
            best, best_dist = cand, d
    return best if best_dist <= max_distance else None
