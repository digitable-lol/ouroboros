"""Parse and query Ouroboros trace files — **JSONL** (one JSON object per line),
the structured storage the runtime sinks emit. Replaces the old multi-line ШАБЛОН
text blocks: structured values, not freetext, so consumers parse instead of grep
(and the kernel serial-capture double-encoding of Cyrillic markers is gone).

Two event lines per call, paired by ``id`` (a short key set; nothing decorative):

    {"p":"in","t":"<ts>","id":"<uuid>","ci":2,"th":"4242.7","fn":"add","a":"1, 2","k":""}
    {"p":"out","id":"<uuid>","fn":"add","r":"3","d":0.000123}

* ``p`` phase — "in" (entered) or "out" (returned/raised).
* ``t`` entry timestamp (ISO-8601 userland, ``uptime+SEC.MS`` kernel); required on
  ``in``, optional on ``out`` (derivable as entry + ``d``).
* ``id`` call id (uuid) — the join key between ``in`` and its ``out``.
* ``ci``/``th`` CPU index and thread token at entry — only on ``in``. Kernel:
  ``ci`` = ``cpu_index(curcpu())``, ``th`` = ``"<pid>.<lid>"`` (curlwp); userland:
  ``ci`` = -1 (unavailable → parsed as None), ``th`` = the process id. The signal
  for concurrency / SMP-race analysis (which thread, which CPU). Optional —
  pre-thread-identity traces omit them.
* ``fn`` qualified name — on BOTH lines so an ``out`` orphaned by a truncated
  capture (its ``in`` scrolled off before a panic) still says what returned.
* ``a``/``k`` positional / kwarg reprs — only on ``in`` (join by ``id`` for args).
* ``r`` result repr  OR  ``x`` "Exc: msg" on raise (mutually exclusive); ``d``
  duration in seconds (a JSON number).

An ``in`` with no matching ``out`` = a call that entered but never completed
(hang / crash / truncated-at-panic). Per-call duration is read straight off ``d``.

Robustness: only lines beginning with ``{`` are parsed, each via ``json.loads``;
anything else (kernel boot spam, a line torn by concurrent-CPU printf) is skipped
and counted in ``malformed`` — not raised.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Record:
    """One COMPLETED call — an ``out`` event joined with its ``in`` (by ``id``).
    ``outcome_kind`` is 'result' or 'raised' (or '' for an orphan out with neither);
    ``duration`` is seconds (float) or None if the producer omitted ``d``."""
    index: int
    started: str
    call_id: str
    name: str
    args: str
    kwargs: str
    outcome_kind: str
    outcome: str
    duration: float | None
    cpu: int | None
    thread: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Loaded:
    """Result of reading a trace: completed ``calls``, ``in_flight`` calls (entered,
    never completed), and the count of ``malformed`` (non-JSON / unknown) lines."""
    calls: list[Record]
    in_flight: list[dict[str, Any]]
    malformed: int


def load(text: str) -> Loaded:
    """Parse a JSONL trace into completed call records + in-flight calls."""
    ins: dict[str, dict[str, Any]] = {}
    calls: list[Record] = []
    in_order: list[dict[str, Any]] = []
    malformed = 0
    order = 0
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            if s:
                malformed += 1
            continue
        try:
            ev = json.loads(s)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(ev, dict):
            malformed += 1
            continue
        if ev.get("p") not in ("in", "out"):
            # Well-formed JSON that isn't a call event (e.g. an `exec` meta
            # record appended by the sandbox) — not a torn line; skip silently.
            continue
        if ev["p"] == "in":
            ins[str(ev.get("id", ""))] = ev
            in_order.append(ev)
            continue
        # "out": join with its entry event (may be missing in a truncated capture).
        # Normalise the join id to str so it pairs with the `in` key above (and the
        # completed/in_flight sets below) regardless of how the producer typed it.
        entry = ins.get(str(ev.get("id", "")), {})
        if "x" in ev:
            kind, outcome = "raised", str(ev["x"])
        elif "r" in ev:
            kind, outcome = "result", str(ev["r"])
        else:
            kind, outcome = "", ""
        d = ev.get("d")
        calls.append(Record(
            index=order,
            started=str(entry.get("t", "")),
            call_id=str(ev.get("id", "")),
            name=str(ev.get("fn") or entry.get("fn", "")),
            args=str(entry.get("a", "")),
            kwargs=str(entry.get("k", "")),
            outcome_kind=kind,
            outcome=outcome,
            duration=float(d) if isinstance(d, (int, float)) else None,
            cpu=_cpu(entry),
            thread=str(entry.get("th", "")),
        ))
        order += 1
    completed = {c.call_id for c in calls}
    in_flight = [{"name": str(ev.get("fn", "")), "call_id": str(ev.get("id", "")),
                  "started": str(ev.get("t", "")),
                  "cpu": _cpu(ev), "thread": str(ev.get("th", ""))}
                 for ev in in_order if str(ev.get("id", "")) not in completed]
    return Loaded(calls=calls, in_flight=in_flight, malformed=malformed)


def _cpu(ev: dict[str, Any]) -> int | None:
    """Read the ``ci`` CPU-index field. The kernel sink emits a real index; the
    userland sink emits -1 ("not available") — both map to None when unknown."""
    ci = ev.get("ci")
    return ci if isinstance(ci, int) and not isinstance(ci, bool) and ci >= 0 else None


def parse(text: str) -> list[Record]:
    """Completed call records (convenience over :func:`load`)."""
    return load(text).calls


def parse_timestamp(started: str) -> float | None:
    """``started`` → seconds (float), or None. Handles ISO-8601 (userland) and
    ``uptime+SEC.MS`` (kernel)."""
    if started.startswith("uptime+"):
        try:
            return float(started[len("uptime+"):])
        except ValueError:
            return None
    try:
        return datetime.datetime.fromisoformat(started).timestamp()
    except ValueError:
        return None


def query(
    records: list[Record],
    *,
    function: str | None = None,
    contains: str | None = None,
    outcome: str | None = None,
    min_duration: float | None = None,
    thread: str | None = None,
    regex: bool = False,
) -> list[Record]:
    """Filter call records, preserving order (and each record's ``index``).

    * ``function`` — match against the qualified name.
    * ``contains`` — match against args/kwargs/outcome.
    * ``outcome``  — exact kind ('result'/'raised'/'unknown').
    * ``min_duration`` — keep only calls whose real duration ``d`` ≥ this many
      seconds (find the slow calls). Calls with no recorded duration are dropped.
    * ``thread`` — exact thread token (``th`` field, e.g. ``"4242.7"`` kernel
      pid.lid) — isolate one thread's calls out of a concurrent trace.
    * ``regex`` — interpret ``function``/``contains`` as case-insensitive regular
      expressions instead of plain substrings (raises ``re.error`` on a bad pattern).
    """
    out = records
    if thread is not None:
        out = [r for r in out if r.thread == thread]
    if function:
        if regex:
            pat = re.compile(function, re.IGNORECASE)
            out = [r for r in out if pat.search(r.name)]
        else:
            f = function.lower()
            out = [r for r in out if f in r.name.lower()]
    if contains:
        if regex:
            pat = re.compile(contains, re.IGNORECASE)
            out = [r for r in out
                   if pat.search(r.args) or pat.search(r.kwargs) or pat.search(r.outcome)]
        else:
            c = contains.lower()
            out = [r for r in out
                   if c in r.args.lower() or c in r.kwargs.lower() or c in r.outcome.lower()]
    if outcome:
        out = [r for r in out if r.outcome_kind == outcome]
    if min_duration is not None:
        out = [r for r in out if r.duration is not None and r.duration >= min_duration]
    return out


def _dur_stats(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    return {
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "mean": round(sum(values) / len(values), 6),
        "total": round(sum(values), 6),
        "count": len(values),
    }


def aggregate(calls: list[Record],
              in_flight: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Summarize completed ``calls``: per-function counts (by outcome) and REAL
    per-call durations (from ``d``), plus ``in_flight`` and the entry ``timespan``."""
    in_flight = list(in_flight or [])
    by: dict[str, dict[str, Any]] = {}
    for r in calls:
        e = by.setdefault(r.name, {"name": r.name, "count": 0,
                                   "result": 0, "raised": 0, "unknown": 0, "_durs": []})
        e["count"] += 1
        key = r.outcome_kind if r.outcome_kind in ("result", "raised") else "unknown"
        e[key] += 1
        if r.duration is not None:
            e["_durs"].append(r.duration)
    by_function = []
    for e in sorted(by.values(), key=lambda e: (-e["count"], e["name"])):
        durs = e.pop("_durs")
        e["duration_seconds"] = _dur_stats(durs)
        by_function.append(e)

    # Per-thread grouping: who ran what, on which CPUs — the concurrency view that
    # turns a flat trace into "thread A did X while thread B did Y" (SMP races).
    bt: dict[str, dict[str, Any]] = {}
    for r in calls:
        if not r.thread:
            continue
        e = bt.setdefault(r.thread, {"thread": r.thread, "count": 0,
                                     "_fns": set(), "_cpus": set()})
        e["count"] += 1
        e["_fns"].add(r.name)
        if r.cpu is not None:
            e["_cpus"].add(r.cpu)
    by_thread = []
    for e in sorted(bt.values(), key=lambda e: (-e["count"], e["thread"])):
        e["functions"] = len(e.pop("_fns"))
        e["cpus"] = sorted(e.pop("_cpus"))
        by_thread.append(e)

    all_durs = [r.duration for r in calls if r.duration is not None]
    pairs = sorted((t, r.started) for r in calls
                   if (t := parse_timestamp(r.started)) is not None)
    timespan = None
    if pairs:
        timespan = {
            "first": pairs[0][1], "last": pairs[-1][1],
            "seconds": round(pairs[-1][0] - pairs[0][0], 6),
            "timestamps_parsed": len(pairs),
            "timestamps_unparsed": len(calls) - len(pairs),
        }
    return {
        "total_calls": len(calls),
        "in_flight": in_flight,
        "by_function": by_function,
        "by_thread": by_thread,
        "duration_seconds": _dur_stats(all_durs),
        "timespan": timespan,
        "note": ("counts/durations are over completed calls; `duration_seconds` are "
                 "REAL per-call durations (exit−entry) from each call's `d`. "
                 "`by_thread` groups calls by the `th` token (CPUs each thread ran "
                 "on); empty for traces with no thread field. "
                 "`in_flight` = entered (`p:in`) but never completed. `timespan` is "
                 "first→last entry time."),
    }
