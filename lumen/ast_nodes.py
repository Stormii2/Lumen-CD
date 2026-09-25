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


@dataclass
class Literal(Node):
    value: str
    kind: str  # "number" | "string" | "bool"


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node


@dataclass
class UnaryOp(Node):
    op: str
    operand: Node


@dataclass
class Call(Node):
    name: str
    args: List[Node] = field(default_factory=list)


@dataclass
class ArrayLiteral(Node):
    elements: List[Node] = field(default_factory=list)


@dataclass
class Index(Node):
    array: Node
    index: Node


@dataclass
class ErrorExpr(Node):
    pass


# ---- statements ---------------------------------------------------------

@dataclass
class VarDecl(Node):
    type_name: str
    name: str
    value: Optional[Node]
    name_span: Optional[Span] = None


@dataclass
class Assignment(Node):
    name: str
    value: Optional[Node]
    index: Optional[Node] = None  # set when assigning into arr[i] = ...


@dataclass
class ExprStmt(Node):
    expr: Node


@dataclass
class Block(Node):
    statements: List[Node] = field(default_factory=list)


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


@dataclass
class Param(Node):
    type_name: str
    name: str


@dataclass
class FuncDecl(Node):
    name: str
    return_type: str
    params: List[Param] = field(default_factory=list)
    body: Block = None


@dataclass
class ErrorStmt(Node):
    pass
