"""Checks that documentation links into the source point where they promise.

The documentation cites the source down to the line — `ouroboros/….py:123`, and
the same `#L123` in a link to GitHub. Those numbers go stale in silence: an edit
higher up the file shifts everything, and the link keeps looking correct while
leading to an arbitrary line. That is exactly what happened when the MCP server
grew by 63 lines: four links on four pages started pointing past their target,
and no check noticed.

How the check works. Listed below are the **anchors** — the places in the source
that documentation is allowed to cite at all — and each is recognised by a piece
of the line's own text, not by its number. The check finds the current line
number of every anchor, then demands that every link in the documentation point
at some anchor's number. Once the source shifts, the check fails and prints the
new numbers to write in.

What the check does not do: it does not know which anchor a particular page
meant, so on a mismatch it shows every anchor of that file. That is enough to
keep a breakage from passing in silence and to keep the repair down to a minute.

Added a link to a new place in the documentation? Add the anchor here too, or the
check will refuse: the list of anchors is deliberately closed.

Run: uv run python scripts/check_doc_links.py   (from the repository root)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

#: (source file, a piece of the anchor line). The piece must occur in the file
#: exactly once — the check demands that too, or an anchor is no anchor.
ANCHORS: list[tuple[str, str]] = [
    ("ouroboros/runtime.py", "_repr = reprlib.Repr()"),
    ("ouroboros/runtime.py", 'return os.environ.get("OUROBOROS_DEBUG_INFO"'),
    ("ouroboros/runtime.py", "def _cpu() -> int:"),
    ("ouroboros/runtime.py", "t0 = time.perf_counter()"),
    # The wrapper line that shows up in a traceback from an instrumented program:
    # pages paste that real output with its line number, and it goes stale too.
    ("ouroboros/runtime.py", "result = fn(*args, **kwargs)"),
    ("ouroboros/languages/base.py", "class CorruptedSourceError(Exception):"),
    ("ouroboros/sandbox/sync.py", "never carried into the output tree"),
    ("ouroboros/mcp/server.py", "_READ_ONLY = ToolAnnotations("),
    ("ouroboros/mcp/server.py", "def transport_from_env("),
]

#: `ouroboros/path.py:12` or `ouroboros/path.py:12-34` in the page text.
REF = re.compile(r"(ouroboros/[A-Za-z0-9_/]*\.py):(\d+)(?:-(\d+))?")

#: `…/blob/main/ouroboros/path.py#L12` or `#L12-L34` inside a link.
URL = re.compile(r"blob/main/(ouroboros/[A-Za-z0-9_/]*\.py)#L(\d+)(?:-L(\d+))?")

#: The runtime helper's line inside a traceback pasted into a page verbatim:
#: `File ".../ouroboros_runtime.py", line 228, in wrapper`. That number goes
#: stale in the very same way, but it looks like neither a text citation nor a
#: link — which is why nothing used to check it. That is where README parted ways
#: with reality: the helper grew, the wrapper moved from line 144 to line 228,
#: and the page kept saying 144.
TRACEBACK = re.compile(r'ouroboros_runtime\.py", line (\d+)')

#: Which source file the helper copied into a project corresponds to.
COPIED_AS = {"ouroboros_runtime.py": "ouroboros/runtime.py"}


def anchor_lines(root: Path) -> tuple[dict[str, dict[int, str]], list[str]]:
    """Current anchor line numbers: file -> {line: piece}. Plus a list of problems."""

    found: dict[str, dict[int, str]] = {}
    problems: list[str] = []
    for rel, needle in ANCHORS:
        path = root / rel
        if not path.exists():
            problems.append(f"anchor points at a file that does not exist: {rel}")
            continue
        hits = [
            i
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if needle in line
        ]
        if len(hits) != 1:
            problems.append(
                f"anchor {rel!r} / {needle!r} occurs {len(hits)} times "
                "(exactly one is needed) — fix the piece of text in ANCHORS"
            )
            continue
        found.setdefault(rel, {})[hits[0]] = needle
    return found, problems


Anchors = dict[str, dict[int, str]]


def _where(valid: dict[int, str]) -> str:
    """The anchors of one file, as the refusal prints them: `12 ('needle')`."""

    return ", ".join(f"{n} ({needle!r})" for n, needle in sorted(valid.items()))


def citation_problems(rel_doc: str, text: str, anchors: Anchors) -> tuple[list[str], int]:
    """`path.py:12` and `#L12` in one page: do they land on an anchor?

    Returns the complaints and how many citations were looked at.
    """

    problems: list[str] = []
    checked = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern in (REF, URL):
            for m in pattern.finditer(line):
                rel, cited = m.group(1), int(m.group(2))
                checked += 1
                valid = anchors.get(rel, {})
                if not valid:
                    problems.append(
                        f"{rel_doc}:{lineno} cites {rel}:{cited}, but no anchor is "
                        f"declared for {rel} in scripts/check_doc_links.py"
                    )
                elif cited not in valid:
                    problems.append(
                        f"{rel_doc}:{lineno} cites {rel}:{cited}, and there is no "
                        f"anchor on that line now. Anchors in this file: {_where(valid)}"
                    )
    return problems, checked


def traceback_problems(rel_doc: str, text: str, anchors: Anchors) -> tuple[list[str], int]:
    """A traceback pasted into a page verbatim carries the helper's line number."""

    problems: list[str] = []
    checked = 0
    rel = COPIED_AS["ouroboros_runtime.py"]
    valid = anchors.get(rel, {})
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in TRACEBACK.finditer(line):
            cited = int(m.group(1))
            checked += 1
            if cited not in valid:
                problems.append(
                    f"{rel_doc}:{lineno}: the pasted traceback says "
                    f"ouroboros_runtime.py line {cited}, while the anchors of "
                    f"{rel} are now on lines: {_where(valid)}"
                )
    return problems, checked


