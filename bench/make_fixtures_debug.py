#!/usr/bin/env python3
"""Fixtures for the opaque-state debugging experiment.

Both arms get the SAME buggy pipeline (pipeline_buggy.py: normalize divides by
max instead of sum). The final output is a single opaque score, so the wrong
value cannot be attributed to a stage from output + traceback alone — only by
reading every function (baseline) or seeing each function's runtime in/out
(ouroboros). This reference computes the CORRECT expected outputs.
"""

from __future__ import annotations

from pathlib import Path

WEIGHTS = {"cpu": 2.0, "mem": 1.5, "disk": 1.0, "net": 0.5}


def correct_score(text: str) -> str:
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        rows.append((parts[1], parts[2]))
    clean = []
    for sensor, raw in rows:
        try:
            v = float(raw)
        except ValueError:
            continue
        if v < 0:
            continue
        clean.append((sensor, v))
    latest = {}
    for sensor, v in clean:
        latest[sensor] = v
    total = sum(latest.values())  # the CORRECT denominator
    norm = {s: v / total for s, v in latest.items()}
    score = sum(frac * WEIGHTS.get(s, 1.0) for s, frac in norm.items())
    return f"{round(score * 100, 2):.2f}\n"


SAMPLE = """\
t1 cpu 30
t2 mem 20
t3 disk 10
t4 net 40
t5 cpu 50
t6 bad xx
t7 disk -5
"""

HIDDEN = """\
a1 cpu 10
a2 cpu 20
a3 mem 60
a4 disk 30
a5 net 80
a6 mem bad
a7 disk -1
a8 net 20
"""


def main() -> None:
    root = Path(__file__).parent
    task = root / "task_debug"
    fixtures = root / "fixtures_debug"
    task.mkdir(exist_ok=True)
    fixtures.mkdir(exist_ok=True)

    (task / "sample.txt").write_text(SAMPLE, encoding="utf-8")
    (task / "sample_expected.txt").write_text(correct_score(SAMPLE), encoding="utf-8")
    (fixtures / "hidden.txt").write_text(HIDDEN, encoding="utf-8")
    (fixtures / "hidden_expected.txt").write_text(correct_score(HIDDEN), encoding="utf-8")

    print("sample expected:", correct_score(SAMPLE).strip())
    print("hidden expected:", correct_score(HIDDEN).strip())


if __name__ == "__main__":
    main()
