#!/usr/bin/env python3
"""
Capture real output of the compiler for a fixed list of scenarios and save it
as text (docs/evidence/NN_name.txt) and as a rendered terminal image
(docs/evidence/NN_name.png). The images are drawn from the captured text with
Pillow - they are NOT screenshots of a live terminal; re-run
`python3 tools/make_evidence.py` any time to regenerate, or take real
screenshots with the same commands.
"""
import os, re, subprocess, sys, textwrap
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "evidence")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

BG, FG = (24, 26, 32), (214, 218, 226)
COL = {31: (255, 106, 106), 33: (240, 192, 90), 34: (110, 168, 254), 32: (120, 210, 150)}

NAIVE = ["--no-phrase-level", "--sync", "semicolon", "--poison", "none", "--no-token-poison",
         "--error-gap", "0", "--no-mute-damaged", "--no-infer-lost-decl", "--no-suppress"]

# (file stem, title, [commands]) ; a command is (args list, show_source_file or None)
SCENARIOS = [
    ("01_run_valid", "Valid program: compile and run", [(["examples/valid.lum"], "examples/valid.lum")]),
    ("02_run_recursion", "Recursion (factorial, fibonacci)", [(["examples/factorial.lum"], "examples/factorial.lum")]),
    ("03_syntax_and_lexical_errors", "Seven defects, seven diagnostics", [(["examples/demo.lum"], "examples/demo.lum")]),
    ("04_same_file_naive", "Same file with every recovery technique switched off", [(["examples/demo.lum"] + NAIVE + ["--check"], None)]),
    ("05_semantic_errors", "Semantic errors with labelled secondary spans", [(["examples/semantic_errors.lum"], "examples/semantic_errors.lum")]),
    ("06_typos_and_repairs", "Typos: keyword, type and variable repairs", [(["examples/typos.lum"], "examples/typos.lum")]),
    ("07_cascade_suppression", "Cascade suppression (one defect -> one diagnostic)", [(["examples/cascade_demo.lum"], "examples/cascade_demo.lum")]),
    ("08_fix_dry_run", "--fix --diff: machine-applicable repairs", [(["examples/demo.lum", "--fix", "--diff"], None)]),
    ("09_runtime_error", "Run-time error with a source caret", [(["examples/runtime_error.lum"], "examples/runtime_error.lum")]),
    ("10_warnings", "Warnings do not block execution", [(["examples/warnings.lum"], "examples/warnings.lum")]),
    ("11_explain", "lumenc --explain", [(["--explain", "E0412"], None), (["--explain", "R0003"], None)]),
    ("12_tac", "Three-address code (--emit-tac)", [(["examples/arrays.lum", "--emit-tac", "--check"], None)]),
    ("13_json", "Machine-readable diagnostics (--json)", [(["examples/typos.lum", "--json"], None)]),
    ("14_grammar", "FIRST / FOLLOW tables derived from the grammar", [(["--grammar"], None)]),
]


def strip(s):
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def run(args):
    p = subprocess.run([sys.executable, "lumenc.py", "--color"] + args, cwd=ROOT, capture_output=True, text=True, timeout=60)
    return (p.stdout + p.stderr).rstrip("\n"), p.returncode


def render(lines, path, title):
    """lines: list of raw strings possibly containing SGR codes."""
    f = ImageFont.truetype(FONT, 15); fb = ImageFont.truetype(FONT_B, 15)
    cw = f.getlength("M"); lh = 21
    width_chars = max(len(strip(l)) for l in lines) + 2
    width_chars = max(60, min(width_chars, 130))
    W = int(cw * width_chars + 48); H = lh * (len(lines) + 3) + 36
    img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse((16 + i * 22, 12, 28 + i * 22, 24), fill=c)
    d.text((W // 2 - len(title) * 4, 10), title, font=f, fill=(150, 156, 168))
    y = 40
    for raw in lines:
        x, color, bold = 24, FG, False
        for part in re.split(r"(\x1b\[[0-9;]*m)", raw):
            m = re.fullmatch(r"\x1b\[([0-9;]*)m", part)
            if m:
                for code in (m.group(1) or "0").split(";"):
                    code = int(code or 0)
                    if code == 0: color, bold = FG, False
                    elif code == 1: bold = True
                    elif code in COL: color = COL[code]
                continue
            d.text((x, y), part, font=fb if bold else f, fill=color)
            x += cw * len(part)
        y += lh
    img.save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    for stem, title, cmds in SCENARIOS:
        raw_lines, txt = [], []
        for args, show in cmds:
            if show:
                with open(os.path.join(ROOT, show), encoding="utf-8") as fh:
                    src = fh.read().splitlines()
                hdr = f"$ cat -n {show}"
                raw_lines.append("\x1b[1m" + hdr + "\x1b[0m"); txt.append(hdr)
                for n, l in enumerate(src, 1):
                    raw_lines.append(f"{n:3}  {l}"); txt.append(f"{n:3}  {l}")
                raw_lines.append(""); txt.append("")
            shown = "python3 lumenc.py " + " ".join(a for a in args)
            raw_lines.append("\x1b[1m$ " + shown + "\x1b[0m"); txt.append("$ " + shown)
            out, rc = run(args)
            raw_lines.extend(out.split("\n")); txt.extend(strip(out).split("\n"))
            raw_lines.append(f"\x1b[34m[exit status {rc}]\x1b[0m"); txt.append(f"[exit status {rc}]")
            raw_lines.append(""); txt.append("")
        with open(os.path.join(OUT, stem + ".txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(txt).rstrip() + "\n")
        if len(raw_lines) <= 125:
            render(raw_lines, os.path.join(OUT, stem + ".png"), title)
        print("wrote", stem, f"({len(raw_lines)} lines)")


if __name__ == "__main__":
    main()
