"""
Module 4 (part 2): TAC Interpreter.

Executes the flat three-address code produced by `tac.py` directly - no
further lowering to machine code (native code generation is not implemented).

Run-time semantics (documented in docs/LANGUAGE.md):

  * `int` is a 64-bit signed integer; overflow is a run-time error, not a
    silent wrap-around.
  * Integer division truncates toward zero and `%` takes the sign of the
    dividend (like C / Java), so `-7 / 2 == -3` and `-7 % 2 == -1`.
  * Division or modulo by zero, an out-of-range array index, a call depth
    beyond MAX_CALL_DEPTH and a runaway loop (MAX_STEPS) are all reported as
    run-time errors that carry the source span of the offending expression.
  * Booleans print as `true` / `false`.

For speed, every function is decoded once into tuples (operand kinds
resolved, constants pre-parsed, labels pre-indexed) before it first runs;
the per-instruction work in the hot loop is then just integer dispatch.
"""

import math
import operator
import sys
from typing import Callable, Dict, List, Optional

from .span import Span
from .tac import TACFunction, MAIN

INT_MIN = -(2 ** 63)
INT_MAX = 2 ** 63 - 1
MAX_CALL_DEPTH = 2000
MAX_STEPS = 5_000_000


class LumenRuntimeError(Exception):
    def __init__(self, message: str, code: str = "R0000", span: Optional[Span] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.span = span


# opcodes
(_CONST, _COPY, _ARITH, _DIV, _MOD, _NEG, _NOT, _PARAM, _CALL, _GOTO, _IF_FALSE,
 _RETURN, _ARR_NEW, _ARR_SET, _ARR_GET, _LABEL, _ITOF, _CMP) = range(18)

_ARITH_OPS = {"+": operator.add, "-": operator.sub, "*": operator.mul}
_CMP_OPS = {"<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge,
            "==": operator.eq, "!=": operator.ne}


def format_value(v) -> str:
    """How `print` renders a value."""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, list):
        return "[" + ", ".join(format_value(x) for x in v) + "]"
    return str(v)


def _parse_const(text: str):
    """Return (is_const, value) for a TAC operand string."""
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return True, text[1:-1]
    if text == "true":
        return True, True
    if text == "false":
        return True, False
    try:
        return True, (float(text) if ("." in text or "e" in text.lower()) else int(text))
    except ValueError:
        return False, text


class _Decoded:
    __slots__ = ("name", "params", "code", "labels")

    def __init__(self, name, params, code, labels):
        self.name, self.params, self.code, self.labels = name, params, code, labels


