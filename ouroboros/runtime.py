"""Runtime logging helper injected into instrumented Python code.

This module is **stdlib-only and fully self-contained** on purpose: the sandbox
copies this exact file into each draft project as ``ouroboros_runtime.py`` so
the instrumented code is portable and keeps working after the draft is synced
to the clean tree. Instrumented files import it as::

    from ouroboros_runtime import log as _ouro_log

The decorator name carries a single leading underscore (``_ouro_log``) rather
than the project's ``__`` convention because a ``@__log`` reference inside a
class body is silently name-mangled to ``_ClassName__log`` and fails.

Every call appends **two JSONL lines** to the ``debug.info`` file — one when
the call is entered, one when it returns/raises — paired by ``id`` (see
SPEC.md). Short keys keep the file compact::

    {"p":"in","t":"<iso>","id":"<uuid>","ci":<cpu>,"th":"<pid.tid>","fn":"<qualname>","a":"<args>","k":"<kwargs>"}
    {"p":"out","id":"<uuid>","fn":"<qualname>","r":"<repr>","d":<seconds>}
    {"p":"out","id":"<uuid>","fn":"<qualname>","x":"<ExcType: msg>","d":<seconds>}

An ``in`` line with no matching ``out`` = a call that entered but never
completed (hang / crash). ``d`` is the real per-call duration in seconds.
"""

from __future__ import annotations

import datetime
import functools
import json
import os
import reprlib
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

_WRITE_LOCK = threading.Lock()

# Bound the size of logged values so a huge argument/return cannot blow up
# debug.info. Tunable, but deterministic.
_repr = reprlib.Repr()
_repr.maxstring = 200
_repr.maxother = 200
_repr.maxlist = 10
_repr.maxdict = 10
_repr.maxtuple = 10


def _debug_info_path() -> str:
    return os.environ.get("OUROBOROS_DEBUG_INFO", "debug.info")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="milliseconds")


def _thread_token() -> str:
    """Thread identity for the ``th`` field: ``<pid>.<thread_ident>`` — distinguishes
    forked processes AND threads sharing one debug.info (the concurrency signal)."""
    return f"{os.getpid()}.{threading.get_ident()}"


def _cpu() -> int:
    """CPU index for the ``ci`` field. ``os.sched_getcpu`` exists only on Linux;
    elsewhere (macOS/Windows) return -1 (parsed as 'unknown')."""
    getcpu = getattr(os, "sched_getcpu", None)
    if getcpu is None:
        return -1
    try:
        return int(getcpu())
    except OSError:
        return -1


def _render_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, str]:
    """Render argument reprs. Called at *entry* so a function that mutates its
    inputs still logs the values it was actually called with."""

    args_repr = ", ".join(_repr.repr(a) for a in args)
    kwargs_repr = ", ".join(f"{k}={_repr.repr(v)}" for k, v in kwargs.items())
    return args_repr, kwargs_repr


def _writeln(obj: dict[str, Any]) -> None:
    # One JSON object per line; compact separators, non-ASCII left intact (so
    # reprs read naturally). ``\n`` written once so a single O_APPEND write
    # carries a whole record.
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _WRITE_LOCK, open(_debug_info_path(), "a", encoding="utf-8") as fh:
        fh.write(line)


def _emit_entry(
    started: str, call_id: uuid.UUID, qualname: str,
    args_repr: str, kwargs_repr: str,
) -> None:
    """The entry (``p:in``) event, written when the call is entered. A call_id
    with an entry but no ``out`` means it never returned (hang/crash)."""
    _writeln({"p": "in", "t": started, "id": str(call_id),
              "ci": _cpu(), "th": _thread_token(),
              "fn": qualname, "a": args_repr, "k": kwargs_repr})


def _emit(
    call_id: uuid.UUID,
    qualname: str,
    *,
    result: Any = None,
    exc: BaseException | None = None,
    duration: float = 0.0,
) -> None:
    out: dict[str, Any] = {"p": "out", "id": str(call_id), "fn": qualname}
    if exc is not None:
        out["x"] = f"{type(exc).__name__}: {exc}"
    else:
        out["r"] = _repr.repr(result)
    out["d"] = round(duration, 6)
    _writeln(out)


def log[F: Callable[..., Any]](fn: F) -> F:
    """Wrap ``fn`` so each call is recorded to ``debug.info``.

    The return value is captured and logged *before* it leaves the wrapper —
    the Python analogue of the ``return (__result = expr)`` invariant — and
    exceptions are logged then re-raised so the original control flow is
    unchanged.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = _now_iso()
        call_id = uuid.uuid4()
        # Snapshot argument reprs BEFORE the call so mutation inside fn does not
        # rewrite the logged inputs.
        args_repr, kwargs_repr = _render_args(args, kwargs)
        _emit_entry(started, call_id, fn.__qualname__, args_repr, kwargs_repr)
        # Monotonic clock for duration (perf_counter), independent of the wall-clock
        # `started` shown in the record — wall time can jump, perf_counter cannot.
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — we log then re-raise
            _emit(call_id, fn.__qualname__, exc=e, duration=time.perf_counter() - t0)
            raise
        _emit(call_id, fn.__qualname__, result=result,
              duration=time.perf_counter() - t0)
        return result

    return wrapper  # type: ignore[return-value]
