"""
Compilation driver: the one place that wires lexer -> parser -> checker ->
TAC -> interpreter together. Both the CLI (lumenc.py), the test-suite and the
evaluation harness call this, so they all exercise the identical pipeline.
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional

from .config import RecoveryConfig, DEFAULT
from .lexer import Lexer
from .parser import Parser
from .checker import TypeChecker
from .tac import TACGenerator
from .diagnostics import finalize
from .span import Diagnostic


@dataclass
class CompileResult:
    source: str
    diagnostics: List[Diagnostic]
    items: list = field(default_factory=list)
    tokens: list = field(default_factory=list)
    functions: Optional[dict] = None     # TAC, only when there are no errors
    timings: dict = field(default_factory=dict)
    unoptimised: Optional[dict] = None   # the TAC before optimisation (only with optimize=True)
    opt_stats: object = None

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def codes(self) -> List[str]:
        return [d.code for d in self.diagnostics]


def compile_source(source: str, config: Optional[RecoveryConfig] = None,
                   want_tac: bool = True, optimize=False) -> CompileResult:
    config = config or DEFAULT
    t = {}
    t0 = time.perf_counter()
    tokens, lex_diags = Lexer(source).scan()
    t["lex"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    parser = Parser(tokens, source, config)
    items = parser.parse_program()
    t["parse"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    checker = TypeChecker(source, config)
    checker.check_program(items)
    t["check"] = time.perf_counter() - t0

    diags = finalize(lex_diags + parser.diagnostics + checker.diagnostics, config)
    res = CompileResult(source, diags, items, tokens, None, t)
    if res.ok and want_tac:
        t0 = time.perf_counter()
        res.functions = TACGenerator().generate(items)
        t["tac"] = time.perf_counter() - t0
        if optimize:
            from .optimizer import optimize_program, OptConfig
            t0 = time.perf_counter()
            res.unoptimised = res.functions
            res.functions, res.opt_stats = optimize_program(
                res.functions, optimize if isinstance(optimize, OptConfig) else OptConfig())
            t["optimize"] = time.perf_counter() - t0
    return res
