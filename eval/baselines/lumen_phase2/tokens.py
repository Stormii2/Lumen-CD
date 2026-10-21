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
    VOID = auto()
    # control-flow / declaration keywords
    IF = auto()
    ELSE = auto()
    WHILE = auto()
    RETURN = auto()
    FUNC = auto()
    # operators / punctuation
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    EQUAL = auto()
    EQEQ = auto()
    NEQ = auto()
    LT = auto()
    GT = auto()
    LE = auto()
    GE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    SEMI = auto()
    COMMA = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACE = auto()
    RBRACE = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    # special
    ERROR = auto()
    EOF = auto()


KEYWORDS = {
    "int": T.INT,
    "float": T.FLOAT,
    "bool": T.BOOL,
    "string": T.STRING_KW,
    "void": T.VOID,
    "true": T.TRUE,
    "false": T.FALSE,
    "if": T.IF,
    "else": T.ELSE,
    "while": T.WHILE,
    "return": T.RETURN,
    "func": T.FUNC,
}

TYPE_KEYWORDS = {"int", "float", "bool", "string", "void"}


@dataclass
class Token:
    kind: T
    text: str
    start: int
    end: int
