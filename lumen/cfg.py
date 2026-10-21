"""
Control-flow graph over three-address code.

A TAC function is a flat list of instructions with labels and jumps. The
optimiser works on a *control-flow graph* (CFG) instead: basic blocks - maximal
straight-line runs with one entry and one exit - connected by edges.

Each block keeps its ordinary instructions in `instrs` and its control transfer
in a separate `term` tuple, so passes never have to reason about jumps hiding
inside the instruction list:

    ("goto", target_block)
    ("br",   cond_operand, false_block, true_block)    # IF_FALSE cond -> false_block
    ("ret",  operand_or_None)

This module provides the graph itself plus the classic analyses SSA needs:
reverse post-order, immediate dominators (the Cooper-Harvey-Kennedy iterative
algorithm), the dominator tree and dominance frontiers (Cytron et al.).
`linearize` turns a CFG back into flat TAC.
"""

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Set

from .tac import TACFunction, TACInstr


@dataclass
class Phi:
    """res = phi(pred_block -> operand, ...): the value depends on which edge
    control arrived by."""
    res: str
    args: Dict[int, str] = field(default_factory=dict)
    var: str = ""          # the source variable this phi merges (set during SSA construction)


@dataclass
class Block:
    id: int
    label: Optional[str] = None
    instrs: List[TACInstr] = field(default_factory=list)
    phis: List[Phi] = field(default_factory=list)
    term: tuple = ("ret", None)
    preds: List[int] = field(default_factory=list)
    term_span: object = None       # source span of the original jump, kept for run-time errors

    def succs(self) -> List[int]:
        t = self.term
        if t[0] == "goto":
            return [t[1]]
        if t[0] == "br":
            return [t[2], t[3]]
        return []

    @property
    def name(self) -> str:
        return self.label or f"B{self.id}"


class CFG:
    def __init__(self):
        self.blocks: Dict[int, Block] = {}
        self.order: List[int] = []          # layout order (used by linearize)
        self.entry: int = 0
        self._next_id = 0

    def new_block(self, after: Optional[int] = None) -> Block:
        b = Block(self._next_id)
        self._next_id += 1
        self.blocks[b.id] = b
        if after is None or after not in self.order:
            self.order.append(b.id)
        else:
            self.order.insert(self.order.index(after) + 1, b.id)
        return b

    def remove_block(self, bid: int):
        del self.blocks[bid]
        self.order.remove(bid)

    def recompute_preds(self):
        for b in self.blocks.values():
            b.preds = []
        for bid in self.order:
            for s in self.blocks[bid].succs():
                if bid not in self.blocks[s].preds:
                    self.blocks[s].preds.append(bid)

    def retarget(self, bid: int, old: int, new: int):
        """Make block `bid` jump to `new` wherever it jumped to `old`."""
        b = self.blocks[bid]
        t = b.term
        if t[0] == "goto" and t[1] == old:
            b.term = ("goto", new)
        elif t[0] == "br":
            b.term = ("br", t[1], new if t[2] == old else t[2], new if t[3] == old else t[3])

    # ---- traversal -------------------------------------------------------
    def rpo(self) -> List[int]:
        """Reverse post-order from the entry (iterative: programs can be long)."""
        seen: Set[int] = set()
        post: List[int] = []
        stack = [(self.entry, iter(self.blocks[self.entry].succs()))]
        seen.add(self.entry)
        while stack:
            bid, it = stack[-1]
            for s in it:
                if s not in seen:
                    seen.add(s)
                    stack.append((s, iter(self.blocks[s].succs())))
                    break
            else:
                post.append(bid)
                stack.pop()
        post.reverse()
        return post

    def remove_unreachable(self) -> int:
        reach = set(self.rpo())
        dead = [b for b in self.order if b not in reach]
        for d in dead:
            for s in self.blocks[d].succs():
                if s in reach:
                    for phi in self.blocks[s].phis:
                        phi.args.pop(d, None)
            self.remove_block(d)
        if dead:
            self.recompute_preds()
        return len(dead)

    def instruction_count(self) -> int:
        return sum(len(b.instrs) + len(b.phis) + 1 for b in self.blocks.values())


# ---------------------------------------------------------------------------
# construction

def build_cfg(fn: TACFunction) -> CFG:
    instrs = [replace(i) for i in fn.instrs]            # never mutate the caller's TAC
    if not instrs or instrs[-1].op not in ("RETURN", "GOTO"):
        instrs.append(TACInstr("RETURN", a=None, span=instrs[-1].span if instrs else None))
    n = len(instrs)
    leader = {0}
    for i, ins in enumerate(instrs):
        if ins.op == "LABEL":
            leader.add(i)
        if ins.op in ("GOTO", "IF_FALSE", "RETURN") and i + 1 < n:
            leader.add(i + 1)
    starts = sorted(leader)

    cfg = CFG()
    # a synthetic entry block guarantees the entry has no predecessors, so phi
    # functions never need to be placed "before" it
    entry = cfg.new_block()
    spans = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else n
        b = cfg.new_block()
        spans.append((b, st, end))
    label_block: Dict[str, int] = {}
    for b, st, end in spans:
        if instrs[st].op == "LABEL":
            b.label = instrs[st].res
            label_block[b.label] = b.id

    for k, (b, st, end) in enumerate(spans):
        body = instrs[st:end]
        if body and body[0].op == "LABEL":
            body = body[1:]
        nxt = spans[k + 1][0].id if k + 1 < len(spans) else None
        term = None
        if body and body[-1].op in ("GOTO", "IF_FALSE", "RETURN"):
            last = body.pop()
            if last.op == "GOTO":
                term = ("goto", label_block[last.a])
            elif last.op == "IF_FALSE":
                term = ("br", last.a, label_block[last.b], nxt)
            else:
                term = ("ret", last.a)
            b.instrs = body
            b.term = term
            b.term_span = last.span
        else:
            b.instrs = body
            b.term = ("goto", nxt) if nxt is not None else ("ret", None)
    entry.term = ("goto", spans[0][0].id)
    cfg.entry = entry.id
    cfg.recompute_preds()
    cfg.remove_unreachable()
    _normalise_branches(cfg)
    _split_critical_edges(cfg)
    return cfg


