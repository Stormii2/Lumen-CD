"""
Seeded-defect corpus generator for the evaluation.

Starts from the valid base programs in eval/programs/ and injects *exactly
one* known defect per mutant (single-defect set) or three well-separated
defects (multi-defect set). Because the defect is injected by us, the ground
truth is known: which line is wrong, which error code(s) a good compiler may
report, and what the original text was (so an auto-fix can be scored by
whether it restores the program).

Everything is deterministic for a given seed.
"""

import os
import random
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lumen.lexer import Lexer                      # noqa: E402
from lumen.tokens import T                         # noqa: E402

PROG_DIR = os.path.join(os.path.dirname(__file__), "programs")
TYPE_KWS = {T.INT, T.FLOAT, T.BOOL, T.STRING_KW}
PARSE_CODES = {"E0100", "E0101", "E0102", "E0103", "E0110", "E0111", "E0112", "E0113",
               "E0114", "E0120", "E0121", "E0122", "E0123", "E0124", "E0125", "E0126",
               "E0201", "E0202", "E0203", "E0204", "E0410", "E0411"}


@dataclass
class Mutant:
    id: str
    program: str
    op: str
    original: str
    source: str
    line: int                       # 1-based line of the defect in `source`
    tol: int                        # report within [line, line+tol] counts as "located"
    expected: Set[str]              # acceptable primary error codes
    lines_added: int = 0
    category: str = ""              # lexical / syntactic / semantic
    pre: int = 0                    # a report up to `pre` lines BEFORE the defect also counts
                                    # (a deleted token is reported at the end of the previous token)


@dataclass
class _Site:
    start: int
    end: int
    repl: str
    expected: Set[str]
    tol: int = 1
    category: str = "syntactic"
    insert_lines: int = 0


def _line_of(src: str, off: int) -> int:
    return src.count("\n", 0, off) + 1


class _Prog:
    def __init__(self, name: str, src: str):
        self.name, self.src = name, src
        self.toks = [t for t in Lexer(src).scan()[0] if t.kind != T.EOF]
        self.var_names: Set[str] = set()
        self.int_names: Set[str] = set()
        self.func_names: Set[str] = set()
        self.decl_idx: Set[int] = set()
        toks = self.toks
        for i, t in enumerate(toks):
            if t.kind != T.IDENT:
                continue
            prev = toks[i - 1] if i else None
            prev2 = toks[i - 2] if i > 1 else None
            if prev is not None and prev.kind in TYPE_KWS | {T.VOID} and prev2 is not None and prev2.kind == T.FUNC:
                self.func_names.add(t.text); self.decl_idx.add(i)
            elif prev is not None and prev.kind in TYPE_KWS:
                self.var_names.add(t.text); self.decl_idx.add(i)
                if prev.kind == T.INT:
                    self.int_names.add(t.text)
            elif prev is not None and prev.kind == T.RBRACKET and prev2 is not None and prev2.kind == T.LBRACKET:
                self.var_names.add(t.text); self.decl_idx.add(i)

    def uses(self, min_len=3):
        for i, t in enumerate(self.toks):
            nxt = self.toks[i + 1] if i + 1 < len(self.toks) else None
            if (t.kind == T.IDENT and i not in self.decl_idx and t.text in self.var_names
                    and len(t.text) >= min_len and not (nxt and nxt.kind == T.LPAREN)):
                yield i, t

    def calls(self):
        for i, t in enumerate(self.toks):
            nxt = self.toks[i + 1] if i + 1 < len(self.toks) else None
            if (t.kind == T.IDENT and t.text in self.func_names and i not in self.decl_idx
                    and nxt and nxt.kind == T.LPAREN):
                yield i, t


def _typo(word: str, rng: random.Random) -> str:
    kind = rng.choice(["swap", "drop", "sub"])
    for _ in range(10):
        if kind == "swap" and len(word) >= 3:
            i = rng.randrange(len(word) - 1)
            if word[i] != word[i + 1]:
                return word[:i] + word[i + 1] + word[i] + word[i + 2:]
        elif kind == "drop":
            i = rng.randrange(len(word))
            return word[:i] + word[i + 1:]
        else:
            i = rng.randrange(len(word))
            c = rng.choice("abcdefghijklmnopqrstuvwxyz")
            if c != word[i]:
                return word[:i] + c + word[i + 1:]
        kind = rng.choice(["swap", "drop", "sub"])
    return word[:-1]


