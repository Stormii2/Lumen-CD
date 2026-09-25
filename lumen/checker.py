"""
Module 2 (continued): Static Type Checker with an Error-Poisoned Type Lattice.

This is the heart of Phase 2's cascade-suppression story. `ERROR` is the
bottom element of the type lattice: any operation touching it silently
yields `ERROR` again instead of raising a *new* diagnostic. One real
semantic fault (an undeclared variable, say) is reported exactly once —
everything downstream that depends on it is poisoned quietly, not
re-diagnosed.

Also performs initialiser type inference, function arity/argument-type
checking, and a simple return-path validation ("does every branch of a
non-void function return a value?").
"""

from typing import List, Optional
from .span import Span, Diagnostic, Label
from .symbol_table import SymbolTable, Symbol
from .suggest import closest_identifier, closest_keyword
from . import ast_nodes as ast

ERROR = "Error"  # the lattice's bottom element


def _is_numeric(t: str) -> bool:
    return t in ("int", "float")


def _is_array(t: str) -> bool:
    return isinstance(t, str) and t.endswith("[]")


def _elem_type(t: str) -> str:
    return t[:-2] if _is_array(t) else ERROR


class TypeChecker:
    def __init__(self, source: str):
        self.src = source
        self.symtab = SymbolTable()
        self.diagnostics: List[Diagnostic] = []
        self._current_return_type: Optional[str] = None
        self._poisoned_count = 0  # how many ops were silently poisoned (for metrics)
        self._register_builtins()

    def _register_builtins(self):
        # `print(...)` is the one built-in: variadic (param_types=None means
        # "don't arity/type-check"), so programs have an observable side effect.
        self.symtab.declare(Symbol("print", "void", "func", Span(0, 0),
                                    param_types=None, return_type="void"))

    # ---- entry point -----------------------------------------------------
    def check_program(self, items: List[ast.Node]):
        # Pass 1: register every function signature first, so calls can
        # forward-reference functions declared later in the file (and so
        # recursive calls resolve during pass 2).
        for item in items:
            if isinstance(item, ast.FuncDecl):
                self._register_function(item)

        # Pass 2: check function bodies and top-level statements.
        for item in items:
            if isinstance(item, ast.FuncDecl):
                self._check_function_body(item)
            else:
                self._check_stmt(item)

    # ---- function registration & checking --------------------------------
    def _register_function(self, decl: ast.FuncDecl):
        param_types = [p.type_name for p in decl.params]
        sym = Symbol(decl.name, decl.return_type, "func", decl.span,
                      param_types=param_types, return_type=decl.return_type)
        if not self.symtab.declare(sym):
            existing = self.symtab.global_scope.lookup_local(decl.name)
            self.diagnostics.append(Diagnostic(
                code="E0300",
                message=f"function '{decl.name}' is already declared in this scope",
                span=Span(decl.span.start, decl.span.start + len(decl.name)),
                labels=[Label(existing.span, "first declared here")] if existing else [],
            ))

    def _check_function_body(self, decl: ast.FuncDecl):
        self.symtab.push_scope(label=f"func:{decl.name}")
        prev_return_type = self._current_return_type
        self._current_return_type = decl.return_type

        seen_params = set()
        for p in decl.params:
            if p.name in seen_params:
                self.diagnostics.append(Diagnostic(
                    code="E0301",
                    message=f"duplicate parameter name '{p.name}'",
                    span=Span(p.span.start, p.span.start + len(p.name)),
                ))
            seen_params.add(p.name)
            self.symtab.declare(Symbol(p.name, p.type_name, "param", p.span))

        if decl.body:
            for stmt in decl.body.statements:
                self._check_stmt(stmt)

            if decl.return_type != "void" and not self._always_returns(decl.body):
                self.diagnostics.append(Diagnostic(
                    code="E0310",
                    message=f"function '{decl.name}' does not return a value on every path",
                    span=Span(decl.span.start, decl.span.start + len(decl.name)),
                    help=f"declared return type is '{decl.return_type}'; add a 'return' on every branch",
                ))

        self._current_return_type = prev_return_type
        self.symtab.pop_scope()

    def _always_returns(self, node: ast.Node) -> bool:
        """Conservative return-path check: a block/if/while 'always returns'
        only if every reachable path ends in a Return statement."""
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
        if isinstance(stmt, ast.VarDecl):
            self._check_var_decl(stmt)
        elif isinstance(stmt, ast.Assignment):
            self._check_assignment(stmt)
        elif isinstance(stmt, ast.ExprStmt):
            self._check_expr(stmt.expr)
        elif isinstance(stmt, ast.Block):
            self.symtab.push_scope(label="block")
            for s in stmt.statements:
                self._check_stmt(s)
            self.symtab.pop_scope()
        elif isinstance(stmt, ast.If):
            cond_t = self._check_expr(stmt.condition)
            if cond_t != "bool" and cond_t != ERROR:
                self.diagnostics.append(Diagnostic(
                    code="E0307",
                    message=f"if-condition must be 'bool', found '{cond_t}'",
                    span=stmt.condition.span,
                ))
            self._check_stmt(stmt.then_branch)
            if stmt.else_branch is not None:
                self._check_stmt(stmt.else_branch)
        elif isinstance(stmt, ast.While):
            cond_t = self._check_expr(stmt.condition)
            if cond_t != "bool" and cond_t != ERROR:
                self.diagnostics.append(Diagnostic(
                    code="E0307",
                    message=f"while-condition must be 'bool', found '{cond_t}'",
                    span=stmt.condition.span,
                ))
            if stmt.body:
                self._check_stmt(stmt.body)
        elif isinstance(stmt, ast.Return):
            self._check_return(stmt)
        elif isinstance(stmt, ast.ErrorStmt):
            pass  # already diagnosed by the parser; poisoned, not re-checked
        # FuncDecl at statement position (nested funcs) is out of scope — ignored.

    def _check_var_decl(self, stmt: ast.VarDecl):
        value_t = self._check_expr(stmt.value) if stmt.value is not None else stmt.type_name

        # Poisoning propagates into the symbol table itself: if the initialiser
        # was already poisoned (its root cause reported once, upstream), the
        # variable's *own* static type becomes Error too — so every later use
        # of this variable is silently poisoned as well, instead of each one
        # re-triggering its own diagnostic. This is what keeps a single typo
        # from cascading across every subsequent statement that touches it.
        declared_type = ERROR if value_t == ERROR else stmt.type_name
        name_span = stmt.name_span or Span(stmt.span.start, stmt.span.start + len(stmt.name))

        if not self.symtab.declare(Symbol(stmt.name, declared_type, "var", name_span)):
            existing = self.symtab.current.lookup_local(stmt.name)
            self.diagnostics.append(Diagnostic(
                code="E0300",
                message=f"variable '{stmt.name}' is already declared in this scope",
                span=name_span,
                labels=[Label(existing.span, "first declared here")] if existing else [],
            ))
            return

        if value_t == ERROR or stmt.type_name == ERROR:
            self._poisoned_count += 1
            return  # poisoned — the root cause was already reported
        if not self._assignable(stmt.type_name, value_t):
            self.diagnostics.append(Diagnostic(
                code="E0302",
                message=f"cannot initialise '{stmt.type_name}' variable '{stmt.name}' with a value of type '{value_t}'",
                span=stmt.value.span if stmt.value else stmt.span,
            ))

    def _check_assignment(self, stmt: ast.Assignment):
        sym = self.symtab.lookup(stmt.name)
        value_t = self._check_expr(stmt.value) if stmt.value is not None else ERROR

        if sym is None:
            candidate = closest_identifier(stmt.name, self.symtab.visible_names())
            self.diagnostics.append(Diagnostic(
                code="E0412",
                message=f"cannot find variable '{stmt.name}' in this scope",
                span=Span(stmt.span.start, stmt.span.start + len(stmt.name)),
                help=f"did you mean '{candidate}'?" if candidate else None,
            ))
            self._poisoned_count += 1
            return

        target_t = sym.type_name
        if stmt.index is not None:
            idx_t = self._check_expr(stmt.index)
            if not _is_array(sym.type_name):
                if sym.type_name != ERROR:
                    self.diagnostics.append(Diagnostic(
                        code="E0306",
                        message=f"'{stmt.name}' has type '{sym.type_name}', which cannot be indexed",
                        span=stmt.span,
                    ))
                self._poisoned_count += 1
                return
            if idx_t != "int" and idx_t != ERROR:
                self.diagnostics.append(Diagnostic(
                    code="E0303", message=f"array index must be 'int', found '{idx_t}'",
                    span=stmt.index.span))
            target_t = _elem_type(sym.type_name)

        if value_t == ERROR or target_t == ERROR:
            self._poisoned_count += 1
            return
        if not self._assignable(target_t, value_t):
            self.diagnostics.append(Diagnostic(
                code="E0302",
                message=f"cannot assign a value of type '{value_t}' to '{stmt.name}' (type '{target_t}')",
                span=stmt.value.span if stmt.value else stmt.span,
            ))

    def _check_return(self, stmt: ast.Return):
        value_t = self._check_expr(stmt.value) if stmt.value is not None else "void"
        if self._current_return_type is None:
            self.diagnostics.append(Diagnostic(
                code="E0309", message="'return' used outside a function",
                span=stmt.span))
            return
        if value_t == ERROR:
            self._poisoned_count += 1
            return
        if self._current_return_type == "void" and stmt.value is not None:
            self.diagnostics.append(Diagnostic(
                code="E0308",
                message="function returns 'void' but a value was returned",
                span=stmt.value.span))
        elif self._current_return_type != "void" and not self._assignable(self._current_return_type, value_t):
            self.diagnostics.append(Diagnostic(
                code="E0308",
                message=f"expected a return value of type '{self._current_return_type}', found '{value_t}'",
                span=stmt.value.span if stmt.value else stmt.span))

    # ---- expressions -------------------------------------------------
    def _check_expr(self, expr: ast.Node) -> str:
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
                candidate = closest_identifier(expr.name, self.symtab.visible_names())
                self.diagnostics.append(Diagnostic(
                    code="E0412",
                    message=f"cannot find variable '{expr.name}' in this scope",
                    span=expr.span,
                    help=f"did you mean '{candidate}'?" if candidate else None,
                ))
                self._poisoned_count += 1
                return ERROR
            return sym.type_name

        if isinstance(expr, ast.UnaryOp):
            t = self._check_expr(expr.operand)
            if t == ERROR:
                self._poisoned_count += 1
                return ERROR
            if expr.op == "-" and not _is_numeric(t):
                self.diagnostics.append(Diagnostic(
                    code="E0303", message=f"unary '-' requires a numeric operand, found '{t}'",
                    span=expr.span))
                return ERROR
            if expr.op == "!" and t != "bool":
                self.diagnostics.append(Diagnostic(
                    code="E0303", message=f"unary '!' requires a 'bool' operand, found '{t}'",
                    span=expr.span))
                return ERROR
            return t

        if isinstance(expr, ast.BinOp):
            lt = self._check_expr(expr.left)
            rt = self._check_expr(expr.right)
            if lt == ERROR or rt == ERROR:
                self._poisoned_count += 1
                return ERROR  # poisoned — no new diagnostic

            if expr.op in ("+", "-", "*", "/", "%"):
                if expr.op == "+" and lt == "string" and rt == "string":
                    return "string"
                if not (_is_numeric(lt) and _is_numeric(rt)):
                    self.diagnostics.append(Diagnostic(
                        code="E0304",
                        message=f"operator '{expr.op}' is not defined for types '{lt}' and '{rt}'",
                        span=expr.span))
                    return ERROR
                return "float" if "float" in (lt, rt) else "int"

            if expr.op in ("<", ">", "<=", ">="):
                if not (_is_numeric(lt) and _is_numeric(rt)):
                    self.diagnostics.append(Diagnostic(
                        code="E0304",
                        message=f"operator '{expr.op}' is not defined for types '{lt}' and '{rt}'",
                        span=expr.span))
                    return ERROR
                return "bool"

            if expr.op in ("==", "!="):
                if lt != rt and not (_is_numeric(lt) and _is_numeric(rt)):
                    self.diagnostics.append(Diagnostic(
                        code="E0304",
                        message=f"cannot compare '{lt}' with '{rt}'",
                        span=expr.span))
                    return ERROR
                return "bool"

            if expr.op in ("&&", "||"):
                if lt != "bool" or rt != "bool":
                    self.diagnostics.append(Diagnostic(
                        code="E0304",
                        message=f"operator '{expr.op}' requires 'bool' operands, found '{lt}' and '{rt}'",
                        span=expr.span))
                    return ERROR
                return "bool"
            return ERROR

        if isinstance(expr, ast.Call):
            return self._check_call(expr)

        if isinstance(expr, ast.ArrayLiteral):
            if not expr.elements:
                return "Error[]"
            elem_types = [self._check_expr(e) for e in expr.elements]
            first = elem_types[0]
            if any(t == ERROR for t in elem_types):
                self._poisoned_count += 1
                return ERROR
            if any(t != first for t in elem_types):
                self.diagnostics.append(Diagnostic(
                    code="E0305",
                    message="array literal elements have inconsistent types",
                    span=expr.span))
                return ERROR
            return first + "[]"

        if isinstance(expr, ast.Index):
            arr_t = self._check_expr(expr.array)
            idx_t = self._check_expr(expr.index)
            if arr_t == ERROR or idx_t == ERROR:
                self._poisoned_count += 1
                return ERROR
            if not _is_array(arr_t):
                self.diagnostics.append(Diagnostic(
                    code="E0306", message=f"type '{arr_t}' cannot be indexed",
                    span=expr.span))
                return ERROR
            if idx_t != "int":
                self.diagnostics.append(Diagnostic(
                    code="E0303", message=f"array index must be 'int', found '{idx_t}'",
                    span=expr.index.span))
                return ERROR
            return _elem_type(arr_t)

        return ERROR

    def _check_call(self, expr: ast.Call) -> str:
        sym = self.symtab.lookup_function(expr.name)
        arg_types = [self._check_expr(a) for a in expr.args]

        if sym is None:
            func_names = [n for n, s in self.symtab.global_scope.symbols.items() if s.kind == "func"]
            candidate = closest_identifier(expr.name, func_names)
            self.diagnostics.append(Diagnostic(
                code="E0413",
                message=f"cannot find function '{expr.name}'",
                span=expr.span,
                help=f"did you mean '{candidate}'?" if candidate else None,
            ))
            self._poisoned_count += 1
            return ERROR

        if any(t == ERROR for t in arg_types):
            self._poisoned_count += 1
            return ERROR

        if sym.param_types is None:
            return sym.return_type  # variadic builtin (e.g. print) — no arity/type check

        if len(arg_types) != len(sym.param_types):
            self.diagnostics.append(Diagnostic(
                code="E0311",
                message=f"'{expr.name}' expects {len(sym.param_types)} argument(s), found {len(arg_types)}",
                span=expr.span,
            ))
            return ERROR

        for i, (given, expected) in enumerate(zip(arg_types, sym.param_types)):
            if not self._assignable(expected, given):
                self.diagnostics.append(Diagnostic(
                    code="E0305",
                    message=f"argument {i + 1} to '{expr.name}' expects '{expected}', found '{given}'",
                    span=expr.args[i].span,
                ))
                return ERROR

        return sym.return_type

    # ---- assignability ------------------------------------------------
    @staticmethod
    def _assignable(target: str, value: str) -> bool:
        if target == value:
            return True
        if target == "float" and value == "int":
            return True  # int widens to float
        return False