def _normalise_branches(cfg: CFG):
    for b in cfg.blocks.values():
        if b.term[0] == "br" and b.term[2] == b.term[3]:
            b.term = ("goto", b.term[2])


def _split_critical_edges(cfg: CFG):
    """An edge from a block with several successors to a block with several
    predecessors is *critical*. Splitting it gives the SSA destruction pass a
    place to put copies that belongs to that one edge only."""
    cfg.recompute_preds()
    for bid in list(cfg.order):
        b = cfg.blocks[bid]
        if b.term[0] != "br":
            continue
        for s in set(b.succs()):
            if len(cfg.blocks[s].preds) > 1:
                mid = cfg.new_block(after=bid)
                mid.term = ("goto", s)
                cfg.retarget(bid, s, mid.id)
    cfg.recompute_preds()


# ---------------------------------------------------------------------------
# dominators

def compute_dominators(cfg: CFG):
    """-> (idom, children, rpo). Cooper, Harvey & Kennedy, "A Simple, Fast
    Dominance Algorithm"."""
    rpo = cfg.rpo()
    index = {b: i for i, b in enumerate(rpo)}
    idom: Dict[int, int] = {cfg.entry: cfg.entry}

    def intersect(a: int, b: int) -> int:
        while a != b:
            while index[a] > index[b]:
                a = idom[a]
            while index[b] > index[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for b in rpo[1:]:
            new = None
            for p in cfg.blocks[b].preds:
                if p in idom:
                    new = p if new is None else intersect(p, new)
            if new is not None and idom.get(b) != new:
                idom[b] = new
                changed = True
    children: Dict[int, List[int]] = {b: [] for b in rpo}
    for b in rpo[1:]:
        children[idom[b]].append(b)
    return idom, children, rpo


def dominance_frontiers(cfg: CFG, idom: Dict[int, int]) -> Dict[int, Set[int]]:
    df: Dict[int, Set[int]] = {b: set() for b in idom}
    for b in idom:
        preds = [p for p in cfg.blocks[b].preds if p in idom]
        if len(preds) >= 2:
            for p in preds:
                runner = p
                while runner != idom[b]:
                    df[runner].add(b)
                    runner = idom[runner]
    return df


# ---------------------------------------------------------------------------
# back to flat TAC

def linearize(cfg: CFG) -> List[TACInstr]:
    """Flatten a (phi-free) CFG into TAC. Fall-through is used wherever the
    target is the next block, so no redundant gotos are emitted."""
    order = [b for b in cfg.order if b in cfg.blocks]
    out: List[TACInstr] = []
    needed: Set[int] = set()
    for i, bid in enumerate(order):
        nxt = order[i + 1] if i + 1 < len(order) else None
        t = cfg.blocks[bid].term
        if t[0] == "goto" and t[1] != nxt:
            needed.add(t[1])
        elif t[0] == "br":
            needed.add(t[2])
            if t[3] != nxt:
                needed.add(t[3])
    for i, bid in enumerate(order):
        b = cfg.blocks[bid]
        nxt = order[i + 1] if i + 1 < len(order) else None
        if bid in needed:
            out.append(TACInstr("LABEL", res=b.name))
        out.extend(b.instrs)
        t = b.term
        if t[0] == "goto":
            if t[1] != nxt:
                out.append(TACInstr("GOTO", a=cfg.blocks[t[1]].name, span=b.term_span))
        elif t[0] == "br":
            out.append(TACInstr("IF_FALSE", a=t[1], b=cfg.blocks[t[2]].name, span=b.term_span))
            if t[3] != nxt:
                out.append(TACInstr("GOTO", a=cfg.blocks[t[3]].name, span=b.term_span))
        else:
            out.append(TACInstr("RETURN", a=t[1], span=b.term_span))
    return out


def format_cfg(cfg: CFG) -> str:
    lines = []
    for bid in cfg.order:
        b = cfg.blocks[bid]
        preds = ", ".join(cfg.blocks[p].name for p in b.preds)
        lines.append(f"{b.name}:" + (f"    ; preds: {preds}" if preds else "    ; entry"))
        for phi in b.phis:
            args = ", ".join(f"{cfg.blocks[p].name}: {v}" for p, v in sorted(phi.args.items()))
            lines.append(f"    {phi.res} = phi({args})")
        for ins in b.instrs:
            lines.append(str(ins))
        t = b.term
        if t[0] == "goto":
            lines.append(f"    goto {cfg.blocks[t[1]].name}")
        elif t[0] == "br":
            lines.append(f"    if {t[1]} goto {cfg.blocks[t[3]].name} else {cfg.blocks[t[2]].name}")
        else:
            lines.append(f"    return {t[1]}" if t[1] is not None else "    return")
    return "\n".join(lines)
