"""
Optimiser: machine-independent optimisation of three-address code on SSA form.

Pipeline for each function
--------------------------
    TAC -> control-flow graph -> SSA -> [ passes, repeated to a fixpoint ] -> out of SSA -> TAC

Passes
------
  * constant folding and propagation - operations on known constants are
    evaluated at compile time and the results flow into later uses;
  * copy propagation - `x = y` is removed by using `y` directly;
  * branch folding and unreachable-code removal - a conditional on a known
    constant becomes a jump, and blocks no longer reachable disappear;
  * trivial-phi removal - a phi whose operands are all the same value is a copy;
  * global value numbering (dominator-based common-subexpression elimination);
  * dead-code elimination - unused, side-effect-free definitions are deleted;
  * jump threading and block merging after leaving SSA.

Semantics-preserving rules
--------------------------
An optimiser must not change what a program does, and in Lumen "what a program
does" includes *which run-time error it raises and where*. So:

  * an operation that can fail (`+ - *` on integers can overflow, `/` and `%`
    can divide by zero, array reads can be out of bounds) is never folded when
    folding would hide the failure, and is never deleted as "dead": removing
    `int unused = 1 / z;` would silently remove a division-by-zero error. It
    *is* deleted when it provably cannot fail (a constant divisor other than
    0 and -1);
  * calls, `print`, array writes and stores to global variables are side
    effects and are always kept;
  * global variables are not put into SSA, so no pass assumes their value is
    unchanged across a call or a store;
  * folding uses exactly the arithmetic of the interpreter (64-bit overflow,
    truncating division), so a folded result equals the computed one.

If SSA construction meets a shape it does not handle, that function is left
unchanged; the optimiser never makes the compiler fail.
"""

import math
import operator
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .cfg import CFG, build_cfg, compute_dominators, linearize
from .interpreter import INT_MAX, INT_MIN, _parse_const
from .ssa import (SSABail, cleanup, construct, def_of, destruct, is_const, operands,
                  simplify_trivial_phis, term_operands, use_fields)
from .tac import MAIN, TACFunction, TACInstr

_ARITH = {"+": operator.add, "-": operator.sub, "*": operator.mul}
_CMP = {"<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge,
        "==": operator.eq, "!=": operator.ne}
# "+" is deliberately absent: on strings it is concatenation, which is not commutative.
_COMMUTATIVE = {"*", "==", "!="}
_BINARY = set(_ARITH) | set(_CMP) | {"/", "%", "&&", "||"}
_UNARY = {"neg", "not", "itof"}
_PURE_OPS = {"CONST", "COPY", "not", "itof"} | set(_CMP) | {"&&", "||"}


@dataclass
class OptConfig:
    """Each optimisation can be switched off, so its contribution can be measured."""
    constants: bool = True      # constant folding, constant propagation, branch folding
    copies: bool = True         # copy propagation, trivial-phi removal
    cse: bool = True            # global value numbering
    dce: bool = True            # dead-code elimination

    def disabled(self) -> list:
        return [k for k in ("constants", "copies", "cse", "dce") if not getattr(self, k)]


DEFAULT_OPT = OptConfig()


@dataclass
class OptStats:
    functions: int = 0
    bailed: int = 0
    instr_before: int = 0
    instr_after: int = 0
    phis_placed: int = 0
    folded: int = 0
    constants_propagated: int = 0
    copies_propagated: int = 0
    phis_removed: int = 0
    branches_folded: int = 0
    blocks_removed: int = 0
    cse: int = 0
    dead_removed: int = 0
    rounds: int = 0

    def summary(self) -> str:
        return (f"{self.instr_before} -> {self.instr_after} instructions; "
                f"{self.folded} folded, {self.constants_propagated} constants and "
                f"{self.copies_propagated} copies propagated, "
                f"{self.branches_folded} branches folded, {self.blocks_removed} blocks removed, "
                f"{self.cse} common subexpressions, {self.dead_removed} dead instructions removed "
                f"({self.phis_placed} phi functions, {self.rounds} rounds)")


# ---------------------------------------------------------------------------
# constant evaluation - mirrors interpreter.py exactly

def _const_text(v) -> Optional[str]:
    """TAC literal text for a Python value, or None if it cannot be written
    back as an operand that the interpreter would read as the same value."""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        t = repr(v)
        return t if ("." in t or "e" in t.lower()) else None
    if isinstance(v, str):
        return None if '"' in v else '"' + v + '"'
    return None


