"""
Recovery configuration.

Every error-handling technique Lumen uses can be switched off independently.
That is what makes the evaluation harness (eval/run_eval.py) an honest
ablation study: the "naive" baseline is simply this same compiler with all
of the techniques below disabled, so any difference in the results is
attributable to those techniques and not to unrelated code differences.
"""

from dataclasses import dataclass, replace

# How the type checker treats the ERROR type.
#   "expr"  - ERROR is the lattice bottom for EXPRESSIONS; a variable keeps its
#             declared type even when its initialiser failed (the default: the
#             evaluation showed "full" gives no measurable benefit, and it hides
#             independent errors that depend on the variable's declared type).
#   "full"  - additionally poisons the variable itself, so every later use of
#             it is silent (the original policy).
#   "none"  - ERROR is just another type name; nothing is poisoned, so every
#             use of a failed expression raises a fresh diagnostic.
POISON_MODES = ("expr", "full", "none")

# How panic-mode recovery chooses where to resume.
#   "first-follow" - stop at ';' / '}' OR at any token in FIRST(statement)
#                    (the synchronisation set derived in grammar.py)
#   "semicolon"    - classic: discard everything up to the next ';' or '}'
SYNC_MODES = ("first-follow", "semicolon")


@dataclass(frozen=True)
class RecoveryConfig:
    phrase_level: bool = True      # single-token repairs (assume a missing ';')
    sync: str = "first-follow"     # panic-mode resynchronisation strategy
    poison: str = "expr"           # type-lattice poisoning mode
    token_poison: bool = True      # diagnostics anchored on a lexical ERROR token are suppressed
    error_gap: int = 3             # yacc-style: drop a parse error reported fewer than N tokens after the last one (0 = off)
    mute_damaged: bool = True      # no semantic diagnostics inside a statement the parser already flagged
    infer_lost_decl: bool = True   # a declaration whose name was lost is bound to the first unknown name used later
    suppress: bool = True          # dedupe + span-containment filter (diagnostics.py)
    fail_fast: bool = False        # report only the first diagnostic, like a classic compiler

    def __post_init__(self):
        if self.poison not in POISON_MODES:
            raise ValueError(f"poison must be one of {POISON_MODES}")
        if self.sync not in SYNC_MODES:
            raise ValueError(f"sync must be one of {SYNC_MODES}")

    def with_(self, **changes) -> "RecoveryConfig":
        return replace(self, **changes)


DEFAULT = RecoveryConfig()

# Named configurations used by the evaluation harness.
NAIVE = RecoveryConfig(phrase_level=False, sync="semicolon", poison="none",
                       token_poison=False, error_gap=0, mute_damaged=False, infer_lost_decl=False,
                       suppress=False)
FAIL_FAST = RecoveryConfig(fail_fast=True)
