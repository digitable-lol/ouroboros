"""Tests for the CLI front-end."""

from __future__ import annotations

import io
import json
import sys

from ouroboros import cli


def _run(argv, stdin=""):
    sys.stdin = io.StringIO(stdin)
    try:
        return cli.main(argv)
    finally:
        sys.stdin = sys.__stdin__


def test_languages(capsys):
    assert _run(["languages"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "python" in out["languages"]


def test_wrap_snippet(capsys):
    rc = _run(["wrap-snippet", "-l", "python"], stdin="def f(x):\n    return x\n")
    assert rc == 0
    out = capsys.readouterr().out
    assert "_ouro_log" in out


def test_wrap_snippet_corrupted(capsys):
    rc = _run(["wrap-snippet", "-l", "python"], stdin="def broken(:\n")
    assert rc == 1


def test_wrap_file_stdout(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")
    rc = _run(["wrap-file", str(f), "--stdout"])
    assert rc == 0
    assert "_ouro_log" in capsys.readouterr().out
    # --stdout must not modify the file
    assert "_ouro_log" not in f.read_text(encoding="utf-8")


def test_wrap_file_in_place(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")
    rc = _run(["wrap-file", str(f)])
    assert rc == 0
    assert "_ouro_log" in f.read_text(encoding="utf-8")


def test_full_workflow(tmp_path, capsys):
    base = str(tmp_path / "site")
    assert _run(["create", base]) == 0
    capsys.readouterr()

    rc = _run(["write", base, "main.py"], stdin="def hi(n):\n    return n\n\nprint(hi('x'))\n")
    assert rc == 0
    capsys.readouterr()

    rc = _run(["execute", base, "--", sys.executable, "main.py"])
    assert rc == 0
    assert "x" in capsys.readouterr().out

    rc = _run(["finish", base])
    assert rc == 0
    finished = json.loads(capsys.readouterr().out)
    assert "main.py" in finished["synced"]


def test_execute_requires_command(tmp_path):
    base = str(tmp_path / "site")
    _run(["create", base])
    assert _run(["execute", base, "--"]) == 2
