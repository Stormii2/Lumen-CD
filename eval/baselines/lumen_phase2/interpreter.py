"""
Module 4 (part 2): TAC Interpreter.

Executes the flat three-address code produced by `tac.py` directly —
no further lowering to machine code (native codegen is explicitly
out of scope per the proposal). Each function call creates a fresh
activation record (a plain dict of local names to values); Python's
own call stack backs recursion, which is enough to demonstrate that
recursive Lumen programs execute correctly.

A single built-in, `print(...)`, is provided purely so a program's
result is externally observable when run from the CLI.
"""

from typing import Dict, List, Optional
from .tac import TACFunction, MAIN


class LumenRuntimeError(Exception):
    pass


def _val(frame: dict, name: Optional[str]):
    """Resolve a TAC operand: a quoted string constant, a bare numeric
    constant, or a variable/temporary name looked up in the current
    activation record."""
    if name is None:
        return None
    if name.startswith('"') and name.endswith('"'):
        return name[1:-1]
    try:
        return float(name) if "." in name else int(name)
    except ValueError:
        return frame.get(name, 0)


def _apply(op: str, a, b):
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if b == 0:
            return 0
        return a // b if isinstance(a, int) and isinstance(b, int) else a / b
    if op == "%":
        return a % b if b != 0 else 0
    if op == "<":
        return 1 if a < b else 0
    if op == ">":
        return 1 if a > b else 0
    if op == "<=":
        return 1 if a <= b else 0
    if op == ">=":
        return 1 if a >= b else 0
    if op == "==":
        return 1 if a == b else 0
    if op == "!=":
        return 1 if a != b else 0
    if op == "&&":
        return 1 if (a and b) else 0
    if op == "||":
        return 1 if (a or b) else 0
    raise LumenRuntimeError(f"unknown operator '{op}'")


class TACInterpreter:
    def __init__(self, functions: Dict[str, TACFunction], max_steps: int = 2_000_000):
        self.functions = functions
        self.output: List[str] = []
        self.max_steps = max_steps
        self._steps = 0

    def run(self):
        self.call_function(MAIN, [])
        return self.output

    def call_function(self, name: str, args: list):
        if name == "print":
            self.output.append(" ".join(str(a) for a in args))
            return 0

        fn = self.functions.get(name)
        if fn is None:
            raise LumenRuntimeError(f"undefined function '{name}' at runtime")

        frame: dict = {}
        for pname, aval in zip(fn.params, args):
            frame[pname] = aval

        labels = {instr.res: i for i, instr in enumerate(fn.instrs) if instr.op == "LABEL"}
        pending_args: list = []
        pc = 0
        while pc < len(fn.instrs):
            self._steps += 1
            if self._steps > self.max_steps:
                raise LumenRuntimeError("step limit exceeded (possible infinite loop)")

            instr = fn.instrs[pc]
            op = instr.op

            if op == "LABEL":
                pc += 1
            elif op == "CONST":
                frame[instr.res] = _val(frame, instr.a)
                pc += 1
            elif op == "COPY":
                frame[instr.res] = _val(frame, instr.a)
                pc += 1
            elif op in ("+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&&", "||"):
                frame[instr.res] = _apply(op, _val(frame, instr.a), _val(frame, instr.b))
                pc += 1
            elif op == "neg":
                frame[instr.res] = -_val(frame, instr.a)
                pc += 1
            elif op == "not":
                frame[instr.res] = 0 if _val(frame, instr.a) else 1
                pc += 1
            elif op == "PARAM":
                pending_args.append(_val(frame, instr.a))
                pc += 1
            elif op == "CALL":
                result = self.call_function(instr.a, pending_args)
                pending_args = []
                frame[instr.res] = result if result is not None else 0
                pc += 1
            elif op == "GOTO":
                pc = labels[instr.a]
            elif op == "IF_FALSE":
                pc = labels[instr.b] if not _val(frame, instr.a) else pc + 1
            elif op == "RETURN":
                return _val(frame, instr.a) if instr.a is not None else None
            elif op == "ARR_NEW":
                frame[instr.res] = [0] * int(instr.a)
                pc += 1
            elif op == "ARR_SET":
                arr = frame.setdefault(instr.res, [])
                idx = int(_val(frame, instr.a))
                val = _val(frame, instr.b)
                if idx >= len(arr):
                    arr.extend([0] * (idx + 1 - len(arr)))
                arr[idx] = val
                pc += 1
            elif op == "ARR_GET":
                arr = frame.get(instr.a, [])
                idx = int(_val(frame, instr.b))
                frame[instr.res] = arr[idx] if 0 <= idx < len(arr) else 0
                pc += 1
            else:
                raise LumenRuntimeError(f"unknown TAC opcode '{op}'")

        return None  # fell off the end with no explicit return (void function)
