"""The bridge between the flang brain and the Python shell around it.

The brain (``trace_brain.flang``, printed into ``_flang/``) holds every rule
that decides something about a trace. It cannot read a file, decode JSON or
keep a hash index — flang has no input/output inside a function at all, and no
maps — so the shell does those, and asks the brain for every answer.

**This module is the only place where a flang value becomes a Python value or
back.** Scattering the conversion over the call sites is how the two halves
drift apart: each site grows its own idea of what an absent duration or an
unknown CPU looks like, and the rule stops living in one language. Everything
crossing the boundary goes through :func:`to_flang` and :func:`from_flang`, and
everything the trace reader needs goes through :class:`Brain`.

What the brain hands back is flat on purpose: scalars and presence flags, never
an absent field. flang has no null, so ``duration`` arrives as a number plus
``has duration``, and this module — once, here — turns that pair into the
``float | None`` the rest of the code already speaks.

The evaluation context is per-:class:`Brain`, not global: it carries mutable
step and depth counters, and one shared counter across threads would be a race
for no gain. Its step limit is switched off, and that is what ``total`` buys:
the compiler proved every function in the brain terminates, so the limit that
exists to stop a non-terminating one has nothing left to stop. The depth limit
stays — it guards the C stack, which no proof about flang can.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ouroboros.brain._flang import flang_runtime as rt, trace_brain as _brain

__all__ = ["Brain", "FlangError", "Variant", "from_flang", "to_flang"]

FlangError = rt.FlangError


@dataclass(frozen=True)
class Variant:
    """A flang sum value on the Python side: the case name and its fields.

    The name is the whole decision — ``match`` in flang branches on it — so it
    is what the shell branches on too, and it is a string rather than an enum
    because the set of cases is declared in the brain, not here.
    """

    name: str
    fields: dict[str, Any]


def to_flang(value: Any) -> rt.Value:
    """A Python value as a flang value.

    ``bool`` is checked before ``int`` because in Python it is one, and in flang
    a flag and a number are different kinds of value. ``str`` is checked before
    the sequence branch for the same reason: iterating it would give a list of
    one-character strings.
    """

    if value is None:
        return rt.nothing()
    if isinstance(value, bool):
        return rt.flag(value)
    if isinstance(value, int | float):
        return rt.number(value)
    if isinstance(value, str):
        return rt.text(value)
    if isinstance(value, Mapping):
        return rt.record({str(k): to_flang(v) for k, v in value.items()})
    if isinstance(value, Sequence):
        return rt.list_of([to_flang(item) for item in value])
    raise TypeError(f"no flang value for {type(value).__name__}")


def from_flang(value: rt.Value) -> Any:
    """A flang value as a Python value.

    Numbers come back as ``float``: flang has no integers, and rounding one to
    ``int`` here would hide that from every caller. The callers that need an
    index do the rounding themselves, where it is visible.

    This runs once per field of every answer — some hundreds of thousands of
    times over one trace — so the scalar case is settled first and by one
    comparison.
    """

    tag = value.tag
    if tag <= rt.TAG_STRING:
        # Every scalar carries its Python value as the payload, and the absent
        # value carries ``None`` — so one comparison answers for all four, in
        # the order the runtime itself declares them (``rt.is_scalar``).
        return value.data
    if tag == rt.TAG_LIST:
        return [from_flang(item) for item in rt.list_items(value)]
    if tag == rt.TAG_RECORD:
        return {name: from_flang(field) for name, field in value.data.items()}
    if tag == rt.TAG_VARIANT:
        return Variant(value.name,
                       {name: from_flang(field) for name, field in value.data.items()})
    raise TypeError(f"unknown flang tag {tag}")


def _text(source: Mapping[str, Any], key: str) -> rt.Value:
    """A field of a decoded event as a flang string, missing meaning empty."""

    found = source.get(key, "")
    # ``str`` of a string is the string, and the producer writes strings here:
    # spending a call on the common case would show up 200,000 times a trace.
    return rt.text(found if type(found) is str else str(found))


class Brain:
    """The flang brain, with one evaluation context, ready to be asked.

    Cheap to build; build one per reader rather than sharing one across threads.
    """

    def __init__(self) -> None:
        ctx = _brain.new_context()
        ctx.max_steps = 0
        self._ctx = ctx

    # ── one line of a trace ────────────────────────────────────────────────

    def line_kind(self, line: str) -> str:
        """``"Blank"``, ``"Malformed"`` or ``"Candidate"`` for one raw line."""

        return _brain.fn_line_kind(self._ctx, rt.text(line)).name

    def event_kind(self, phase: Any) -> str:
        """``"Entered"``, ``"Returned"`` or ``"Ignored"`` for a decoded ``p``.

        A phase that is not a string at all cannot be either of the two the
        brain knows, so it is handed over as the empty string and comes back
        ignored — the same answer, reached by the same rule.
        """

        text = phase if isinstance(phase, str) else ""
        return _brain.fn_event_kind(self._ctx, rt.text(text)).name

    # ── the two event shapes ───────────────────────────────────────────────

    def entry(self, event: Mapping[str, Any]) -> rt.Value:
        """A decoded ``p:in`` object as a flang ``«Entry»``."""

        # Whether the producer wrote an integer is a question about Python
        # types, so it is answered here; whether that integer means a CPU at all
        # is a rule, and the brain answers it. A bool is an int in Python and
        # never a CPU index, so it is turned away with the missing field.
        cpu = event.get("ci")
        if isinstance(cpu, bool) or not isinstance(cpu, int):
            present, index = False, 0.0
        else:
            present, index = True, float(cpu)
        return _brain.fn_entry_from(
            self._ctx,
            _text(event, "id"), _text(event, "t"), _text(event, "fn"),
            _text(event, "a"), _text(event, "k"),
            rt.flag(present), rt.number(index),
            _text(event, "th"),
        )

    def no_entry(self) -> rt.Value:
        """The empty ``«Entry»`` an exit gets when its entry never arrived."""

        return _brain.fn_no_entry(self._ctx)

    def exit(self, event: Mapping[str, Any]) -> rt.Value:
        """A decoded ``p:out`` object as a flang ``«Exit»``."""

        duration = event.get("d")
        if isinstance(duration, int | float):
            timed, seconds = True, float(duration)
        else:
            timed, seconds = False, 0.0
        return _brain.fn_exit_from(
            self._ctx,
            _text(event, "id"), _text(event, "fn"),
            rt.flag("x" in event), _text(event, "x"),
            rt.flag("r" in event), _text(event, "r"),
            rt.flag(timed), rt.number(seconds),
        )

    # ── the answers ────────────────────────────────────────────────────────

    def completed_call(self, index: int, entry: rt.Value, exit: rt.Value) -> dict[str, Any]:
        """One completed call, keyed the way :class:`ouroboros.trace.Record` is."""

        call = from_flang(_brain.fn_completed_call(self._ctx, rt.number(index), entry, exit))
        return {
            "index": int(call["index"]),
            "started": call["started"],
            "call_id": call["call id"],
            "name": call["name"],
            "args": call["args"],
            "kwargs": call["kwargs"],
            "outcome_kind": call["outcome kind"],
            "outcome": call["outcome"],
            "duration": call["duration"] if call["has duration"] else None,
            "cpu": int(call["cpu"]) if call["has cpu"] else None,
            "thread": call["thread"],
        }

    def flight_view(self, entry: rt.Value) -> dict[str, Any]:
        """What an unfinished call shows: everything its entry knew."""

        flight = from_flang(_brain.fn_flight_view(self._ctx, entry))
        return {
            "name": flight["name"],
            "call_id": flight["call id"],
            "started": flight["started"],
            "cpu": int(flight["cpu"]) if flight["has cpu"] else None,
            "thread": flight["thread"],
        }

    def same_call(self, entry: rt.Value, exit: rt.Value) -> bool:
        """Does this exit belong to this entry? The rule the shell index obeys."""

        return bool(from_flang(_brain.fn_same_call(self._ctx, entry, exit)))

    def is_in_flight(self, entry: rt.Value, completed_ids: Sequence[str]) -> bool:
        """Did this entry never come back? The definition, scanning the ids.

        Linear in the number of completed calls, so the reader uses a hash set
        instead and the tests hold the two answers against each other.
        """

        ids = rt.list_of([rt.text(one) for one in completed_ids])
        return bool(from_flang(_brain.fn_is_in_flight(self._ctx, entry, ids)))

    # ── cutting values down to size ────────────────────────────────────────

    @property
    def value_characters_limit(self) -> int:
        """How much of one rendered value survives."""

        return int(from_flang(_brain.fn_value_characters_limit(self._ctx)))

    @property
    def list_items_limit(self) -> int:
        """How many items of one rendered list survive."""

        return int(from_flang(_brain.fn_list_items_limit(self._ctx)))

    @property
    def record_bytes_limit(self) -> int:
        """The ceiling one written record stays under."""

        return int(from_flang(_brain.fn_record_bytes_limit(self._ctx)))

    @property
    def shrink_budget(self) -> int:
        """The ceiling less the room the ellipsis markers need."""

        return int(from_flang(_brain.fn_shrink_budget(self._ctx)))

    def shorten_text(self, text: str, limit: int) -> str:
        """A rendered value cut to ``limit`` characters, ellipsis in the middle."""

        return str(from_flang(
            _brain.fn_shorten_text(self._ctx, rt.text(text), rt.number(limit))))

    def shorten_items(self, pieces: Sequence[str], limit: int) -> list[str]:
        """Rendered items cut to ``limit``, with one marker standing for the rest."""

        items = rt.list_of([rt.text(one) for one in pieces])
        return [str(one) for one in
                from_flang(_brain.fn_shorten_items(self._ctx, items, rt.number(limit)))]

    def halved(self, text: str) -> str:
        """Half of a value field — one step of fitting a record under the ceiling."""

        return str(from_flang(_brain.fn_halved(self._ctx, rt.text(text))))

    def longest_field(self, args: str, kwargs: str, produced: str, raised: str) -> str:
        """Which of the four value fields to halve next; ties go to the earlier one."""

        return _brain.fn_longest_field(
            self._ctx, rt.text(args), rt.text(kwargs), rt.text(produced),
            rt.text(raised)).name

    def anything_to_shrink(self, args: str, kwargs: str, produced: str,
                           raised: str) -> bool:
        """Is there anything left to halve, or is the record as small as it gets?"""

        return bool(from_flang(_brain.fn_anything_to_shrink(
            self._ctx, rt.text(args), rt.text(kwargs), rt.text(produced),
            rt.text(raised))))

    def record_fits(self, size: int) -> bool:
        """Does a record of this many bytes fit under the ceiling?"""

        return bool(from_flang(_brain.fn_record_fits(self._ctx, rt.number(size))))

    def within_shrink_budget(self, size: int) -> bool:
        """Is a record of this many bytes small enough to stop halving?"""

        return bool(from_flang(
            _brain.fn_within_shrink_budget(self._ctx, rt.number(size))))