class TACInterpreter:
    def __init__(self, functions: Dict[str, TACFunction], max_steps: int = MAX_STEPS,
                 max_depth: int = MAX_CALL_DEPTH,
                 print_fn: Optional[Callable[[str], None]] = None):
        self.functions = functions
        self.output: List[str] = []
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.print_fn = print_fn
        self._steps = 0
        self._depth = 0
        main = functions.get(MAIN)
        self._global_names = main.global_names if main is not None else set()
        self.globals: dict = {}
        self._decoded: Dict[str, _Decoded] = {}
        self._pending: list = []

    @property
    def steps(self) -> int:
        return self._steps

    # ---- decoding ---------------------------------------------------------
    def _operand(self, text: Optional[str]):
        """-> (kind, value): kind 0 = constant, 1 = frame variable, 2 = global variable"""
        if text is None:
            return 0, None
        is_const, val = _parse_const(text)
        if is_const:
            return 0, val
        return (2 if val in self._global_names else 1), val

    def _decode(self, fn: TACFunction) -> _Decoded:
        cached = self._decoded.get(fn.name)
        if cached is not None:
            return cached
        code = []
        labels = {}
        for idx, ins in enumerate(fn.instrs):
            op = ins.op
            ak, av = self._operand(ins.a) if op not in ("GOTO", "CALL", "CALL_VOID", "LABEL") else (0, ins.a)
            bk, bv = self._operand(ins.b) if op not in ("IF_FALSE", "CALL", "CALL_VOID") else (0, ins.b)
            res = ins.res
            res_g = res in self._global_names if res is not None else False
            extra = None
            if op == "LABEL":
                labels[res] = idx
                opc = _LABEL
            elif op == "CONST":
                opc = _CONST
            elif op == "COPY":
                opc = _COPY
            elif op in _ARITH_OPS:
                opc, extra = _ARITH, _ARITH_OPS[op]
            elif op in _CMP_OPS:
                opc, extra = _CMP, _CMP_OPS[op]
            elif op == "/":
                opc = _DIV
            elif op == "%":
                opc = _MOD
            elif op == "&&":
                opc, extra = _ARITH, (lambda x, y: bool(x and y))
            elif op == "||":
                opc, extra = _ARITH, (lambda x, y: bool(x or y))
            elif op == "neg":
                opc = _NEG
            elif op == "not":
                opc = _NOT
            elif op == "itof":
                opc = _ITOF
            elif op == "PARAM":
                opc = _PARAM
            elif op in ("CALL", "CALL_VOID"):
                opc, extra = _CALL, int(ins.b) if ins.b is not None else 0
            elif op == "GOTO":
                opc = _GOTO
            elif op == "IF_FALSE":
                opc, extra = _IF_FALSE, ins.b
            elif op == "RETURN":
                opc = _RETURN
            elif op == "ARR_NEW":
                opc = _ARR_NEW
            elif op == "ARR_SET":
                opc = _ARR_SET
            elif op == "ARR_GET":
                opc = _ARR_GET
            else:
                raise LumenRuntimeError(f"unknown TAC opcode '{op}'", "R0099", ins.span)
            code.append((opc, ak, av, bk, bv, res, res_g, extra, ins.span))
        # resolve jump targets once
        d = _Decoded(fn.name, fn.params, code, labels)
        self._decoded[fn.name] = d
        return d

    # ---- running --------------------------------------------------------
    def run(self):
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, self.max_depth * 3 + 1000))
        try:
            self.call_function(MAIN, [])
        except RecursionError:
            raise LumenRuntimeError(
                f"stack overflow: the call stack grew beyond what the interpreter can hold",
                "R0005")
        finally:
            sys.setrecursionlimit(old)
        return self.output

    def _emit_line(self, args: list):
        line = " ".join(format_value(a) for a in args)
        self.output.append(line)
        if self.print_fn is not None:
            self.print_fn(line)

    def call_function(self, name: str, args: list, call_span: Optional[Span] = None):
        if name == "print":
            self._emit_line(args)
            return 0

        fn = self.functions.get(name)
        if fn is None:
            raise LumenRuntimeError(f"undefined function '{name}' at run time", "R0099", call_span)

        self._depth += 1
        if self._depth > self.max_depth:
            raise LumenRuntimeError(
                f"stack overflow: call depth exceeded {self.max_depth} "
                f"(runaway recursion in '{name}'?)", "R0005", call_span)
        try:
            return self._execute(self._decode(fn), args, name == MAIN)
        finally:
            self._depth -= 1

    def _execute(self, dec: _Decoded, args: list, is_main: bool):
        G = self.globals
        frame = G if is_main else {}
        for pname, aval in zip(dec.params, args):
            frame[pname] = aval

        code = dec.code
        labels = dec.labels
        n = len(code)
        pending = self._pending
        max_steps = self.max_steps
        steps = self._steps
        pc = 0
        span = None
        try:
            while pc < n:
                steps += 1
                if steps > max_steps:
                    self._steps = steps
                    raise LumenRuntimeError(
                        f"step limit of {max_steps} exceeded (possible infinite loop)",
                        "R0006", span)
                opc, ak, av, bk, bv, res, res_g, extra, span = code[pc]

                if opc == _CONST:
                    (G if res_g else frame)[res] = av
                    pc += 1
                elif opc == _COPY:
                    (G if res_g else frame)[res] = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    pc += 1
                elif opc == _IF_FALSE:
                    v = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    pc = labels[extra] if not v else pc + 1
                elif opc == _GOTO:
                    pc = labels[av]
                elif opc == _LABEL:
                    pc += 1
                elif opc == _CMP:
                    a = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    b = bv if bk == 0 else (frame[bv] if bk == 1 else G[bv])
                    (G if res_g else frame)[res] = extra(a, b)
                    pc += 1
                elif opc == _ARITH:
                    a = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    b = bv if bk == 0 else (frame[bv] if bk == 1 else G[bv])
                    r = extra(a, b)
                    if r.__class__ is int and not (INT_MIN <= r <= INT_MAX):
                        raise LumenRuntimeError("integer overflow: result does not fit in 64 bits",
                                                "R0004", span)
                    (G if res_g else frame)[res] = r
                    pc += 1
                elif opc == _DIV:
                    a = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    b = bv if bk == 0 else (frame[bv] if bk == 1 else G[bv])
                    if b == 0:
                        raise LumenRuntimeError("division by zero", "R0001", span)
                    if a.__class__ is int and b.__class__ is int:
                        q = abs(a) // abs(b)
                        r = -q if (a < 0) != (b < 0) else q
                        if not (INT_MIN <= r <= INT_MAX):
                            raise LumenRuntimeError(
                                "integer overflow: result does not fit in 64 bits", "R0004", span)
                    else:
                        r = a / b
                    (G if res_g else frame)[res] = r
                    pc += 1
                elif opc == _MOD:
                    a = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    b = bv if bk == 0 else (frame[bv] if bk == 1 else G[bv])
                    if b == 0:
                        raise LumenRuntimeError("modulo by zero", "R0002", span)
                    if a.__class__ is int and b.__class__ is int:
                        m = abs(a) % abs(b)
                        r = -m if a < 0 else m
                    else:
                        r = math.fmod(a, b)
                    (G if res_g else frame)[res] = r
                    pc += 1
                elif opc == _NEG:
                    a = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    r = -a
                    if r.__class__ is int and r > INT_MAX:
                        raise LumenRuntimeError("integer overflow: result does not fit in 64 bits",
                                                "R0004", span)
                    (G if res_g else frame)[res] = r
                    pc += 1
                elif opc == _NOT:
                    a = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    (G if res_g else frame)[res] = not a
                    pc += 1
                elif opc == _ITOF:
                    a = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    (G if res_g else frame)[res] = float(a)
                    pc += 1
                elif opc == _PARAM:
                    pending.append(av if ak == 0 else (frame[av] if ak == 1 else G[av]))
                    pc += 1
                elif opc == _CALL:
                    count = extra
                    if count:
                        call_args = pending[-count:]
                        del pending[-count:]
                    else:
                        call_args = []
                    self._steps = steps
                    result = self.call_function(av, call_args, span)
                    steps = self._steps
                    (G if res_g else frame)[res] = result if result is not None else 0
                    pc += 1
                elif opc == _RETURN:
                    self._steps = steps
                    if ak == 0 and av is None:
                        return None
                    return av if ak == 0 else (frame[av] if ak == 1 else G[av])
                elif opc == _ARR_NEW:
                    (G if res_g else frame)[res] = [0] * int(av)
                    pc += 1
                elif opc == _ARR_SET:
                    arr = G[res] if res_g else frame[res]
                    idx = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    val = bv if bk == 0 else (frame[bv] if bk == 1 else G[bv])
                    if not (0 <= idx < len(arr)):
                        raise LumenRuntimeError(
                            f"array index out of bounds: index {idx}, length {len(arr)}",
                            "R0003", span)
                    arr[idx] = val
                    pc += 1
                elif opc == _ARR_GET:
                    arr = av if ak == 0 else (frame[av] if ak == 1 else G[av])
                    idx = bv if bk == 0 else (frame[bv] if bk == 1 else G[bv])
                    if not (0 <= idx < len(arr)):
                        raise LumenRuntimeError(
                            f"array index out of bounds: index {idx}, length {len(arr)}",
                            "R0003", span)
                    (G if res_g else frame)[res] = arr[idx]
                    pc += 1
                else:
                    raise LumenRuntimeError(f"unknown opcode {opc}", "R0099", span)
        except KeyError as e:
            raise LumenRuntimeError(
                f"variable '{e.args[0]}' was read before it was assigned", "R0007", span)
        finally:
            self._steps = max(self._steps, steps)
        self._steps = steps
        return None  # fell off the end with no explicit return (void function)
