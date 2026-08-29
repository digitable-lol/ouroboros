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
import inspect
import json
import os
import reprlib
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

#: Marks this file as the sink itself, so the Python transformer leaves it
#: alone. It has to: the sandbox copies this module out as
#: ``ouroboros_runtime.py``, and instrumented code imports the decorator FROM
#: it. Wrapping it makes the file import itself (a circular import that kills
#: every instrumented program on line 1) and, if that somehow resolved, would
#: make each log write log itself. Wrapping the tool's own tree is the sharpest
#: test it has, and this one file is the part that must stay out of the loop.
OUROBOROS_RUNTIME_MODULE = True

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


#: Hard ceiling on one record, in bytes including the newline. ``PIPE_BUF`` is
#: 4096 on Linux, and SPEC.md §1 promises each record is written with a single
#: append and stays under it — that promise is what lets several processes share
#: one debug.info. Per-value short reprs alone do not deliver it: a call with 30
#: arguments produced a 6208-byte line, the kernel tore it, and the parser
#: silently counted both halves as malformed and dropped them.
MAX_RECORD_BYTES = 4096

#: Fields that may be shortened to fit. ``fn``/``id``/``t`` are what makes a torn
#: record identifiable at all, so they are never touched.
_SHRINKABLE = ("a", "k", "r", "x")

_ELLIPSIS = "…"


def _encode(obj: dict[str, Any]) -> str:
    # Compact separators, non-ASCII left intact (so reprs read naturally).
    # ``\n`` written once so a single O_APPEND write carries a whole record.
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"


def _bounded(obj: dict[str, Any]) -> str:
    """Encode ``obj``, halving its longest value field until the line fits.

    Halving rather than a fixed per-field cut because the overflow can come from
    one enormous argument or from thirty ordinary ones, and the same rule has to
    handle both. Every field it touches ends in an ellipsis, so a reader can tell
    a shortened value from a complete one.
    """

    line = _encode(obj)
    if len(line.encode("utf-8")) <= MAX_RECORD_BYTES:
        return line
    obj = dict(obj)
    trimmed: set[str] = set()
    budget = MAX_RECORD_BYTES - 64  # headroom for the ellipsis markers added below
    for _ in range(64):
        field = max(_SHRINKABLE, key=lambda name: len(obj.get(name, "")))
        value = obj.get(field, "")
        if not value:
            break
        obj[field] = value[: len(value) // 2]
        trimmed.add(field)
        if len(_encode(obj).encode("utf-8")) <= budget:
            break
    for field in trimmed:
        obj[field] += _ELLIPSIS
    return _encode(obj)


def _writeln(obj: dict[str, Any]) -> None:
    with _WRITE_LOCK, open(_debug_info_path(), "a", encoding="utf-8") as fh:
        fh.write(_bounded(obj))


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


def _adopt[F: Callable[..., Any]](fn: F, wrapper: Callable[..., Any]) -> F:
    """Make ``wrapper`` pass for ``fn`` under introspection.

    ``functools.update_wrapper`` copies name/qualname/doc/module/dict and sets
    ``__wrapped__``, but NOT ``__defaults__`` / ``__kwdefaults__`` — so code that
    reads ``f.__defaults__`` (argparse-style helpers, serializers, test tooling)
    saw ``None`` on every wrapped function. They are inert on the wrapper's own
    ``(*args, **kwargs)`` signature, so copying them is free and restores the
    answer the caller expects.
    """

    functools.update_wrapper(wrapper, fn)
    wrapper.__defaults__ = getattr(fn, "__defaults__", None)
    wrapper.__kwdefaults__ = getattr(fn, "__kwdefaults__", None)
    return wrapper  # type: ignore[return-value]


def log[F: Callable[..., Any]](fn: F) -> F:
    """Wrap ``fn`` so each call is recorded to ``debug.info``.

    The return value is captured and logged *before* it leaves the wrapper —
    the Python analogue of the ``return (__result = expr)`` invariant — and
    exceptions are logged then re-raised so the original control flow is
    unchanged.

    The wrapper is built in the *same flavour* as the function it wraps:
    ``async def`` for a coroutine, a generator for a generator, an async
    generator for an async generator. A single plain-function wrapper would have
    been simpler, but it makes ``inspect.isgeneratorfunction`` and
    ``inspect.iscoroutinefunction`` answer ``False`` for a function that plainly
    is one — and those two answers steer real dispatch code (frameworks decide
    whether to ``await`` a handler by asking exactly that question). Losing them
    changes what the program does, which is the one thing instrumentation must
    never do.
    """

    if inspect.isasyncgenfunction(fn):
        return _adopt(fn, _async_gen_wrapper(fn))
    if inspect.iscoroutinefunction(fn):
        return _adopt(fn, _coroutine_wrapper(fn))
    if inspect.isgeneratorfunction(fn):
        return _adopt(fn, _generator_wrapper(fn))
    return _adopt(fn, _plain_wrapper(fn))


def _plain_wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = _Call(fn, args, kwargs)
        try:
            result = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — we log then re-raise
            call.raised(e)
            raise
        call.returned(result)
        return result

    return wrapper


def _coroutine_wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = _Call(fn, args, kwargs)
        try:
            result = await fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — we log then re-raise
            call.raised(e)
            raise
        call.returned(result)
        return result

    return wrapper


def _generator_wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Entry is recorded when iteration starts, not when the generator object
        # is created — for a generator function those are different moments, and
        # the first is the one that means "the body ran".
        call = _Call(fn, args, kwargs)
        try:
            result = yield from fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — we log then re-raise
            call.raised(e)
            raise
        call.returned(result)
        return result

    return wrapper


def _async_gen_wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = _Call(fn, args, kwargs)
        try:
            async for item in fn(*args, **kwargs):
                yield item
        except BaseException as e:  # noqa: BLE001 — we log then re-raise
            call.raised(e)
            raise
        # An async generator cannot carry a return value, so there is none to log.
        call.returned(None)

    return wrapper


class _Call:
    """One in-flight call: emits the ``in`` record now, the ``out`` record later.

    Shared by all four wrapper flavours so the record schema is written in
    exactly one place.
    """

    __slots__ = ("_id", "_qualname", "_t0")

    def __init__(self, fn: Callable[..., Any], args: tuple[Any, ...],
                 kwargs: dict[str, Any]) -> None:
        self._qualname = fn.__qualname__
        self._id = uuid.uuid4()
        started = _now_iso()
        # Snapshot argument reprs BEFORE the call so mutation inside fn does not
        # rewrite the logged inputs.
        args_repr, kwargs_repr = _render_args(args, kwargs)
        _emit_entry(started, self._id, self._qualname, args_repr, kwargs_repr)
        # Monotonic clock for duration (perf_counter), started AFTER the entry
        # record is written so the sink's own write cost is not charged to the
        # call being measured.
        self._t0 = time.perf_counter()

    def returned(self, result: Any) -> None:
        _emit(self._id, self._qualname, result=result,
              duration=time.perf_counter() - self._t0)

    def raised(self, exc: BaseException) -> None:
        _emit(self._id, self._qualname, exc=exc,
              duration=time.perf_counter() - self._t0)
