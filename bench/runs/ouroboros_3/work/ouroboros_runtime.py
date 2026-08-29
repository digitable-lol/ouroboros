"""Runtime logging helper injected into instrumented Python code.

This module is **stdlib-only and fully self-contained** on purpose: the sandbox
copies this exact file into each draft project as ``ouroboros_runtime.py`` so
the instrumented code is portable and keeps working after the draft is synced
to the clean tree. Instrumented files import it as::

    from ouroboros_runtime import log as _ouro_log

The decorator name carries a single leading underscore (``_ouro_log``) rather
than the project's ``__`` convention because a ``@__log`` reference inside a
class body is silently name-mangled to ``_ClassName__log`` and fails.

Every call appends one record to the ``debug.info`` file in the format the
SKILL contract specifies::

    ШАБЛОН_НАЧАЛО:
        <iso-datetime>: <uuid>: <qualified function name>
        args: (...) kwargs: {...}
        result: <repr>            # or  raised: <ExcType>: <message>
    ШАБЛОН_КОНЕЦ
"""

from __future__ import annotations

import datetime
import functools
import os
import reprlib
import threading
import uuid
from typing import Any, Callable, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

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


def _render_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, str]:
    """Render argument reprs. Called at *entry* so a function that mutates its
    inputs still logs the values it was actually called with."""

    args_repr = ", ".join(_repr.repr(a) for a in args)
    kwargs_repr = ", ".join(f"{k}={_repr.repr(v)}" for k, v in kwargs.items())
    return args_repr, kwargs_repr


def _emit(
    started: str,
    call_id: uuid.UUID,
    qualname: str,
    args_repr: str,
    kwargs_repr: str,
    *,
    result: Any = None,
    exc: BaseException | None = None,
) -> None:
    if exc is not None:
        outcome = f"raised: {type(exc).__name__}: {exc}"
    else:
        outcome = f"result: {_repr.repr(result)}"

    record = (
        "ШАБЛОН_НАЧАЛО:\n"
        f"    {started}: {call_id}: {qualname}\n"
        f"    args: ({args_repr}) kwargs: {{{kwargs_repr}}}\n"
        f"    {outcome}\n"
        "ШАБЛОН_КОНЕЦ\n"
    )
    with _WRITE_LOCK:
        with open(_debug_info_path(), "a", encoding="utf-8") as fh:
            fh.write(record)


def log(fn: _F) -> _F:
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
        try:
            result = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — we log then re-raise
            _emit(started, call_id, fn.__qualname__, args_repr, kwargs_repr, exc=e)
            raise
        _emit(started, call_id, fn.__qualname__, args_repr, kwargs_repr, result=result)
        return result

    return wrapper  # type: ignore[return-value]
