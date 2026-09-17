"""
Module 2: Complete Lexer with Recovery.

Hand-written, maximal-munch scanner. On a lexical fault it emits a
Diagnostic and resynchronises rather than aborting, so downstream phases
still see a full (if partly error-tokened) stream.
"""

from typing import List, Tuple
from .span import Span, Diagnostic
from .tokens import Token, T, KEYWORDS

_SIMPLE = {
    "+": T.PLUS, "-": T.MINUS, "*": T.STAR, "/": T.SLASH,
    "=": T.EQUAL, ";": T.SEMI, "(": T.LPAREN, ")": T.RPAREN,
}


class Lexer:
    def __init__(self, source: str):
        self.src = source
        self.n = len(source)
        self.pos = 0
        self.tokens: List[Token] = []
        self.diagnostics: List[Diagnostic] = []

    def _peek(self, offset=0) -> str:
        i = self.pos + offset
        return self.src[i] if i < self.n else ""

    def _advance(self) -> str:
        ch = self.src[self.pos]
        self.pos += 1
        return ch

    def scan(self) -> Tuple[List[Token], List[Diagnostic]]:
        while self.pos < self.n:
            ch = self._peek()

            if ch in " \t\r\n":
                self.pos += 1
                continue

            if ch == "/" and self._peek(1) == "/":
                while self.pos < self.n and self._peek() != "\n":
                    self.pos += 1
                continue

            start = self.pos

            if ch.isalpha() or ch == "_":
                self._scan_ident_or_keyword(start)
                continue

            if ch.isdigit():
                self._scan_number(start)
                continue

            if ch == '"':
                self._scan_string(start)
                continue

            if ch in _SIMPLE:
                self._advance()
                self.tokens.append(Token(_SIMPLE[ch], ch, start, self.pos))
                continue

            # Unrecognised character: lexical recovery — report and skip it.
            self._advance()
            self.diagnostics.append(Diagnostic(
                code="E0001",
                message=f"invalid character '{ch}'",
                span=Span(start, self.pos),
                help="this character is not part of the Lumen alphabet; it was skipped",
            ))

        self.tokens.append(Token(T.EOF, "", self.pos, self.pos))
        return self.tokens, self.diagnostics

    def _scan_ident_or_keyword(self, start: int):
        while self._peek().isalnum() or self._peek() == "_":
            self.pos += 1
        text = self.src[start:self.pos]
        kind = KEYWORDS.get(text, T.IDENT)
        self.tokens.append(Token(kind, text, start, self.pos))

    def _scan_number(self, start: int):
        is_float = False
        while self._peek().isdigit():
            self.pos += 1
        if self._peek() == "." and self._peek(1).isdigit():
            is_float = True
            self.pos += 1
            while self._peek().isdigit():
                self.pos += 1
            # malformed: a second decimal point, e.g. 12.34.56
            if self._peek() == "." :
                bad_start = self.pos
                while self._peek().isdigit() or self._peek() == ".":
                    self.pos += 1
                text = self.src[start:self.pos]
                self.diagnostics.append(Diagnostic(
                    code="E0003",
                    message=f"malformed numeric literal '{text}'",
                    span=Span(start, self.pos),
                    help="a numeric literal may contain at most one decimal point",
                ))
                self.tokens.append(Token(T.ERROR, text, start, self.pos))
                return
        # malformed: digits immediately followed by letters, e.g. 12abc
        if self._peek().isalpha():
            while self._peek().isalnum() or self._peek() == "_":
                self.pos += 1
            text = self.src[start:self.pos]
            self.diagnostics.append(Diagnostic(
                code="E0003",
                message=f"malformed numeric literal '{text}'",
                span=Span(start, self.pos),
                help="identifiers may not begin with a digit",
            ))
            self.tokens.append(Token(T.ERROR, text, start, self.pos))
            return
        text = self.src[start:self.pos]
        self.tokens.append(Token(T.NUMBER, text, start, self.pos))

    def _scan_string(self, start: int):
        self.pos += 1  # consume opening quote
        while True:
            ch = self._peek()
            if ch == "":
                # Unterminated string: recover by synthetically closing it.
                text = self.src[start:self.pos]
                self.diagnostics.append(Diagnostic(
                    code="E0002",
                    message="unterminated string literal",
                    span=Span(start, self.pos),
                    help="a closing '\"' was inserted at end of file",
                ))
                self.tokens.append(Token(T.STRING, text, start, self.pos))
                return
            if ch == "\n":
                text = self.src[start:self.pos]
                self.diagnostics.append(Diagnostic(
                    code="E0002",
                    message="unterminated string literal",
                    span=Span(start, self.pos),
                    help="a closing '\"' was inserted at end of line",
                ))
                self.tokens.append(Token(T.STRING, text, start, self.pos))
                return
            if ch == '"':
                self.pos += 1
                text = self.src[start:self.pos]
                self.tokens.append(Token(T.STRING, text, start, self.pos))
                return
            self.pos += 1
