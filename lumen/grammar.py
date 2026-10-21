"""
Lumen's grammar as data, with computed FIRST / FOLLOW sets.

The hand-written recursive-descent parser in parser.py implements this
grammar. Writing the grammar down as data lets us do three things that the
compiler-design syllabus asks for and that a hand-written parser alone does
not demonstrate:

  1. compute nullable / FIRST / FOLLOW sets by the standard fixed-point
     algorithm,
  2. prove the grammar is LL(1) (no predict-set conflicts), and
  3. *derive* the parser's panic-mode synchronisation set from FIRST(statement)
     instead of hard-coding it.

Notation: a production is a list of symbols. A symbol that is a key of
GRAMMAR is a non-terminal; any other symbol is a terminal and is the *name*
of a token kind (see tokens.T). An empty list is the epsilon production.
"""

from typing import Dict, List, Set, Tuple

EPS = "ε"
EOF = "EOF"

# fmt: off
GRAMMAR: Dict[str, List[List[str]]] = {
    "program":      [["item_list"]],
    "item_list":    [["item", "item_list"], []],
    "item":         [["func_decl"], ["statement"]],

    "func_decl":    [["FUNC", "type", "IDENT", "LPAREN", "params", "RPAREN", "block"]],
    "type":         [["INT"], ["FLOAT"], ["BOOL"], ["STRING_KW"], ["VOID"]],
    "params":       [["param", "param_tail"], []],
    "param_tail":   [["COMMA", "param", "param_tail"], []],
    "param":        [["type", "arr_suffix", "IDENT"]],
    "arr_suffix":   [["LBRACKET", "RBRACKET"], []],

    "block":        [["LBRACE", "stmt_list", "RBRACE"]],
    "stmt_list":    [["statement", "stmt_list"], []],
    "statement":    [["block"], ["if_stmt"], ["while_stmt"], ["return_stmt"],
                     ["var_decl"], ["ident_stmt"]],
    "if_stmt":      [["IF", "LPAREN", "expr", "RPAREN", "block", "else_part"]],
    "else_part":    [["ELSE", "else_body"], []],
    "else_body":    [["block"], ["if_stmt"]],
    "while_stmt":   [["WHILE", "LPAREN", "expr", "RPAREN", "block"]],
    "return_stmt":  [["RETURN", "opt_expr", "SEMI"]],
    "opt_expr":     [["expr"], []],
    "var_decl":     [["type", "arr_suffix", "IDENT", "EQUAL", "expr", "SEMI"]],
    "ident_stmt":   [["IDENT", "ident_tail"]],
    "ident_tail":   [["LBRACKET", "expr", "RBRACKET", "EQUAL", "expr", "SEMI"],
                     ["LPAREN", "args", "RPAREN", "SEMI"],
                     ["EQUAL", "expr", "SEMI"]],

    # expressions, one non-terminal per precedence level (loosest first)
    "expr":         [["or_e"]],
    "or_e":         [["and_e", "or_t"]],
    "or_t":         [["OR", "and_e", "or_t"], []],
    "and_e":        [["eq_e", "and_t"]],
    "and_t":        [["AND", "eq_e", "and_t"], []],
    "eq_e":         [["rel_e", "eq_t"]],
    "eq_t":         [["EQEQ", "rel_e", "eq_t"], ["NEQ", "rel_e", "eq_t"], []],
    "rel_e":        [["add_e", "rel_t"]],
    "rel_t":        [["LT", "add_e", "rel_t"], ["GT", "add_e", "rel_t"],
                     ["LE", "add_e", "rel_t"], ["GE", "add_e", "rel_t"], []],
    "add_e":        [["mul_e", "add_t"]],
    "add_t":        [["PLUS", "mul_e", "add_t"], ["MINUS", "mul_e", "add_t"], []],
    "mul_e":        [["unary", "mul_t"]],
    "mul_t":        [["STAR", "unary", "mul_t"], ["SLASH", "unary", "mul_t"],
                     ["PERCENT", "unary", "mul_t"], []],
    "unary":        [["MINUS", "unary"], ["NOT", "unary"], ["postfix"]],
    "postfix":      [["primary", "post_t"]],
    "post_t":       [["LBRACKET", "expr", "RBRACKET", "post_t"], []],
    "primary":      [["NUMBER"], ["STRING"], ["TRUE"], ["FALSE"],
                     ["IDENT", "call_t"], ["LPAREN", "expr", "RPAREN"],
                     ["LBRACKET", "args", "RBRACKET"]],
    "call_t":       [["LPAREN", "args", "RPAREN"], []],
    "args":         [["expr", "args_tail"], []],
    "args_tail":    [["COMMA", "expr", "args_tail"], []],
}
# fmt: on

START = "program"


def is_nonterminal(sym: str) -> bool:
    return sym in GRAMMAR


def terminals() -> Set[str]:
    out: Set[str] = set()
    for prods in GRAMMAR.values():
        for p in prods:
            for s in p:
                if not is_nonterminal(s):
                    out.add(s)
    return out


