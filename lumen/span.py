"""
Module 1: Source Manager & Span Model.

Maps byte offsets in the source file to (line, column) positions, and
defines the Span/Label/Suggestion/Diagnostic building blocks that every
later phase (lexer, parser, renderer) emits.
"""

from dataclasses import dataclass, field
from typing import List, Optional


class SourceMap:
    """Maps a flat byte offset into 1-indexed (line, column)."""

    def __init__(self, source: str):
        self.source = source
        # line_starts[i] = offset of the first character of line i+1
        self.line_starts: List[int] = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                self.line_starts.append(i + 1)

    def offset_to_linecol(self, offset: int):
        # binary search for the line containing `offset`
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

    def line_text(self, line_number: int) -> str:
        """Return the raw text of a 1-indexed line, without the newline."""
        start = self.line_starts[line_number - 1]
        end = (
            self.line_starts[line_number]
            if line_number < len(self.line_starts)
            else len(self.source)
        )
        return self.source[start:end].rstrip("\n\r")


@dataclass
class Span:
    start: int  # byte offset, inclusive
    end: int    # byte offset, exclusive

    def line_col(self, smap: SourceMap):
        return smap.offset_to_linecol(self.start)


@dataclass
class Label:
    span: Span
    text: str


@dataclass
class Suggestion:
    text: str          # e.g. "did you mean `total`?"
    replacement: Optional[str] = None
    applicable: bool = False   # True => usable by --fix


@dataclass
class Diagnostic:
    code: str            # e.g. "E0201"
    message: str
    span: Span
    labels: List[Label] = field(default_factory=list)
    help: Optional[str] = None
    note: Optional[str] = None
    suggestion: Optional[Suggestion] = None
    severity: str = "error"
