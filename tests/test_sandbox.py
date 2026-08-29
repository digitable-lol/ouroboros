"""Sandbox layer: project/git lifecycle, wrap-on-save CRUD, execute, finish."""

from __future__ import annotations

import sys

import pytest

from ouroboros.languages import CorruptedSourceError
from ouroboros.sandbox import (
    Project,
    SandboxError,
    delete_file,
    execute,
    finish,
    list_files,
    read_file,
    write_file,
)
from ouroboros.sandbox.project import (
    CLEAN_DIRNAME,
    DRAFT_DIRNAME,
    RUNTIME_FILENAME,
)


@pytest.fixture
def project(tmp_path) -> Project:
    return Project.create(tmp_path / "site")


def test_create_provisions_draft_git_and_runtime(tmp_path):
    proj = Project.create(tmp_path / "site")
    assert proj.draft.name == DRAFT_DIRNAME
    assert proj.clean.name == CLEAN_DIRNAME
    assert (proj.draft / ".git").is_dir()
    assert (proj.draft / RUNTIME_FILENAME).is_file()
    assert (proj.draft / ".gitignore").read_text().strip() == "debug.info"
    assert proj.git_log() == ["ouroboros: init draft"]


def test_create_rejects_existing_then_exist_ok(tmp_path):
    Project.create(tmp_path / "site")
    with pytest.raises(SandboxError):
        Project.create(tmp_path / "site")
    # exist_ok re-opens instead of failing
    reopened = Project.create(tmp_path / "site", exist_ok=True)
    assert reopened.draft.exists()


def test_write_python_wraps_and_commits(project):
    out = write_file(project, "calc.py", "def add(a, b):\n    return a + b\n")
    assert out.wrapped and out.language == "python"
    assert out.functions_wrapped == 1
    saved = read_file(project, "calc.py")
    assert "_ouro_log" in saved
    assert "calc.py" in list_files(project)
    # one commit per operation
    assert project.git_log()[0].startswith("ouroboros: write calc.py")


def test_rewriting_identical_content_still_commits(project):
    src = "def f(x):\n    return x\n"
    write_file(project, "x.py", src)
    n_before = len(project.git_log())
    # identical content: git would say "nothing to commit"; --allow-empty keeps
    # the one-commit-per-operation invariant instead of erroring.
    write_file(project, "x.py", src)
    assert len(project.git_log()) == n_before + 1


def test_write_unsupported_extension_passthrough(project):
    out = write_file(project, "notes.txt", "hello\n")
    assert not out.wrapped
    assert out.language is None
    assert read_file(project, "notes.txt") == "hello\n"


def test_write_corrupted_python_is_rejected(project):
    with pytest.raises(CorruptedSourceError):
        write_file(project, "bad.py", "def broken(:\n")
    # nothing was written or committed
    assert "bad.py" not in list_files(project)
    assert all("bad.py" not in s for s in project.git_log())


def test_path_escape_is_blocked(project):
    with pytest.raises(SandboxError):
        write_file(project, "../escape.py", "def f():\n    return 1\n")


def test_delete_file(project):
    write_file(project, "x.py", "def f():\n    return 1\n")
    delete_file(project, "x.py")
    assert "x.py" not in list_files(project)
    assert project.git_log()[0] == "ouroboros: delete x.py"


def test_execute_runs_and_writes_debug_info(project):
    # a draft program whose wrapped function logs to debug.info when run
    write_file(
        project,
        "main.py",
        "def greet(name):\n    return 'hi ' + name\n\nprint(greet('world'))\n",
    )
    res = execute(project, [sys.executable, "main.py"])
    assert res.returncode == 0
    assert "hi world" in res.stdout

    import json

    from ouroboros.trace import load

    text = project.debug_info_path().read_text(encoding="utf-8")
    # structured runtime record from the injected decorator
    loaded = load(text)
    assert loaded.malformed == 0
    greet = [c for c in loaded.calls if c.name == "greet"]
    assert len(greet) == 1
    assert greet[0].outcome_kind == "result" and greet[0].outcome == "'hi world'"
    # JSONL `exec` meta record with captured stdout + exit code
    execs = [json.loads(ln) for ln in text.splitlines()
             if ln.strip() and json.loads(ln).get("p") == "exec"]
    assert len(execs) == 1
    assert execs[0]["rc"] == 0 and "hi world" in execs[0]["out"]


def test_execute_nonzero_exit_captured(project):
    write_file(project, "boom.py", "raise SystemExit(3)\n")
    res = execute(project, [sys.executable, "boom.py"])
    assert res.returncode == 3


def test_finish_mirrors_draft_excluding_git_and_debug_info(project):
    write_file(project, "main.py", "def f():\n    return 1\n")
    execute(project, [sys.executable, "-c", "print('hi')"])
    assert project.debug_info_path().exists()

    synced = finish(project)
    clean = project.clean
    assert (clean / "main.py").is_file()
    assert (clean / RUNTIME_FILENAME).is_file()
    assert not (clean / ".git").exists()
    assert not (clean / "debug.info").exists()
    assert "main.py" in synced
    assert "debug.info" not in synced
