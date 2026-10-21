"""
Module 3: Parser with layered error recovery.

Recursive descent over the grammar in grammar.py, with Pratt (precedence
climbing) for expressions. Error recovery is layered, cheapest first:

  1. Token poisoning   - a lexical ERROR token is the lexical analogue of the
                         ERROR type: any diagnostic anchored on it is
                         suppressed, because the lexer already reported it.
  2. Phrase-level      - a single-token repair lets the parse continue
                         (assume a missing ';' or '=' or ')', fix a mistyped
                         keyword) and is recorded as a repair suggestion.
  3. Panic mode        - on an unrecoverable statement, discard tokens up to
                         the synchronisation set derived from FIRST/FOLLOW
                         (grammar.SYNC_SET) and install an ErrorStmt.

Every loop that consumes tokens has a progress guarantee, and nesting depth
is bounded, so no input - however malformed - can hang or crash the parser.
"""

from typing import List, Optional
from .span import (Span, Diagnostic, Label, Suggestion, Edit, MACHINE, MAYBE)
from .tokens import Token, T, TYPE_KEYWORDS, KEYWORDS, CONTROL_KEYWORDS
from .suggest import rank_candidates, unique_best
from .config import RecoveryConfig, DEFAULT
from .grammar import SYNC_SET
from . import ast_nodes as ast

_TYPE_TOKENS = {T.INT, T.FLOAT, T.BOOL, T.STRING_KW, T.VOID}

# Token kinds at which panic mode may resume (derived from the grammar).
_SYNC_KINDS = {T[name] for name in SYNC_SET}
_STARTER_KINDS = _SYNC_KINDS - {T.SEMI, T.RBRACE}
_EXPR_START_NO_IDENT = {T.NUMBER, T.STRING, T.TRUE, T.FALSE, T.LPAREN,
                        T.LBRACKET, T.MINUS, T.NOT}

MAX_NESTING = 120        # recursion bound for nested statements/expressions
MAX_AST_DEPTH = 800      # bound on expression-tree height (e.g. 1+1+1+...)

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

_DEFAULT_VALUE = {"int": "0", "float": "0.0", "bool": "false", "string": '""'}


class _TooComplex(Exception):
    """Raised (and caught inside the parser) when nesting exceeds the limits."""


