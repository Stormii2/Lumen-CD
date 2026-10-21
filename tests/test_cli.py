import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lumenc(*args, input_text=None, cwd=None):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "lumenc.py"), *args],
                       capture_output=True, text=True, cwd=cwd or ROOT, timeout=60)
    return p.returncode, p.stdout, p.stderr


def write(tmp_path, name, text):
    f = tmp_path / name
    f.write_text(text)
    return str(f)


def test_run_valid_program(tmp_path):
    f = write(tmp_path, "a.lum", "print(6 * 7);\n")
    rc, out, _ = lumenc(f, "--no-color")
    assert rc == 0 and out.strip().endswith("42")
    assert "compilation succeeded" in out


def test_compile_error_exit_code_and_caret(tmp_path):
    f = write(tmp_path, "b.lum", "int x = nope;\n")
    rc, out, _ = lumenc(f, "--no-color")
    assert rc == 1
    assert "error[E0412]" in out and "^^^^" in out and "codegen and execution skipped" in out


def test_runtime_error_exit_code_and_caret(tmp_path):
    f = write(tmp_path, "c.lum", "int z = 0;\nprint(1);\nprint(5 / z);\n")
    rc, out, _ = lumenc(f, "--no-color")
    assert rc == 2
    assert out.splitlines()[0] == "1" or "1\n" in out         # output before the failure is kept
    assert "error[R0001]: division by zero" in out and "5 / z" in out


def test_check_flag_does_not_run(tmp_path):
    f = write(tmp_path, "d.lum", "print(1);\n")
    rc, out, _ = lumenc(f, "--check", "--no-color")
    assert rc == 0 and out.strip().splitlines()[-1] != "1"


def test_emit_tac(tmp_path):
    f = write(tmp_path, "e.lum", "int x = 1 + 2;\n")
    rc, out, _ = lumenc(f, "--emit-tac", "--check", "--no-color")
    assert rc == 0 and "func __main__()" in out and "+" in out


def test_json_output_is_valid_and_structured(tmp_path):
    f = write(tmp_path, "f.lum", "int x = 5\nint y = nope;\n")
    rc, out, _ = lumenc(f, "--json")
    data = json.loads(out)
    assert rc == 1 and data["ok"] is False
    codes = [d["code"] for d in data["diagnostics"]]
    assert codes == ["E0201", "E0412"]
    d0 = data["diagnostics"][0]
    assert d0["line"] == 1 and d0["suggestion"]["applicability"] == "machine-applicable"
    assert d0["suggestion"]["edits"][0]["replacement"] == ";"


def test_json_ok_program(tmp_path):
    f = write(tmp_path, "g.lum", "print(1);\n")
    rc, out, _ = lumenc(f, "--json")
    assert rc == 0 and json.loads(out) == {"file": f, "ok": True, "diagnostics": []}


def test_fix_rewrites_file(tmp_path):
    f = write(tmp_path, "h.lum", "int x = 5\nprint(x);\n")
    rc, out, _ = lumenc(f, "--fix", "--no-color")
    assert rc == 0 and "applied 1 fix" in out
    assert open(f).read() == "int x = 5;\nprint(x);\n"


def test_fix_diff_does_not_modify_file(tmp_path):
    src = "int x = 5\nprint(x);\n"
    f = write(tmp_path, "i.lum", src)
    rc, out, _ = lumenc(f, "--fix", "--diff", "--no-color")
    assert open(f).read() == src
    assert "-int x = 5" in out and "+int x = 5;" in out


def test_fix_leaves_unsafe_guess_alone_unless_asked(tmp_path):
    src = 'string s = "abc;\n'
    f = write(tmp_path, "j.lum", src)
    lumenc(f, "--fix", "--no-color")
    assert open(f).read() == src
    lumenc(f, "--fix", "--unsafe-fixes", "--no-color")
    assert open(f).read() != src


def test_explain(tmp_path):
    rc, out, _ = lumenc("--explain", "E0412")
    assert rc == 0 and out.startswith("E0412") and "Example that triggers it" in out


def test_explain_unknown_code():
    rc, _, err = lumenc("--explain", "E9999")
    assert rc == 3 and "unknown code" in err


def test_list_codes():
    rc, out, _ = lumenc("--list-codes")
    assert rc == 0 and "E0412" in out and "R0001" in out and "W0001" in out


def test_grammar_tables():
    rc, out, _ = lumenc("--grammar")
    assert rc == 0 and "FIRST" in out and "FOLLOW" in out


def test_missing_file():
    rc, _, err = lumenc("/nonexistent/file.lum")
    assert rc == 3 and "cannot read" in err


def test_no_arguments():
    rc, _, err = lumenc()
    assert rc == 3 and "usage" in err.lower()


def test_binary_garbage_file_is_a_clean_error(tmp_path):
    f = tmp_path / "bin.lum"
    f.write_bytes(bytes(range(128, 256)) * 4)
    rc, out, err = lumenc(str(f), "--no-color")
    assert rc == 3 and "Traceback" not in err


def test_ablation_switches_change_the_diagnostic_count(tmp_path):
    f = write(tmp_path, "k.lum", open(os.path.join(ROOT, "examples", "demo.lum")).read())
    _, full, _ = lumenc(f, "--no-color", "--check")
    _, naive, _ = lumenc(f, "--no-color", "--check", "--no-phrase-level", "--sync", "semicolon",
                         "--poison", "none", "--no-token-poison", "--error-gap", "0",
                         "--no-mute-damaged", "--no-infer-lost-decl", "--no-suppress")
    assert "7 errors generated" in full
    count = int(naive.split(" errors generated")[0].split()[-1])
    assert count > 7           # with every technique off the same file reports far more


def test_fail_fast_reports_one(tmp_path):
    f = write(tmp_path, "l.lum", "int a = x;\nint b = y;\n")
    _, out, _ = lumenc(f, "--no-color", "--fail-fast")
    assert "1 error generated" in out


def test_time_flag(tmp_path):
    f = write(tmp_path, "m.lum", "print(1);\n")
    rc, out, _ = lumenc(f, "--time", "--no-color")
    assert rc == 0 and "timings:" in out and "TAC steps" in out


def test_version():
    rc, out, _ = lumenc("--version")
    assert rc == 0 and "lumenc 3." in out