def compute_nullable() -> Set[str]:
    nullable: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for nt, prods in GRAMMAR.items():
            if nt in nullable:
                continue
            for p in prods:
                if all(s in nullable for s in p):  # epsilon prods satisfy this vacuously
                    nullable.add(nt)
                    changed = True
                    break
    return nullable


def first_of_sequence(seq: List[str], first: Dict[str, Set[str]],
                       nullable: Set[str]) -> Set[str]:
    """FIRST of a sequence of symbols; includes EPS iff the whole sequence is nullable."""
    out: Set[str] = set()
    for s in seq:
        if not is_nonterminal(s):
            out.add(s)
            return out
        out |= first[s]
        if s not in nullable:
            return out
    out.add(EPS)
    return out


def compute_first() -> Dict[str, Set[str]]:
    nullable = compute_nullable()
    first: Dict[str, Set[str]] = {nt: set() for nt in GRAMMAR}
    changed = True
    while changed:
        changed = False
        for nt, prods in GRAMMAR.items():
            for p in prods:
                add = first_of_sequence(p, first, nullable) - {EPS}
                if not add <= first[nt]:
                    first[nt] |= add
                    changed = True
    for nt in nullable:
        first[nt].add(EPS)
    return first


def compute_follow() -> Dict[str, Set[str]]:
    nullable = compute_nullable()
    first = compute_first()
    follow: Dict[str, Set[str]] = {nt: set() for nt in GRAMMAR}
    follow[START].add(EOF)
    changed = True
    while changed:
        changed = False
        for nt, prods in GRAMMAR.items():
            for p in prods:
                for i, sym in enumerate(p):
                    if not is_nonterminal(sym):
                        continue
                    rest = p[i + 1:]
                    rest_first = first_of_sequence(rest, first, nullable)
                    add = rest_first - {EPS}
                    if EPS in rest_first:
                        add = add | follow[nt]
                    if not add <= follow[sym]:
                        follow[sym] |= add
                        changed = True
    return follow


def predict_conflicts() -> List[Tuple[str, str, List[int]]]:
    """LL(1) check: for each non-terminal, the predict sets of its
    productions must be pairwise disjoint. Returns a list of
    (non-terminal, terminal, [production indexes]) conflicts; an empty list
    means the grammar is LL(1)."""
    nullable = compute_nullable()
    first = compute_first()
    follow = compute_follow()
    conflicts = []
    for nt, prods in GRAMMAR.items():
        seen: Dict[str, List[int]] = {}
        for idx, p in enumerate(prods):
            f = first_of_sequence(p, first, nullable)
            predict = (f - {EPS}) | (follow[nt] if EPS in f else set())
            for tok in predict:
                seen.setdefault(tok, []).append(idx)
        for tok, idxs in seen.items():
            if len(idxs) > 1:
                conflicts.append((nt, tok, idxs))
    return conflicts


def is_ll1() -> bool:
    return not predict_conflicts()


# ---- derived parser tables ------------------------------------------------

_FIRST = compute_first()
_FOLLOW = compute_follow()

FIRST = _FIRST
FOLLOW = _FOLLOW

# Tokens that can only begin a statement/declaration. IDENT and '{' are left
# out on purpose: an identifier also appears in the *middle* of statements
# (inside expressions), so resuming at one would re-trigger the same error.
# This is the standard "strong starter" refinement of FIRST(statement).
_WEAK = {"IDENT", "LBRACE"}
STATEMENT_STARTERS: Set[str] = (_FIRST["statement"] | {"FUNC"}) - {EPS} - _WEAK

# Panic-mode synchronisation set: where it is safe to resume parsing.
SYNC_SET: Set[str] = STATEMENT_STARTERS | {"SEMI", "RBRACE"}


def format_tables() -> str:
    """Human-readable FIRST/FOLLOW tables (used by `lumenc --grammar`)."""
    def fmt(s: Set[str]) -> str:
        return "{ " + ", ".join(sorted(s)) + " }"
    nullable = compute_nullable()
    lines = []
    w = max(len(nt) for nt in GRAMMAR)
    lines.append(f"{'non-terminal'.ljust(w)}  nullable  FIRST / FOLLOW")
    lines.append("-" * 78)
    for nt in GRAMMAR:
        lines.append(f"{nt.ljust(w)}  {'yes' if nt in nullable else 'no ':8}  "
                     f"FIRST  = {fmt(_FIRST[nt] - {EPS})}")
        lines.append(f"{' ' * w}  {' ' * 8}  FOLLOW = {fmt(_FOLLOW[nt])}")
    lines.append("")
    conflicts = predict_conflicts()
    lines.append("LL(1) check: " + ("PASS - no predict-set conflicts"
                                    if not conflicts else f"FAIL - {conflicts}"))
    lines.append("panic-mode sync set: " + fmt(SYNC_SET))
    return "\n".join(lines)
