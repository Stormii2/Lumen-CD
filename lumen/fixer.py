"""
Auto-fix engine (`lumenc --fix`).

Applies the edits attached to diagnostics. By default only MACHINE-applicable
suggestions are applied (the repair is the unique sensible reading); with
`unsafe=True`, MAYBE-INCORRECT ones are applied too. Edits are applied
back-to-front, and any edit that overlaps one already chosen is skipped, so a
single pass can never corrupt the text. `fix_to_fixpoint` re-compiles after
each pass because one repair can unmask the next defect.
"""

from typing import List, Tuple
from .span import MACHINE, MAYBE, Diagnostic
from .config import RecoveryConfig


def collect_edits(diags: List[Diagnostic], unsafe: bool = False):
    chosen = []
    for d in diags:
        s = d.suggestion
        if s is None or not s.edits:
            continue
        if s.applicability == MACHINE or (unsafe and s.applicability == MAYBE):
            chosen.append((d, s))
    return chosen


def apply_suggestions(source: str, diags: List[Diagnostic], unsafe: bool = False
                      ) -> Tuple[str, List[Diagnostic]]:
    """One pass. Returns (new_source, diagnostics_whose_fix_was_applied)."""
    taken: List[Tuple[int, int]] = []
    edits = []
    applied = []
    for d, s in collect_edits(diags, unsafe):
        spans = [(e.span.start, e.span.end) for e in s.edits]
        if any(a < tb and ta < b or (a == b == ta == tb)
               for a, b in spans for ta, tb in taken):
            continue
        # a suggestion's edits are applied all-or-nothing
        taken.extend(spans)
        edits.extend(s.edits)
        applied.append(d)
    out = source
    for e in sorted(edits, key=lambda e: (e.span.start, e.span.end), reverse=True):
        out = out[:e.span.start] + e.replacement + out[e.span.end:]
    return out, applied


def fix_to_fixpoint(source: str, config: RecoveryConfig = None, unsafe: bool = False,
                    max_passes: int = 5):
    """Repeat compile -> apply until nothing more can be applied.
    Returns (fixed_source, total_fixes_applied, final CompileResult)."""
    from .driver import compile_source
    total = 0
    res = compile_source(source, config, want_tac=False)
    for _ in range(max_passes):
        new, applied = apply_suggestions(source, res.diagnostics, unsafe)
        if not applied or new == source:
            break
        total += len(applied)
        source = new
        res = compile_source(source, config, want_tac=False)
    return source, total, res
