"""The numbers the landing page claims are the numbers the measurement took.

The landing page repeats the experiment table — how much a trace adds to a model
reading someone else's code — because that table is the reason a visitor keeps
reading. A repeated number is a number that can drift, and this one would drift
silently: nothing in the build compares a claim on the front page against the
page the claim came from.

So it is compared here. `docs/measurements.md` holds the measurement;
`site/pages/index.md` repeats four of its rows. Both are parsed as tables, the
four rows are matched by the name of who answered, and every cell must agree.

Exit codes: 0 — they agree; 1 — they do not.

Run::

    uv run python scripts/check_landing_claims.py
    uv run python scripts/check_landing_claims.py --self-test
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "measurements.md"
LANDING = ROOT / "site" / "pages" / "index.md"

#: The row is found by who answered, and the model name is the part that never
#: gets reworded — a heading above the table might, the backticked name will not.
ANSWERERS = ("qwen3.5:4b", "qwen2.5:14b-instruct", "qwen3:32b", "Claude Opus 5")

_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")


def rows(text: str) -> dict[str, list[str]]:
    """Every table row keyed by the answerer it names, as its bare numbers."""

    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        for who in ANSWERERS:
            if who in line and who not in found:
                cells = [c.strip() for c in line.strip("|").split("|")][1:]
                found[who] = [n for cell in cells for n in _NUMBER.findall(cell)]
    return found


def problems(source_text: str, landing_text: str) -> list[str]:
    """What the landing page claims that the measurement does not say."""

    measured = rows(source_text)
    claimed = rows(landing_text)
    out: list[str] = []

    for who in ANSWERERS:
        if who not in measured:
            out.append(
                f"the measurement in docs/measurements.md no longer has a row for "
                f"{who} — the landing page still claims one"
            )
            continue
        if who not in claimed:
            out.append(
                f"the landing page dropped the row for {who}; either put it back or "
                f"take the claim out of scripts/check_landing_claims.py"
            )
            continue
        # The landing table carries no interval column, so it is a prefix match.
        head = measured[who][: len(claimed[who])]
        if head != claimed[who]:
            out.append(
                f"{who}: the landing page says {claimed[who]}, the measurement says "
                f"{head} — site/pages/index.md and docs/measurements.md have parted ways"
            )
    return out


def self_test() -> int:
    """A guard that has never gone red cannot be told from one that cannot."""

    source = SOURCE.read_text(encoding="utf-8")
    landing = LANDING.read_text(encoding="utf-8")
    cases: list[tuple[str, str, str, bool]] = [
        ("the tree as it stands", source, landing, False),
        (
            "the landing page inflates a number",
            source,
            landing.replace("| 600 | 44.0% | 78.3% |", "| 600 | 44.0% | 98.3% |"),
            True,
        ),
        (
            "the measurement was re-taken and the landing page was not",
            source.replace("| 600 | 61.0% | 84.7% |", "| 600 | 61.0% | 71.2% |"),
            landing,
            True,
        ),
        (
            "the landing page drops a row",
            source,
            "\n".join(line for line in landing.splitlines() if "qwen3:32b" not in line),
            True,
        ),
    ]

    disagreed = 0
    for name, src, land, expect_refusal in cases:
        refused = bool(problems(src, land))
        agreed = refused == expect_refusal
        disagreed += not agreed
        want = "a refusal" if expect_refusal else "silence"
        got = "a refusal" if refused else "silence"
        print(f"  {'✓' if agreed else '✗'} {name}: expected {want}, got {got}")

    if disagreed:
        print(f"Self-test: {disagreed} cases disagreed with what was expected.")
        return 1
    print(f"Self-test: {len(cases)} cases, all agreed.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()

    found = problems(
        SOURCE.read_text(encoding="utf-8"), LANDING.read_text(encoding="utf-8")
    )
    if found:
        print("The landing page and the measurement disagree:")
        print()
        for line in found:
            print(f"  - {line}")
        print()
        print("The measurement is the source: re-take it with")
        print("scripts/measure/trace-help/run.sh, then carry the numbers over.")
        return 1

    print(
        f"The landing page repeats {len(ANSWERERS)} rows of the measurement, "
        f"and every number agrees."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
