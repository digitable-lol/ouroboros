"""Tests for the injected runtime logging helper (JSONL sink)."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
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


# --------------------------------------------------------------------------- #
# The four flavours of wrapper.
#
# A single plain-function wrapper would be simpler, but it makes
# `inspect.iscoroutinefunction` and friends answer False for a function that
# plainly is one — and frameworks decide whether to `await` a handler by asking
# exactly that. Losing the answer changes what the program does, which is the
# one thing instrumentation must never do. So each flavour is checked twice:
# the trace it writes, and the question introspection still answers about it.
# --------------------------------------------------------------------------- #

def _drive(gen):
    """Run a generator to exhaustion; return (yielded items, return value)."""

    items = []
    while True:
        try:
            items.append(next(gen))
        except StopIteration as stop:
            return items, stop.value


async def _drain(agen):
    return [item async for item in agen]


def test_a_coroutine_is_still_a_coroutine_after_wrapping(debug_info):
    @runtime.log
    async def fetch(n):
        return n * 2

    assert inspect.iscoroutinefunction(fetch)
    assert asyncio.run(fetch(21)) == 42
    c = _calls(debug_info)[0]
    assert c.name.endswith("fetch") and c.args == "21"
    assert c.outcome_kind == "result" and c.outcome == "42"


def test_a_coroutine_that_raises_is_logged_and_re_raised(debug_info):
    @runtime.log
    async def fail():
        raise RuntimeError("async nope")

    with pytest.raises(RuntimeError, match="async nope"):
        asyncio.run(fail())
    c = _calls(debug_info)[0]
    assert c.outcome_kind == "raised" and c.outcome == "RuntimeError: async nope"


def test_a_generator_is_still_a_generator_after_wrapping(debug_info):
    @runtime.log
    def counter(n):
        total = 0
        for i in range(n):
            total += i
            yield i
        return f"summed {total}"

    assert inspect.isgeneratorfunction(counter)
    items, returned = _drive(counter(3))
    assert items == [0, 1, 2]
    assert returned == "summed 3"      # the value `yield from` hands back
    c = _calls(debug_info)[0]
    assert c.outcome_kind == "result" and c.outcome == "'summed 3'"


def test_a_generator_is_logged_when_it_runs_not_when_it_is_created(debug_info):
    """For a generator function, "called" and "the body ran" are different
    moments, and only the second one means anything in a trace."""

    @runtime.log
    def counter():
        yield 1

    gen = counter()
    assert not debug_info.exists()                        # nothing has run yet

    next(gen)
    assert [ln["p"] for ln in _lines(debug_info)] == ["in"]  # entered, not done


def test_a_generator_that_raises_is_logged_and_re_raised(debug_info):
    @runtime.log
    def counter():
        yield 1
        raise ValueError("mid-stream")

    gen = counter()
    assert next(gen) == 1
    with pytest.raises(ValueError, match="mid-stream"):
        next(gen)
    c = _calls(debug_info)[0]
    assert c.outcome_kind == "raised" and c.outcome == "ValueError: mid-stream"


def test_an_async_generator_is_still_an_async_generator_after_wrapping(debug_info):
    @runtime.log
    async def stream(n):
        for i in range(n):
            yield i

    assert inspect.isasyncgenfunction(stream)
    assert asyncio.run(_drain(stream(3))) == [0, 1, 2]
    c = _calls(debug_info)[0]
    # An async generator carries no return value, so there is none to log.
    assert c.outcome_kind == "result" and c.outcome == "None"


def test_an_async_generator_that_raises_is_logged_and_re_raised(debug_info):
    @runtime.log
    async def stream():
        yield 1
        raise KeyError("async mid-stream")

    with pytest.raises(KeyError):
        asyncio.run(_drain(stream()))
    c = _calls(debug_info)[0]
    assert c.outcome_kind == "raised" and c.outcome.startswith("KeyError:")


def test_a_plain_function_is_not_mistaken_for_any_of_the_three(debug_info):
    @runtime.log
    def plain():
        return 1

    assert not inspect.iscoroutinefunction(plain)
    assert not inspect.isgeneratorfunction(plain)
    assert not inspect.isasyncgenfunction(plain)


@pytest.mark.parametrize("flavour", ["plain", "coroutine", "generator", "asyncgen"])
def test_the_wrapper_keeps_the_defaults_the_caller_can_read(debug_info, flavour):
    """`functools.update_wrapper` copies name/doc/module but NOT `__defaults__`
    or `__kwdefaults__`, so code that reads them off a function — argparse-style
    helpers, serializers, test tooling — saw None on every wrapped function."""

    def plain(a, b=2, *, c=3):
        return a

    async def coroutine(a, b=2, *, c=3):
        return a

    def generator(a, b=2, *, c=3):
        yield a

    async def asyncgen(a, b=2, *, c=3):
        yield a

    original = {"plain": plain, "coroutine": coroutine,
                "generator": generator, "asyncgen": asyncgen}[flavour]
    wrapped = runtime.log(original)

    assert wrapped.__defaults__ == (2,)
    assert wrapped.__kwdefaults__ == {"c": 3}
    assert wrapped.__name__ == flavour
    assert inspect.signature(wrapped) == inspect.signature(original)


# --------------------------------------------------------------------------- #
# Two record-ceiling cases the direct `_bounded` tests above do not reach,
# because both need a real call to produce them.
# --------------------------------------------------------------------------- #

def test_a_long_exception_message_is_shortened(debug_info):
    """`x` is the one value that does NOT go through the short-repr: it is built
    as "Type: message", and the message is whatever the exception carries — a
    dumped structure, a diff, a kernel string. Nothing bounds it but this."""

    @runtime.log
    def boom():
        raise ValueError("z" * 10_000)

    with pytest.raises(ValueError):
        boom()

    out = _lines(debug_info)[1]
    assert len(runtime._encode(out).encode("utf-8")) <= runtime.MAX_RECORD_BYTES
    assert out["x"].startswith("ValueError: ") and out["x"].endswith(runtime._ELLIPSIS)


def test_the_name_wins_when_nothing_else_can_be_cut(debug_info):
    """When the qualified name alone overruns the record, the record goes over
    the ceiling rather than losing the one field that makes it identifiable. A
    too-long record can still be attributed to its call; a nameless one cannot
    be attributed at all."""

    scope: dict[str, object] = {"log": runtime.log}
    name = "N" * 4200
    exec(f"class {name}:\n"
         f"    @log\n"
         f"    def m(self):\n"
         f"        return 1\n", scope)
    scope[name]().m()

    entry = _lines(debug_info)[0]
    assert entry["fn"].startswith(name)              # kept whole, ceiling or no
    # `a` held the instance repr; halved away to nothing, it keeps only the mark
    # that says something was there. `k` was empty all along and is left alone.
    assert entry["a"] == runtime._ELLIPSIS and entry["k"] == ""
    assert len(runtime._encode(entry).encode("utf-8")) > runtime.MAX_RECORD_BYTES


# --------------------------------------------------------------------------- #
# The `ci` field.
#
# `os.sched_getcpu` is Linux-only and even there the interpreter exposes it only
# if it was built against a libc that offers it — the interpreter this is
# measured on does not, so the reading path below never runs here. It runs on
# the machines the traces are collected on, which is why it is pinned with the
# call supplied rather than left to whatever the host happens to have.
# --------------------------------------------------------------------------- #

def test_the_cpu_the_call_ran_on_reaches_the_record(debug_info, monkeypatch):
    monkeypatch.setattr(os, "sched_getcpu", lambda: 3, raising=False)

    @runtime.log
    def f():
        return 1

    f()

    assert _lines(debug_info)[0]["ci"] == 3


def test_a_refused_cpu_reading_is_recorded_as_unknown(debug_info, monkeypatch):
    """The reading is a syscall and may fail (a CPU hot-unplugged under the
    thread, a seccomp policy). Losing the column is acceptable; taking the
    traced program down with an OSError raised from inside the logger is not."""

    def refuse():
        raise OSError("sched_getcpu refused")

    monkeypatch.setattr(os, "sched_getcpu", refuse, raising=False)

    @runtime.log
    def f():
        return 1

    assert f() == 1                                   # the call itself survives
    assert _lines(debug_info)[0]["ci"] == -1          # and the column reads unknown
