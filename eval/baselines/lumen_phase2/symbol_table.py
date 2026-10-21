"""
Module 2: Scoped Symbol Table.

Scope-chained hash tables for insertion, lookup and scope management.
Doubles as the candidate source for identifier "did you mean" repair
suggestions (Module 3's repair engine draws its candidates from here).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from .span import Span


@dataclass
class Symbol:
    name: str
    type_name: str            # "int", "float", "bool", "string", "int[]", or "Error"
    kind: str                 # "var" | "param" | "func"
    span: Span
    param_types: Optional[List[str]] = None   # only for kind == "func"
    return_type: Optional[str] = None         # only for kind == "func"


class Scope:
    def __init__(self, parent: Optional["Scope"] = None, label: str = ""):
        self.parent = parent
        self.label = label
        self.symbols: Dict[str, Symbol] = {}

    def declare(self, sym: Symbol) -> bool:
        """Returns False if `sym.name` is already declared in *this* scope
        (a redeclaration) — the caller reports that as a diagnostic."""
        if sym.name in self.symbols:
            return False
        self.symbols[sym.name] = sym
        return True

    def lookup_local(self, name: str) -> Optional[Symbol]:
        return self.symbols.get(name)


class SymbolTable:
    """A stack of chained scopes: global -> function -> block -> block ...
    Lookup walks outward from the innermost scope, which is what gives
    Lumen lexical (not dynamic) scoping."""

    def __init__(self):
        self.global_scope = Scope(label="global")
        self.current = self.global_scope

    def push_scope(self, label: str = "") -> Scope:
        self.current = Scope(parent=self.current, label=label)
        return self.current

    def pop_scope(self):
        assert self.current.parent is not None, "cannot pop the global scope"
        self.current = self.current.parent

    def declare(self, sym: Symbol) -> bool:
        return self.current.declare(sym)

    def lookup(self, name: str) -> Optional[Symbol]:
        scope = self.current
        while scope is not None:
            sym = scope.lookup_local(name)
            if sym is not None:
                return sym
            scope = scope.parent
        return None

    def lookup_function(self, name: str) -> Optional[Symbol]:
        """Functions live in the global scope only (no nested function decls
        in this language subset), so this always checks the global scope."""
        sym = self.global_scope.lookup_local(name)
        return sym if sym is not None and sym.kind == "func" else None

    def visible_names(self) -> List[str]:
        """All identifier names visible from the current scope — the
        candidate pool for edit-distance 'did you mean' suggestions."""
        names = []
        scope = self.current
        while scope is not None:
            names.extend(n for n, s in scope.symbols.items() if s.kind != "func")
            scope = scope.parent
        return names
