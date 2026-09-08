"""Prints the "what lands in the record fields" table from a real run of every language.

Why. This table in `docs/languages.md` has already become a lie once — and not
from an edit to the page itself, but from someone's edit to the language
backends. Before it, C, C++ and Elixir wrote the string `a=2, b=3` into field
`a`; the edit brought them all to values without names, the tests locked that in,
and the page went on promising names. No check catches that: the page and the
code are joined by nothing but somebody's attention.

How it works here. The table is not written, it is printed — from the very run
that `tests/test_schema_parity.py` lives on: a real backend, a real compiler, a
real `debug.info`. Between the marks in the page the text belongs to the machine.

    uv run python scripts/schema_facts.py --measure   # run and rewrite
    uv run python scripts/schema_facts.py             # compare

The measurement takes minutes (C, C++, Elixir and Go get compiled) and is
therefore run by hand. The comparison costs seconds and hangs in `scripts/qa.sh`.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

FACTS = ROOT / "docs" / "schema-facts.json"
#: Both editions of the page: a name with no suffix is the English one, `.ru` is
#: the Russian one. The machine prints the table, so each edition's column labels
#: live here rather than in the page — otherwise a translation of the page would
#: quietly part ways with what was measured. The Russian labels below are data:
#: they must match the Russian page word for word.
PAGES = {
    ROOT / "docs" / "languages.md": {
        "head": "| language | field `a` (positional) | field `k` (keyword) |",
        "empty": "empty",
        "unavailable": "not measured on this machine",
        "unavailable_short": "not measured",
    },
    ROOT / "docs" / "languages.ru.md": {
        "head": "| язык | поле `a` (по позиции) | поле `k` (именованные) |",
        "empty": "пусто",
        "unavailable": "не снято на этой машине",
        "unavailable_short": "не снято",
    },
}

MARK = re.compile(
    r"(<!--schema-facts-->)(.*?)(<!--/schema-facts-->)", re.DOTALL
)

#: How to name the languages in the table.
TITLES = {"python": "Python", "javascript": "JavaScript",
          "c": "C", "cpp": "C++", "elixir": "Elixir",
          "go": "Go", "java": "Java", "csharp": "C#"}


def measure() -> dict[str, Any]:
    """Runs the same call in every language and looks at what got recorded."""

    from test_schema_parity import _ADD, _LANGS, _records, _skip_unless_available

    out: dict[str, Any] = {}
    for lang in _LANGS:
        try:
            _skip_unless_available(lang)
        # BLE001: `_skip_unless_available` signals "no toolchain here" by raising
        # pytest.skip.Exception, which is not an Exception subclass this file can
        # name without importing pytest into a script that does not need it.
        except Exception as e:  # noqa: BLE001
            out[lang] = {"unavailable": str(e)[:120]}
            print(f"  {lang}: skipped — {e}")
            continue
        with tempfile.TemporaryDirectory() as td:
            recs = _records(lang, Path(td))
        # The record for `add(2, 3)` specifically. Java has no file level to
        # append a driver to, so its `main` is instrumented along with the rest
        # and is the FIRST `in` line — taking that one would print `main`'s own
        # arguments into a table that says it is about `add`.
        entry = next(r for r in recs if r["p"] == "in" and r["fn"] == _ADD[lang])
        out[lang] = {"a": entry["a"], "k": entry["k"]}
        print(f"  {lang}: a={entry['a']!r} k={entry['k']!r}")
    return out


def render(facts: dict[str, Any], words: dict[str, str]) -> str:
    rows = [words["head"], "|---|---|---|"]
    for lang, title in TITLES.items():
        f = facts.get(lang)
        if f is None or "unavailable" in (f or {}):
            rows.append(
                f"| {title} | {words['unavailable']} | {words['unavailable_short']} |")
            continue
        a = f"`{f['a']}`" if f["a"] else words["empty"]
        k = f"`{f['k']}`" if f["k"] else words["empty"]
        rows.append(f"| {title} | {a} | {k} |")
    return "\n" + "\n".join(rows) + "\n"


def apply(facts: dict[str, Any]) -> bool:
    changed = False
    for page, words in PAGES.items():
        text = page.read_text(encoding="utf-8")
        if not MARK.search(text):
            raise SystemExit(f"{page.name} has no <!--schema-facts--> marks")
        table = render(facts, words)

        # `table=table` binds the table NOW, not at call time: without it the
        # closure would look at the loop variable and the second page would get
        # the table of the last edition.
        def swap(m: re.Match[str], table: str = table) -> str:
            return m.group(1) + table + m.group(3)

        new = MARK.sub(swap, text)
        if new != text:
            page.write_text(new, encoding="utf-8")
            changed = True
    return changed


def main() -> int:
    if "--measure" in sys.argv:
        print("== running one call in every language ==")
        facts = measure()
        FACTS.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
        changed = apply(facts)
        print(f"written to {FACTS.relative_to(ROOT)}")
        print("pages updated" if changed else "pages already matched")
        return 0

    if not FACTS.exists():
        print(f"no {FACTS.relative_to(ROOT)} — run with --measure")
        return 1
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    for page, words in PAGES.items():
        want = render(facts, words)
        got = MARK.search(page.read_text(encoding="utf-8"))
        if got is None:
            print(f"{page.name} has no <!--schema-facts--> marks")
            return 1
        if got.group(2) != want:
            print(f"The record-field table in {page.name} parted ways with the "
                  f"measurement:\n")
            print("  in the page:\n" + got.group(2).rstrip())
            print("\n  measured by the run:\n" + want.rstrip())
            print("\nThe fix: uv run python scripts/schema_facts.py --measure")
            return 1
    print(f"The record-field table matches the measured run in {len(PAGES)} editions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