def fold(op: str, a, b=None):
    """Evaluate `op` on constants, or return None when the operation could
    fail (so the failure is kept for run time) or has no literal form."""
    try:
        if op in _ARITH:
            r = _ARITH[op](a, b)
            if r.__class__ is int and not (INT_MIN <= r <= INT_MAX):
                return None
        elif op in _CMP:
            r = _CMP[op](a, b)
        elif op == "/":
            if b == 0:
                return None
            if a.__class__ is int and b.__class__ is int:
                q = abs(a) // abs(b)
                r = -q if (a < 0) != (b < 0) else q
                if not (INT_MIN <= r <= INT_MAX):
                    return None
            else:
                r = a / b
        elif op == "%":
            if b == 0:
                return None
            if a.__class__ is int and b.__class__ is int:
                m = abs(a) % abs(b)
                r = -m if a < 0 else m
            else:
                r = math.fmod(a, b)
        elif op == "&&":
            r = bool(a and b)
        elif op == "||":
            r = bool(a or b)
        elif op == "neg":
            r = -a
            if r.__class__ is int and r > INT_MAX:
                return None
        elif op == "not":
            r = not a
        elif op == "itof":
            r = float(a)
        else:
            return None
    except Exception:
        return None
    if r.__class__ is float and not math.isfinite(r):
        return None
    return r


# ---------------------------------------------------------------------------

