"""
Static Single Assignment form.

In SSA every variable is assigned exactly once: the n-th assignment to `x`
defines a new name `x$n`. Where control flow joins and two different versions
could arrive, a phi function picks one according to the edge taken:

    B3:  x$3 = phi(B1: x$1, B2: x$2)

Construction follows Cytron et al.: phi functions are placed on the iterated
dominance frontier of each variable's definitions, and a walk of the dominator
tree renames every definition and use. Placement is *pruned* - a phi is only
inserted where the variable is live - so no phi ever has an undefined operand.

Only function-local names are put into SSA. Global variables stay as they are:
any call may read or write them, so they behave like memory, not like values
the optimiser can reason about. Arrays are references; `ARR_SET` mutates the
object an array name refers to but does not give the name a new value, so it is
a *use* of the array name, never a definition.

`destruct` converts back to ordinary TAC: each phi becomes copies at the end of
its predecessors (a parallel copy, sequentialised, with a temporary when the
copies form a cycle - the classic "swap problem").
"""

from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set

from .cfg import CFG, Block, Phi, build_cfg, compute_dominators, dominance_frontiers
from .tac import TACFunction, TACInstr
from .interpreter import _parse_const


class SSABail(Exception):
    """The function has a shape SSA construction does not handle; leave it alone."""


_BINARY = {"+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&&", "||"}
_UNARY = {"neg", "not", "itof"}


def is_const(text: Optional[str]) -> bool:
    return text is not None and _parse_const(text)[0]


def use_fields(ins: TACInstr) -> List[str]:
    """Names of the attributes of `ins` that are *read* operands."""
    op = ins.op
    if op in ("GOTO", "LABEL", "CONST", "CALL", "CALL_VOID", "ARR_NEW"):
        return []
    if op == "ARR_SET":
        return ["a", "b", "res"]
    if op in ("PARAM", "RETURN", "COPY", "IF_FALSE") or op in _UNARY:
        return ["a"]
    return ["a", "b"]            # binary operators and ARR_GET


def def_of(ins: TACInstr) -> Optional[str]:
    if ins.op in ("ARR_SET", "PARAM", "RETURN", "GOTO", "IF_FALSE", "LABEL"):
        return None
    return ins.res


def operands(ins: TACInstr) -> List[str]:
    out = []
    for f in use_fields(ins):
        v = getattr(ins, f)
        if v is not None and not is_const(v):
            out.append(v)
    return out


def term_operands(term: tuple) -> List[str]:
    if term[0] == "br" and not is_const(term[1]):
        return [term[1]]
    if term[0] == "ret" and term[1] is not None and not is_const(term[1]):
        return [term[1]]
    return []


# ---------------------------------------------------------------------------

def local_names(cfg: CFG, params: Iterable[str], globals_: Set[str]) -> Set[str]:
    names = set(params)
    for b in cfg.blocks.values():
        for ins in b.instrs:
            d = def_of(ins)
            if d is not None:
                names.add(d)
    return names - globals_


def _liveness(cfg: CFG, locals_: Set[str]) -> Dict[int, Set[str]]:
    """live-in sets restricted to local names (backward dataflow)."""
    use: Dict[int, Set[str]] = {}
    kill: Dict[int, Set[str]] = {}
    for bid, b in cfg.blocks.items():
        u, k = set(), set()
        for ins in b.instrs:
            for o in operands(ins):
                if o in locals_ and o not in k:
                    u.add(o)
            d = def_of(ins)
            if d in locals_:
                k.add(d)
        for o in term_operands(b.term):
            if o in locals_ and o not in k:
                u.add(o)
        use[bid], kill[bid] = u, k
    live_in = {bid: set(use[bid]) for bid in cfg.blocks}
    rpo = cfg.rpo()
    changed = True
    while changed:
        changed = False
        for bid in reversed(rpo):
            out: Set[str] = set()
            for s in cfg.blocks[bid].succs():
                out |= live_in[s]
            new = use[bid] | (out - kill[bid])
            if new != live_in[bid]:
                live_in[bid] = new
                changed = True
    return live_in


