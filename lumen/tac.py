"""
Module 4 (part 1): Three-Address Code Generation.

Walks a *type-checked* (diagnostic-free) AST and lowers it into flat
three-address code: one operator and at most two operands per
instruction, with explicit temporaries and labels. This is the classic
TAC form from the Dragon Book, used here as Lumen's only intermediate
representation (no SSA / optimisation passes — those are out of scope).

Each user-defined function becomes its own flat instruction list; the
program's top-level statements are collected into a synthetic
`__main__` "function" so the interpreter has one uniform entry point.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from . import ast_nodes as ast

MAIN = "__main__"


@dataclass
class TACInstr:
    op: str
    a: Optional[str] = None
    b: Optional[str] = None
    res: Optional[str] = None

    def __str__(self):
        if self.op == "LABEL":
            return f"{self.res}:"
        if self.op == "GOTO":
            return f"    goto {self.a}"
        if self.op == "IF_FALSE":
            return f"    if_false {self.a} goto {self.b}"
        if self.op == "PARAM":
            return f"    param {self.a}"
        if self.op == "CALL":
            return f"    {self.res} = call {self.a}, {self.b}"
        if self.op == "CALL_VOID":
            return f"    call {self.a}, {self.b}"
        if self.op == "RETURN":
            return f"    return {self.a}" if self.a is not None else "    return"
        if self.op == "COPY":
            return f"    {self.res} = {self.a}"
        if self.op == "ARR_NEW":
            return f"    {self.res} = new_array[{self.a}]"
        if self.op == "ARR_GET":
            return f"    {self.res} = {self.a}[{self.b}]"
        if self.op == "ARR_SET":
            return f"    {self.res}[{self.a}] = {self.b}"
        if self.op == "CONST":
            return f"    {self.res} = {self.a}"
        if self.b is not None:
            return f"    {self.res} = {self.a} {self.op} {self.b}"
        return f"    {self.res} = {self.op} {self.a}"


@dataclass
class TACFunction:
    name: str
    params: List[str]
    instrs: List[TACInstr]


class TACGenerator:
    def __init__(self):
        self._temp_n = 0
        self._label_n = 0
        self.functions: Dict[str, TACFunction] = {}
        self._instrs: List[TACInstr] = []

    def _temp(self) -> str:
        self._temp_n += 1
        return f"t{self._temp_n}"

    def _label(self) -> str:
        self._label_n += 1
        return f"L{self._label_n}"

    def _emit(self, instr: TACInstr):
        self._instrs.append(instr)

    # ---- entry point -----------------------------------------------------
    def generate(self, items: List[ast.Node]) -> Dict[str, TACFunction]:
        main_stmts = []
        for item in items:
            if isinstance(item, ast.FuncDecl):
                self._gen_function(item)
            else:
                main_stmts.append(item)

        self._instrs = []
        for stmt in main_stmts:
            self._gen_stmt(stmt)
        self.functions[MAIN] = TACFunction(MAIN, [], self._instrs)
        return self.functions

    def _gen_function(self, decl: ast.FuncDecl):
        self._instrs = []
        for stmt in decl.body.statements:
            self._gen_stmt(stmt)
        self.functions[decl.name] = TACFunction(
            decl.name, [p.name for p in decl.params], self._instrs)

    # ---- statements --------------------------------------------------
    def _gen_stmt(self, stmt: ast.Node):
        if isinstance(stmt, ast.VarDecl):
            if isinstance(stmt.value, ast.ArrayLiteral):
                self._gen_array_init(stmt.name, stmt.value)
            else:
                val = self._gen_expr(stmt.value) if stmt.value is not None else "0"
                self._emit(TACInstr("COPY", a=val, res=stmt.name))

        elif isinstance(stmt, ast.Assignment):
            if stmt.index is not None:
                idx = self._gen_expr(stmt.index)
                val = self._gen_expr(stmt.value)
                self._emit(TACInstr("ARR_SET", a=idx, b=val, res=stmt.name))
            else:
                val = self._gen_expr(stmt.value)
                self._emit(TACInstr("COPY", a=val, res=stmt.name))

        elif isinstance(stmt, ast.ExprStmt):
            self._gen_expr(stmt.expr)

        elif isinstance(stmt, ast.Block):
            for s in stmt.statements:
                self._gen_stmt(s)

        elif isinstance(stmt, ast.If):
            cond = self._gen_expr(stmt.condition)
            l_else = self._label()
            l_end = self._label()
            self._emit(TACInstr("IF_FALSE", a=cond, b=l_else))
            self._gen_stmt(stmt.then_branch)
            self._emit(TACInstr("GOTO", a=l_end))
            self._emit(TACInstr("LABEL", res=l_else))
            if stmt.else_branch is not None:
                self._gen_stmt(stmt.else_branch)
            self._emit(TACInstr("LABEL", res=l_end))

        elif isinstance(stmt, ast.While):
            l_start = self._label()
            l_end = self._label()
            self._emit(TACInstr("LABEL", res=l_start))
            cond = self._gen_expr(stmt.condition)
            self._emit(TACInstr("IF_FALSE", a=cond, b=l_end))
            if stmt.body:
                self._gen_stmt(stmt.body)
            self._emit(TACInstr("GOTO", a=l_start))
            self._emit(TACInstr("LABEL", res=l_end))

        elif isinstance(stmt, ast.Return):
            val = self._gen_expr(stmt.value) if stmt.value is not None else None
            self._emit(TACInstr("RETURN", a=val))

        # ErrorStmt: nothing to lower — codegen only runs on diagnostic-free programs.

    def _gen_array_init(self, name: str, lit: ast.ArrayLiteral):
        n = str(len(lit.elements))
        self._emit(TACInstr("ARR_NEW", a=n, res=name))
        for i, el in enumerate(lit.elements):
            val = self._gen_expr(el)
            self._emit(TACInstr("ARR_SET", a=str(i), b=val, res=name))

    # ---- expressions -------------------------------------------------
    def _gen_expr(self, expr: ast.Node) -> str:
        if isinstance(expr, ast.Literal):
            t = self._temp()
            value = expr.value
            if expr.kind == "bool":
                value = "1" if value == "true" else "0"
            self._emit(TACInstr("CONST", a=value, res=t))
            return t

        if isinstance(expr, ast.Ident):
            return expr.name

        if isinstance(expr, ast.UnaryOp):
            val = self._gen_expr(expr.operand)
            t = self._temp()
            op = "neg" if expr.op == "-" else "not"
            self._emit(TACInstr(op, a=val, res=t))
            return t

        if isinstance(expr, ast.BinOp):
            l = self._gen_expr(expr.left)
            r = self._gen_expr(expr.right)
            t = self._temp()
            self._emit(TACInstr(expr.op, a=l, b=r, res=t))
            return t

        if isinstance(expr, ast.Call):
            arg_temps = [self._gen_expr(a) for a in expr.args]
            for at in arg_temps:
                self._emit(TACInstr("PARAM", a=at))
            t = self._temp()
            self._emit(TACInstr("CALL", a=expr.name, b=str(len(arg_temps)), res=t))
            return t

        if isinstance(expr, ast.Index):
            arr = self._gen_expr(expr.array)
            idx = self._gen_expr(expr.index)
            t = self._temp()
            self._emit(TACInstr("ARR_GET", a=arr, b=idx, res=t))
            return t

        if isinstance(expr, ast.ArrayLiteral):
            t = self._temp()
            self._emit(TACInstr("ARR_NEW", a=str(len(expr.elements)), res=t))
            for i, el in enumerate(expr.elements):
                val = self._gen_expr(el)
                self._emit(TACInstr("ARR_SET", a=str(i), b=val, res=t))
            return t

        # ErrorExpr or anything unexpected — should not reach codegen.
        t = self._temp()
        self._emit(TACInstr("CONST", a="0", res=t))
        return t


def format_program(functions: Dict[str, TACFunction]) -> str:
    lines = []
    for name, fn in functions.items():
        header = f"func {name}({', '.join(fn.params)}):" if name != MAIN else "func __main__():"
        lines.append(header)
        for instr in fn.instrs:
            lines.append(str(instr))
        lines.append("")
    return "\n".join(lines)
