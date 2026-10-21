"""
AST node dataclasses.

Every node carries a Span. Fields marked "annotation" are filled in by the
type checker (checker.py) and consumed by the TAC generator, so the
generator never has to repeat scope resolution or type inference:

  ty      - the static type of an expression (e.g. "int", "float[]")
  uid     - the unique storage name of a variable (resolves shadowing)
  depth   - nesting depth of an expression tree (parser; bounds recursion)
"""

from dataclasses import dataclass, field
from typing import Optional, List
from .span import Span


@dataclass
class Node:
    span: Span


# ---- expressions ------------------------------------------------------

@dataclass
class Ident(Node):
    name: str
    uid: Optional[str] = None       # annotation
    ty: Optional[str] = None        # annotation
    depth: int = 1


@dataclass
class Literal(Node):
    value: str
    kind: str  # "number" | "string" | "bool"
    ty: Optional[str] = None        # annotation
    depth: int = 1


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node
    ty: Optional[str] = None        # annotation
    depth: int = 1


@dataclass
class UnaryOp(Node):
    op: str
    operand: Node
    ty: Optional[str] = None        # annotation
    depth: int = 1


@dataclass
class Call(Node):
    name: str
    args: List[Node] = field(default_factory=list)
    ty: Optional[str] = None        # annotation: the call's result type
    param_tys: Optional[List[str]] = None   # annotation: callee's parameter types
    depth: int = 1


@dataclass
class ArrayLiteral(Node):
    elements: List[Node] = field(default_factory=list)
    ty: Optional[str] = None        # annotation
    elem_ty: Optional[str] = None   # annotation: element type after widening
    depth: int = 1


@dataclass
class Index(Node):
    array: Node
    index: Node
    ty: Optional[str] = None        # annotation
    depth: int = 1


@dataclass
class ErrorExpr(Node):
    ty: Optional[str] = None
    depth: int = 1


# ---- statements ---------------------------------------------------------

@dataclass
class VarDecl(Node):
    type_name: str
    name: str
    value: Optional[Node]
    name_span: Optional[Span] = None
    uid: Optional[str] = None       # annotation


@dataclass
class Assignment(Node):
    name: str
    value: Optional[Node]
    index: Optional[Node] = None  # set when assigning into arr[i] = ...
    uid: Optional[str] = None       # annotation
    target_ty: Optional[str] = None  # annotation: type of the assigned slot


@dataclass
class ExprStmt(Node):
    expr: Node


@dataclass
class Block(Node):
    statements: List[Node] = field(default_factory=list)
    recovered: bool = False      # parser had to invent/force the closing '}'


@dataclass
class If(Node):
    condition: Node
    then_branch: Block
    else_branch: Optional[Block] = None


@dataclass
class While(Node):
    condition: Node
    body: Block = None


@dataclass
class Return(Node):
    value: Optional[Node]
    ret_ty: Optional[str] = None    # annotation: the enclosing function's return type


@dataclass
class Param(Node):
    type_name: str
    name: str
    uid: Optional[str] = None       # annotation


@dataclass
class FuncDecl(Node):
    name: str
    return_type: str
    params: List[Param] = field(default_factory=list)
    body: Block = None
    name_span: Optional[Span] = None


@dataclass
class ErrorStmt(Node):
    """A statement the parser could not recover into a real node. If the
    broken text was a declaration whose *name* was still readable, the name
    is kept so the checker can declare it as a poisoned variable - otherwise
    every later use of it would be reported as 'undeclared' (a cascade)."""
    decl_name: Optional[str] = None
    decl_span: Optional[Span] = None
    lost_decl: bool = False      # a declaration whose *name* could not be read
