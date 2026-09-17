#!/usr/bin/env python3
"""
lumenc — Phase 1 prototype CLI for the Lumen diagnostics-first compiler.

Usage:
    python3 lumenc.py <source-file> [--no-color]

Runs the lexer and the parser subset (variable declarations, assignments,
expressions) over the given file, collects every diagnostic from both
phases (lexical + syntactic recovery is exercised, not just the first
fault), and renders them with the caret renderer.

Phase 1 scope only: no symbol table, type checker, suppression engine or
--fix patcher yet (those land in Phases 2 and 3 per the project roadmap).
"""

import sys
from lumen import Lexer, Parser, render_all


def main(argv):
    if len(argv) < 2:
        print("usage: python3 lumenc.py <source-file> [--no-color]")
        return 1

    path = argv[1]
    use_color = "--no-color" not in argv

    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        print(f"lumenc: cannot read '{path}': {e}")
        return 1

    lexer = Lexer(source)
    tokens, lex_diags = lexer.scan()

    parser = Parser(tokens, source)
    parser.parse_program()
    parse_diags = parser.diagnostics

    all_diags = lex_diags + parse_diags
    # Stable order: by source position, so a lexical fault and a later
    # syntactic fault print in the order a reader would hit them.
    all_diags.sort(key=lambda d: d.span.start)

    print(f"$ lumenc {path}")
    print(render_all(all_diags, source, path, use_color=use_color))
    return 1 if all_diags else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