class _FunctionOptimiser:
    def __init__(self, cfg: CFG, ssa_names: Set[str], stats: OptStats, opts: OptConfig = DEFAULT_OPT):
        self.opts = opts
        self.cfg = cfg
        self.ssa = ssa_names
        self.stats = stats
        self._const_cache: Dict[str, Tuple[bool, object]] = {}

    # ---- helpers -----------------------------------------------------------
    def const(self, text: Optional[str]) -> Tuple[bool, object]:
        if text is None:
            return False, None
        r = self._const_cache.get(text)
        if r is None:
            r = _parse_const(text)
            self._const_cache[text] = r
        return r

    def replaceable(self, text: str) -> bool:
        """May a use be replaced by this operand? Only constants and SSA names:
        a global could change between the definition and the use."""
        return self.const(text)[0] or text in self.ssa

    def run(self):
        for _ in range(24):
            self.stats.rounds += 1
            changed = False
            changed |= self.simplify_cfg()
            changed |= self.propagate()
            if self.opts.cse:
                changed |= self.gvn()
            if self.opts.dce:
                changed |= self.dce()
            if not changed:
                break

    # ---- control flow -------------------------------------------------------
    def simplify_cfg(self) -> bool:
        cfg = self.cfg
        changed = False
        for b in list(cfg.blocks.values()):
            t = b.term
            if t[0] != "br":
                continue
            is_c, val = self.const(t[1])
            if not is_c or not self.opts.constants:
                continue
            chosen, dropped = (t[3], t[2]) if val else (t[2], t[3])
            b.term = ("goto", chosen)
            if dropped != chosen:
                for phi in cfg.blocks[dropped].phis:
                    phi.args.pop(b.id, None)
            self.stats.branches_folded += 1
            changed = True
        cfg.recompute_preds()
        removed = cfg.remove_unreachable()
        if removed:
            self.stats.blocks_removed += removed
            changed = True
        subst = simplify_trivial_phis(cfg) if self.opts.copies else {}
        if subst:
            self.stats.phis_removed += len(subst)
            self._apply(subst)
            changed = True
        return changed

    def _count(self, new: str):
        if is_const(new):
            self.stats.constants_propagated += 1
        else:
            self.stats.copies_propagated += 1

    def _apply(self, subst: Dict[str, str]) -> int:
        """Replace every use of a name in `subst` (following chains).
        Returns how many operands changed."""
        n = 0

        def sub(x):
            nonlocal n
            seen, orig = 0, x
            while x in subst and seen < 10000:
                x = subst[x]
                seen += 1
            if x != orig:
                n += 1
                self._count(x)
            return x
        for b in self.cfg.blocks.values():
            for phi in b.phis:
                for p in phi.args:
                    phi.args[p] = sub(phi.args[p])
            for ins in b.instrs:
                for f in use_fields(ins):
                    v = getattr(ins, f)
                    if v is not None:
                        setattr(ins, f, sub(v))
            t = b.term
            if t[0] == "br":
                b.term = ("br", sub(t[1]), t[2], t[3])
            elif t[0] == "ret" and t[1] is not None:
                b.term = ("ret", sub(t[1]))
        return n

    # ---- constant folding and copy propagation ------------------------------------
    def propagate(self) -> bool:
        cfg = self.cfg
        subst: Dict[str, str] = {}
        changed = False

        def sub(x):
            while x in subst:
                x = subst[x]
            return x

        def put(name, value) -> bool:
            """Record name := value (resolved). False if nothing new, or if it
            would make a cycle among values that only refer to each other."""
            r = sub(value)
            if r == name or subst.get(name) == r:
                return False
            if is_const(r):
                if not self.opts.constants:
                    return False
            elif not self.opts.copies:
                return False
            subst[name] = r
            return True

        for bid in cfg.rpo():
            b = cfg.blocks[bid]
            keep = []
            for phi in b.phis:
                for p in phi.args:
                    phi.args[p] = sub(phi.args[p])
                vals = {v for v in phi.args.values() if v != phi.res}
                if len(vals) == 1:
                    v = next(iter(vals))
                    if self.replaceable(v) and put(phi.res, v):
                        self.stats.phis_removed += 1
                        changed = True
                        continue
                keep.append(phi)
            b.phis = keep
            for ins in b.instrs:
                for f in use_fields(ins):
                    v = getattr(ins, f)
                    if v is not None:
                        nv = sub(v)
                        if nv != v and (f != "res" or not is_const(nv)):
                            setattr(ins, f, nv)
                            self._count(nv)
                            changed = True
                op = ins.op
                res = ins.res
                if op in ("CONST", "COPY"):
                    if res in self.ssa and ins.a is not None and self.replaceable(ins.a):
                        if self.const(ins.a)[0] and self.const(ins.a)[1].__class__ is str \
                                and '"' in self.const(ins.a)[1]:
                            continue
                        put(res, ins.a)
                    continue
                folded = self._try_fold(ins) if self.opts.constants else None
                if folded is not None:
                    ins.op, ins.a, ins.b = "CONST", folded, None
                    self.stats.folded += 1
                    changed = True
                    if res in self.ssa:
                        put(res, folded)
            t = b.term
            if t[0] == "br":
                nc = sub(t[1])
                if nc != t[1]:
                    b.term = ("br", nc, t[2], t[3])
                    self._count(nc)
                    changed = True
            elif t[0] == "ret" and t[1] is not None:
                nr = sub(t[1])
                if nr != t[1]:
                    b.term = ("ret", nr)
                    self._count(nr)
                    changed = True
        # uses in blocks that come earlier in RPO than their definition (phi
        # arguments along back edges) are resolved by one more sweep
        if subst and self._apply(subst):
            changed = True
        return changed

    def _try_fold(self, ins: TACInstr) -> Optional[str]:
        op = ins.op
        if op in _UNARY:
            ca, va = self.const(ins.a)
            if ca:
                r = fold(op, va)
                return _const_text(r) if r is not None else None
            return None
        if op in _BINARY:
            ca, va = self.const(ins.a)
            cb, vb = self.const(ins.b)
            if ca and cb:
                r = fold(op, va, vb)
                return _const_text(r) if r is not None else None
        return None

    # ---- global value numbering -----------------------------------------------
    def gvn(self) -> bool:
        cfg = self.cfg
        _, children, _ = compute_dominators(cfg)
        table: Dict[tuple, str] = {}
        changed = False
        todo = [("enter", cfg.entry)]
        while todo:
            item = todo.pop()
            if item[0] == "exit":
                for k in item[1]:
                    table.pop(k, None)
                continue
            b = cfg.blocks[item[1]]
            added = []
            for ins in b.instrs:
                op = ins.op
                if op not in _BINARY and op not in _UNARY:
                    continue
                keyed = [ins.a] if op in _UNARY else [ins.a, ins.b]
                if not all(self.replaceable(x) for x in keyed):
                    continue
                if op in _COMMUTATIVE:
                    keyed.sort()
                key = (op, *keyed)
                prior = table.get(key)
                if prior is not None:
                    ins.op, ins.a, ins.b = "COPY", prior, None
                    self.stats.cse += 1
                    changed = True
                elif ins.res in self.ssa:
                    table[key] = ins.res
                    added.append(key)
            todo.append(("exit", added))
            for c in reversed(children.get(b.id, [])):
                todo.append(("enter", c))
        return changed

    # ---- dead code ---------------------------------------------------------------------
    def _pure(self, ins: TACInstr) -> bool:
        if ins.res not in self.ssa:
            return False                     # a store to a global is a side effect
        op = ins.op
        if op in _PURE_OPS:
            return True
        if op in ("/", "%"):
            c, v = self.const(ins.b)
            return c and v.__class__ in (int, float) and v != 0 and v != -1
        return False                         # + - * neg can overflow; ARR_GET can fault

    def dce(self) -> bool:
        cfg = self.cfg
        uses: Counter = Counter()
        defsite: Dict[str, tuple] = {}
        for b in cfg.blocks.values():
            for phi in b.phis:
                defsite[phi.res] = ("phi", b, phi)
                for v in phi.args.values():
                    uses[v] += 1
            for ins in b.instrs:
                for o in operands(ins):
                    uses[o] += 1
                d = def_of(ins)
                if d is not None and self._pure(ins):
                    defsite[d] = ("ins", b, ins)
            for o in term_operands(b.term):
                uses[o] += 1
        dead_phis: Set[int] = set()
        dead_ins: Set[int] = set()
        work = [n for n in defsite if uses[n] == 0]
        while work:
            n = work.pop()
            kind, b, obj = defsite[n]
            if id(obj) in dead_phis or id(obj) in dead_ins:
                continue
            if kind == "phi":
                dead_phis.add(id(obj))
                ops = list(obj.args.values())
            else:
                dead_ins.add(id(obj))
                ops = operands(obj)
            for o in ops:
                if is_const(o):
                    continue
                uses[o] -= 1
                if uses[o] == 0 and o in defsite:
                    work.append(o)
        if not dead_phis and not dead_ins:
            return False
        for b in cfg.blocks.values():
            if dead_phis:
                b.phis = [p for p in b.phis if id(p) not in dead_phis]
            if dead_ins:
                b.instrs = [i for i in b.instrs if id(i) not in dead_ins]
        self.stats.dead_removed += len(dead_phis) + len(dead_ins)
        return True


