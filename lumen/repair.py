"""
Module 3 (continued): the Repair Suggestion Engine.

Turns "this name does not exist" into ranked, classified repair
suggestions. The two ingredients are

  * a ranked candidate list (Damerau-Levenshtein distance over the names
    visible in the current scope, with a length-aware threshold and a
    type-compatibility preference - see suggest.rank_candidates), and
  * an *applicability class* that says how safe it is to apply the edit
    mechanically:

        machine-applicable  one clearly-best candidate; safe for `--fix`
        maybe-incorrect     a plausible guess; needs `--unsafe-fixes`

The classification is deliberately conservative: a rename is only
machine-applicable when the best candidate is UNIQUE at the minimum edit
distance, within ONE edit, and the misspelt name is at least 4 characters
(very short names are close to everything). The evaluation harness measures
how often that conservatism is justified (see docs/EVALUATION.md).
"""

from typing import Dict, Iterable, List, Optional, Tuple

from .span import Span, Edit, Suggestion, MACHINE, MAYBE
from .suggest import rank_candidates, unique_best


def _decisive(ranked: List[Tuple[str, int]], preferred: Optional[Dict[str, bool]]) -> bool:
    """Is there one clearly-best candidate?"""
    if not ranked:
        return False
    if unique_best(ranked):
        return True
    if preferred:
        best_d = ranked[0][1]
        tied = [c for c, d in ranked if d == best_d]
        return sum(1 for c in tied if preferred.get(c)) == 1
    return False


def suggest_name(word: str, span: Span, candidates: Iterable[str],
                 preferred: Optional[Dict[str, bool]] = None,
                 what: str = "variable") -> Tuple[Optional[str], Optional[Suggestion]]:
    """Return (help_text, Suggestion) for an unknown `word`, or (None, None)."""
    ranked = rank_candidates(word, candidates, preferred)
    if not ranked:
        return None, None
    best, dist = ranked[0]
    tied = [c for c, d in ranked if d == dist]

    if len(tied) > 1 and not _decisive(ranked, preferred):
        shown = ", ".join(f"'{c}'" for c in tied[:3])
        help_text = f"did you mean one of {shown}?"
    else:
        help_text = f"did you mean '{best}'?"

    decisive = _decisive(ranked, preferred)
    applicability = MACHINE if (decisive and dist <= 1 and len(word) >= 4) else MAYBE
    return help_text, Suggestion(f"rename the {what} to '{best}'",
                                 [Edit(span, best)], applicability)


def suggest_condition(cond_text: str, span: Span) -> Suggestion:
    """`while (n)` with an int `n`: compare against zero explicitly."""
    return Suggestion("compare the value with zero to make it a 'bool'",
                      [Edit(span, f"{cond_text} != 0")], MAYBE)


def suggest_assign_instead(decl_start: int, name_start: int) -> Suggestion:
    """`int x = 5;` when `x` already exists: drop the type to assign instead."""
    return Suggestion("remove the type to assign to the existing variable",
                      [Edit(Span(decl_start, name_start), "")], MAYBE)
