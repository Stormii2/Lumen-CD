"""
Module 3: Parser Subset with Panic-Mode Recovery.

Recursive-descent parsing of variable declarations, assignments and
expressions (Pratt precedence). Two recovery strategies are demonstrated:

  * Phrase-level recovery — a single-token substitution (a mistyped type
    keyword) lets the parse continue, and is recorded as a suggestion.
  * Panic-mode recovery — on an unrecoverable statement, tokens are
    discarded up to the next ';' (the FOLLOW-derived synchronisation
    point for statements) and an ErrorStmt is installed so semantic
    analysis (Phase 2) could still walk the tree.
"""

from typing import List, Optional
from .span import Span, Diagnostic, Label, Suggestion
from .tokens import Token, T, TYPE_KEYWORDS
from .suggest import closest_keyword
from . import ast_nodes as ast

_TYPE_TOKENS = {T.INT, T.FLOAT, T.BOOL, T.STRING_KW}

# binding power for binary operators: (left, right)
_BIN_BP = {
    "+": (10, 11), "-": (10, 11),
    "*": (20, 21), "/": (20, 21),
}


class Parser:
    def __init__(self, tokens: List[Token], source: str):
        self.toks = tokens
        self.src = source
        self.i = 0
        self.diagnostics: List[Diagnostic] = []

    # ---- token helpers -------------------------------------------------
    def _cur(self) -> Token:
        return self.toks[self.i]

    def _at_end(self) -> bool:
        return self._cur().kind == T.EOF

    def _advance(self) -> Token:
        tok = self.toks[self.i]
        if not self._at_end():
            self.i += 1
        return tok

    def _check(self, kind: T) -> bool:
        return self._cur().kind == kind

    def _synchronize(self):
        """Panic-mode: discard tokens up to the statement boundary (';')."""
        while not self._at_end():
            if self._cur().kind == T.SEMI:
                self._advance()
                return
            self._advance()

    # ---- entry point -----------------------------------------------------
    def parse_program(self) -> List[ast.Node]:
        statements = []
        while not self._at_end():
            statements.append(self._statement())
        return statements

    # ---- statements --------------------------------------------------
    def _statement(self) -> ast.Node:
        start = self._cur().start

        # exact type keyword: `int x = expr;`
        if self._cur().kind in _TYPE_TOKENS:
            return self._var_decl()

        # possible mistyped type keyword, e.g. `itn x = 5;`
        if self._cur().kind == T.IDENT:
            candidate = closest_keyword(self._cur().text, TYPE_KEYWORDS, max_distance=2)
            if candidate and self._looks_like_decl_after_typo():
                return self._var_decl(typo_for=candidate)

        # assignment: `x = expr;`
        if self._cur().kind == T.IDENT:
            return self._assignment()

        # anything else at statement position is unrecoverable here
        bad = self._cur()
        self.diagnostics.append(Diagnostic(
            code="E0100",
            message=f"unexpected token '{bad.text or bad.kind.name}' at start of statement",
            span=Span(bad.start, max(bad.end, bad.start + 1)),
            help="expected a type keyword or an identifier",
        ))
        self._synchronize()
        return ast.ErrorStmt(Span(start, self._cur().start))

    def _looks_like_decl_after_typo(self) -> bool:
        """Peek ahead: TYPO IDENT '=' looks like a declaration."""
        return (
            self.i + 1 < len(self.toks)
            and self.toks[self.i + 1].kind == T.IDENT
        )

    def _var_decl(self, typo_for: Optional[str] = None) -> ast.Node:
        type_tok = self._advance()

        if typo_for is not None:
            self.diagnostics.append(Diagnostic(
                code="E0410",
                message=f"unknown type '{type_tok.text}'",
                span=Span(type_tok.start, type_tok.end),
                help=f"did you mean '{typo_for}'?",
                suggestion=Suggestion(
                    text=f"did you mean '{typo_for}'?",
                    replacement=typo_for,
                    applicable=True,
                ),
            ))
            type_name = typo_for
        else:
            type_name = type_tok.text

        if not self._check(T.IDENT):
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0101",
                message="expected an identifier after type keyword",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))
            self._synchronize()
            return ast.ErrorStmt(Span(type_tok.start, self._cur().start))

        name_tok = self._advance()

        if not self._check(T.EQUAL):
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0102",
                message="expected '=' in variable declaration",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
                labels=[Label(Span(name_tok.start, name_tok.end), "declared here")],
            ))
            self._synchronize()
            return ast.ErrorStmt(Span(type_tok.start, self._cur().start))

        self._advance()  # consume '='
        value = self._expression()
        self._expect_semi(name_tok)
        return ast.VarDecl(Span(type_tok.start, self._prev_end()), type_name, name_tok.text, value)

    def _assignment(self) -> ast.Node:
        name_tok = self._advance()
        if not self._check(T.EQUAL):
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0103",
                message="expected '=' after identifier in assignment",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))
            self._synchronize()
            return ast.ErrorStmt(Span(name_tok.start, self._cur().start))
        self._advance()  # consume '='
        value = self._expression()
        self._expect_semi(name_tok)
        return ast.Assignment(Span(name_tok.start, self._prev_end()), name_tok.text, value)

    def _expect_semi(self, anchor_tok: Token):
        """Phrase-level recovery: a missing ';' doesn't stop the parse —
        report it and carry on as if it were there."""
        if self._check(T.SEMI):
            self._advance()
            return
        prev = self.toks[self.i - 1] if self.i > 0 else anchor_tok
        self.diagnostics.append(Diagnostic(
            code="E0201",
            message="expected ';' after statement",
            span=Span(prev.end, prev.end + 1),
            help="a ';' was assumed here so parsing could continue",
        ))

    def _prev_end(self) -> int:
        return self.toks[self.i - 1].end if self.i > 0 else 0

    # ---- expressions (Pratt) ------------------------------------------
    def _expression(self, min_bp: int = 0) -> ast.Node:
        left = self._primary()
        while True:
            tok = self._cur()
            op = tok.text if tok.kind in (T.PLUS, T.MINUS, T.STAR, T.SLASH) else None
            if op is None or op not in _BIN_BP:
                break
            l_bp, r_bp = _BIN_BP[op]
            if l_bp < min_bp:
                break
            self._advance()
            right = self._expression(r_bp)
            left = ast.BinOp(Span(left.span.start, right.span.end), op, left, right)
        return left

    def _primary(self) -> ast.Node:
        tok = self._cur()
        if tok.kind == T.NUMBER:
            self._advance()
            return ast.Literal(Span(tok.start, tok.end), tok.text, "number")
        if tok.kind == T.STRING:
            self._advance()
            return ast.Literal(Span(tok.start, tok.end), tok.text, "string")
        if tok.kind in (T.TRUE, T.FALSE):
            self._advance()
            return ast.Literal(Span(tok.start, tok.end), tok.text, "bool")
        if tok.kind == T.IDENT:
            self._advance()
            return ast.Ident(Span(tok.start, tok.end), tok.text)
        if tok.kind == T.LPAREN:
            self._advance()
            inner = self._expression()
            if self._check(T.RPAREN):
                self._advance()
            else:
                bad = self._cur()
                self.diagnostics.append(Diagnostic(
                    code="E0202",
                    message="expected ')' to close expression",
                    span=Span(bad.start, max(bad.end, bad.start + 1)),
                ))
            return inner

        # No valid primary expression here.
        bad = self._cur()
        self.diagnostics.append(Diagnostic(
            code="E0203",
            message=f"expected an expression, found '{bad.text or bad.kind.name}'",
            span=Span(bad.start, max(bad.end, bad.start + 1)),
        ))
        if not self._at_end() and bad.kind != T.SEMI:
            self._advance()
        return ast.ErrorExpr(Span(bad.start, bad.end))
