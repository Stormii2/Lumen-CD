#!/usr/bin/env python3
"""Generate docs/ERROR_CODES.md from the catalogue in lumen/explain.py."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lumen.explain import CATALOG        # noqa: E402

GROUPS = [("E00", "Lexical errors (E00xx)"), ("E01", "Syntax errors - declarations and blocks (E01xx)"),
          ("E02", "Syntax errors - statements and expressions (E02xx)"), ("E03", "Type and semantic errors (E03xx)"),
          ("E04", "Name and spelling errors (E04xx)"), ("W0", "Warnings (W00xx)"), ("R0", "Run-time errors (R00xx)")]
L = ["# Diagnostic code reference\n",
     "Generated from `lumen/explain.py`; `lumenc --explain CODE` prints the same text. The test-suite compiles every example below and asserts it raises exactly that code.\n"]
for prefix, title in GROUPS:
    codes = [c for c in sorted(CATALOG) if c.startswith(prefix)]
    if not codes:
        continue
    L.append(f"\n## {title}\n")
    for c in codes:
        summary, text, bad, fixed = CATALOG[c]
        L.append(f"### {c} - {summary}\n\n{text}\n")
        if bad:
            L.append("```\n" + bad + "\n```\n")
        if fixed:
            L.append("Corrected:\n\n```\n" + fixed + "\n```\n")
with open(os.path.join(ROOT, "docs", "ERROR_CODES.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print(len(CATALOG), "codes ->", "docs/ERROR_CODES.md")