def construct(cfg: CFG, params: List[str], globals_: Set[str]) -> Set[str]:
    """Convert `cfg` to SSA in place. Returns the set of SSA names."""
    locals_ = local_names(cfg, params, globals_)
    idom, children, rpo = compute_dominators(cfg)
    df = dominance_frontiers(cfg, idom)
    live_in = _liveness(cfg, locals_)

    defs: Dict[str, Set[int]] = defaultdict(set)
    for bid in rpo:
        for ins in cfg.blocks[bid].instrs:
            d = def_of(ins)
            if d in locals_:
                defs[d].add(bid)
    for p in params:
        defs[p].add(cfg.entry)

    for v in sorted(locals_):
        placed: Set[int] = set()
        work = list(defs[v])
        while work:
            b = work.pop()
            for d in df.get(b, ()):
                if d not in placed and v in live_in[d]:
                    cfg.blocks[d].phis.append(Phi(res=v, var=v))
                    placed.add(d)
                    if d not in defs[v]:
                        defs[v].add(d)
                        work.append(d)

    counter: Dict[str, int] = defaultdict(int)
    stacks: Dict[str, List[str]] = {v: [] for v in locals_}
    ssa_names: Set[str] = set()
    for p in params:
        if p in stacks:
            stacks[p].append(p)          # version 0 keeps the parameter's own name
            ssa_names.add(p)

    def fresh(v: str) -> str:
        counter[v] += 1
        n = f"{v}${counter[v]}"
        stacks[v].append(n)
        ssa_names.add(n)
        return n

    def cur(v: str) -> str:
        if not stacks[v]:
            raise SSABail(f"'{v}' used before any definition")
        return stacks[v][-1]

    todo = [("enter", cfg.entry)]
    while todo:
        item = todo.pop()
        if item[0] == "exit":
            for v in item[1]:
                stacks[v].pop()
            continue
        b = cfg.blocks[item[1]]
        pushed: List[str] = []
        for phi in b.phis:
            phi.res = fresh(phi.var)
            pushed.append(phi.var)
        for ins in b.instrs:
            for f in use_fields(ins):
                val = getattr(ins, f)
                if val in locals_:
                    setattr(ins, f, cur(val))
            d = def_of(ins)
            if d in locals_:
                ins.res = fresh(d)
                pushed.append(d)
        t = b.term
        if t[0] == "br" and t[1] in locals_:
            b.term = ("br", cur(t[1]), t[2], t[3])
        elif t[0] == "ret" and t[1] in locals_:
            b.term = ("ret", cur(t[1]))
        for s in b.succs():
            for phi in cfg.blocks[s].phis:
                phi.args[b.id] = cur(phi.var)
        todo.append(("exit", pushed))
        for c in reversed(children[b.id]):
            todo.append(("enter", c))
    return ssa_names


# ---------------------------------------------------------------------------

def simplify_trivial_phis(cfg: CFG) -> Dict[str, str]:
    """phi whose operands are all the same value (ignoring itself) is a copy."""
    subst: Dict[str, str] = {}
    for b in cfg.blocks.values():
        keep = []
        for phi in b.phis:
            vals = {v for v in phi.args.values() if v != phi.res}
            if len(vals) == 1:
                subst[phi.res] = next(iter(vals))
            else:
                keep.append(phi)
        b.phis = keep
    return subst


def destruct(cfg: CFG, fresh_prefix: str = "swap") -> None:
    """Replace every phi by copies on its incoming edges (in place)."""
    counter = [0]
    # remaining single-valued phis become plain copies in their own block
    for b in list(cfg.blocks.values()):
        if not b.phis:
            continue
        for p in b.preds:
            pb = cfg.blocks[p]
            if pb.term[0] != "goto":
                raise SSABail("phi on a critical edge")
            copies = [(phi.res, phi.args[p]) for phi in b.phis if phi.res != phi.args[p]]
            pb.instrs.extend(_sequentialise(copies, counter, fresh_prefix))
        b.phis = []


def _sequentialise(copies: List[tuple], counter: List[int], prefix: str) -> List[TACInstr]:
    """Order a set of simultaneous copies `dst <- src` so each reads the value
    it should. A cycle (a <- b, b <- a) is broken with one temporary."""
    out: List[TACInstr] = []
    pending = list(copies)
    while pending:
        progressed = False
        for i, (d, s) in enumerate(pending):
            if all(d != s2 for j, (d2, s2) in enumerate(pending) if j != i):
                out.append(_copy(d, s))
                pending.pop(i)
                progressed = True
                break
        if not progressed:
            d0 = pending[0][0]
            counter[0] += 1
            tmp = f"{prefix}${counter[0]}"
            out.append(_copy(tmp, d0))
            pending = [(d, tmp if s == d0 else s) for d, s in pending]
    return out


def _copy(dst: str, src: str) -> TACInstr:
    if is_const(src):
        return TACInstr("CONST", a=src, res=dst)
    return TACInstr("COPY", a=src, res=dst)


# ---------------------------------------------------------------------------
# clean-up of the phi-free CFG