# ---- operators: each returns a list of candidate sites -----------------------
def op_missing_semicolon(p, rng):
    out = []
    for i, t in enumerate(p.toks[:-1]):
        if t.kind == T.SEMI:
            nxt = p.toks[i + 1]
            if _line_of(p.src, nxt.start) > _line_of(p.src, t.start):   # `;` ends its line
                out.append(_Site(t.start, t.end, "", {"E0201"}, tol=1, category="syntactic"))
    return out


def op_var_typo(p, rng):
    out = []
    for i, t in p.uses():
        new = _typo(t.text, rng)
        if new not in p.var_names and new not in p.func_names and new.isidentifier():
            out.append(_Site(t.start, t.end, new, {"E0412"}, tol=0, category="semantic"))
    return out


def op_undeclared_var(p, rng):
    return [_Site(t.start, t.end, "zzq" + str(rng.randrange(10)), {"E0412"}, tol=0,
                  category="semantic") for i, t in p.uses()]


def op_type_keyword_typo(p, rng):
    out = []
    for i, t in enumerate(p.toks):
        if t.kind in TYPE_KWS and i + 1 < len(p.toks) and (
                p.toks[i + 1].kind == T.IDENT or p.toks[i + 1].kind == T.LBRACKET):
            new = _typo(t.text, rng)
            if new not in ("int", "float", "bool", "string") and len(new) >= 3:
                out.append(_Site(t.start, t.end, new, {"E0410"}, tol=0, category="syntactic"))
    return out


def op_keyword_typo(p, rng):
    out = []
    for t in p.toks:
        if t.kind in (T.WHILE, T.RETURN):
            for new in ([("whlie", "wihle")] if t.kind == T.WHILE else [("retrun", "reutrn")])[0:1]:
                out.append(_Site(t.start, t.end, rng.choice(new), {"E0411"}, tol=0,
                                 category="syntactic"))
    return out


def op_missing_rparen(p, rng):
    return [_Site(t.start, t.end, "", PARSE_CODES, tol=1) for t in p.toks if t.kind == T.RPAREN]


def op_missing_lparen_cond(p, rng):
    out = []
    for i, t in enumerate(p.toks[:-1]):
        if t.kind in (T.IF, T.WHILE) and p.toks[i + 1].kind == T.LPAREN:
            n = p.toks[i + 1]
            out.append(_Site(n.start, n.end, "", PARSE_CODES, tol=1))
    return out


def op_missing_rbrace(p, rng):
    last_line = p.src.count("\n") + 2   # EOF sits on the line after the final newline
    return [_Site(t.start, t.end, "", PARSE_CODES, tol=last_line, category="syntactic")
            for t in p.toks if t.kind == T.RBRACE]


def op_missing_equals(p, rng):
    out = []
    for i, t in enumerate(p.toks[:-2]):
        if t.kind == T.EQUAL and i >= 2 and p.toks[i - 1].kind == T.IDENT \
                and (p.toks[i - 2].kind in TYPE_KWS or p.toks[i - 2].kind == T.RBRACKET):
            out.append(_Site(t.start, t.end, "", {"E0102"} | PARSE_CODES, tol=1))
    return out


def op_type_mismatch(p, rng):
    out = []
    toks = p.toks
    for i, t in enumerate(toks[:-3]):
        if (t.kind == T.INT and toks[i + 1].kind == T.IDENT and toks[i + 2].kind == T.EQUAL
                and toks[i + 3].kind == T.NUMBER and toks[i + 4].kind == T.SEMI):
            n = toks[i + 3]
            out.append(_Site(n.start, n.end, '"oops"', {"E0302"}, tol=0, category="semantic"))
    return out


def op_unterminated_string(p, rng):
    return [_Site(t.end - 1, t.end, "", {"E0002"}, tol=0, category="lexical")
            for t in p.toks if t.kind == T.STRING and t.text.endswith('"') and len(t.text) >= 2]


def op_bad_char(p, rng):
    out = []
    for i, t in enumerate(p.toks):
        if t.kind in (T.IDENT, T.NUMBER):
            out.append(_Site(t.end, t.end, rng.choice("@$`~"), {"E0001"}, tol=0, category="lexical"))
    return out


