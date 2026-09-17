from dataclasses import dataclass
from typing import Optional
from .span import Span


@dataclass
class Node:
    span: Span


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
class ErrorExpr(Node):
    pass


@dataclass
class VarDecl(Node):
    type_name: str
    name: str
    value: Optional[Node]


@dataclass
class Assignment(Node):
    name: str
    value: Optional[Node]


@dataclass
class ExprStmt(Node):
    expr: Node


@dataclass
class ErrorStmt(Node):
    pass
