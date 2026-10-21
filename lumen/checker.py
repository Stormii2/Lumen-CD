"""
Module 2 (continued): Static Type Checker with an Error-Poisoned Type Lattice.

`ERROR` is the bottom element of the type lattice: any operation touching it
silently yields `ERROR` again instead of raising a *new* diagnostic. One real
semantic fault (an undeclared variable, say) is reported exactly once -
everything downstream that depends on it is poisoned quietly.

The checker also

  * resolves scopes and gives every variable a unique storage name (`uid`),
    so the code generator handles shadowing and globals correctly,
  * annotates the AST with static types (`ty`), so the generator can insert
    int -> float conversions exactly where the language widens,
  * validates function arity / argument types and return paths,
  * and attaches ranked, classified repair suggestions (repair.py).

Poisoning strength is configurable (config.POISON_MODES) so the evaluation
harness can measure what poisoning is worth.
"""

from typing import List, Optional, Set
from .span import Span, Diagnostic, Label
from .symbol_table import SymbolTable, Symbol
from .suggest import rank_candidates
from .config import RecoveryConfig, DEFAULT
from . import repair
from . import ast_nodes as ast

ERROR = "Error"          # the lattice's bottom element
EMPTY_ARRAY = "Any[]"    # type of the literal [] before it meets a declared type


def _is_numeric(t: str) -> bool:
    return t in ("int", "float")


def _is_array(t: str) -> bool:
    return isinstance(t, str) and t.endswith("[]")


def _elem_type(t: str) -> str:
    return t[:-2] if _is_array(t) and t != EMPTY_ARRAY else ERROR


def _show(t: str) -> str:
    return "an empty array literal" if t == EMPTY_ARRAY else f"'{t}'"


