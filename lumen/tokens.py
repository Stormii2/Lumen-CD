from dataclasses import dataclass
from enum import Enum, auto


class T(Enum):
    # literals
    NUMBER = auto()
    STRING = auto()
    TRUE = auto()
    FALSE = auto()
    IDENT = auto()
    # type keywords
    INT = auto()
    FLOAT = auto()
    BOOL = auto()
    STRING_KW = auto()
    # operators / punctuation
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    EQUAL = auto()
    SEMI = auto()
    LPAREN = auto()
    RPAREN = auto()
    # special
    ERROR = auto()
    EOF = auto()


KEYWORDS = {
    "int": T.INT,
    "float": T.FLOAT,
    "bool": T.BOOL,
    "string": T.STRING_KW,
    "true": T.TRUE,
    "false": T.FALSE,
}

TYPE_KEYWORDS = {"int", "float", "bool", "string"}


@dataclass
class Token:
    kind: T
    text: str
    start: int
    end: int