def op_bad_number(p, rng):
    return [_Site(t.start, t.end, t.text + ".5.5" if "." not in t.text else t.text + ".7",
                  {"E0003"}, tol=0, category="lexical")
            for t in p.toks if t.kind == T.NUMBER]


def op_fn_typo(p, rng):
    out = []
    for i, t in p.calls():
        if len(t.text) >= 4:
            new = _typo(t.text, rng)
            if new not in p.func_names and new != "print":
                out.append(_Site(t.start, t.end, new, {"E0413"}, tol=0, category="semantic"))
    return out


def op_extra_arg(p, rng):
    out = []
    toks = p.toks
    for i, t in p.calls():
        j = i + 2
        depth = 1
        while j < len(toks) and depth:
            if toks[j].kind == T.LPAREN:
                depth += 1
            elif toks[j].kind == T.RPAREN:
                depth -= 1
            j += 1
        close = toks[j - 1]
        out.append(_Site(close.start, close.start, ", 1", {"E0311"}, tol=0, category="semantic"))
    return out


def op_int_condition(p, rng):
    out = []
    toks = p.toks
    for i, t in enumerate(toks[:-4]):
        if t.kind in (T.IF, T.WHILE) and toks[i + 1].kind == T.LPAREN and toks[i + 2].kind == T.IDENT \
                and toks[i + 2].text in p.int_names and toks[i + 3].kind in (
                    T.LT, T.GT, T.LE, T.GE, T.EQEQ, T.NEQ):
            j = i + 3
            while toks[j].kind != T.RPAREN:
                j += 1
            out.append(_Site(toks[i + 3].start, toks[j].start, "", {"E0307"}, tol=0, category="semantic"))
    return out


def op_missing_rbracket(p, rng):
    return [_Site(t.start, t.end, "", PARSE_CODES, tol=1) for t in p.toks if t.kind == T.RBRACKET]


def op_dup_decl(p, rng):
    out = []
    toks = p.toks
    for i, t in enumerate(toks[:-4]):
        if (t.kind == T.INT and toks[i + 1].kind == T.IDENT and toks[i + 2].kind == T.EQUAL):
            semi = i + 3
            while toks[semi].kind != T.SEMI:
                semi += 1
            nm = toks[i + 1].text
            out.append(_Site(toks[semi].end, toks[semi].end, f"\nint {nm} = 0;", {"E0300"},
                             tol=0, category="semantic", insert_lines=1))
    return out


def op_stray_brace(p, rng):
    out = []
    for t in p.toks:
        if t.kind == T.SEMI and _line_of(p.src, t.start) < p.src.count("\n"):
            depth = 0
            for u in p.toks:
                if u.start >= t.start:
                    break
                depth += (u.kind == T.LBRACE) - (u.kind == T.RBRACE)
            if depth == 0:
                out.append(_Site(t.end, t.end, "\n}", {"E0124"}, tol=0, insert_lines=1))
    return out


OPERATORS: Dict[str, Callable] = {
    "missing_semicolon": op_missing_semicolon,
    "var_typo": op_var_typo,
    "undeclared_var": op_undeclared_var,
    "type_keyword_typo": op_type_keyword_typo,
    "keyword_typo": op_keyword_typo,
    "missing_rparen": op_missing_rparen,
    "missing_cond_lparen": op_missing_lparen_cond,
    "missing_rbrace": op_missing_rbrace,
    "missing_equals": op_missing_equals,
    "type_mismatch": op_type_mismatch,
    "unterminated_string": op_unterminated_string,
    "bad_char": op_bad_char,
    "bad_number": op_bad_number,
    "fn_typo": op_fn_typo,
    "extra_arg": op_extra_arg,
    "int_condition": op_int_condition,
    "missing_rbracket": op_missing_rbracket,
    "dup_decl": op_dup_decl,
    "stray_brace": op_stray_brace,
}


def load_programs() -> Dict[str, str]:
    progs = {}
    for fn in sorted(os.listdir(PROG_DIR)):
        if fn.endswith(".lum"):
            with open(os.path.join(PROG_DIR, fn), encoding="utf-8") as f:
                progs[fn[:-4]] = f.read()
    return progs


def _apply(src: str, site: _Site) -> str:
    return src[:site.start] + site.repl + src[site.end:]


