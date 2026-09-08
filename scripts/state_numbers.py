"""Keeps the state numbers in the documentation equal to the measured ones.

Why. Numbers written in by hand part ways with reality in silence. In this tree
it has happened twice already: `ARCHITECTURE.md` promised 91 % coverage — 9.5
points above a ceiling that was out of reach even with perfect tests — and
"~105 tests" when there were 164. Neither was ever recounted after being written
in. `README.md` meanwhile said "167 of 167" when the test count had reached 440.

How. The pages carry marks of the form::

    <!--state:tests-->440<!--/state-->

Inside a mark the text belongs to the machine. `--measure` runs the tests with
coverage, puts the measurement into `docs/state.json` and rewrites the marks.
With no arguments it compares instead: the marks are checked against
`docs/state.json`, while the cheap numbers (how many tests, how many tools, which
languages) are recounted right now. On a mismatch it refuses and says what the
number has become.

The comparison costs seconds and therefore hangs in `scripts/qa.sh`. The full
measurement takes minutes and is therefore run by hand, when the numbers change.

Run::

    uv run python scripts/state_numbers.py            # compare
    uv run python scripts/state_numbers.py --measure   # measure and rewrite
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: Where the measurement is kept. It lives in the tree because the comparison
#: must work without a second run, and because the difference in `git diff` shows
#: what has moved.
STATE_FILE = ROOT / "docs" / "state.json"

#: The pages that carry the marks. Both editions: a name with no suffix is the
#: English one, `.ru` is the Russian one. The machine rewrites the Russian one
#: too, or it would drift from the English one exactly as quietly as both used to
#: drift from reality.
PAGES = ["README.md", "README.ru.md", "ARCHITECTURE.md",
         "docs/index.md", "docs/index.ru.md",
         "docs/install.md", "docs/install.ru.md"]

MARK = re.compile(r"<!--state:([a-z_]+)-->(.*?)<!--/state-->", re.DOTALL)


def measure() -> dict[str, Any]:
    """Runs the tests with coverage and collects the numbers."""

    print("== running the tests with coverage (this is not quick) ==")
    proc = subprocess.run(
        ["uv", "run", "pytest", "--cov", "--cov-report=json:.coverage.json"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    sys.stdout.write(proc.stdout[-2000:])
    if proc.returncode != 0:
        raise SystemExit("the tests did not pass — leaving the state numbers alone")

    # The total is looked for across the whole output: with coverage enabled the
    # last line is the message about writing the report, not "N passed".
    found = re.findall(r"(\d+) passed", proc.stdout)
    if not found:
        raise SystemExit(f"could not parse the test total: "
                         f"{proc.stdout.strip()[-300:]!r}")
    tests = int(found[-1])

    with (ROOT / ".coverage.json").open(encoding="utf-8") as fh:
        cov = json.load(fh)
    totals = cov["totals"]
    percent = totals["percent_covered"]
    # Uncovered units are statements plus branches. This number appears in the
    # coverage breakdown in ARCHITECTURE.md and has drifted before: the table said
    # 56 when the merged tree had 58.
    uncovered = totals["missing_lines"] + totals["missing_branches"]
    units = totals["num_statements"] + totals["num_branches"]

    return {
        "version": version(),
        "tests": tests,
        "uncovered_units": uncovered,
        "total_units": units,
        # DOWN, not to nearest: 99.51 % is not "100 %". A coverage number rounded
        # up promises what is not there, and promises like that are exactly what
        # this file exists to fight.
        "coverage_percent": int(percent),
        "coverage_exact": round(percent, 2),
        "mcp_tools": tool_count(),
        "languages": languages(),
        "measured_with": "pytest --cov (statements and branches)",
    }


def version() -> str:
    """The release number — from pyproject.toml, the one place where it is real.

    It also used to stand by hand in eight places, and it has drifted: the pages
    promised 0.2.1 after the package had already moved on.
    """

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if m is None:
        raise SystemExit("no version line found in pyproject.toml")
    return m.group(1)


def tool_count() -> int:
    """How many tools the server declares — from the reference taken live."""

    path = ROOT / "docs" / "mcp-tools.json"
    with path.open(encoding="utf-8") as fh:
        return int(json.load(fh)["declared_tool_count"])


def languages() -> int:
    proc = subprocess.run(
        ["uv", "run", "ouroboros", "languages"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return len(json.loads(proc.stdout)["languages"])


def collected_tests() -> int:
    """How many tests are collected — without running them, which is quick."""

    proc = subprocess.run(
        ["uv", "run", "pytest", "--collect-only"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    # pytest prints the collection total in two different ways: usually as the
    # line "N tests collected", and under double quiet (`-q` is already in
    # pyproject and a second `-q` comes from here) per file, as "path: N".
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    if m is not None:
        return int(m.group(1))
    per_file = re.findall(r"^\S+\.py: (\d+)$", proc.stdout, re.MULTILINE)
    if per_file:
        return sum(int(n) for n in per_file)
    raise SystemExit(f"could not parse the test collection: "
                     f"{proc.stdout.strip()[-300:]!r}")


def apply_marks(state: dict[str, Any]) -> list[str]:
    """Rewrites the marks in the pages. Returns the list of changed ones."""

    changed = []
    for name in PAGES:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")

        # `page=name` binds the page name NOW, not at call time: without it the
        # closure would look at the loop variable, and the error message would
        # name the last page instead of the one with the problem.
        def swap(m: re.Match[str], page: str = name) -> str:
            key = m.group(1)
            if key not in state:
                raise SystemExit(f"{page}: mark {key!r} — no such number is measured")
            return f"<!--state:{key}-->{state[key]}<!--/state-->"

        new = MARK.sub(swap, text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(name)
    return changed


def check(state: dict[str, Any]) -> list[str]:
    """Compares the marks with the measurement and with what can be recounted now."""

    problems: list[str] = []

    # The cheap numbers are recounted: if there are more tests now and the
    # measurement is old, that is what has to be said, rather than comparing two
    # equally stale records.
    now = collected_tests()
    if now != state.get("tests"):
        problems.append(
            f"there are {now} tests now, while docs/state.json records "
            f"{state.get('tests')} — run --measure"
        )
    now_version = version()
    if now_version != state.get("version"):
        problems.append(
            f"pyproject.toml says version {now_version}, docs/state.json says "
            f"{state.get('version')} — run --measure"
        )
    tools = tool_count()
    if tools != state.get("mcp_tools"):
        problems.append(
            f"the reference lists {tools} tools, docs/state.json says "
            f"{state.get('mcp_tools')} — run --measure"
        )

    for name in PAGES:
        text = (ROOT / name).read_text(encoding="utf-8")
        for m in MARK.finditer(text):
            key, shown = m.group(1), m.group(2)
            if key not in state:
                problems.append(f"{name}: mark {key!r} — no such number is measured")
            elif shown != str(state[key]):
                problems.append(
                    f"{name}: mark {key} says {shown!r}, the measurement is "
                    f"{state[key]!r}"
                )
    return problems


def main() -> int:
    if "--measure" in sys.argv:
        state = measure()
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        changed = apply_marks(state)
        print(f"\nmeasured: {json.dumps(state, ensure_ascii=False)}")
        print(f"written to {STATE_FILE.relative_to(ROOT)}")
        print("pages updated: " + (", ".join(changed) if changed else "nothing to change"))
        return 0

    if not STATE_FILE.exists():
        print(f"no {STATE_FILE.relative_to(ROOT)} — run with --measure")
        return 1
    with STATE_FILE.open(encoding="utf-8") as fh:
        state = json.load(fh)

    problems = check(state)
    if problems:
        print("The state numbers parted ways with the measurement:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nThe fix: uv run python scripts/state_numbers.py --measure")
        return 1
    print(f"The state numbers agree with the measurement ({state['tests']} tests, "
          f"coverage {state['coverage_percent']} %).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
