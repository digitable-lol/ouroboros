"""Sandbox layer: project/git lifecycle, wrap-on-save CRUD, execute, finish."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ouroboros.languages import CorruptedSourceError, Transformer, WrapResult
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


# --------------------------------------------------------------------------- #
# Defect: WriteOutcome.committed was the literal True, never derived from what
# git actually did. `git add -A` skips ignored paths, so writing an ignored file
# produced an EMPTY commit and the report still claimed the write was committed.
# --------------------------------------------------------------------------- #


def test_committed_is_derived_not_asserted(project):
    """debug.info is in the draft's .gitignore: `git add -A` never stages it, so
    the write is not in git history and `committed` must say so."""
    out = write_file(project, "debug.info", "runtime artifact, not source\n")

    assert out.committed is False
    assert "debug.info" not in list_files(project)


def test_committed_true_for_a_tracked_file(project):
    """The other side of the same check: a normal source file IS committed."""
    out = write_file(project, "calc.py", "def f():\n    return 1\n")

    assert out.committed is True
    assert "calc.py" in list_files(project)


# --------------------------------------------------------------------------- #
# Defect: finish carried build artifacts (__pycache__ / *.pyc) into the clean
# tree, so the "clean" copy shipped compiled leftovers of the draft.
# --------------------------------------------------------------------------- #


def test_finish_omits_python_bytecode_cache(project):
    write_file(project, "main.py", "def f():\n    return 1\n")
    # running the draft imports the runtime helper, which leaves __pycache__
    execute(project, [sys.executable, "main.py"])
    assert (project.draft / "__pycache__").is_dir()

    synced = finish(project)

    assert not (project.clean / "__pycache__").exists()
    assert all("__pycache__" not in s for s in synced)
    assert all(not s.endswith(".pyc") for s in synced)
    assert "main.py" in synced        # real source still crosses over


def test_finish_twice_rebuilds_the_output_tree(project):
    """The second finish wipes the tree first: a file deleted from the draft must
    not survive in the copy."""
    write_file(project, "keep.py", "def f():\n    return 1\n")
    write_file(project, "gone.py", "def g():\n    return 2\n")
    finish(project)
    assert (project.clean / "gone.py").is_file()

    delete_file(project, "gone.py")
    synced = finish(project)

    assert (project.clean / "keep.py").is_file()
    assert not (project.clean / "gone.py").exists()
    assert "gone.py" not in synced


def test_finish_copies_subdirectories(project):
    out = write_file(project, "pkg/mod.py", "def f():\n    return 1\n")
    # a nested path must also be reported as committed (the tracked-file check
    # runs on a path relative to the draft, not on the bare name)
    assert out.committed is True

    synced = finish(project)

    assert (project.clean / "pkg" / "mod.py").is_file()
    assert str(Path("pkg") / "mod.py") in synced


def test_open_rejects_a_base_without_a_git_repo(tmp_path):
    (tmp_path / "site" / DRAFT_DIRNAME).mkdir(parents=True)
    with pytest.raises(SandboxError, match="no draft git repo"):
        Project.open(tmp_path / "site")


def test_a_failing_git_command_is_reported(tmp_path):
    """A draft carrying a `.git` that is not a repository (a half-copied project):
    open() accepts it on the name, and the first git call must fail loudly."""
    draft = tmp_path / "site" / DRAFT_DIRNAME
    (draft / ".git").mkdir(parents=True)
    proj = Project.open(tmp_path / "site")
    with pytest.raises(SandboxError, match="git ls-files failed"):
        list_files(proj)


def test_write_commits_a_language_that_needs_no_runtime_helper(project, monkeypatch):
    """`Transformer.runtime_asset` may return None (the base class says so). No
    shipped backend does, so drive it with a stand-in: the write must still land
    and commit, and no helper file may appear in the draft."""

    class _NoHelper(Transformer):
        language = "nohelper"
        extensions = (".nohelper",)

        def wrap_source(self, source, *, filename=None, only=None, minimal=False):
            return WrapResult(code=source + "# wrapped\n", language=self.language,
                              functions_wrapped=1)

    monkeypatch.setattr("ouroboros.sandbox.crud.transformer_for_path",
                        lambda rel: _NoHelper())
    before = {p.name for p in project.draft.iterdir()}

    out = write_file(project, "m.nohelper", "body\n")

    assert out.wrapped and out.language == "nohelper" and out.committed is True
    assert read_file(project, "m.nohelper").endswith("# wrapped\n")
    new_files = {p.name for p in project.draft.iterdir()} - before
    assert new_files == {"m.nohelper"}


def test_committed_survives_a_filename_with_glob_characters(project):
    """The tracked-file check hands the name to git as a pathspec; without the
    literal marker a name containing `*` or `?` would be read as a pattern."""
    out = write_file(project, "we[i]rd?name*.py", "def f():\n    return 1\n")

    assert out.committed is True
    assert "we[i]rd?name*.py" in list_files(project)
