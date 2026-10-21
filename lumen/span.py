"""
Module 1: Source Manager & Span Model.

Maps byte offsets in the source file to (line, column) positions, and
defines the Span/Label/Edit/Suggestion/Diagnostic building blocks that every
phase (lexer, parser, checker, interpreter, renderer, fixer) shares.
"""

from dataclasses import dataclass, field
from typing import List, Optional

# Applicability classes for a repair suggestion (modelled on rustc's
# `Applicability`). Only MACHINE edits are applied by `--fix`; MAYBE edits
# need `--unsafe-fixes`, because they are a guess at the programmer's intent.
MACHINE = "machine-applicable"
MAYBE = "maybe-incorrect"
INFO = "info"        # advice only - there is no edit to apply


class SourceMap:
    """Maps a flat byte offset into 1-indexed (line, column)."""

    def __init__(self, source: str):
        self.source = source
        self.line_starts: List[int] = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                self.line_starts.append(i + 1)

    def offset_to_linecol(self, offset: int):
        offset = max(0, min(offset, len(self.source)))
        lo, hi = 0, len(self.line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        line = lo + 1
        col = offset - self.line_starts[lo] + 1
        return line, col

    def line_count(self) -> int:
        return len(self.line_starts)

    def line_text(self, line_number: int) -> str:
        line_number = max(1, min(line_number, len(self.line_starts)))
        start = self.line_starts[line_number - 1]
        end = (
            self.line_starts[line_number]
            if line_number < len(self.line_starts)
            else len(self.source)
        )
        return self.source[start:end].rstrip("\n\r")


@dataclass
class Span:
    start: int
    end: int

    def line_col(self, smap: SourceMap):
        return smap.offset_to_linecol(self.start)

    def contains(self, other: "Span") -> bool:
        return self.start <= other.start and other.end <= self.end


@dataclass
class Label:
    span: Span
    text: str


@dataclass
class Edit:
    """Replace source[span.start:span.end] with `replacement`.
    A zero-width span is a pure insertion; an empty replacement is a deletion."""
    span: Span
    replacement: str


@dataclass
class Suggestion:
    text: str
    edits: List[Edit] = field(default_factory=list)
    applicability: str = MAYBE

    @property
    def applicable(self) -> bool:
        return self.applicability == MACHINE and bool(self.edits)

    @property
    def replacement(self) -> Optional[str]:
        return self.edits[0].replacement if self.edits else None


@dataclass
class Diagnostic:
    code: str
    message: str
    span: Span
    labels: List[Label] = field(default_factory=list)
    help: Optional[str] = None
    note: Optional[str] = None
    suggestion: Optional[Suggestion] = None
    severity: str = "error"          # "error" | "warning"
    phase: str = "sema"              # "lex" | "parse" | "sema" | "runtime"

    def to_dict(self, smap: Optional[SourceMap] = None) -> dict:
        d = {
            "code": self.code,
            "severity": self.severity,
            "phase": self.phase,
            "message": self.message,
            "span": {"start": self.span.start, "end": self.span.end},
        }
        if smap is not None:
            line, col = self.span.line_col(smap)
            d["line"], d["column"] = line, col
        if self.help:
            d["help"] = self.help
        if self.note:
            d["note"] = self.note
        if self.labels:
            d["labels"] = [{"span": {"start": l.span.start, "end": l.span.end},
                            "text": l.text} for l in self.labels]
        if self.suggestion is not None:
            d["suggestion"] = {
                "text": self.suggestion.text,
                "applicability": self.suggestion.applicability,
                "edits": [{"start": e.span.start, "end": e.span.end,
                           "replacement": e.replacement}
                          for e in self.suggestion.edits],
            }
        return d
