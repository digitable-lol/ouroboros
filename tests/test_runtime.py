"""Tests for the injected runtime logging helper (JSONL sink)."""

from __future__ import annotations

import importlib
import json
import re

import pytest

import ouroboros.runtime as runtime
from ouroboros.trace import load


@pytest.fixture
def debug_info(tmp_path, monkeypatch):
    path = tmp_path / "debug.info"
    monkeypatch.setenv("OUROBOROS_DEBUG_INFO", str(path))
    importlib.reload(runtime)  # re-read env-dependent module state if any
    return path


def _calls(path):
    """Completed call records parsed from the JSONL trace."""
    return load(path.read_text(encoding="utf-8")).calls


def _lines(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln]


def test_logs_args_and_result(debug_info):
    @runtime.log
    def add(a, b):
        return a + b

    assert add(2, 3) == 5
    calls = _calls(debug_info)
    assert len(calls) == 1
    c = calls[0]
    assert c.name.endswith("add") and c.args == "2, 3"
    assert c.outcome_kind == "result" and c.outcome == "5"
    assert c.duration is not None


def test_emits_in_and_out_lines(debug_info):
    @runtime.log
    def f():
        return 1

    f()
    lines = _lines(debug_info)
    assert [ln["p"] for ln in lines] == ["in", "out"]
    assert lines[0]["id"] == lines[1]["id"]  # paired by id
    assert lines[0]["fn"].endswith("f") and lines[0]["fn"] == lines[1]["fn"]
    assert "a" in lines[0] and "a" not in lines[1]  # args only on entry
    assert "d" in lines[1]


def test_logs_kwargs(debug_info):
    @runtime.log
    def greet(name, *, loud=False):
        return name

    greet("x", loud=True)
    assert _calls(debug_info)[0].kwargs == "loud=True"


def test_logs_exception_and_reraises(debug_info):
    @runtime.log
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        boom()
    c = _calls(debug_info)[0]
    assert c.outcome_kind == "raised" and c.outcome == "ValueError: nope"
    assert c.duration is not None  # a raised completion still carries a duration


def test_record_has_datetime_and_uuid(debug_info):
    @runtime.log
    def f():
        return 1

    f()
    entry = _lines(debug_info)[0]
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", entry["t"])
    assert re.match(r"[0-9a-f]{8}-[0-9a-f]{4}-", entry["id"])


def test_preserves_qualname(debug_info):
    @runtime.log
    def named(x):
        return x

    assert named.__name__ == "named"


def test_args_snapshotted_before_call(debug_info):
    """A function that mutates its argument must log the pre-call value."""

    @runtime.log
    def consume(items):
        items.clear()
        return len(items)

    consume([1, 2, 3])
    c = _calls(debug_info)[0]
    assert c.args == "[1, 2, 3]"  # logged inputs, not the post-mutation []
    assert c.outcome == "0"


def test_large_values_are_bounded(debug_info):
    @runtime.log
    def big(s):
        return s

    big("z" * 100_000)
    c = _calls(debug_info)[0]
    # reprlib truncates; the record must not contain the full 100k blob
    assert len(c.args) < 5_000 and len(c.outcome) < 5_000


def test_lines_are_valid_jsonl(debug_info):
    @runtime.log
    def quote(s):
        return s

    quote('he said "hi"\nbye\t\\done')
    # every non-empty line must round-trip through json.loads (escaping is correct)
    lines = _lines(debug_info)
    assert all(isinstance(ln, dict) for ln in lines)
    assert lines[0]["a"] == repr('he said "hi"\nbye\t\\done')
