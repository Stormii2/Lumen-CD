from .config import RecoveryConfig, DEFAULT, NAIVE, FAIL_FAST
from .lexer import Lexer
from .parser import Parser
from .checker import TypeChecker
from .tac import TACGenerator, format_program
from .interpreter import TACInterpreter, LumenRuntimeError, format_value
from .renderer import render_all, render_diagnostic, summary_line
from .span import SourceMap, Span, Diagnostic, Edit, Suggestion
from .driver import compile_source, CompileResult
from .fixer import apply_suggestions, fix_to_fixpoint
from .explain import explain
from .optimizer import optimize_program, OptStats, OptConfig, ssa_text

__version__ = "3.0.0"

__all__ = [
    "RecoveryConfig", "DEFAULT", "NAIVE", "FAIL_FAST", "Lexer", "Parser", "TypeChecker",
    "TACGenerator", "format_program", "TACInterpreter", "LumenRuntimeError", "format_value",
    "render_all", "render_diagnostic", "summary_line", "SourceMap", "Span", "Diagnostic",
    "Edit", "Suggestion", "compile_source", "CompileResult", "apply_suggestions",
    "fix_to_fixpoint", "explain", "optimize_program", "OptStats", "OptConfig", "ssa_text",
]