def build_single(seed=2024, per_op_per_prog=2) -> List[Mutant]:
    rng = random.Random(seed)
    out: List[Mutant] = []
    for pname, src in load_programs().items():
        prog = _Prog(pname, src)
        for opname, fn in OPERATORS.items():
            sites = fn(prog, rng)
            rng.shuffle(sites)
            for k, s in enumerate(sites[:per_op_per_prog]):
                msrc = _apply(src, s)
                line = _line_of(src, s.start) + (1 if s.insert_lines and s.repl.startswith("\n") else 0)
                out.append(Mutant(f"{pname}:{opname}:{k}", pname, opname, src, msrc, line, s.tol,
                                  set(s.expected), s.insert_lines, s.category,
                                  pre=1 if opname.startswith("missing_") else 0))
    return out


def build_multi(seed=77, n_cases=120, k=3) -> List[List[Mutant]]:
    """Programs with k simultaneous, well-separated defects. Returns, per case,
    the list of component Mutants (their `source` field is the combined text and
    their `line` already accounts for lines added by other insertions)."""
    rng = random.Random(seed)
    progs = {n: _Prog(n, s) for n, s in load_programs().items()}
    safe_ops = [o for o in OPERATORS if o not in ("missing_rbrace", "stray_brace", "dup_decl")]
    cases = []
    tries = 0
    while len(cases) < n_cases and tries < n_cases * 30:
        tries += 1
        prog = rng.choice(list(progs.values()))
        picks = []
        used_lines = []
        for opname in rng.sample(safe_ops, len(safe_ops)):
            sites = OPERATORS[opname](prog, rng)
            rng.shuffle(sites)
            for s in sites:
                ln = _line_of(prog.src, s.start)
                if all(abs(ln - u) >= 3 for u in used_lines):
                    picks.append((opname, s, ln)); used_lines.append(ln)
                    break
            if len(picks) == k:
                break
        if len(picks) < k:
            continue
        picks.sort(key=lambda x: x[1].start, reverse=True)       # apply bottom-up
        src = prog.src
        for _, s, _ in picks:
            src = _apply(src, s)
        comps = []
        for opname, s, ln in picks:
            comps.append(Mutant(f"{prog.name}:multi{len(cases)}:{opname}", prog.name, opname,
                                prog.src, src, ln, s.tol, set(s.expected), 0, s.category,
                                pre=1 if opname.startswith("missing_") else 0))
        cases.append(comps)
    return cases


JUNK = [")", "(", "}", "{", ";", "+", "*", "=", "else", "5", "x", "]", "[", ",", "&&", "int", "return", "while"]


def build_fuzz(seed=31337, n=300) -> List[Mutant]:
    """Token-level fuzz mutants: delete / insert / replace ONE random token.
    Unlike the targeted operators above there is no "expected code" (any error
    is acceptable); only localisation and cascade size are scored. Mutants the
    reference compiler accepts as valid programs are discarded (a deleted
    redundant token can leave a legal program)."""
    from lumen import compile_source          # local import: corpus is also used standalone
    rng = random.Random(seed)
    progs = [_Prog(n_, s_) for n_, s_ in load_programs().items()]
    out: List[Mutant] = []
    tries = 0
    while len(out) < n and tries < n * 20:
        tries += 1
        p = rng.choice(progs)
        t = rng.choice(p.toks)
        kind = rng.choice(["delete", "insert", "replace"])
        if kind == "delete":
            site = _Site(t.start, t.end, "", set())
        elif kind == "insert":
            site = _Site(t.start, t.start, rng.choice(JUNK) + " ", set())
        else:
            site = _Site(t.start, t.end, rng.choice(JUNK), set())
        msrc = _apply(p.src, site)
        if msrc == p.src or compile_source(msrc, want_tac=False).ok:
            continue
        out.append(Mutant(f"{p.name}:fuzz_{kind}:{len(out)}", p.name, "fuzz_" + kind, p.src, msrc,
                          _line_of(p.src, t.start), 1, set(), 0, "fuzz", pre=1))
    return out


if __name__ == "__main__":
    ms = build_single()
    print(len(ms), "single-defect mutants")
    from collections import Counter
    print(Counter(m.op for m in ms))
    print(len(build_multi()), "multi-defect cases")