def cleanup(cfg: CFG) -> None:
    """Thread jumps through empty blocks and merge straight-line block pairs.
    Predecessor lists are updated incrementally (a full recompute per change
    would make this quadratic on long programs)."""
    for blk in cfg.blocks.values():
        if blk.term[0] == "br" and blk.term[2] == blk.term[3]:
            blk.term = ("goto", blk.term[2])
    cfg.recompute_preds()
    removed: set = set()

    # 1. an empty block that only jumps: send its predecessors straight to its target
    changed = True
    while changed:
        changed = False
        for bid in cfg.order:
            if bid in removed or bid == cfg.entry:
                continue
            b = cfg.blocks[bid]
            if b.instrs or b.term[0] != "goto" or b.term[1] == bid:
                continue
            target = b.term[1]
            tp = cfg.blocks[target].preds
            for p in list(b.preds):
                cfg.retarget(p, bid, target)
                if p not in tp:
                    tp.append(p)
            if bid in tp:
                tp.remove(bid)
            b.preds = []
            removed.add(bid)
            changed = True
    for blk in cfg.blocks.values():
        if blk.id not in removed and blk.term[0] == "br" and blk.term[2] == blk.term[3]:
            blk.term = ("goto", blk.term[2])

    # 2. merge B into its only predecessor P when P jumps only to B
    changed = True
    while changed:
        changed = False
        for bid in cfg.order:
            if bid in removed or bid == cfg.entry:
                continue
            b = cfg.blocks[bid]
            if len(b.preds) != 1:
                continue
            pid = b.preds[0]
            pb = cfg.blocks[pid]
            if pid == bid or pid in removed or pb.term[0] != "goto" or pb.term[1] != bid:
                continue
            pb.instrs.extend(b.instrs)
            pb.term = b.term
            pb.term_span = b.term_span
            for sid in b.succs():
                sp = cfg.blocks[sid].preds
                if pid in sp:
                    sp.remove(bid)
                else:
                    sp[sp.index(bid)] = pid
            b.preds = []
            removed.add(bid)
            changed = True

    if removed:
        for bid in removed:
            del cfg.blocks[bid]
        cfg.order = [b for b in cfg.order if b not in removed]
    cfg.remove_unreachable()
    cfg.recompute_preds()


# ---------------------------------------------------------------------------
# convenience

def to_ssa(fn: TACFunction, globals_: Set[str]):
    """-> (cfg, ssa_names) for one function, or raises SSABail."""
    cfg = build_cfg(fn)
    names = construct(cfg, fn.params, globals_)
    return cfg, names


def verify_ssa(cfg: CFG, params: List[str], ssa_names: Set[str]) -> List[str]:
    """Check the defining properties of SSA; returns a list of problems.

      1. every SSA name is defined exactly once;
      2. every use is dominated by its definition (a phi operand must be
         available at the end of the corresponding predecessor);
      3. each phi has exactly one operand per predecessor.
    """
    from .cfg import compute_dominators
    problems: List[str] = []
    idom, _, rpo = compute_dominators(cfg)

    def dominates(a: int, b: int) -> bool:
        while True:
            if a == b:
                return True
            if b == cfg.entry or b not in idom:
                return False
            b = idom[b]

    where: Dict[str, tuple] = {p: (cfg.entry, -1) for p in params if p in ssa_names}
    for bid in rpo:
        b = cfg.blocks[bid]
        for phi in b.phis:
            if phi.res in where:
                problems.append(f"{phi.res} defined twice")
            where[phi.res] = (bid, -1)
        for i, ins in enumerate(b.instrs):
            d = def_of(ins)
            if d in ssa_names:
                if d in where:
                    problems.append(f"{d} defined twice")
                where[d] = (bid, i)
    for bid in rpo:
        b = cfg.blocks[bid]
        if sorted(b.preds) != sorted(set(b.preds)):
            problems.append(f"{b.name}: duplicate predecessors")
        for phi in b.phis:
            if set(phi.args) != set(p for p in b.preds if p in idom):
                problems.append(f"{b.name}: phi {phi.res} operands {sorted(phi.args)} != preds {sorted(b.preds)}")
            for p, v in phi.args.items():
                if v in ssa_names and v in where and not dominates(where[v][0], p):
                    problems.append(f"{b.name}: phi operand {v} does not dominate edge from block {p}")
        for i, ins in enumerate(b.instrs):
            for o in operands(ins):
                if o in ssa_names:
                    if o not in where:
                        problems.append(f"{o} used but never defined")
                    else:
                        db, di = where[o]
                        if db == bid and di >= i:
                            problems.append(f"{o} used before its definition in {b.name}")
                        elif db != bid and not dominates(db, bid):
                            problems.append(f"{o} in {b.name} not dominated by its definition")
        for o in term_operands(b.term):
            if o in ssa_names and (o not in where or not dominates(where[o][0], bid)):
                problems.append(f"{o} in terminator of {b.name} not dominated by its definition")
    return problems
