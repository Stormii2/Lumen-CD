#!/usr/bin/env python3
"""
lumenc — Lumen compiler driver (Phase 2: full front-end + semantics + TAC).

Usage:
    python3 lumenc.py <source-file> [--no-color] [--emit-tac] [--quiet-run]

Pipeline: lex -> parse -> type-check (with error poisoning) -> collect and
render every diagnostic from all three phases. If the program is entirely
diagnostic-free, it is lowered to three-address code and executed by the
TAC interpreter, with `print(...)` output shown.

If there are diagnostics, codegen/execution is skipped — same as a real
compiler refusing to run a program that didn't type-check.
"""

import sys
from lumen import (Lexer, Parser, TypeChecker, TACGenerator, format_program,
                    TACInterpreter, LumenRuntimeError, render_all)


def main(argv):
    if len(argv) < 2:
        print("usage: python3 lumenc.py <source-file> [--no-color] [--emit-tac] [--quiet-run]")
        return 1

    path = argv[1]
    use_color = "--no-color" not in argv
    emit_tac = "--emit-tac" in argv
    quiet_run = "--quiet-run" in argv

    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        print(f"lumenc: cannot read '{path}': {e}")
        return 1

    lexer = Lexer(source)
    tokens, lex_diags = lexer.scan()

    parser = Parser(tokens, source)
    items = parser.parse_program()
    parse_diags = parser.diagnostics

    checker = TypeChecker(source)
    checker.check_program(items)
    check_diags = checker.diagnostics

    all_diags = lex_diags + parse_diags + check_diags
    all_diags.sort(key=lambda d: d.span.start)

    print(f"$ lumenc {path}")
    if all_diags:
        print(render_all(all_diags, source, path, use_color=use_color))
        print(f"(codegen and execution skipped — {len(all_diags)} diagnostic(s) reported)")
        return 1

    print("compilation succeeded — 0 diagnostics.\n")

    gen = TACGenerator()
    functions = gen.generate(items)

    if emit_tac:
        print("--- three-address code ---")
        print(format_program(functions))
        print("--- end TAC ---\n")

    if not quiet_run:
        try:
            interp = TACInterpreter(functions)
            output = interp.run()
            print("--- program output ---")
            if output:
                for line in output:
                    print(line)
            else:
                print("(no output — program produced no print() calls)")
        except LumenRuntimeError as e:
            print(f"runtime error: {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
