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


# --------------------------------------------------------------------------- #
# The per-record ceiling (SPEC.md §1).
#
# Each record must be written with one append and stay under PIPE_BUF, because
# that is the whole reason several processes can share one debug.info. These run
# in-process against `_bounded` directly: the end-to-end parity tests exercise
# the same promise, but through a subprocess, where `pytest --cov` cannot see it.
# --------------------------------------------------------------------------- #

def _record(**over):
    rec = {"p": "in", "t": "2026-08-29T00:00:00.001", "id": "abc-123",
           "ci": 0, "th": "t1", "fn": "m.many", "a": "", "k": ""}
    rec.update(over)
    return rec


def test_short_record_is_left_exactly_as_it_was():
    rec = _record(a="1, 2, 3")

    line = runtime._bounded(rec)

    assert json.loads(line) == rec
    assert runtime._ELLIPSIS not in line


def test_thirty_large_arguments_fit_under_the_ceiling():
    """The measured failure: 30 args produced a 6208-byte line and the kernel
    tore it, after which the parser counted both halves as malformed."""

    rec = _record(a=", ".join("'" + "x" * 200 + "'" for _ in range(30)))

    line = runtime._bounded(rec)

    assert len(line.encode("utf-8")) <= runtime.MAX_RECORD_BYTES
    assert json.loads(line)["a"].endswith(runtime._ELLIPSIS)


def test_one_enormous_argument_also_fits():
    """Overflow can come from thirty ordinary values or from one huge one."""

    rec = _record(a="'" + "y" * 100_000 + "'")

    line = runtime._bounded(rec)

    assert len(line.encode("utf-8")) <= runtime.MAX_RECORD_BYTES


def test_identifying_fields_are_never_shortened():
    """fn/id/t are what make a record identifiable; shortening them would make
    an over-long record unattributable instead of merely incomplete."""

    rec = _record(fn="m." + "n" * 300, a="z" * 8000)

    got = json.loads(runtime._bounded(rec))

    assert got["fn"] == rec["fn"]
    assert got["id"] == rec["id"]
    assert got["t"] == rec["t"]


def test_shortening_never_splits_a_character():
    """Half a character would make the whole line undecodable, losing the record
    that shortening exists to preserve."""

    rec = _record(a="'" + "ё" * 40_000 + "'")

    line = runtime._bounded(rec)

    assert len(line.encode("utf-8")) <= runtime.MAX_RECORD_BYTES
    json.loads(line)  # decodes, so no character was cut in half
    line.encode("utf-8").decode("utf-8")


def test_every_shortened_field_is_marked_and_untouched_ones_are_not():
    rec = _record(a="a" * 6000, k="k=1")

    got = json.loads(runtime._bounded(rec))

    assert got["a"].endswith(runtime._ELLIPSIS)
    assert got["k"] == "k=1"
