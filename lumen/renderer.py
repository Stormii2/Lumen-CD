"""
Module 4: Caret Renderer.

Renders a Diagnostic as a Clang/Rust-style caret display: the source
line, an underline at the fault span, optional secondary labels, and
help/note text. ANSI colour is used when the output is a terminal.
"""

import sys
from typing import List
from .span import SourceMap, Diagnostic

RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _color(text: str, code: str, use_color: bool) -> str:
    return f"{code}{text}{RESET}" if use_color else text


def render_diagnostic(diag: Diagnostic, smap: SourceMap, filename: str,
                       use_color: bool = True) -> str:
    line, col = diag.span.line_col(smap)
    line_text = smap.line_text(line)
    gutter = str(line)
    pad = " " * len(gutter)

    span_len = max(1, diag.span.end - diag.span.start)
    caret_line = " " * (col - 1) + "^" * min(span_len, max(1, len(line_text) - col + 2))

    sev_color = RED if diag.severity == "error" else YELLOW
    out = []
    out.append(_color(f"{diag.severity}[{diag.code}]", sev_color, use_color) +
               _color(f": {diag.message}", BOLD, use_color))
    out.append(f"{pad}--> {filename}:{line}:{col}")
    out.append(f"{pad} |")
    out.append(f"{gutter} | {line_text}")
    out.append(f"{pad} | " + _color(caret_line, sev_color, use_color))

    for label in diag.labels:
        l_line, l_col = label.span.line_col(smap)
        if l_line != line:
            l_text = smap.line_text(l_line)
            l_gutter = str(l_line)
            l_pad = " " * len(l_gutter)
            l_len = max(1, label.span.end - label.span.start)
            underline = " " * (l_col - 1) + "-" * l_len
            out.append(f"{l_pad} |")
            out.append(f"{l_gutter} | {l_text}")
            out.append(f"{l_pad} | " + _color(underline, BLUE, use_color) + f" {label.text}")

    if diag.help:
        out.append(f"{pad} |")
        out.append(_color("help", BLUE, use_color) + f": {diag.help}")
    if diag.note:
        out.append(_color("note", BLUE, use_color) + f": {diag.note}")
    out.append("")
    return "\n".join(out)


def render_all(diagnostics: List[Diagnostic], source: str, filename: str,
                use_color: bool = True) -> str:
    smap = SourceMap(source)
    blocks = [render_diagnostic(d, smap, filename, use_color) for d in diagnostics]
    summary = (
        f"{len(diagnostics)} error{'s' if len(diagnostics) != 1 else ''} generated."
        if diagnostics else "no errors generated."
    )
    return "\n".join(blocks) + summary
