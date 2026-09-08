"""Finds words that mix Cyrillic and Latin letters inside one word.

Such a word is almost always a keyboard-layout slip: a Russian word with one or
two letters typed in Latin. It reads like the ordinary word, and no eye finds it
in two thousand lines, because `а`, `е`, `о`, `с`, `р`, `х` look the same in the
two alphabets. Searching for the word does not find it either — the search term
is typed correctly, only the text is not.

The rule of this tree: a word is either all Cyrillic or an English word. There is
no such thing as a half-and-half word.

What is not a problem: a Latin word standing next to a Russian one (`поле a`,
`git`), names taken from the code (`write_file`), a Russian ending hyphenated
onto a Latin name (`JSON-строка`) — a hyphen splits the word into parts, and each
part is of one alphabet.

Run: uv run python scripts/check_no_mixed_script.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CYR = re.compile(r"[А-Яа-яЁё]")
LAT = re.compile(r"[A-Za-z]")
#: A word is a run of letters. Hyphen and underscore separate words: they join
#: parts of different alphabets on purpose (`JSON-строка`, `write_file`).
WORD = re.compile("[A-Za-z" + "А-Яа-яЁё]+")  # split so it does not match itself

#: An escape sequence inside a code string: a newline written before a Russian
#: word glues its Latin letter onto that word and yields a phantom find. Such
#: sequences are dropped before the scan, or the check would flag its own output.
ESCAPE = re.compile(r"\\.")

#: Marker meaning "the mixed word right here is deliberate" — for the examples
#: that prose about this very problem has to spell out. Kept in Russian on
#: purpose: it is written into Russian text, and it is data, not a message.
OPT_OUT = "смешанные-алфавиты: нарочно"

#: Where we look: the whole tree. It used to be four directories plus three files
#: named one by one, and `README.ru.md` — the longest Russian page there is, so
#: the likeliest place for a layout slip — was in neither list. A guard that
#: looks away from the riskiest file is worth less than its green line suggests.
EXTS = {".md", ".py", ".sh", ".toml"}

#: Directory names that are never ours: vendored packages, caches, working trees.
SKIP_PARTS = {".git", ".venv", "node_modules", "__pycache__", "_js", "_flang",
              ".probe-work", ".trace-help-work"}

#: Paths that hold input data or a machine's output rather than our text: the
#: measurement's specimen programs, the recorded benchmark runs, the captured
#: output shown on the site, the recorded answers of the trace-help experiment.
SKIP_PREFIXES = (
    "bench/runs", "bench/runs_debug", "bench/task", "bench/task_debug",
    "scripts/measure/samples", "scripts/measure/trace-help/agents",
    "scripts/measure/trace-help/programs", "site/examples/captured", "site/out",
)


def targets(root: Path) -> list[Path]:
    """Every file of the tree the rule applies to, in a stable order."""

    found = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in EXTS or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if SKIP_PARTS & set(path.parts) or rel.startswith(SKIP_PREFIXES):
            continue
        found.append(path)
    return found


def main(root: Path | None = None) -> int:
    """`root` is a parameter so a test can point the guard at a tree of its own."""

    root = root or Path(__file__).resolve().parent.parent

    bad: list[str] = []
    for path in targets(root):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if OPT_OUT in line:
                continue
            for m in WORD.finditer(ESCAPE.sub(" ", line)):
                w = m.group()
                if CYR.search(w) and LAT.search(w):
                    bad.append(f"{path.relative_to(root)}:{n}: {w!r} in line: {line.strip()[:90]}")

    if bad:
        print("Words with mixed alphabets (Cyrillic and Latin inside one word):\n")
        for b in bad:
            print(f"  - {b}")
        print(f"\nTotal: {len(bad)}. A word is either all Cyrillic or an English "
              f"word: retype the letters that came from the wrong keyboard layout. "
              f"If a mix is deliberate, put {OPT_OUT!r} on that line.")
        return 1
    print("No words mix alphabets.")
    return 0


if __name__ == "__main__":  # pragma: no cover — the entry point, not a rule
    sys.exit(main())
