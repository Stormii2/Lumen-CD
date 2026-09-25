"""
Module 3 (Phase 2 extension): Full Parser with Panic-Mode + Phrase-Level Recovery.

Recursive-descent parsing of the complete Phase-2 language subset:
function declarations, blocks, if/else, while, return, arrays, indexing,
calls, and Pratt-precedence expressions (arithmetic, comparison, logical).
Two recovery strategies carry over unchanged from Phase 1:

  * Phrase-level recovery — a single-token substitution (a mistyped type
    keyword, a missing ';') lets the parse continue.
  * Panic-mode recovery — on an unrecoverable statement, tokens are
    discarded up to the next ';' or '}' and an ErrorStmt is installed.
"""

from typing import List, Optional
from .span import Span, Diagnostic, Label, Suggestion
from .tokens import Token, T, TYPE_KEYWORDS
from .suggest import closest_keyword
from . import ast_nodes as ast

_TYPE_TOKENS = {T.INT, T.FLOAT, T.BOOL, T.STRING_KW, T.VOID}

# binding power for binary operators: (left, right) — higher binds tighter
_BIN_BP = {
    "||": (1, 2),
    "&&": (3, 4),
    "==": (5, 6), "!=": (5, 6),
    "<": (7, 8), ">": (7, 8), "<=": (7, 8), ">=": (7, 8),
    "+": (10, 11), "-": (10, 11),
    "*": (20, 21), "/": (20, 21), "%": (20, 21),
}
_TOKEN_TO_OP = {
    T.OR: "||", T.AND: "&&", T.EQEQ: "==", T.NEQ: "!=",
    T.LT: "<", T.GT: ">", T.LE: "<=", T.GE: ">=",
    T.PLUS: "+", T.MINUS: "-", T.STAR: "*", T.SLASH: "/", T.PERCENT: "%",
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
        """Panic-mode: discard tokens up to ';' or '}' (statement/block boundary)."""
        while not self._at_end():
            if self._cur().kind == T.SEMI:
                self._advance()
                return
            if self._cur().kind == T.RBRACE:
                return  # leave '}' for the enclosing block to consume
            self._advance()

    # ---- entry point -----------------------------------------------------
    def parse_program(self) -> List[ast.Node]:
        items = []
        while not self._at_end():
            if self._check(T.FUNC):
                items.append(self._func_decl())
            else:
                items.append(self._statement())
        return items

    # ---- function declarations ----------------------------------------
    def _func_decl(self) -> ast.Node:
        start = self._cur().start
        self._advance()  # 'func'

        if not self._cur().kind in _TYPE_TOKENS:
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0110",
                message="expected a return type after 'func'",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))
            self._synchronize()
            return ast.ErrorStmt(Span(start, self._cur().start))
        return_type = self._advance().text

        if not self._check(T.IDENT):
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0111",
                message="expected a function name",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))
            self._synchronize()
            return ast.ErrorStmt(Span(start, self._cur().start))
        name_tok = self._advance()

        params: List[ast.Param] = []
        if self._check(T.LPAREN):
            self._advance()
            if not self._check(T.RPAREN):
                params.append(self._param())
                while self._check(T.COMMA):
                    self._advance()
                    params.append(self._param())
            if self._check(T.RPAREN):
                self._advance()
            else:
                bad = self._cur()
                self.diagnostics.append(Diagnostic(
                    code="E0112",
                    message="expected ')' after parameter list",
                    span=Span(bad.start, max(bad.end, bad.start + 1)),
                ))
        else:
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0113",
                message="expected '(' after function name",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))

        body = self._block()
        return ast.FuncDecl(Span(start, self._prev_end()), name_tok.text, return_type, params, body)

    def _param(self) -> ast.Param:
        start = self._cur().start
        if self._cur().kind in _TYPE_TOKENS:
            type_name = self._advance().text
        else:
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0114",
                message="expected a parameter type",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))
            type_name = "Error"
        if self._check(T.LBRACKET):
            self._advance()
            if self._check(T.RBRACKET):
                self._advance()
            type_name += "[]"
        name = self._advance().text if self._check(T.IDENT) else "?"
        return ast.Param(Span(start, self._prev_end()), type_name, name)

    # ---- blocks & statements --------------------------------------------
    def _block(self) -> ast.Block:
        start = self._cur().start
        if not self._check(T.LBRACE):
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0120",
                message="expected '{' to start a block",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))
            return ast.Block(Span(start, start), [])
        self._advance()
        stmts = []
        while not self._check(T.RBRACE) and not self._at_end():
            stmts.append(self._statement())
        if self._check(T.RBRACE):
            self._advance()
        else:
            self.diagnostics.append(Diagnostic(
                code="E0121",
                message="expected '}' to close block",
                span=Span(self._cur().start, self._cur().start + 1),
            ))
        return ast.Block(Span(start, self._prev_end()), stmts)

    def _statement(self) -> ast.Node:
        start = self._cur().start

        if self._check(T.LBRACE):
            return self._block()
        if self._check(T.IF):
            return self._if_stmt()
        if self._check(T.WHILE):
            return self._while_stmt()
        if self._check(T.RETURN):
            return self._return_stmt()

        if self._cur().kind in _TYPE_TOKENS:
            return self._var_decl()

        if self._cur().kind == T.IDENT:
            candidate = closest_keyword(self._cur().text, TYPE_KEYWORDS, max_distance=2)
            if candidate and self._looks_like_decl_after_typo():
                return self._var_decl(typo_for=candidate)
            return self._ident_led_statement()

        bad = self._cur()
        self.diagnostics.append(Diagnostic(
            code="E0100",
            message=f"unexpected token '{bad.text or bad.kind.name}' at start of statement",
            span=Span(bad.start, max(bad.end, bad.start + 1)),
            help="expected a type keyword, an identifier, 'if', 'while', 'return', or '{'",
        ))
        self._synchronize()
        return ast.ErrorStmt(Span(start, self._cur().start))

    def _looks_like_decl_after_typo(self) -> bool:
        return (
            self.i + 1 < len(self.toks)
            and self.toks[self.i + 1].kind == T.IDENT
        )

    def _if_stmt(self) -> ast.Node:
        start = self._advance().start  # 'if'
        cond = self._paren_expr()
        then_b = self._block()
        else_b = None
        if self._check(T.ELSE):
            self._advance()
            else_b = self._block() if self._check(T.LBRACE) else ast.Block(
                Span(self._cur().start, self._cur().start), [self._statement()])
        return ast.If(Span(start, self._prev_end()), cond, then_b, else_b)

    def _while_stmt(self) -> ast.Node:
        start = self._advance().start  # 'while'
        cond = self._paren_expr()
        body = self._block()
        return ast.While(Span(start, self._prev_end()), cond, body)

    def _return_stmt(self) -> ast.Node:
        start = self._advance().start  # 'return'
        value = None
        if not self._check(T.SEMI):
            value = self._expression()
        self._expect_semi(self.toks[self.i - 1])
        return ast.Return(Span(start, self._prev_end()), value)

    def _paren_expr(self) -> ast.Node:
        if self._check(T.LPAREN):
            self._advance()
        else:
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0122",
                message="expected '(' after condition keyword",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))
        expr = self._expression()
        if self._check(T.RPAREN):
            self._advance()
        else:
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0202",
                message="expected ')' to close condition",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))
        return expr

    def _var_decl(self, typo_for: Optional[str] = None) -> ast.Node:
        type_tok = self._advance()

        if typo_for is not None:
            self.diagnostics.append(Diagnostic(
                code="E0410",
                message=f"unknown type '{type_tok.text}'",
                span=Span(type_tok.start, type_tok.end),
                help=f"did you mean '{typo_for}'?",
                suggestion=Suggestion(text=f"did you mean '{typo_for}'?",
                                       replacement=typo_for, applicable=True),
            ))
            type_name = typo_for
        else:
            type_name = type_tok.text

        if self._check(T.LBRACKET):
            self._advance()
            if self._check(T.RBRACKET):
                self._advance()
            type_name += "[]"

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

        self._advance()
        value = self._expression()
        self._expect_semi(name_tok)
        return ast.VarDecl(Span(type_tok.start, self._prev_end()), type_name, name_tok.text, value,
                            name_span=Span(name_tok.start, name_tok.end))

    def _ident_led_statement(self) -> ast.Node:
        name_tok = self._advance()

        # arr[i] = expr;
        if self._check(T.LBRACKET):
            self._advance()
            idx = self._expression()
            if self._check(T.RBRACKET):
                self._advance()
            else:
                bad = self._cur()
                self.diagnostics.append(Diagnostic(
                    code="E0123", message="expected ']' after index",
                    span=Span(bad.start, max(bad.end, bad.start + 1))))
            if not self._check(T.EQUAL):
                bad = self._cur()
                self.diagnostics.append(Diagnostic(
                    code="E0103", message="expected '=' after indexed target",
                    span=Span(bad.start, max(bad.end, bad.start + 1))))
                self._synchronize()
                return ast.ErrorStmt(Span(name_tok.start, self._cur().start))
            self._advance()
            value = self._expression()
            self._expect_semi(name_tok)
            return ast.Assignment(Span(name_tok.start, self._prev_end()), name_tok.text, value, idx)

        # foo(args);  — call statement
        if self._check(T.LPAREN):
            call = self._finish_call(name_tok)
            self._expect_semi(name_tok)
            return ast.ExprStmt(Span(name_tok.start, self._prev_end()), call)

        # x = expr;
        if not self._check(T.EQUAL):
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0103",
                message="expected '=' after identifier in assignment",
                span=Span(bad.start, max(bad.end, bad.start + 1)),
            ))
            self._synchronize()
            return ast.ErrorStmt(Span(name_tok.start, self._cur().start))
        self._advance()
        value = self._expression()
        self._expect_semi(name_tok)
        return ast.Assignment(Span(name_tok.start, self._prev_end()), name_tok.text, value)

    def _expect_semi(self, anchor_tok: Token):
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
        left = self._unary()
        while True:
            tok = self._cur()
            op = _TOKEN_TO_OP.get(tok.kind)
            if op is None or op not in _BIN_BP:
                break
            l_bp, r_bp = _BIN_BP[op]
            if l_bp < min_bp:
                break
            self._advance()
            right = self._expression(r_bp)
            left = ast.BinOp(Span(left.span.start, right.span.end), op, left, right)
        return left

    def _unary(self) -> ast.Node:
        tok = self._cur()
        if tok.kind in (T.MINUS, T.NOT):
            self._advance()
            operand = self._unary()
            return ast.UnaryOp(Span(tok.start, operand.span.end), tok.text, operand)
        return self._postfix(self._primary())

    def _postfix(self, expr: ast.Node) -> ast.Node:
        while True:
            if self._check(T.LBRACKET):
                self._advance()
                idx = self._expression()
                end = self._cur().end
                if self._check(T.RBRACKET):
                    self._advance()
                expr = ast.Index(Span(expr.span.start, end), expr, idx)
                continue
            break
        return expr

    def _finish_call(self, name_tok: Token) -> ast.Node:
        self._advance()  # '('
        args = []
        if not self._check(T.RPAREN):
            args.append(self._expression())
            while self._check(T.COMMA):
                self._advance()
                args.append(self._expression())
        end = self._cur().end
        if self._check(T.RPAREN):
            self._advance()
        else:
            bad = self._cur()
            self.diagnostics.append(Diagnostic(
                code="E0202", message="expected ')' to close argument list",
                span=Span(bad.start, max(bad.end, bad.start + 1))))
        return ast.Call(Span(name_tok.start, end), name_tok.text, args)

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
            if self._check(T.LPAREN):
                return self._finish_call(tok)
            return ast.Ident(Span(tok.start, tok.end), tok.text)
        if tok.kind == T.LBRACKET:
            self._advance()
            elements = []
            if not self._check(T.RBRACKET):
                elements.append(self._expression())
                while self._check(T.COMMA):
                    self._advance()
                    elements.append(self._expression())
            end = self._cur().end
            if self._check(T.RBRACKET):
                self._advance()
            return ast.ArrayLiteral(Span(tok.start, end), elements)
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

        bad = self._cur()
        self.diagnostics.append(Diagnostic(
            code="E0203",
            message=f"expected an expression, found '{bad.text or bad.kind.name}'",
            span=Span(bad.start, max(bad.end, bad.start + 1)),
        ))
        if not self._at_end() and bad.kind != T.SEMI:
            self._advance()
        return ast.ErrorExpr(Span(bad.start, bad.end))
