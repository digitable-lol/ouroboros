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

#: Where we look. Sources and pages; vendored and generated trees are left alone.
ROOTS = ("docs", "scripts", "ouroboros", "tests")
EXTS = {".md", ".py", ".sh", ".toml"}
SKIP_PARTS = {".venv", "node_modules", "__pycache__", "_js", "_flang", ".probe-work"}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    targets = [root / "README.md", root / "ARCHITECTURE.md", root / "SPEC.md"]
    for r in ROOTS:
        targets += [p for p in (root / r).rglob("*") if p.suffix in EXTS]

    bad: list[str] = []
    for path in sorted(set(targets)):
        if not path.is_file() or SKIP_PARTS & set(path.parts):
            continue
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


if __name__ == "__main__":
    sys.exit(main())