class Parser:
    def __init__(self, tokens: List[Token], source: str,
                 config: Optional[RecoveryConfig] = None):
        self.cfg = config or DEFAULT
        if self.cfg.token_poison:
            tokens = self._skip_error_tokens(tokens)
        self.toks = tokens
        self.src = source
        self.i = 0
        self.diagnostics: List[Diagnostic] = []
        self._nest = 0
        self._block_depth = 0
        self._complex_reported = False
        self._poisoned_at = -1     # token index where an expression error left the cursor
        self._last_err_i = -10_000  # token index at the last *reported* parse error
        self._n_reports = 0         # parse faults seen so far (even if silenced)
        self._brace_excess = (sum(t.kind == T.RBRACE for t in tokens)
                              - sum(t.kind == T.LBRACE for t in tokens))

    @staticmethod
    def _skip_error_tokens(tokens: List[Token]) -> List[Token]:
        """Token poisoning, parser side (malformed literals stay in the stream
        as operand placeholders): a character the lexer already
        rejected is removed from the stream (exactly what its suggested fix
        does), and its neighbours are marked damaged so a diagnostic anchored
        on them - fallout of the same fault - is suppressed."""
        out: List[Token] = []
        pending_damage = False
        for t in tokens:
            if t.kind == T.ERROR and len(t.text) == 1 and not t.text.isalnum():
                if out:
                    out[-1].damaged = True
                pending_damage = True
                continue
            if pending_damage:
                t.damaged = True
                pending_damage = False
            out.append(t)
        return out

    # ---- token helpers -------------------------------------------------
    def _cur(self) -> Token:
        return self.toks[self.i]

    def _peek(self, n: int = 1) -> Token:
        j = min(self.i + n, len(self.toks) - 1)
        return self.toks[j]

    def _at_end(self) -> bool:
        return self._cur().kind == T.EOF

    def _advance(self) -> Token:
        tok = self.toks[self.i]
        if not self._at_end():
            self.i += 1
        return tok

    def _check(self, kind: T) -> bool:
        return self._cur().kind == kind

    def _prev_end(self) -> int:
        return self.toks[self.i - 1].end if self.i > 0 else 0

    @staticmethod
    def _span_of(tok: Token) -> Span:
        return Span(tok.start, max(tok.end, tok.start + 1))

    def _report(self, diag: Diagnostic, anchor: Optional[Token] = None, benign: bool = False):
        """Record a parse diagnostic - unless it is anchored on a token the
        lexer already reported as damaged (token poisoning)."""
        diag.phase = "parse"
        if not benign:
            self._n_reports += 1
        if (self.cfg.token_poison and anchor is not None
                and (anchor.kind == T.ERROR or anchor.damaged)):
            return
        gap = self.cfg.error_gap
        if gap and 0 <= self.i - self._last_err_i < gap:
            return          # still inside the region the last error left behind
        self._last_err_i = self.i
        self.diagnostics.append(diag)

    # ---- panic mode ---------------------------------------------------------
    def _synchronize(self):
        """Discard tokens until a safe resume point. Consumes a ';' (end of
        the broken statement); stops *before* a '}' or - in first-follow mode -
        before any token that can only begin a statement, so the next
        well-formed statement is not swallowed by the recovery."""
        use_first = self.cfg.sync == "first-follow"
        while not self._at_end():
            k = self._cur().kind
            if k == T.SEMI:
                self._advance()
                return
            if k == T.RBRACE:
                return
            if use_first and k in _STARTER_KINDS:
                return
            self._advance()

    def _enter(self):
        self._nest += 1
        if self._nest > MAX_NESTING:
            self._nest -= 1          # keep enter/leave balanced when we bail out
            raise _TooComplex()

    def _leave(self):
        self._nest -= 1

    def _report_too_complex(self):
        if self._complex_reported:
            return
        self._complex_reported = True
        tok = self._cur()
        self._report(Diagnostic(
            code="E0204",
            message="expression or block is nested too deeply",
            span=self._span_of(tok),
            help=f"Lumen limits nesting to {MAX_NESTING} levels and expression height "
                 f"to {MAX_AST_DEPTH}; split the code into smaller statements",
        ))

    # ---- entry point -----------------------------------------------------
    def parse_program(self) -> List[ast.Node]:
        items = []
        while not self._at_end():
            i0 = self.i
            self._fix_keyword_typo()
            if self._check(T.FUNC):
                items.append(self._func_decl())
            else:
                items.append(self._statement())
            if self.i == i0 and not self._at_end():
                self._advance()      # progress guarantee
        return items

    # ---- phrase-level repair of mistyped keywords ---------------------------
    def _fix_keyword_typo(self):
        """`whlie (x < 3) {`, `retrun x;`, `fucn int f()` : the identifier is a
        near-miss of a control keyword AND the surrounding tokens can only be
        that construct. Substitute the keyword token and continue."""
        if not self.cfg.phrase_level:
            return
        tok = self._cur()
        if tok.kind != T.IDENT or tok.text in KEYWORDS:
            return
        ranked = rank_candidates(tok.text, CONTROL_KEYWORDS)
        if not ranked or not unique_best(ranked):
            return
        cand = ranked[0][0]
        nxt = self._peek(1)
        if cand in ("if", "while"):
            ok = nxt.kind == T.LPAREN and self._header_follows(self.i + 1)
        elif cand == "return":
            ok = nxt.kind in (T.IDENT, T.NUMBER, T.STRING, T.TRUE, T.FALSE,
                              T.MINUS, T.NOT, T.SEMI)
        elif cand == "func":
            ok = nxt.kind in _TYPE_TOKENS
        else:
            ok = False
        if not ok:
            return
        span = Span(tok.start, tok.end)
        self._report(Diagnostic(
            code="E0411",
            message=f"unknown keyword '{tok.text}'",
            span=span,
            help=f"did you mean '{cand}'?",
            suggestion=Suggestion(f"replace '{tok.text}' with '{cand}'",
                                  [Edit(span, cand)], MACHINE),
        ))
        self.toks[self.i] = Token(KEYWORDS[cand], cand, tok.start, tok.end)

    def _header_follows(self, idx: int) -> bool:
        """toks[idx] is '('. True if this parenthesised group is followed by
        '{' (an if/while header) - a call statement would end in ';' first."""
        depth = 0
        j = idx
        while j < len(self.toks):
            k = self.toks[j].kind
            if k == T.LPAREN:
                depth += 1
            elif k == T.RPAREN:
                depth -= 1
                if depth == 0:
                    return j + 1 < len(self.toks) and self.toks[j + 1].kind == T.LBRACE
            elif k == T.LBRACE:
                return True
            elif k in (T.SEMI, T.RBRACE, T.EOF):
                return False
            j += 1
        return False

    # ---- function declarations ----------------------------------------
    def _func_decl(self) -> ast.Node:
        start = self._cur().start
        self._advance()  # 'func'

        if self._cur().kind in _TYPE_TOKENS:
            return_type = self._advance().text
        elif (self._check(T.IDENT) and self._peek(1).kind == T.IDENT
              and self._peek(2).kind == T.LPAREN and self.cfg.phrase_level):
            tok = self._advance()
            return_type = self._report_bad_type(tok, rank_candidates(tok.text, sorted(TYPE_KEYWORDS)))
        elif (self._check(T.IDENT) and self._peek(1).kind == T.LPAREN
              and self.cfg.phrase_level):
            # `func add(int a) {...}` - the return type is missing. Carry on
            # with the poisoned type so the body is not blamed for it.
            bad = self._cur()
            self._report(Diagnostic(
                code="E0110",
                message="expected a return type after 'func'",
                span=self._span_of(bad),
                help="write the return type before the name, e.g. 'func int " + bad.text + "(...)'",
            ), bad)
            return_type = "Error"
        else:
            bad = self._cur()
            self._report(Diagnostic(
                code="E0110",
                message="expected a return type after 'func'",
                span=self._span_of(bad),
            ), bad)
            self._synchronize()
            return ast.ErrorStmt(Span(start, self._cur().start))

        if not self._check(T.IDENT):
            bad = self._cur()
            self._report(Diagnostic(
                code="E0111",
                message="expected a function name",
                span=self._span_of(bad),
            ), bad)
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
                self._report(Diagnostic(
                    code="E0112",
                    message="expected ')' after parameter list",
                    span=self._span_of(bad),
                ), bad)
        else:
            bad = self._cur()
            self._report(Diagnostic(
                code="E0113",
                message="expected '(' after function name",
                span=self._span_of(bad),
            ), bad)

        body = self._block()
        return ast.FuncDecl(Span(start, self._prev_end()), name_tok.text, return_type, params, body,
                            name_span=Span(name_tok.start, name_tok.end))

    def _param(self) -> ast.Param:
        start = self._cur().start
        if self._cur().kind in _TYPE_TOKENS:
            type_name = self._advance().text
        elif (self._check(T.IDENT) and self.cfg.phrase_level
              and (self._peek(1).kind == T.IDENT
                   or (self._peek(1).kind == T.LBRACKET and self._peek(2).kind in (T.RBRACKET, T.IDENT)))):
            tok = self._advance()
            type_name = self._report_bad_type(tok, rank_candidates(tok.text, sorted(TYPE_KEYWORDS)))
        else:
            bad = self._cur()
            self._report(Diagnostic(
                code="E0114",
                message="expected a parameter type",
                span=self._span_of(bad),
            ), bad)
            type_name = "Error"
        type_name = self._array_suffix(type_name)
        name = self._advance().text if self._check(T.IDENT) else "?"
        return ast.Param(Span(start, self._prev_end()), type_name, name)

    # ---- blocks & statements --------------------------------------------
    def _block(self) -> ast.Block:
        start = self._cur().start
        if not self._check(T.LBRACE):
            bad = self._cur()
            at = self._prev_end()
            self._report(Diagnostic(
                code="E0120",
                message="expected '{' to start a block",
                span=self._span_of(bad),
                suggestion=Suggestion("insert the missing '{'", [Edit(Span(at, at), " {")], MAYBE),
            ), bad)
            if not self.cfg.phrase_level:
                return ast.Block(Span(start, start), [])
            if self._brace_excess <= 0:
                # no spare '}' anywhere: the author wrote a one-statement body
                if self._cur().kind in _STARTER_KINDS and self._cur().kind != T.RBRACE:
                    st = self._statement()
                    return ast.Block(Span(start, self._prev_end()), [st], recovered=True)
                return ast.Block(Span(start, start), [], recovered=True)
            # more '}' than '{' in the file: a '{' was lost here. Carry on as
            # if it were present so the matching '}' is not reported as stray.
            virtual_open = True
        else:
            self._advance()
            virtual_open = False
        self._block_depth += 1
        stmts = []
        closed_early = False
        recovered = virtual_open
        while not self._check(T.RBRACE) and not self._at_end():
            if self._check(T.FUNC) and self.cfg.phrase_level and self._brace_excess < 0:
                # a function declaration can only start at the top level, so
                # the block we are in must have lost its closing brace
                at = self._prev_end()
                self._report(Diagnostic(
                    code="E0121",
                    message="expected '}' to close block before 'func'",
                    span=Span(at, at + 1) if at < len(self.src) else Span(at, at),
                    labels=[Label(Span(start, start + 1), "the block opened here is never closed")],
                    help="functions cannot be nested; a '}' is missing before this 'func'",
                    suggestion=Suggestion("insert the missing '}'", [Edit(Span(at, at), "\n}")], MAYBE),
                ))
                closed_early = True
                recovered = True
                break
            i0 = self.i
            stmts.append(self._statement())
            if self.i == i0 and not self._at_end() and not self._check(T.RBRACE):
                self._advance()      # progress guarantee
        self._block_depth -= 1
        if closed_early:
            pass
        elif self._check(T.RBRACE):
            self._advance()
        else:
            recovered = True
            end = len(self.src)
            self._report(Diagnostic(
                code="E0121",
                message="expected '}' to close block",
                span=Span(self._cur().start, self._cur().start + 1),
                help="the block opened here is never closed",
                suggestion=Suggestion("add the closing '}'",
                                      [Edit(Span(end, end), "\n}")], MAYBE),
            ), self._cur())
        blk = ast.Block(Span(start, self._prev_end()), stmts)
        blk.recovered = recovered
        return blk

    def _statement(self) -> ast.Node:
        start = self._cur().start
        try:
            self._enter()
            try:
                n0 = self._n_reports
                node = self._statement_inner()
                if (self._n_reports > n0
                        and isinstance(node, (ast.VarDecl, ast.Assignment, ast.ExprStmt, ast.Return))):
                    node.damaged = True      # the checker will not pile semantic errors on top
                return node
            finally:
                self._leave()
        except _TooComplex:
            self._report_too_complex()
            self._synchronize()
            return ast.ErrorStmt(Span(start, max(start, self._cur().start)))

    def _statement_inner(self) -> ast.Node:
        start = self._cur().start
        self._fix_keyword_typo()
        kind = self._cur().kind

        if kind == T.LBRACE:
            return self._block()
        if kind == T.IF:
            return self._if_stmt()
        if kind == T.WHILE:
            return self._while_stmt()
        if kind == T.RETURN:
            return self._return_stmt()
        if kind in _TYPE_TOKENS:
            return self._var_decl()

        if kind == T.ERROR:
            # Damaged text already reported by the lexer: skip the rest of
            # this statement silently rather than report a second error.
            if self.cfg.token_poison:
                self._synchronize()
                return ast.ErrorStmt(Span(start, self._cur().start))

        if kind == T.RBRACE:
            bad = self._advance()      # only reachable at top level
            self._report(Diagnostic(
                code="E0124",
                message="unmatched '}' - there is no block to close",
                span=self._span_of(bad),
                suggestion=Suggestion("remove the stray '}'",
                                      [Edit(Span(bad.start, bad.end), "")], MAYBE),
            ), bad)
            return ast.ErrorStmt(Span(start, bad.end))

        if kind == T.ELSE:
            bad = self._advance()
            self._report(Diagnostic(
                code="E0125",
                message="'else' without a matching 'if'",
                span=self._span_of(bad),
                help="an 'else' must directly follow the closing '}' of an 'if' block",
            ), bad)
            if self._check(T.LBRACE):
                self._block()           # consume the orphan block in one piece
            elif self._cur().kind in _STARTER_KINDS or self._check(T.IDENT):
                self._statement()
            return ast.ErrorStmt(Span(start, self._prev_end()))

        if kind == T.FUNC:
            if self._block_depth > 0:
                self._report(Diagnostic(
                    code="E0126",
                    message="functions must be declared at the top level",
                    span=self._span_of(self._cur()),
                    help="move this function out of the enclosing block; nested functions are not supported",
                ), self._cur())
                self._func_decl()       # parse it whole so its body is not mistaken for statements
                return ast.ErrorStmt(Span(start, self._prev_end()))
            return self._func_decl()

        if kind == T.IDENT:
            if self.cfg.phrase_level:
                name_idx = self._decl_shape()
                if name_idx is not None:
                    return self._decl_with_bad_type()
            return self._ident_led_statement()

        bad = self._cur()
        self._report(Diagnostic(
            code="E0100",
            message=f"unexpected token '{bad.text or bad.kind.name}' at start of statement",
            span=self._span_of(bad),
            help="expected a type keyword, an identifier, 'if', 'while', 'return', or '{'",
        ), bad)
        self._synchronize()
        return ast.ErrorStmt(Span(start, self._cur().start))

    def _decl_shape(self) -> Optional[int]:
        """If the tokens here look like `IDENT [ ] IDENT` or `IDENT IDENT` -
        a declaration whose type name is unreadable - return the index of the
        variable-name token; otherwise None."""
        j = self.i + 1
        if j < len(self.toks) and self.toks[j].kind == T.LBRACKET:
            if j + 1 < len(self.toks) and self.toks[j + 1].kind == T.RBRACKET:
                j += 2
            else:
                return None
        if j < len(self.toks) and self.toks[j].kind == T.IDENT:
            return j
        return None

    def _decl_with_bad_type(self) -> ast.Node:
        tok = self._cur()
        ranked = rank_candidates(tok.text, sorted(TYPE_KEYWORDS))
        return self._var_decl(typo_ranked=ranked)

    def _if_stmt(self) -> ast.Node:
        start = self._advance().start  # 'if'
        cond = self._paren_expr()
        then_b = self._block()
        else_b = None
        self._maybe_fix_else_typo()
        if self._check(T.ELSE):
            self._advance()
            else_b = (ast.Block(Span(self._cur().start, self._cur().start), [self._statement()])
                      if self._check(T.IF) else self._block())
        return ast.If(Span(start, self._prev_end()), cond, then_b, else_b)

    def _maybe_fix_else_typo(self):
        if not self.cfg.phrase_level:
            return
        tok = self._cur()
        if tok.kind != T.IDENT or tok.text in KEYWORDS:
            return
        if self._peek(1).kind not in (T.LBRACE, T.IF):
            return
        ranked = rank_candidates(tok.text, ["else"])
        if not ranked:
            return
        span = Span(tok.start, tok.end)
        self._report(Diagnostic(
            code="E0411",
            message=f"unknown keyword '{tok.text}'",
            span=span,
            help="did you mean 'else'?",
            suggestion=Suggestion(f"replace '{tok.text}' with 'else'",
                                  [Edit(span, "else")], MACHINE),
        ))
        self.toks[self.i] = Token(T.ELSE, "else", tok.start, tok.end)

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

    def _close_paren_suggestion(self) -> Suggestion:
        prev_end = self._prev_end()
        nxt = self._cur().kind
        applicability = MACHINE if nxt in (T.LBRACE, T.SEMI) else MAYBE
        return Suggestion("insert the missing ')'", [Edit(Span(prev_end, prev_end), ")")],
                          applicability)

    def _paren_expr(self) -> ast.Node:
        if self._check(T.LPAREN):
            self._advance()
        else:
            bad = self._cur()
            self._report(Diagnostic(
                code="E0122",
                message="expected '(' after condition keyword",
                span=self._span_of(bad),
            ), bad)
        expr = self._expression()
        if self._check(T.RPAREN):
            self._advance()
        else:
            bad = self._cur()
            self._report(Diagnostic(
                code="E0202",
                message="expected ')' to close condition",
                span=self._span_of(bad),
                suggestion=self._close_paren_suggestion(),
            ), bad)
            if self.cfg.phrase_level and not self._check(T.LBRACE):
                # junk between the condition and its block: skip to the '{' so
                # the block is parsed normally, and distrust the condition
                first = self.i
                while (not self._at_end() and self._cur().kind not in (T.LBRACE, T.SEMI, T.RBRACE)):
                    self._advance()
                if self.i > first:
                    expr = ast.ErrorExpr(Span(expr.span.start, self._prev_end()))
        return expr

    def _var_decl(self, typo_ranked=None) -> ast.Node:
        type_tok = self._advance()
        type_name = type_tok.text

        if typo_ranked is not None:
            type_name = self._report_bad_type(type_tok, typo_ranked)

        type_name = self._array_suffix(type_name)

        if not self._check(T.IDENT):
            bad = self._cur()
            self._report(Diagnostic(
                code="E0101",
                message="expected an identifier after type keyword",
                span=self._span_of(bad),
            ), bad)
            self._synchronize()
            return ast.ErrorStmt(Span(type_tok.start, self._cur().start), lost_decl=True)
        name_tok = self._advance()
        name_span = Span(name_tok.start, name_tok.end)

        if not self._check(T.EQUAL):
            return self._decl_missing_equal(type_tok, type_name, name_tok, name_span)

        self._advance()
        value = self._expression()
        self._expect_semi(name_tok)
        return ast.VarDecl(Span(type_tok.start, self._prev_end()), type_name, name_tok.text, value,
                            name_span=name_span)


    def _array_suffix(self, type_name: str) -> str:
        """Parse an optional `[]` after a type name, reporting a missing ']'."""
        if not self._check(T.LBRACKET):
            return type_name
        self._advance()
        if self._check(T.RBRACKET):
            self._advance()
        else:
            at = self._prev_end()
            bad = self._cur()
            self._report(Diagnostic(
                code="E0123", message="expected ']' to close the array type",
                span=self._span_of(bad),
                help="array types are written 'int[]'",
                suggestion=Suggestion("insert the missing ']'", [Edit(Span(at, at), "]")], MACHINE),
            ), bad)
        return type_name if type_name == "Error" else type_name + "[]"

    def _report_bad_type(self, type_tok: Token, ranked) -> str:
        span = Span(type_tok.start, type_tok.end)
        if ranked:
            best = ranked[0][0]
            others = [c for c, d in ranked[1:] if d == ranked[0][1]]
            if others:
                help_text = "did you mean " + ", ".join(
                    f"'{c}'" for c in [best] + others[:2]) + "?"
            else:
                help_text = f"did you mean '{best}'?"
            self._report(Diagnostic(
                code="E0410",
                message=f"unknown type '{type_tok.text}'",
                span=span,
                help=help_text,
                suggestion=Suggestion(
                    f"replace '{type_tok.text}' with '{best}'",
                    [Edit(span, best)],
                    MACHINE if unique_best(ranked) else MAYBE),
            ))
            return best
        self._report(Diagnostic(
            code="E0410",
            message=f"unknown type '{type_tok.text}'",
            span=span,
            help="the types are: int, float, bool, string, void (arrays: int[], float[], ...)",
        ))
        return "Error"

    def _decl_missing_equal(self, type_tok: Token, type_name: str, name_tok: Token,
                             name_span: Span) -> ast.Node:
        cur = self._cur()
        base = type_name[:-2] if type_name.endswith("[]") else type_name

        # `int x;` - the initialiser is missing. Recover by keeping the
        # declaration (so later uses of x are not 'undeclared').
        if cur.kind == T.SEMI and self.cfg.phrase_level:
            default = "[]" if type_name.endswith("[]") else _DEFAULT_VALUE.get(base)
            sug = None
            if default is not None:
                sug = Suggestion(f"initialise '{name_tok.text}' with a default value",
                                 [Edit(Span(name_tok.end, name_tok.end), f" = {default}")], MAYBE)
            self._report(Diagnostic(
                code="E0102",
                message=f"variable '{name_tok.text}' must be initialised where it is declared",
                span=name_span,
                help="Lumen has no uninitialised variables; give it an initial value",
                suggestion=sug,
            ), name_tok)
            self._advance()                 # the ';'
            return ast.VarDecl(Span(type_tok.start, self._prev_end()), type_name,
                                name_tok.text, None, name_span=name_span)

        # `int x 5;` - '=' missing before an unambiguous expression start.
        if (cur.kind in _EXPR_START_NO_IDENT and self.cfg.phrase_level
                and self._same_line(name_tok, cur)):
            self._report(Diagnostic(
                code="E0102",
                message="expected '=' in variable declaration",
                span=self._span_of(cur),
                labels=[Label(name_span, "declared here")],
                help="an '=' was assumed here so parsing could continue",
                suggestion=Suggestion("insert '=' after the variable name",
                                      [Edit(Span(name_tok.end, name_tok.end), " =")], MAYBE),
            ), cur)
            value = self._expression()
            self._expect_semi(name_tok)
            return ast.VarDecl(Span(type_tok.start, self._prev_end()), type_name,
                                name_tok.text, value, name_span=name_span)

        self._report(Diagnostic(
            code="E0102",
            message="expected '=' in variable declaration",
            span=self._span_of(cur),
            labels=[Label(name_span, "declared here")],
        ), cur)
        self._synchronize()
        # keep the name: a poisoned declaration beats a cascade of 'undeclared'
        return ast.ErrorStmt(Span(type_tok.start, self._cur().start),
                              decl_name=name_tok.text, decl_span=name_span)

    def _same_line(self, a: Token, b: Token) -> bool:
        return "\n" not in self.src[a.end:b.start]

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
                self._report(Diagnostic(
                    code="E0123", message="expected ']' after index",
                    span=self._span_of(bad),
                    suggestion=Suggestion("insert the missing ']'",
                                          [Edit(Span(self._prev_end(), self._prev_end()), "]")], MAYBE),
                ), bad)
            if not self._check(T.EQUAL):
                bad = self._cur()
                self._report(Diagnostic(
                    code="E0103", message="expected '=' after indexed target",
                    span=self._span_of(bad)), bad)
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
            self._report(Diagnostic(
                code="E0103",
                message="expected '=' after identifier in assignment",
                span=self._span_of(bad),
                labels=[Label(Span(name_tok.start, name_tok.end), "this identifier starts the statement")],
            ), bad)
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
        cur = self._cur()

        # fallout of damage that was already reported - say nothing more
        if self.cfg.token_poison and (cur.kind == T.ERROR or prev.kind == T.ERROR
                                      or prev.damaged or cur.damaged):
            return
        if self._poisoned_at == self.i:
            return                       # the expression error already explains this

        applicable = (MACHINE if (cur.kind in _STARTER_KINDS or cur.kind in (T.RBRACE, T.EOF)
                                  or (cur.kind == T.IDENT and not self._same_line(prev, cur)))
                      else MAYBE)
        diag = Diagnostic(
            code="E0201",
            message="expected ';' after statement",
            span=Span(prev.end, prev.end + 1),
            help="a ';' was assumed here so parsing could continue",
            suggestion=Suggestion("insert ';'", [Edit(Span(prev.end, prev.end), ";")], applicable),
        )
        if self.cfg.phrase_level:
            self._report(diag, benign=(applicable == MACHINE))
        else:
            # classic panic-only behaviour: report, then discard to the next ';'
            diag.help = None
            diag.suggestion = None
            self._report(diag)
            while not self._at_end() and self._cur().kind not in (T.SEMI, T.RBRACE):
                self._advance()
            if self._check(T.SEMI):
                self._advance()

    # ---- expressions (Pratt) ------------------------------------------
    def _mk_depth(self, node, *children):
        d = 1 + max((getattr(c, "depth", 1) for c in children), default=0)
        node.depth = d
        if d > MAX_AST_DEPTH:
            raise _TooComplex()
        return node

    def _expression(self, min_bp: int = 0) -> ast.Node:
        self._enter()
        try:
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
                left = self._mk_depth(
                    ast.BinOp(Span(left.span.start, right.span.end), op, left, right), left, right)
            return left
        finally:
            self._leave()

    def _unary(self) -> ast.Node:
        tok = self._cur()
        if tok.kind in (T.MINUS, T.NOT):
            self._enter()
            try:
                self._advance()
                operand = self._unary()
                return self._mk_depth(
                    ast.UnaryOp(Span(tok.start, operand.span.end), tok.text, operand), operand)
            finally:
                self._leave()
        return self._postfix(self._primary())

    def _postfix(self, expr: ast.Node) -> ast.Node:
        while self._check(T.LBRACKET):
            self._advance()
            idx = self._expression()
            end = self._cur().end
            if self._check(T.RBRACKET):
                self._advance()
            else:
                bad = self._cur()
                self._report(Diagnostic(
                    code="E0123", message="expected ']' after index",
                    span=self._span_of(bad),
                    suggestion=Suggestion("insert the missing ']'",
                                          [Edit(Span(self._prev_end(), self._prev_end()), "]")], MAYBE),
                ), bad)
                end = self._prev_end()
            expr = self._mk_depth(ast.Index(Span(expr.span.start, end), expr, idx), expr, idx)
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
            self._report(Diagnostic(
                code="E0202", message="expected ')' to close argument list",
                span=self._span_of(bad),
                suggestion=self._close_paren_suggestion(),
            ), bad)
            end = self._prev_end()
        return self._mk_depth(ast.Call(Span(name_tok.start, end), name_tok.text, args), *args)

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
            else:
                bad = self._cur()
                self._report(Diagnostic(
                    code="E0123", message="expected ']' to close the array literal",
                    span=self._span_of(bad),
                    suggestion=Suggestion("insert the missing ']'",
                                          [Edit(Span(self._prev_end(), self._prev_end()), "]")], MAYBE),
                ), bad)
                end = self._prev_end()
            return self._mk_depth(ast.ArrayLiteral(Span(tok.start, end), elements), *elements)
        if tok.kind == T.LPAREN:
            self._advance()
            inner = self._expression()
            if self._check(T.RPAREN):
                self._advance()
            else:
                bad = self._cur()
                self._report(Diagnostic(
                    code="E0202",
                    message="expected ')' to close expression",
                    span=self._span_of(bad),
                    suggestion=self._close_paren_suggestion(),
                ), bad)
            return inner

        # Not the start of an expression.
        bad = self._cur()
        if bad.kind == T.ERROR and self.cfg.token_poison:
            self._advance()              # already reported by the lexer: poisoned silently
            return ast.ErrorExpr(Span(bad.start, bad.end))
        self._report(Diagnostic(
            code="E0203",
            message=f"expected an expression, found '{bad.text or bad.kind.name}'",
            span=self._span_of(bad),
        ), bad)
        if not self._at_end() and bad.kind in _SYNC_KINDS - {T.SEMI}:
            pass                        # never swallow a '}' or the start of the next statement
        elif not self._at_end() and bad.kind != T.SEMI:
            self._advance()
        self._poisoned_at = self.i
        return ast.ErrorExpr(Span(bad.start, bad.end))
