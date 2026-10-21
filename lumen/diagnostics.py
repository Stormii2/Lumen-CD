"""
Diagnostic post-processing: ordering, de-duplication and span-containment
suppression.

Even with token poisoning in the parser and ERROR-type poisoning in the
checker, two phases can still describe the same defect. This final layer
removes what is left:

  1. exact duplicates (same code, same span);
  2. a later-phase diagnostic whose span lies strictly inside the span of an
     earlier-phase *error* (lex < parse < sema) - the inner message is a
     consequence of the outer one, not an independent defect.

Warnings are never used to suppress anything, and never suppressed by
containment (they describe valid-but-suspicious code).
"""

from typing import List
from .config import RecoveryConfig, DEFAULT

_PHASE_RANK = {"lex": 0, "parse": 1, "sema": 2, "runtime": 3}


def finalize(diags: List, config: RecoveryConfig = DEFAULT) -> List:
    diags = sorted(diags, key=lambda d: (d.span.start, d.span.end, d.code))
    if config.suppress:
        seen = set()
        unique = []
        for d in diags:
            key = (d.code, d.span.start, d.span.end)
            if key in seen:
                continue
            seen.add(key)
            unique.append(d)
        diags = unique

        errors = [d for d in diags if d.severity == "error"]
        kept = []
        for d in diags:
            if d.severity == "error":
                rank = _PHASE_RANK.get(d.phase, 2)
                swallowed = any(
                    _PHASE_RANK.get(e.phase, 2) < rank
                    and e.span.end > e.span.start
                    and e.span.contains(d.span)
                    and (e.span.start, e.span.end) != (d.span.start, d.span.end)
                    for e in errors)
                if swallowed:
                    continue
            kept.append(d)
        diags = kept
    if config.fail_fast:
        errs = [d for d in diags if d.severity == "error"]
        diags = errs[:1] if errs else diags
    return diags


def count(diags: List):
    errors = sum(1 for d in diags if d.severity == "error")
    return errors, len(diags) - errors