def text_and_link_agree(rel_doc: str, text: str) -> list[str]:
    """The number in the prose and the number in the link beside it must match.

    Editing one of the two and not the other is the ordinary slip.
    """

    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        text_refs = {(m.group(1), m.group(2), m.group(3)) for m in REF.finditer(line)}
        url_refs = {(m.group(1), m.group(2), m.group(3)) for m in URL.finditer(line)}
        # Whatever the link pattern finds, the text pattern finds too; only the
        # remainder is compared, or every line would look different.
        if url_refs and not url_refs <= text_refs:
            problems.append(
                f"{rel_doc}:{lineno}: the number in the text and the number in the "
                f"link disagree — {sorted(text_refs)} against {sorted(url_refs)}"
            )
    return problems


def pages(root: Path) -> list[Path]:
    """Every Markdown page of the tree, skipping what was vendored in."""

    return [d for d in sorted(root.glob("**/*.md"))
            if ".venv" not in d.parts and "node_modules" not in d.parts]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    anchors, problems = anchor_lines(root)

    checked = 0
    for doc in pages(root):
        rel_doc = str(doc.relative_to(root))
        text = doc.read_text(encoding="utf-8")
        for found, n in (citation_problems(rel_doc, text, anchors),
                         traceback_problems(rel_doc, text, anchors)):
            problems += found
            checked += n
        problems += text_and_link_agree(rel_doc, text)

    # The same problem is caught both by the text and by the link — report it
    # once, keeping the order in which it was found.
    problems = list(dict.fromkeys(problems))

    if problems:
        print("Documentation links into the source parted ways with the source:\n")
        for p in problems:
            print(f"  - {p}")
        print(f"\nLinks checked: {checked}. Problems: {len(problems)}. Write in the "
              f"line numbers printed above, or add the new place to ANCHORS in "
              f"scripts/check_doc_links.py.")
        return 1

    print(f"Documentation links into the source: {checked} checked, all land on anchors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
