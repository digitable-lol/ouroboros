"""Cross-language contract test.

The premise of the project is *universal* logging: every backend must emit the
same JSONL record schema (SPEC.md). This runs the same ``add(2, 3) -> 5`` call
through the Python and JavaScript runtime helpers, parses both traces, and
asserts the records match once the intentionally-volatile / dialect fields are
normalized away:

* ``t`` (timestamp) and ``id`` (uuid) — unique per call,
* ``d`` (duration) — a real measured value, AND rendered per-language (Python
  ``json`` emits ``1e-06`` where JS emits ``0.000001`` for the same number),
* ``ci``/``th`` (CPU / thread identity) — real per-run values AND a per-language
  *dialect* (Python ``th`` is ``pid.thread_ident``, JS is ``pid.threadId``): an
  opaque token, like the repr dialect, not required to match byte-for-byte,
* the repr *dialect* (Python ``'x'`` vs JS ``"x"``) — so we use numeric args /
  result here, which repr identically in both languages.

Pinning this with two backends is cheap; reconciling four after drift is not.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading

import pytest

import ouroboros.runtime as runtime


def _normalize_lines(text: str) -> list[dict]:
    """Parse JSONL and blank out the volatile / dialect fields (t, id, d, ci, th)."""
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        ev = json.loads(ln)
        for k in ("t", "id", "d", "ci", "th"):
            if k in ev:
                ev[k] = "<X>"
        out.append(ev)
    return out


# Module-level so __qualname__ is the bare "add" — matching the bare name JS
# passes. (The qualified-name field is a per-language dialect, like the repr.)
@runtime.log
def add(a, b):
    return a + b


def _python_lines(tmp_path, monkeypatch) -> list[dict]:
    path = tmp_path / "py.debug.info"
    monkeypatch.setenv("OUROBOROS_DEBUG_INFO", str(path))
    add(2, 3)
    return _normalize_lines(path.read_text(encoding="utf-8"))


def _js_lines(tmp_path) -> list[dict]:
    from ouroboros.languages.javascript import JavaScriptTransformer

    name, src = JavaScriptTransformer().runtime_asset()
    (tmp_path / name).write_text(src, encoding="utf-8")
    (tmp_path / "run.js").write_text(
        'const rt = require("./ouroboros_runtime.js");\n'
        'const ctx = rt.enter("add", [2, 3]);\n'
        "rt.exit(ctx, 5);\n",
        encoding="utf-8",
    )
    path = tmp_path / "js.debug.info"
    subprocess.run(
        ["node", "run.js"],
        cwd=str(tmp_path),
        env={"OUROBOROS_DEBUG_INFO": str(path), "PATH": os.environ.get("PATH", "")},
        check=True,
        capture_output=True,
        text=True,
    )
    return _normalize_lines(path.read_text(encoding="utf-8"))


# The schema both backends must emit (SPEC.md), hand-written from the contract —
# NOT pasted from a run — so this catches implementation drift.
_EXPECTED = [
    {"p": "in", "t": "<X>", "id": "<X>", "ci": "<X>", "th": "<X>",
     "fn": "add", "a": "2, 3", "k": ""},
    {"p": "out", "id": "<X>", "fn": "add", "r": "5", "d": "<X>"},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_python_and_js_emit_identical_schema(tmp_path, monkeypatch):
    py = _python_lines(tmp_path, monkeypatch)
    js = _js_lines(tmp_path)
    assert py == js, f"\n--- python ---\n{py}\n--- js ---\n{js}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_js_thread_identity_is_real(tmp_path):
    """Pin the JS `th` VALUE (blanked by the schema test): "<pid>.<threadId>",
    threadId 0 on the main thread — a regression to "" would slip the blanked check."""
    from ouroboros.languages.javascript import JavaScriptTransformer

    name, src = JavaScriptTransformer().runtime_asset()
    (tmp_path / name).write_text(src, encoding="utf-8")
    (tmp_path / "run.js").write_text(
        'const rt = require("./ouroboros_runtime.js");\n'
        'rt.exit(rt.enter("add", [2, 3]), 5);\n', encoding="utf-8")
    path = tmp_path / "js.debug.info"
    subprocess.run(["node", "run.js"], cwd=str(tmp_path),
                   env={"OUROBOROS_DEBUG_INFO": str(path), "PATH": os.environ.get("PATH", "")},
                   check=True, capture_output=True, text=True)
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert re.fullmatch(r"\d+\.0", rec["th"]), rec["th"]   # <pid>.<main threadId=0>
    assert rec["ci"] == -1


def test_python_matches_the_spec_schema(tmp_path, monkeypatch):
    assert _python_lines(tmp_path, monkeypatch) == _EXPECTED


def test_python_thread_cpu_identity_is_real(tmp_path, monkeypatch):
    """The schema tests blank ci/th — so pin the actual captured VALUES here, else a
    regression to th="" would pass them. Python `th` is the exact pid.thread_ident."""
    monkeypatch.setenv("OUROBOROS_DEBUG_INFO", str(tmp_path / "d.info"))

    @runtime.log
    def add(a, b):
        return a + b

    add(2, 3)
    rec = json.loads((tmp_path / "d.info").read_text(encoding="utf-8").splitlines()[0])
    assert rec["th"] == f"{os.getpid()}.{threading.get_ident()}"
    assert isinstance(rec["ci"], int)   # os.sched_getcpu() (Linux) or -1
