"""Guards the language of the documentation: English by default, Russian as a suffixed pair.

The tree has one rule, and it is read off the file name. `README.md` is English,
`README.ru.md` is Russian. No suffix means English, and there is no room for
"well, this one is historically in Russian": the file name promises a language,
and a machine holds it to the promise.

Why a machine. A tree is translated once and drifts back gradually: a Russian
paragraph is added to an English page, a Russian counterpart is never created, a
language switch points at the wrong file. Each of those looks like a trifle on
its own and is invisible in a review — a page is hundreds of lines. Together, in
a month, they put the tree back where it started.

What is checked:

1. **No Cyrillic in a file without the suffix.** Except for a closed list of
   words that stand there on purpose: the label of the language switch and the
   two directory names the tool used before the rename (`черновик`, `чистовик` —
   `LEGACY_DRAFT_DIRNAME`/`LEGACY_CLEAN_DIRNAME` in
   `ouroboros/sandbox/project.py`). Those are not prose but a string a person
   sees in their own file system: the tool still picks up an old project, and an
   English page must be able to call the directory by the name it actually has.
   The nominative case and nothing else — «в черновике» and «из чистовика» are
   prose already, and the guard goes red on them.
2. **Every `*.ru.md` page has an English counterpart.** A Russian edition without
   an English one is exactly "Russian by default" seen from the other side.
3. **The pair is declared from both sides.** An English page opens with the line
   `**English** · [Русский](name.ru.md)`, a Russian one with
   `[English](name.md) · **Русский**`. The link must lead to a file that exists:
   a switch into the void is worse than no switch at all.
4. **The numbers in the flang description match `docs/state.json`.** The file
   `docs/ouroboros.flang` is the Russian description of the tool, checked by the
   flang compiler. Types and termination it judges by itself; that there are
   eight languages, seventeen MCP tools and that the version is the current one —
   only the tree knows.
5. **The ledger of what is not done.** Pages outside the scope of this work that
   exist only in Russian for now are listed below by name, with a reason. The
   ledger can only shrink: once a page in it becomes English or disappears, the
   guard demands that the line be struck out. An unlisted Russian page is a
   refusal.

Run::

    uv run python scripts/check_doc_language.py
    uv run python scripts/check_doc_language.py --self-test

`--self-test` is the negative control. A check that has never gone red means
nothing: the self-test feeds the guard pages that are knowingly broken (English
with a Russian paragraph, Russian without a counterpart, a switch pointing at the
wrong file) and demands that it refuse on every one of them — and stay silent on
a sound page.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Where the pages are looked for. Vendored (`node_modules`) and generated trees
#: are left alone.
ROOTS = (".", "docs", "bin", "design", "packaging", "skill", "scripts", "bench")
SKIP_PARTS = {"node_modules", ".venv", "__pycache__", ".git", "runs", "runs_debug",
              "task", "task_debug", "fixtures", "fixtures_debug", "programs", "agents"}

CYR = re.compile(r"[А-Яа-яЁё]+")

#: Cyrillic that stands in an English page on purpose. The list is closed: every
#: word here is either the label of the link to the Russian counterpart or a real
#: name on disk. There is no prose in it, and there can be none. These words are
#: the guard's DATA, not its output: they stay in Russian.
ALLOWED = {
    "Русский",              # the label of the language switch
    # The directory names from before the rename. The tool still reads them off
    # the disk, so an English page is entitled to call a directory by the name it
    # has in a person's file system. Nominative case only: an inflected word is
    # prose rather than a name, and it is deliberately absent from this list.
    "черновик", "чистовик",
}

#: The language switch: the first line of a page after the header.
EN_SWITCH = re.compile(r"^\*\*English\*\* · \[Русский\]\(([^)]+)\)\s*$", re.MULTILINE)
RU_SWITCH = re.compile(r"^\[English\]\(([^)]+)\) · \*\*Русский\*\*\s*$", re.MULTILINE)

#: The ledger: pages that have no English edition yet. It can only shrink — the
#: guard demands that a line be struck out as soon as the page is translated or
#: deleted.
PENDING: dict[str, str] = {
    "skill/SKILL.md":
        "a skill for an AI agent, 555 lines; it gets translated together with the\n"
        "skill, not with the documentation",
    "design/brief.md":
        "the customer's original brief — a historical document, not to be rewritten",
    "design/example.md":
        "a walk through instrumentation language by language, a draft for the brief",
    "design/skill-draft.md": "a draft of the skill, lives next to design/brief.md",
    "bin/README.md": "three release scripts; belongs to packaging, not documentation",
    "packaging/asdf/README.md": "belongs to packaging",
    "scripts/measure/trace-help/README.md":
        'the log of the "does a trace help" experiment, 582 lines — a record of\n'
        "runs rather than a page",
    "scripts/measure/trace-help/scale-up-master-prompt.md":
        "the prompt for that same experiment",
}


def pages() -> list[Path]:
    """Every page of the tree, except vendored and generated ones."""

    out: dict[Path, None] = {}
    for r in ROOTS:
        base = ROOT / r
        if not base.is_dir():
            continue
        pattern = "*.md" if r == "." else "**/*.md"
        for p in sorted(base.glob(pattern)):
            if set(p.relative_to(ROOT).parts) & SKIP_PARTS:
                continue
            out[p] = None
    return list(out)


def foreign_words(text: str) -> list[str]:
    """The Cyrillic words on a page that have no business being there."""

    return [w for w in CYR.findall(text) if w not in ALLOWED]


def page_problems(rel: str, text: str, exists: set[str]) -> list[str]:
    """The problems of one page. `exists` — which paths the tree holds.

    A separate function rather than a piece of the walk, precisely so that the
    self-test can hand it a made-up page and confirm that the guard goes red on
    it.
    """

    problems: list[str] = []
    is_ru = rel.endswith(".ru.md")
    stem = rel[: -len(".ru.md")] if is_ru else rel[: -len(".md")]
    en, ru = f"{stem}.md", f"{stem}.ru.md"
    name_en, name_ru = Path(en).name, Path(ru).name

    if is_ru:
        if en not in exists:
            problems.append(
                f"{rel}: a Russian edition with no English one. A name without the "
                f"suffix means English — create {en} or rename this page"
            )
        m = RU_SWITCH.search(text)
        if m is None:
            problems.append(
                f"{rel}: the switch line `[English]({name_en}) · **Русский**` is missing"
            )
        elif m.group(1) != name_en:
            problems.append(
                f"{rel}: the switch points at {m.group(1)!r}, while the counterpart "
                f"is {name_en!r}"
            )
        return problems

    words = foreign_words(text)
    if words:
        shown = ", ".join(sorted(set(words))[:8])
        problems.append(
            f"{rel}: Cyrillic in a file without the suffix ({len(words)} words: "
            f"{shown}). Russian text lives in {ru}, not here"
        )

    if ru in exists:
        m = EN_SWITCH.search(text)
        if m is None:
            problems.append(
                f"{rel}: the page has the counterpart {ru}, but the switch line "
                f"`**English** · [Русский]({name_ru})` is missing"
            )
        elif m.group(1) != name_ru:
            problems.append(
                f"{rel}: the switch points at {m.group(1)!r}, while the counterpart "
                f"is {name_ru!r}"
            )
    return problems


def flang_numbers() -> list[str]:
    """The numbers of the Russian flang description against `docs/state.json`."""

    spec = ROOT / "docs" / "ouroboros.flang"
    state_file = ROOT / "docs" / "state.json"
    if not spec.exists():
        return [f"no {spec.relative_to(ROOT)} — the Russian description in flang"]
    if not state_file.exists():
        return [f"no {state_file.relative_to(ROOT)}"]

    text = spec.read_text(encoding="utf-8")
    state = json.loads(state_file.read_text(encoding="utf-8"))

    #: function name in the description -> key in state.json. The names are the
    #: guard's data: they are Russian because the description they look for is.
    claims = {"Языков": "languages", "Средств MCP": "mcp_tools", "Версия": "version"}
    problems: list[str] = []
    for fn, key in claims.items():
        block = re.search(
            r"тотальная функция «" + re.escape(fn) + r"».*?(?=\nтотальная функция |\Z)",
            text, re.DOTALL,
        )
        if block is None:
            problems.append(
                f"docs/ouroboros.flang: no function \u00ab{fn}\u00bb — the "
                f"description has stopped claiming {key}"
            )
            continue
        m = re.search(r"ожидается\s+(\"[^\"]*\"|\S+)", block.group(0))
        if m is None:
            problems.append(
                f"docs/ouroboros.flang: \u00ab{fn}\u00bb has no example with an "
                "expected value — a claim without an example is not checked by the "
                "compiler"
            )
            continue
        said = m.group(1).strip('"')
        want = str(state.get(key))
        if said != want:
            problems.append(
                f"docs/ouroboros.flang: \u00ab{fn}\u00bb promises {said!r}, while "
                f"docs/state.json has {key} = {want!r}"
            )
    return problems


def ledger_problems(seen: dict[str, str]) -> list[str]:
    """The ledger of what is not done: it must shrink, not live forever."""

    problems: list[str] = []
    for rel, why in sorted(PENDING.items()):
        if rel not in seen:
            problems.append(
                f"{rel}: listed in the PENDING ledger, and there is no such file — "
                "strike the line out"
            )
            continue
        if not foreign_words(seen[rel]):
            problems.append(
                f"{rel}: listed in the PENDING ledger ({why}), and there is no "
                "Russian left in it — strike the line out"
            )
    return problems


def selftest() -> int:
    """The negative control: the guard must go red on a broken page."""

    exists = {"a.md", "a.ru.md", "solo.ru.md"}
    cases: list[tuple[str, str, str, bool]] = [
        (
            "English page with a Russian paragraph",
            "a.md",
            "**English** · [Русский](a.ru.md)\n\n# A\n\nЭто русский абзац.\n",
            True,
        ),
        (
            "English page calls a directory by its former name",
            "a.md",
            "**English** · [Русский](a.ru.md)\n\n# A\n\n"
            "A draft made before the rename is a directory named черновик.\n",
            False,
        ),
        (
            "English page inflects that same word — that is prose already",
            "a.md",
            "**English** · [Русский](a.ru.md)\n\n# A\n\nThe draft lives in черновике.\n",
            True,
        ),
        (
            "Russian page with no English counterpart",
            "solo.ru.md",
            "[English](solo.md) · **Русский**\n\n# Соло\n",
            True,
        ),
        (
            "English page with no switch while the counterpart exists",
            "a.md",
            "# A\n\nPlain English page.\n",
            True,
        ),
        (
            "switch points at the wrong counterpart",
            "a.ru.md",
            "[English](other.md) · **Русский**\n\n# А\n",
            True,
        ),
        (
            "a sound pair",
            "a.md",
            "**English** · [Русский](a.ru.md)\n\n# A\n\nPlain English page.\n",
            False,
        ),
        (
            "a sound Russian half of a pair",
            "a.ru.md",
            "[English](a.md) · **Русский**\n\n# А\n\nРусская страница.\n",
            False,
        ),
    ]

    bad = 0
    for name, rel, text, must_fail in cases:
        got = page_problems(rel, text, exists)
        red = bool(got)
        mark = "✓" if red == must_fail else "✗"
        if red != must_fail:
            bad += 1
        want = "a refusal" if must_fail else "silence"
        print(f"  {mark} {name}: expected {want}, got "
              f"{'a refusal' if red else 'silence'}")
        if red != must_fail and got:
            for g in got:
                print(f"      {g}")

    # A live negative control: take a real English page of the tree and break it
    # in memory. The guard has to notice.
    live = ROOT / "README.md"
    live_cases = 0
    if live.exists():
        live_cases = 2
        text = live.read_text(encoding="utf-8")
        spoiled = text + "\n\nЭтот абзац подсунут самопроверкой.\n"
        if not page_problems("README.md", spoiled, {"README.md", "README.ru.md"}):
            print("  ✗ live control: a broken README.md passed the check")
            bad += 1
        else:
            print("  ✓ live control: the Russian paragraph in README.md was caught")
        if page_problems("README.md", text, {"README.md", "README.ru.md"}):
            print("  ✗ live control: the real README.md does not pass the check")
            bad += 1
        else:
            print("  ✓ live control: the real README.md passes")

    if bad:
        print(f"\nSelf-test: {bad} cases disagreed with what was expected.")
        return 1
    # `live_cases`, not a literal 2: with no README.md the two live controls do
    # not run, and a count that says they did is the check lying about itself.
    print(f"\nSelf-test: {len(cases) + live_cases} cases, all agreed.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return selftest()

    found = pages()
    seen = {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in found}
    exists = set(seen)

    problems: list[str] = []
    pairs = 0
    for rel, text in sorted(seen.items()):
        if rel in PENDING:
            continue
        problems += page_problems(rel, text, exists)
        if rel.endswith(".ru.md"):
            pairs += 1

    problems += flang_numbers()
    problems += ledger_problems(seen)

    if problems:
        print("The language of the documentation parted ways with the file names:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nThe rule: a name without the suffix is English, `.ru.md` is Russian.")
        return 1

    print(f"Pages checked: {len(seen) - len(PENDING)}, of them pairs: {pairs}. "
          f"No Cyrillic in files without the suffix.")
    if PENDING:
        print(f"The PENDING ledger still holds {len(PENDING)} pages with no English "
              f"edition — they are outside the scope of this work and are named one "
              f"by one inside the guard.")
    return 0


if __name__ == "__main__":  # pragma: no cover — the entry point, not a rule
    sys.exit(main())
