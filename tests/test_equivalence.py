"""Behaviour equivalence: a wrapped program must behave exactly like the plain one.

This is the suite that guards the tool's central promise. Every program in
``equivalence_cases`` is built and run twice — once as written, once after
``wrap_source`` — and the exit code, stdout and stderr must match to the byte.

Why whole programs and not assertions on the transformer's output: the failures
that matter here are the ones no output inspection would flag. A dropped
``"use strict"`` leaves valid code that runs in the wrong mode. A demoted module
docstring leaves valid code whose ``__doc__`` is now ``None``. A captured return
value silently costs a copy elision, so a constructor runs that did not run
before. Only the real runtime notices those.

The instrumented copy gets its own directory and its own ``debug.info``, so the
sink never collides between the two runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest
from equivalence_cases import CASES, EXTRA_CASES, Case

from ouroboros.languages import transformer_for_language

TIMEOUT = 180

#: How each language turns a source file into something runnable. ``compile`` is
#: the argv template (``{}`` = source file name) or ``None`` for interpreted
#: languages; ``run`` is the argv used afterwards.
_TOOLCHAIN = {
    # None: no external tool to look for — the running interpreter is used.
    "python": (None, None),
    "javascript": ("node", None),
    "elixir": ("elixir", None),
    "c": ("gcc", ["gcc", "-std=gnu11"]),
    "cpp": ("g++", ["g++", "-std=c++17"]),
}


def _have(tool: str | None) -> bool:
    return tool is None or shutil.which(tool) is not None


def _run(argv: list[str], cwd, env_extra: dict[str, str] | None = None):
    env = {**os.environ, **(env_extra or {})}
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return ("TIMEOUT", "", "")
    except OSError as e:
        # e.g. Exec format error when a `#!` line stopped being line 1 — a
        # behaviour difference, not a harness failure.
        return ("EXEC-FAILED", "", f"{type(e).__name__}: {e}")
    return p.returncode, p.stdout, p.stderr


def _materialise(case: Case, root, code: str, *, wrapped: bool) -> None:
    """Write one variant of the program into its own directory."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / case.filename
    path.write_text(code, encoding="utf-8")
    if case.executable:
        path.chmod(0o755)
    if not wrapped:
        return
    tx = transformer_for_language(case.lang)
    asset = tx.runtime_asset()
    if asset is not None:
        name, text = asset
        (root / name).write_text(text, encoding="utf-8")


def _execute(case: Case, root, *, wrapped: bool):
    """Build (if the language needs it) and run, returning (rc, stdout, stderr).

    A build failure is reported as its own outcome rather than an exception, so
    'the wrapped copy no longer compiles' shows up as a behaviour difference —
    which is exactly what it is.
    """
    _tool, compile_argv = _TOOLCHAIN[case.lang]
    if compile_argv is not None:
        rc, out, err = _run([*compile_argv, case.filename, "-o", "prog.bin"], root)
        if rc != 0:
            return ("BUILD-FAILED", out, err)
        return _run(["./prog.bin"], root, _debug_env(root, wrapped))
    if case.executable:
        return _run([f"./{case.filename}"], root, _debug_env(root, wrapped))
    interp = {"python": [sys.executable], "javascript": ["node"],
              "elixir": ["elixir"]}[case.lang]
    return _run([*interp, case.filename], root, _debug_env(root, wrapped))


def _debug_env(root, wrapped: bool) -> dict[str, str]:
    return {"OUROBOROS_DEBUG_INFO": str(root / "debug.info")} if wrapped else {}


def _wrapped_code(case: Case) -> str:
    tx = transformer_for_language(case.lang)
    res = tx.wrap_source(case.source, filename=case.filename)
    if case.lang == "elixir":
        # The trace module is a separate .ex file; a script has to compile it
        # before the instrumented module refers to it.
        asset = tx.runtime_asset()
        assert asset is not None
        return f'Code.require_file("{asset[0]}")\n' + res.code
    return res.code


def _check(case: Case, tmp_path) -> None:
    tool, _ = _TOOLCHAIN[case.lang]
    if not _have(tool):
        pytest.skip(f"{tool} not installed")
    plain_dir, wrap_dir = tmp_path / "plain", tmp_path / "wrap"
    _materialise(case, plain_dir, case.source, wrapped=False)
    _materialise(case, wrap_dir, _wrapped_code(case), wrapped=True)
    plain = _execute(case, plain_dir, wrapped=False)
    wrapped = _execute(case, wrap_dir, wrapped=True)
    assert plain[0] != "BUILD-FAILED", f"baseline does not build: {plain[2][:400]}"
    assert wrapped == plain, (
        f"instrumentation changed the program.\n"
        f"  plain  : rc={plain[0]!r}\n    out={plain[1]!r}\n    err={plain[2][-500:]!r}\n"
        f"  wrapped: rc={wrapped[0]!r}\n    out={wrapped[1]!r}\n    err={wrapped[2][-500:]!r}"
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.ident)
def test_wrapped_behaves_identically(case: Case, tmp_path) -> None:
    _check(case, tmp_path)


@pytest.mark.parametrize("case", EXTRA_CASES, ids=lambda c: c.ident)
def test_wrapped_behaves_identically_extra(case: Case, tmp_path) -> None:
    _check(case, tmp_path)
