#!/usr/bin/env python3
"""How long the trace reader takes on a fixed sample, so the number can be argued with.

The reader's rules live in flang and are printed into Python
(``ouroboros/brain/trace_brain.flang``, see ``docs/sdd/brain-in-flang.md``), and
that print is not free. The cost was first written down as a ratio with no way
to reproduce it; this is the way.

The sample is built here rather than committed: five megabytes of JSONL in the
tree to answer one question is a poor trade, and a seeded generator gives the
same bytes on every machine and every run — 39,640 lines, 19,600 completed
calls, 200 that entered and never returned, 240 lines of boot spam and blank.
Those proportions are a kernel capture's, which is what the reader is for.

Run::

    uv run python scripts/measure/trace_reading.py
    uv run python scripts/measure/trace_reading.py --repeats 9 --split

``--split`` times each question the reader asks the brain, separately, over the
same sample: that is what says whether a slow reader is slow at the boundary or
slow inside a rule. When it was first asked, the answer was neither obvious nor
what anyone guessed — the whole boundary was 3 % of the time.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

#: The seed, the counts and the shape of a record are all fixed: two runs of
#: this file differ in timing and in nothing else.
SEED = 20260908
COMPLETED = 19600
IN_FLIGHT = 200
NOISE = 240

NAMES = ("pkg.mod.add", "pkg.mod.fetch", "pkg.deep.walk", "srv.handler.dispatch",
         "srv.handler.render", "util.text.shorten", "util.io.read_all")


def sample() -> str:
    """The trace the reader is timed on. Same bytes every time."""

    rng = random.Random(SEED)
    events: list[dict[str, object]] = []
    for n in range(COMPLETED + IN_FLIGHT):
        call_id = f"{n:08x}-4f3a-4c1d-9e7b-{n:012x}"
        name = NAMES[n % len(NAMES)]
        events.append({"p": "in", "t": f"uptime+{n * 0.000137:.6f}", "id": call_id,
                       "ci": (n % 4) if n % 7 else -1, "th": f"{4242 + n % 3}.{n % 11}",
                       "fn": name,
                       "a": ", ".join(str(rng.randint(0, 9999)) for _ in range(n % 4)),
                       "k": "" if n % 3 else "flag=True"})
        if n < COMPLETED:
            done: dict[str, object] = {"p": "out", "id": call_id, "fn": name,
                                       "d": round(0.000021 * (1 + n % 97), 9)}
            # One call in twenty-three raises, which is roughly what a capture of
            # a failing run looks like and exercises the other outcome branch.
            if n % 23 == 0:
                done["x"] = "ValueError: nope"
            else:
                done["r"] = str(rng.randint(0, 999999))
            events.append(done)

    records = [json.dumps(one, separators=(",", ":")) for one in events]
    # Boot spam and blank lines are spread through the file rather than heaped at
    # the end: the reader sorts every line, and a run of them in one place would
    # measure the branch predictor instead of the rule.
    noise = ["" if n % 2 else f"[   {n * 1.5:8.3f}] kernel: boot spam line {n}"
             for n in range(NOISE)]
    step = len(records) // (NOISE + 1)
    lines: list[str] = []
    placed = 0
    for index, one in enumerate(records):
        lines.append(one)
        if index and index % step == 0 and placed < NOISE:
            lines.append(noise[placed])
            placed += 1
    lines.extend(noise[placed:])
    return "\n".join(lines) + "\n"


def best_of(work: Callable[[], object], repeats: int) -> tuple[float, float]:
    """Best and median seconds over ``repeats`` runs.

    Best, not mean: the thing being measured is the work, and everything the
    machine adds on top of it — another process, a page fault — only ever adds.
    """

    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        work()
        times.append(time.perf_counter() - start)
    return min(times), statistics.median(times)


def split(text: str, repeats: int) -> None:
    """Time each question the reader asks the brain, alone, over the same sample."""

    from ouroboros.brain import Brain
    from ouroboros.brain._flang import flang_runtime as rt

    lines = text.splitlines()
    events = [json.loads(one) for one in lines if one.startswith("{")]
    entries = [one for one in events if one.get("p") == "in"]
    exits = [one for one in events if one.get("p") == "out"]
    brain = Brain()
    entered = [brain.entry(one) for one in entries]
    left = [brain.exit(one) for one in exits]

    print("  the shell, without the brain:")
    for label, work in (
        ("split the text into lines", lambda: text.splitlines()),
        ("decode the JSON", lambda: [json.loads(one) for one in lines
                                     if one.startswith("{")]),
    ):
        low, _ = best_of(work, repeats)
        print(f"    {label:<44} {low * 1000:8.1f} ms")

    print("  the brain, one question at a time:")
    for label, work, count in (
        ("«Line kind», per line", lambda: [brain.line_kind(one) for one in lines],
         len(lines)),
        ("«Event kind», per decoded event",
         lambda: [brain.event_kind(one.get("p")) for one in events], len(events)),
        ("«Entry from», per entry",
         lambda: [brain.entry(one) for one in entries], len(entries)),
        ("«Exit from», per exit", lambda: [brain.exit(one) for one in exits], len(exits)),
        ("«Completed call», per exit",
         lambda: [brain.completed_call(n, a, b)
                  for n, (a, b) in enumerate(zip(entered, left, strict=False))],
         len(exits)),
    ):
        low, _ = best_of(work, repeats)
        print(f"    {label:<44} {low * 1000:8.1f} ms  ({low / count * 1e6:5.2f} us each)")

    # The boundary itself, with no rule behind it: a flang string in, a flang
    # string out. Multiply by the crossings a trace makes and you have the most
    # the boundary could possibly cost.
    low, _ = best_of(lambda: [rt.text(one) for one in lines], repeats)
    print(f"    {'a value across the boundary, nothing else':<44} "
          f"{low * 1000:8.1f} ms  ({low / len(lines) * 1e6:5.2f} us each)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5,
                        help="how many times to read the sample (default 5)")
    parser.add_argument("--split", action="store_true",
                        help="also time each question the reader asks the brain")
    parser.add_argument("--write", type=Path,
                        help="write the sample here instead of measuring")
    args = parser.parse_args()

    text = sample()
    if args.write:
        args.write.write_text(text, encoding="utf-8")
        print(f"{args.write}: {len(text.splitlines())} lines, {len(text)} bytes")
        return 0

    from ouroboros.trace import load

    loaded = load(text)
    print(f"sample: {len(text.splitlines())} lines, {len(text)} bytes — "
          f"{len(loaded.calls)} completed calls, {len(loaded.in_flight)} in flight, "
          f"{loaded.malformed} malformed")
    low, middle = best_of(lambda: load(text), args.repeats)
    print(f"  ouroboros.trace.load       best {low:.3f} s   median {middle:.3f} s   "
          f"of {args.repeats} runs")
    if args.split:
        split(text, args.repeats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