class TypeChecker:
    def __init__(self, source: str, config: Optional[RecoveryConfig] = None):
        self.src = source
        self.cfg = config or DEFAULT
        self.symtab = SymbolTable()
        self.diagnostics: List[Diagnostic] = []
        self._current_return_type: Optional[str] = None
        self._poisoned_count = 0  # how many operations were silently poisoned (for metrics)
        self._lost_decls: list = []   # scopes holding a declaration whose name the parser lost
        self._reserved_globals: Set[str] = set()
        self._frame_uids: Set[str] = set()
        self._register_builtins()

    # ---- helpers ---------------------------------------------------------
    def _bad(self, t: Optional[str]) -> bool:
        """Is `t` a poisoned type *that should suppress diagnostics*?
        In poison mode "none" the ERROR type is not treated as bottom."""
        return t == ERROR and self.cfg.poison != "none"

    def _err(self, code: str, message: str, span: Span, **kw) -> None:
        self.diagnostics.append(Diagnostic(code=code, message=message, span=span, **kw))

    def _register_builtins(self):
        # `print(...)` is the one built-in: variadic (param_types=None means
        # "don't arity/type-check"), so programs have an observable side effect.
        self.symtab.declare(Symbol("print", "void", "func", Span(0, 0),
                                    param_types=None, return_type="void"))

    def _fresh_uid(self, name: str, is_global: bool) -> str:
        if is_global:
            uid = name
        else:
            uid, k = name, 0
            while uid in self._frame_uids or uid in self._reserved_globals:
                k += 1
                uid = f"{name}_{k}"
        self._frame_uids.add(uid)
        return uid

    def _declare(self, name: str, type_name: str, kind: str, span: Span) -> Optional[Symbol]:
        at_global = self.symtab.current is self.symtab.global_scope and kind == "var"
        sym = Symbol(name, type_name, kind, span,
                      uid=self._fresh_uid(name, at_global), is_global=at_global)
        return sym if self.symtab.declare(sym) else None

    # ---- entry point -----------------------------------------------------
    def check_program(self, items: List[ast.Node]):
        self._reserved_globals = {i.name for i in items if isinstance(i, ast.VarDecl)}

        # Pass 1: register every function signature first, so calls can
        # forward-reference functions declared later in the file (and so
        # recursive calls resolve during pass 2).
        for item in items:
            if isinstance(item, ast.FuncDecl):
                self._register_function(item)

        # Pass 2: check function bodies and top-level statements.
        self._check_stmts([i for i in items], top_level=True)

    def _check_stmts(self, stmts: List[ast.Node], top_level: bool = False):
        prev = None
        for stmt in stmts:
            if (isinstance(prev, ast.Return) and not isinstance(stmt, (ast.ErrorStmt, ast.FuncDecl))
                    and not top_level):
                self._err("W0002", "unreachable code after 'return'", stmt.span,
                          severity="warning",
                          help="this statement can never run; remove it or move the 'return'")
            if isinstance(stmt, ast.FuncDecl):
                self._check_function_body(stmt)
            else:
                self._check_stmt(stmt)
            prev = stmt

    # ---- function registration & checking --------------------------------
    def _register_function(self, decl: ast.FuncDecl):
        param_types = [p.type_name for p in decl.params]
        name_span = decl.name_span or Span(decl.span.start, decl.span.start + len(decl.name))
        sym = Symbol(decl.name, decl.return_type, "func", name_span,
                      param_types=param_types, return_type=decl.return_type,
                      param_names=[p.name for p in decl.params])
        if not self.symtab.declare(sym):
            existing = self.symtab.global_scope.lookup_local(decl.name)
            self._err("E0300", f"function '{decl.name}' is already declared in this scope",
                      name_span,
                      labels=[Label(existing.span, "first declared here")] if existing else [])

    def _check_function_body(self, decl: ast.FuncDecl):
        self.symtab.push_scope(label=f"func:{decl.name}")
        prev_return_type = self._current_return_type
        prev_uids = self._frame_uids
        self._current_return_type = decl.return_type
        self._frame_uids = set()

        seen_params = set()
        for p in decl.params:
            if p.name in seen_params:
                self._err("E0301", f"duplicate parameter name '{p.name}'",
                          Span(p.span.start, p.span.start + len(p.name)))
            seen_params.add(p.name)
            ptype = p.type_name
            if ptype == "void" or ptype == "void[]":
                self._err("E0312", f"parameter '{p.name}' cannot have type 'void'", p.span)
                ptype = ERROR
            sym = self._declare(p.name, ptype, "param", p.span)
            p.uid = sym.uid if sym else p.name

        if decl.body:
            self._check_stmts(decl.body.statements)

            if (decl.return_type not in ("void", ERROR)
                    and not decl.body.recovered
                    and not self._always_returns(decl.body)):
                name_span = decl.name_span or Span(decl.span.start, decl.span.start + len(decl.name))
                self._err("E0310", f"function '{decl.name}' does not return a value on every path",
                          name_span,
                          help=f"declared return type is '{decl.return_type}'; add a 'return' on every branch")

        self._frame_uids = prev_uids
        self._current_return_type = prev_return_type
        self.symtab.pop_scope()

    def _always_returns(self, node: ast.Node) -> bool:
        """Conservative return-path check: a block/if 'always returns' only
        if every reachable path ends in a Return statement."""
        if isinstance(node, ast.Return):
            return True
        if isinstance(node, ast.Block):
            return any(self._always_returns(s) for s in node.statements)
        if isinstance(node, ast.If):
            if node.else_branch is None:
                return False  # no else => the false path falls through
            return self._always_returns(node.then_branch) and self._always_returns(node.else_branch)
        return False

    # ---- statements ------------------------------------------------------
    def _check_stmt(self, stmt: ast.Node):
        if self.cfg.mute_damaged and getattr(stmt, "damaged", False):
            # the parser already reported a fault inside this statement; check
            # it (so declarations still take effect) but drop what it yields
            mark = len(self.diagnostics)
            self._check_stmt_inner(stmt)
            del self.diagnostics[mark:]
            return
        self._check_stmt_inner(stmt)

    def _check_stmt_inner(self, stmt: ast.Node):
        if isinstance(stmt, ast.VarDecl):
            self._check_var_decl(stmt)
        elif isinstance(stmt, ast.Assignment):
            self._check_assignment(stmt)
        elif isinstance(stmt, ast.ExprStmt):
            self._check_expr(stmt.expr)
        elif isinstance(stmt, ast.Block):
            self.symtab.push_scope(label="block")
            self._check_stmts(stmt.statements)
            self.symtab.pop_scope()
        elif isinstance(stmt, ast.If):
            self._check_condition(stmt.condition, "if")
            self._check_stmt(stmt.then_branch)
            if stmt.else_branch is not None:
                self._check_stmt(stmt.else_branch)
        elif isinstance(stmt, ast.While):
            self._check_condition(stmt.condition, "while")
            if stmt.body:
                self._check_stmt(stmt.body)
        elif isinstance(stmt, ast.Return):
            self._check_return(stmt)
        elif isinstance(stmt, ast.ErrorStmt):
            # Already diagnosed by the parser. If the broken text was a
            # declaration, still declare its name - poisoned - so later uses
            # are silent instead of 'undeclared'.
            if stmt.decl_name:
                self._declare(stmt.decl_name, ERROR, "var", stmt.decl_span or stmt.span)
            elif stmt.lost_decl and self.cfg.infer_lost_decl:
                self._lost_decls.append(self.symtab.current)
        elif isinstance(stmt, ast.FuncDecl):
            pass  # nested functions are rejected by the parser (E0126)

    def _check_condition(self, cond: ast.Node, kind: str):
        cond_t = self._check_expr(cond)
        if cond_t != "bool" and not self._bad(cond_t):
            sug = None
            if cond_t == "int":
                sug = repair.suggest_condition(self.src[cond.span.start:cond.span.end], cond.span)
            self._err("E0307", f"{kind}-condition must be 'bool', found {_show(cond_t)}",
                      cond.span, suggestion=sug,
                      help="Lumen has no implicit int-to-bool conversion" if cond_t == "int" else None)

    def _check_var_decl(self, stmt: ast.VarDecl):
        hint = stmt.type_name if stmt.type_name != ERROR else None
        value_t = self._check_expr(stmt.value, hint) if stmt.value is not None else stmt.type_name
        name_span = stmt.name_span or Span(stmt.span.start, stmt.span.start + len(stmt.name))

        declared_type = stmt.type_name
        if stmt.type_name in ("void", "void[]"):
            self._err("E0312", f"variable '{stmt.name}' cannot have type 'void'", name_span,
                      help="'void' is only valid as a function's return type")
            declared_type = ERROR
        # Poisoning propagates into the symbol table itself ("full" mode): if
        # the initialiser was already poisoned (its root cause reported once,
        # upstream), the variable's *own* static type becomes Error too, so
        # every later use is silently poisoned instead of re-diagnosed.
        elif self._bad(value_t) and self.cfg.poison == "full":
            declared_type = ERROR

        sym = self._declare(stmt.name, declared_type, "var", name_span)
        if sym is None:
            existing = self.symtab.current.lookup_local(stmt.name)
            sug = None
            if existing is not None and existing.type_name == stmt.type_name:
                sug = repair.suggest_assign_instead(stmt.span.start, name_span.start)
            self._err("E0300", f"variable '{stmt.name}' is already declared in this scope",
                      name_span,
                      labels=[Label(existing.span, "first declared here")] if existing else [],
                      suggestion=sug)
            stmt.uid = existing.uid if existing else stmt.name
            return
        stmt.uid = sym.uid

        if declared_type == ERROR and stmt.type_name in ("void", "void[]"):
            return
        if self._bad(value_t) or self._bad(stmt.type_name):
            self._poisoned_count += 1
            return  # poisoned - the root cause was already reported
        if not self._assignable(stmt.type_name, value_t):
            self._err("E0302",
                      f"cannot initialise '{stmt.type_name}' variable '{stmt.name}' "
                      f"with a value of type {_show(value_t)}",
                      stmt.value.span if stmt.value else stmt.span)

    def _check_assignment(self, stmt: ast.Assignment):
        sym = self.symtab.lookup(stmt.name)
        # a symbol's type is the best hint for the right-hand side
        hint = None
        if sym is not None and sym.kind != "func" and sym.type_name != ERROR:
            hint = _elem_type(sym.type_name) if stmt.index is not None else sym.type_name
        value_t = self._check_expr(stmt.value, hint) if stmt.value is not None else ERROR

        if sym is None:
            self._unknown_variable(stmt.name,
                                   Span(stmt.span.start, stmt.span.start + len(stmt.name)),
                                   value_t if not self._bad(value_t) else None)
            if stmt.index is not None:
                self._check_expr(stmt.index)
            return
        if sym.kind == "func":
            self._err("E0415", f"'{stmt.name}' is a function and cannot be assigned to",
                      Span(stmt.span.start, stmt.span.start + len(stmt.name)))
            self._poisoned_count += 1
            return

        stmt.uid = sym.uid
        target_t = sym.type_name
        if stmt.index is not None:
            idx_t = self._check_expr(stmt.index)
            if not _is_array(sym.type_name):
                if not self._bad(sym.type_name):
                    self._err("E0306",
                              f"'{stmt.name}' has type '{sym.type_name}', which cannot be indexed",
                              stmt.span)
                self._poisoned_count += 1
                return
            if idx_t != "int" and not self._bad(idx_t):
                self._err("E0303", f"array index must be 'int', found {_show(idx_t)}",
                          stmt.index.span)
            target_t = _elem_type(sym.type_name)
        stmt.target_ty = target_t

        if self._bad(value_t) or self._bad(target_t):
            self._poisoned_count += 1
            return
        if not self._assignable(target_t, value_t):
            self._err("E0302",
                      f"cannot assign a value of type {_show(value_t)} to '{stmt.name}' (type '{target_t}')",
                      stmt.value.span if stmt.value else stmt.span)

    def _check_return(self, stmt: ast.Return):
        stmt.ret_ty = self._current_return_type
        hint = self._current_return_type if self._current_return_type not in (None, "void", ERROR) else None
        value_t = self._check_expr(stmt.value, hint) if stmt.value is not None else "void"
        if self._current_return_type is None:
            self._err("E0309", "'return' used outside a function", stmt.span)
            return
        if self._bad(value_t) or self._current_return_type == ERROR:
            self._poisoned_count += 1
            return
        if self._current_return_type == "void" and stmt.value is not None:
            self._err("E0308", "function returns 'void' but a value was returned",
                      stmt.value.span)
        elif (self._current_return_type != "void"
              and not self._assignable(self._current_return_type, value_t)):
            self._err("E0308",
                      f"expected a return value of type '{self._current_return_type}', "
                      f"found {_show(value_t)}",
                      stmt.value.span if stmt.value else stmt.span)

    # ---- unknown names ----------------------------------------------------
    def _unknown_variable(self, name: str, span: Span, hint_type: Optional[str]) -> str:
        syms = self.symtab.visible_symbols()
        preferred = ({s.name: s.type_name == hint_type for s in syms}
                     if hint_type else None)
        help_text, sug = repair.suggest_name(name, span, [s.name for s in syms], preferred)
        if sug is None and self._lost_decls:
            # a declaration earlier lost its name; this unknown name, which
            # resembles nothing in scope, is most plausibly that variable
            scope = self._lost_decls.pop(0)
            sym = Symbol(name, ERROR, "var", span, uid=self._fresh_uid(name, scope is self.symtab.global_scope),
                         is_global=scope is self.symtab.global_scope)
            scope.declare(sym)
            return ERROR
        self._err("E0412", f"cannot find variable '{name}' in this scope", span,
                  help=help_text, suggestion=sug)
        self._poisoned_count += 1
        return ERROR

    # ---- expressions -------------------------------------------------
    def _check_expr(self, expr: ast.Node, hint: Optional[str] = None) -> str:
        t = self._check_expr_inner(expr, hint)
        expr.ty = t
        return t

    def _check_expr_inner(self, expr: ast.Node, hint: Optional[str]) -> str:
        if isinstance(expr, ast.ErrorExpr):
            self._poisoned_count += 1
            return ERROR

        if isinstance(expr, ast.Literal):
            if expr.kind == "number":
                return "float" if "." in expr.value else "int"
            if expr.kind == "string":
                return "string"
            if expr.kind == "bool":
                return "bool"
            return ERROR

        if isinstance(expr, ast.Ident):
            sym = self.symtab.lookup(expr.name)
            if sym is None:
                return self._unknown_variable(expr.name, expr.span, hint)
            if sym.kind == "func":
                self._err("E0415", f"'{expr.name}' is a function and cannot be used as a value",
                          expr.span, help=f"to call it, write '{expr.name}(...)'")
                self._poisoned_count += 1
                return ERROR
            expr.uid = sym.uid
            return sym.type_name

        if isinstance(expr, ast.UnaryOp):
            t = self._check_expr(expr.operand)
            if self._bad(t):
                self._poisoned_count += 1
                return ERROR
            if expr.op == "-" and not _is_numeric(t):
                self._err("E0303", f"unary '-' requires a numeric operand, found {_show(t)}",
                          expr.span)
                return ERROR
            if expr.op == "!" and t != "bool":
                self._err("E0303", f"unary '!' requires a 'bool' operand, found {_show(t)}",
                          expr.span)
                return ERROR
            return t

        if isinstance(expr, ast.BinOp):
            return self._check_binop(expr)

        if isinstance(expr, ast.Call):
            return self._check_call(expr)

        if isinstance(expr, ast.ArrayLiteral):
            return self._check_array_literal(expr, hint)

        if isinstance(expr, ast.Index):
            arr_t = self._check_expr(expr.array)
            idx_t = self._check_expr(expr.index)
            if self._bad(arr_t) or self._bad(idx_t):
                self._poisoned_count += 1
                return ERROR
            if arr_t == EMPTY_ARRAY:
                return ERROR
            if not _is_array(arr_t):
                self._err("E0306", f"type {_show(arr_t)} cannot be indexed", expr.span)
                return ERROR
            if idx_t != "int":
                self._err("E0303", f"array index must be 'int', found {_show(idx_t)}",
                          expr.index.span)
                return ERROR
            return _elem_type(arr_t)

        return ERROR

    def _check_binop(self, expr: ast.BinOp) -> str:
        lt = self._check_expr(expr.left)
        rt = self._check_expr(expr.right)
        if self._bad(lt) or self._bad(rt):
            self._poisoned_count += 1
            return ERROR  # poisoned - no new diagnostic
        op = expr.op

        if op in ("+", "-", "*", "/", "%"):
            if op == "+" and lt == "string" and rt == "string":
                return "string"
            if not (_is_numeric(lt) and _is_numeric(rt)):
                self._err("E0304",
                          f"operator '{op}' is not defined for types {_show(lt)} and {_show(rt)}",
                          expr.span)
                return ERROR
            if op in ("/", "%") and isinstance(expr.right, ast.Literal) \
                    and expr.right.kind == "number" and float(expr.right.value) == 0:
                self._err("W0001", "division by the constant 0", expr.right.span,
                          severity="warning",
                          note="this will stop the program with a run-time error")
            return "float" if "float" in (lt, rt) else "int"

        if op in ("<", ">", "<=", ">="):
            if not (_is_numeric(lt) and _is_numeric(rt)):
                self._err("E0304",
                          f"operator '{op}' is not defined for types {_show(lt)} and {_show(rt)}",
                          expr.span)
                return ERROR
            return "bool"

        if op in ("==", "!="):
            if lt != rt and not (_is_numeric(lt) and _is_numeric(rt)):
                self._err("E0304", f"cannot compare {_show(lt)} with {_show(rt)}", expr.span)
                return ERROR
            return "bool"

        if op in ("&&", "||"):
            if lt != "bool" or rt != "bool":
                self._err("E0304",
                          f"operator '{op}' requires 'bool' operands, found {_show(lt)} and {_show(rt)}",
                          expr.span)
                return ERROR
            return "bool"
        return ERROR

    def _check_array_literal(self, expr: ast.ArrayLiteral, hint: Optional[str]) -> str:
        elem_hint = _elem_type(hint) if hint and _is_array(hint) and hint != EMPTY_ARRAY else None
        if elem_hint == ERROR:
            elem_hint = None
        if not expr.elements:
            if elem_hint is not None:
                expr.elem_ty = elem_hint
                return hint
            return EMPTY_ARRAY
        elem_types = [self._check_expr(e, elem_hint) for e in expr.elements]
        if any(self._bad(t) for t in elem_types):
            self._poisoned_count += 1
            return ERROR
        # a declared element type wins when every element fits it (int widens to float)
        if elem_hint is not None and all(self._assignable(elem_hint, t) for t in elem_types):
            expr.elem_ty = elem_hint
            return elem_hint + "[]"
        first = elem_types[0]
        if all(t == first for t in elem_types):
            expr.elem_ty = first
            return first + "[]"
        if all(_is_numeric(t) for t in elem_types):      # mixed int/float promotes to float
            expr.elem_ty = "float"
            return "float[]"
        self._err("E0305", "array literal elements have inconsistent types", expr.span)
        return ERROR

    def _check_call(self, expr: ast.Call) -> str:
        sym = self.symtab.lookup_function(expr.name)
        hints = sym.param_types if (sym is not None and sym.param_types is not None) else []
        arg_types = [self._check_expr(a, hints[i] if i < len(hints) and hints[i] != ERROR else None)
                     for i, a in enumerate(expr.args)]

        if sym is None:
            var = self.symtab.lookup(expr.name)
            if var is not None and var.kind in ("var", "param"):
                self._err("E0414", f"'{expr.name}' is a variable, not a function", expr.span,
                          labels=[Label(var.span, "declared here as a variable")])
                self._poisoned_count += 1
                return ERROR
            funcs = [n for n, s in self.symtab.global_scope.symbols.items() if s.kind == "func"]
            name_span = Span(expr.span.start, expr.span.start + len(expr.name))
            help_text, sug = repair.suggest_name(expr.name, name_span, funcs, what="function")
            self._err("E0413", f"cannot find function '{expr.name}'", name_span,
                      help=help_text, suggestion=sug)
            self._poisoned_count += 1
            return ERROR

        if any(self._bad(t) for t in arg_types):
            self._poisoned_count += 1
            return ERROR

        if sym.param_types is None:                       # variadic built-in (print)
            for a, t in zip(expr.args, arg_types):
                if t == "void":
                    self._err("E0313", "a 'void' call has no value to use here", a.span)
                    return ERROR
            return sym.return_type
        expr.param_tys = list(sym.param_types)

        if len(arg_types) != len(sym.param_types):
            sig = ", ".join(f"{t} {n}" for t, n in zip(sym.param_types, sym.param_names or []))
            self._err("E0311",
                      f"'{expr.name}' expects {len(sym.param_types)} argument(s), found {len(arg_types)}",
                      expr.span,
                      labels=[Label(sym.span, f"'{expr.name}' is declared here")],
                      help=f"signature: {expr.name}({sig})")
            return ERROR

        for i, (given, expected) in enumerate(zip(arg_types, sym.param_types)):
            if expected == ERROR:
                continue
            if not self._assignable(expected, given):
                self._err("E0305",
                          f"argument {i + 1} to '{expr.name}' expects '{expected}', found {_show(given)}",
                          expr.args[i].span)
                return ERROR

        return sym.return_type

    # ---- assignability ------------------------------------------------
    @staticmethod
    def _assignable(target: str, value: str) -> bool:
        if target == value:
            return True
        if target == "float" and value == "int":
            return True  # int widens to float
        if value == EMPTY_ARRAY and _is_array(target):
            return True  # [] fits any array type
        return False
