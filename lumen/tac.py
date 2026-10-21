"""
Module 4 (part 1): Three-Address Code Generation.

Walks a *type-checked* (error-free) AST and lowers it into flat
three-address code: one operator and at most two operands per instruction,
with explicit temporaries and labels - the classic TAC form from the Dragon
Book. The optimiser (optimizer.py) takes this form into SSA, improves it and
returns ordinary TAC, so the interpreter runs optimised and unoptimised code
the same way.

The generator relies on annotations left by the type checker:

  * variables use their unique storage name (`uid`), so a variable declared
    in an inner block never overwrites an outer variable of the same name;
  * `itof` conversions are inserted exactly where the language widens an
    `int` to a `float` (initialisers, assignments, arguments, returns);
  * `&&` and `||` short-circuit: the right operand is only evaluated when
    the left one does not already decide the result.

Each user-defined function becomes its own flat instruction list; the
program's top-level statements are collected into a synthetic `__main__`
"function" so the interpreter has one uniform entry point. Every
instruction remembers the source span it came from, so a run-time error can
be reported with a caret exactly like a compile-time one.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from .span import Span
from . import ast_nodes as ast

MAIN = "__main__"


@dataclass
class TACInstr:
    op: str
    a: Optional[str] = None
    b: Optional[str] = None
    res: Optional[str] = None
    span: Optional[Span] = None

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
    global_names: Set[str] = field(default_factory=set)   # only meaningful on __main__


class TACGenerator:
    def __init__(self):
        self._temp_n = 0
        self._label_n = 0
        self.functions: Dict[str, TACFunction] = {}
        self._instrs: List[TACInstr] = []
        self._span: Optional[Span] = None

    def _temp(self) -> str:
        self._temp_n += 1
        return f"t{self._temp_n}"

    def _label(self) -> str:
        self._label_n += 1
        return f"L{self._label_n}"

    def _emit(self, instr: TACInstr, span: Optional[Span] = None):
        instr.span = span if span is not None else self._span
        self._instrs.append(instr)

    # ---- entry point -----------------------------------------------------
    def generate(self, items: List[ast.Node]) -> Dict[str, TACFunction]:
        main_stmts = []
        for item in items:
            if isinstance(item, ast.FuncDecl):
                self._gen_function(item)
            elif isinstance(item, ast.ErrorStmt):
                continue
            else:
                main_stmts.append(item)

        self._instrs = []
        for stmt in main_stmts:
            self._gen_stmt(stmt)
        globals_ = {(s.uid or s.name) for s in main_stmts if isinstance(s, ast.VarDecl)}
        self.functions[MAIN] = TACFunction(MAIN, [], self._instrs, global_names=globals_)
        return self.functions

    def _gen_function(self, decl: ast.FuncDecl):
        self._instrs = []
        self._span = decl.span
        for stmt in decl.body.statements:
            self._gen_stmt(stmt)
        self.functions[decl.name] = TACFunction(
            decl.name, [(p.uid or p.name) for p in decl.params], self._instrs)

    # ---- conversions ---------------------------------------------------
    def _coerce(self, val: str, from_ty: Optional[str], to_ty: Optional[str]) -> str:
        """Insert the int -> float widening the type checker accepted."""
        if to_ty == "float" and from_ty == "int":
            t = self._temp()
            self._emit(TACInstr("itof", a=val, res=t))
            return t
        return val

    # ---- statements --------------------------------------------------
    def _gen_stmt(self, stmt: ast.Node):
        saved = self._span
        self._span = stmt.span
        try:
            self._gen_stmt_inner(stmt)
        finally:
            self._span = saved

    def _gen_stmt_inner(self, stmt: ast.Node):
        if isinstance(stmt, ast.VarDecl):
            uid = stmt.uid or stmt.name
            if isinstance(stmt.value, ast.ArrayLiteral):
                self._gen_array_init(uid, stmt.value)
            else:
                if stmt.value is not None:
                    val = self._gen_expr(stmt.value)
                    val = self._coerce(val, getattr(stmt.value, "ty", None), stmt.type_name)
                else:
                    val = "0"
                self._emit(TACInstr("COPY", a=val, res=uid))

        elif isinstance(stmt, ast.Assignment):
            uid = stmt.uid or stmt.name
            if stmt.index is not None:
                idx = self._gen_expr(stmt.index)
                val = self._gen_expr(stmt.value)
                val = self._coerce(val, getattr(stmt.value, "ty", None), stmt.target_ty)
                self._emit(TACInstr("ARR_SET", a=idx, b=val, res=uid))
            else:
                if isinstance(stmt.value, ast.ArrayLiteral):
                    self._gen_array_init(uid, stmt.value)
                else:
                    val = self._gen_expr(stmt.value)
                    val = self._coerce(val, getattr(stmt.value, "ty", None), stmt.target_ty)
                    self._emit(TACInstr("COPY", a=val, res=uid))

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
            if stmt.value is not None:
                val = self._gen_expr(stmt.value)
                val = self._coerce(val, getattr(stmt.value, "ty", None), stmt.ret_ty)
            else:
                val = None
            self._emit(TACInstr("RETURN", a=val))

        # ErrorStmt: nothing to lower - codegen only runs on error-free programs.

    def _gen_array_init(self, name: str, lit: ast.ArrayLiteral):
        n = str(len(lit.elements))
        self._emit(TACInstr("ARR_NEW", a=n, res=name))
        for i, el in enumerate(lit.elements):
            val = self._gen_expr(el)
            val = self._coerce(val, getattr(el, "ty", None), lit.elem_ty)
            self._emit(TACInstr("ARR_SET", a=str(i), b=val, res=name))

    # ---- expressions -------------------------------------------------
    def _gen_expr(self, expr: ast.Node) -> str:
        if isinstance(expr, ast.Literal):
            t = self._temp()
            self._emit(TACInstr("CONST", a=expr.value, res=t), expr.span)
            return t

        if isinstance(expr, ast.Ident):
            return expr.uid or expr.name

        if isinstance(expr, ast.UnaryOp):
            val = self._gen_expr(expr.operand)
            t = self._temp()
            op = "neg" if expr.op == "-" else "not"
            self._emit(TACInstr(op, a=val, res=t), expr.span)
            return t

        if isinstance(expr, ast.BinOp):
            if expr.op in ("&&", "||"):
                return self._gen_short_circuit(expr)
            l = self._gen_expr(expr.left)
            r = self._gen_expr(expr.right)
            t = self._temp()
            self._emit(TACInstr(expr.op, a=l, b=r, res=t), expr.span)
            return t

        if isinstance(expr, ast.Call):
            arg_temps = []
            for i, a in enumerate(expr.args):
                v = self._gen_expr(a)
                if expr.param_tys and i < len(expr.param_tys):
                    v = self._coerce(v, getattr(a, "ty", None), expr.param_tys[i])
                arg_temps.append(v)
            for at in arg_temps:
                self._emit(TACInstr("PARAM", a=at), expr.span)
            t = self._temp()
            self._emit(TACInstr("CALL", a=expr.name, b=str(len(arg_temps)), res=t), expr.span)
            return t

        if isinstance(expr, ast.Index):
            arr = self._gen_expr(expr.array)
            idx = self._gen_expr(expr.index)
            t = self._temp()
            self._emit(TACInstr("ARR_GET", a=arr, b=idx, res=t), expr.span)
            return t

        if isinstance(expr, ast.ArrayLiteral):
            t = self._temp()
            self._gen_array_init(t, expr)
            return t

        # ErrorExpr or anything unexpected - should not reach codegen.
        t = self._temp()
        self._emit(TACInstr("CONST", a="0", res=t))
        return t

    def _gen_short_circuit(self, expr: ast.BinOp) -> str:
        """a && b  /  a || b with short-circuit evaluation."""
        res = self._temp()
        l_end = self._label()
        left = self._gen_expr(expr.left)
        if expr.op == "&&":
            l_false = self._label()
            self._emit(TACInstr("IF_FALSE", a=left, b=l_false))
            right = self._gen_expr(expr.right)
            self._emit(TACInstr("COPY", a=right, res=res))
            self._emit(TACInstr("GOTO", a=l_end))
            self._emit(TACInstr("LABEL", res=l_false))
            self._emit(TACInstr("CONST", a="false", res=res))
        else:
            l_right = self._label()
            self._emit(TACInstr("IF_FALSE", a=left, b=l_right))
            self._emit(TACInstr("CONST", a="true", res=res))
            self._emit(TACInstr("GOTO", a=l_end))
            self._emit(TACInstr("LABEL", res=l_right))
            right = self._gen_expr(expr.right)
            self._emit(TACInstr("COPY", a=right, res=res))
        self._emit(TACInstr("LABEL", res=l_end))
        return res


def format_program(functions: Dict[str, TACFunction]) -> str:
    lines = []
    for name, fn in functions.items():
        header = f"func {name}({', '.join(fn.params)}):" if name != MAIN else "func __main__():"
        lines.append(header)
        for instr in fn.instrs:
            lines.append(str(instr))
        lines.append("")
    return "\n".join(lines)