# ---------------------------------------------------------------------------

def optimize_function(fn: TACFunction, globals_: Set[str], stats: Optional[OptStats] = None,
                      opts: OptConfig = DEFAULT_OPT) -> TACFunction:
    stats = stats if stats is not None else OptStats()
    stats.functions += 1
    stats.instr_before += sum(1 for i in fn.instrs if i.op != "LABEL")
    try:
        cfg = build_cfg(fn)
        names = construct(cfg, fn.params, globals_)
        stats.phis_placed += sum(len(b.phis) for b in cfg.blocks.values())
        _FunctionOptimiser(cfg, names, stats, opts).run()
        subst = simplify_trivial_phis(cfg)
        if subst:
            _FunctionOptimiser(cfg, names, stats, opts)._apply(subst)
        destruct(cfg)
        cleanup(cfg)
        instrs = linearize(cfg)
    except SSABail:
        stats.bailed += 1
        stats.instr_after += sum(1 for i in fn.instrs if i.op != "LABEL")
        return fn
    stats.instr_after += sum(1 for i in instrs if i.op != "LABEL")
    return TACFunction(fn.name, list(fn.params), instrs, global_names=fn.global_names)


def _escaping_globals(functions: Dict[str, TACFunction]) -> Set[str]:
    """Globals some function other than __main__ reads or writes. Only these
    are visible to calls; a global used by __main__ alone behaves like any
    local value and can be optimised in SSA form."""
    main = functions.get(MAIN)
    if main is None:
        return set()
    names = set(main.global_names)
    used: Set[str] = set()
    for name, fn in functions.items():
        if name == MAIN:
            continue
        for ins in fn.instrs:
            for o in operands(ins):
                if o in names:
                    used.add(o)
            d = def_of(ins)
            if d in names:
                used.add(d)
    return used


def optimize_program(functions: Dict[str, TACFunction], opts: OptConfig = DEFAULT_OPT,
                     ) -> Tuple[Dict[str, TACFunction], OptStats]:
    stats = OptStats()
    globals_ = _escaping_globals(functions)
    out = {name: optimize_function(fn, globals_, stats, opts) for name, fn in functions.items()}
    return out, stats


def ssa_text(functions: Dict[str, TACFunction], optimized: bool = False) -> str:
    """Pretty-print each function in SSA form (optionally after optimisation)."""
    from .cfg import format_cfg
    globals_ = _escaping_globals(functions)
    parts = []
    for name, fn in functions.items():
        header = f"func {name}({', '.join(fn.params)}):" if name != MAIN else "func __main__():"
        try:
            cfg = build_cfg(fn)
            names = construct(cfg, fn.params, globals_)
            if optimized:
                _FunctionOptimiser(cfg, names, OptStats()).run()
            parts.append(header + "\n" + format_cfg(cfg))
        except SSABail as e:
            parts.append(header + f"\n    ; SSA construction skipped: {e}")
    return "\n\n".join(parts) + "\n"
