"""Checks the documentation pages: the links between them and each page's header.

Why. `scripts/check_doc_links.py` guards links into SOURCE LINES. Nobody watched
the links between the pages themselves, and they break the same quiet way: a
section is renamed, the link to it stays behind and goes on looking correct.
Jekyll does not complain while building the site; the reader simply lands at the
top of the page instead of the place that was meant.

What is checked:

* a link to a file (`limits.md`, `../SPEC.md`) — the file exists;
* a link with an anchor (`limits.md#where-instrumentation-changes-behaviour`) —
  that file has a heading which yields such an anchor;
* the page header (whatever sits between the two leading `---`) — it parses as
  YAML. The most common breakage is caught: an unquoted value with a colon and a
  space inside it. For a page that means "the title vanished"; for a skill file
  it means the skill does not load at all, and in neither case is there an error
  to see;
* `docs/_config.yml` — by the same rule. Different file, same breakage, and it
  slipped through precisely because the check only covered page headers: on
  29 August an unquoted `description:` with a colon stopped the site build for a
  full day, and all that time the site silently served the previous snapshot.

The anchor is computed by the very code that assigns it when the site is built:
`site/build.py` imports `anchor_for` from here. The heading is lowercased,
backticks and emphasis are stripped, punctuation is dropped, spaces become
hyphens, and a repeated anchor gets a number (`-1`, `-2`).

What the check does NOT do: it does not follow external links (`http://`,
`https://`) — that would need the network, and the gate must work without one.

Run: uv run python scripts/check_doc_anchors.py   (from the repository root)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: What counts as documentation.
GLOBS = ("docs/**/*.md", "*.md", "skill/*.md")

#: `[text](target)` — a target with no spaces in it.
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

#: A Markdown heading.
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

#: A `key: value` line in a page header or in the build configuration.
FRONT_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*): (.*)$")

#: The configuration GitHub builds the site by. Breaks as quietly as a header.
CONFIG = ROOT / "docs" / "_config.yml"


def anchor_for(text: str, taken: set[str]) -> str:
    """The anchor that the heading `text` will get on the page.

    One rule for the whole repository: `site/build.py` assigns anchors with this
    same function when it builds the documentation pages for the site. While
    kramdown assigned them, the rule had to be restated here from memory of its
    behaviour; now that we build the pages ourselves there is nothing to restate —
    the check and the build call one piece of code. They have no way left to
    drift apart.

    The heading is lowercased, backticks and emphasis are stripped, punctuation
    is dropped, spaces become hyphens. A repeated anchor gets a number (`-1`,
    `-2`) — that is what `taken` is for.
    """

    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*?([^*]*)\*\*?", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\s\-]", "", text.lower(), flags=re.UNICODE)
    anchor = text.strip().replace(" ", "-")
    base, n = anchor, 1
    while anchor in taken:
        anchor = f"{base}-{n}"
        n += 1
    return anchor


def anchors(path: Path) -> set[str]:
    """The anchors this page will make out of its own headings."""

    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = HEADING.match(line)
        if m:
            out.add(anchor_for(m.group(2), out))
    return out


def front_matter_problems(path: Path) -> list[str]:
    """Problems in a page header that silently break YAML parsing.

    One is checked, and it is the most common one: an unquoted value with a colon
    and a space inside it. YAML reads that as a nested mapping and refuses to
    parse the whole header. For a page that means "no title"; for a skill file it
    means the skill does not load at all, and in neither case is there an error to
    see — that is exactly how the header of skill/SKILL.md broke.

    Checked by hand rather than through YAML: pulling in an outside library for
    one line is not worth it, and this breakage is caught by a three-line rule.
    """

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return []
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return [f"{path}: the header is opened but never closed"]

    return unquoted_colon_problems(path, parts[1], first_lineno=2,
                                   what="page header")


def config_problems() -> list[str]:
    """The same breakage in `docs/_config.yml` — the site build configuration.

    Same rule as for a page header, different file, and that is exactly why the
    breakage slipped through: the header check was already in place on 29 August
    when an unquoted `description:` with a colon stopped the site build for a full
    day. Here the price is higher: not "a page lost its title" but the whole site
    frozen on the previous snapshot, serving yesterday without a single complaint.
    """

    if not CONFIG.exists():
        # Not "nothing to check": without this file the site does not build at
        # all. Returning an empty list here is how a check stops checking.
        return [f"{CONFIG}: missing — the site has no build configuration"]
    return unquoted_colon_problems(CONFIG, CONFIG.read_text(encoding="utf-8"),
                                   first_lineno=1, what="build configuration")


def unquoted_colon_problems(path: Path, text: str, first_lineno: int, what: str) -> list[str]:
    """Unquoted values with a colon and a space inside them.

    YAML reads that as a nested mapping and refuses to parse the whole file.
    Checked by hand rather than through YAML: pulling in an outside library for
    one rule is not worth it, and this breakage is caught in three lines.
    """

    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), first_lineno):
        m = FRONT_FIELD.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if not value or value[0] in "\"'[{|>":
            continue  # quoted, a list, a mapping or a block — it will parse
        if ": " in value:
            problems.append(
                f"{path}:{lineno}: the value of {key!r} is unquoted and contains a "
                f"colon followed by a space — YAML will not parse this {what}; put "
                "the value in double quotes"
            )
    return problems


def pages() -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file():
                seen[p] = None
    return list(seen)


def link_problems(page: Path, cache: dict[Path, set[str]]) -> tuple[list[str], int]:
    """Every link out of one page: does the file exist, does the heading exist?

    ``cache`` holds the anchors already read off a page, so a page linked from
    forty others is parsed once. Returns the complaints and how many links were
    looked at.
    """

    bad: list[str] = []
    checked = 0
    rel = page.relative_to(ROOT)
    for target in LINK.findall(page.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#!")):
            continue
        path_part, _, fragment = target.partition("#")
        checked += 1

        if path_part:
            dest = (page.parent / path_part).resolve()
            if not dest.exists():
                bad.append(f"  - {rel}: no such file: {path_part}")
                continue
        else:
            dest = page  # a link into the same page

        if fragment and dest.suffix == ".md":
            if dest not in cache:
                cache[dest] = anchors(dest)
            if fragment not in cache[dest]:
                where = dest.relative_to(ROOT)
                bad.append(f"  - {rel}: {where} has no heading with anchor #{fragment}")
    return bad, checked


def main() -> int:
    cache: dict[Path, set[str]] = {}
    bad = [f"  - {p.replace(str(ROOT) + '/', '')}" for p in config_problems()]
    for page in pages():
        bad += [f"  - {p.replace(str(ROOT) + '/', '')}"
                for p in front_matter_problems(page)]

    checked = 0
    for page in pages():
        found, n = link_problems(page, cache)
        bad += found
        checked += n

    if bad:
        print("Problems in the documentation pages:\n", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        print(f"\nLinks checked: {checked}. Problems: {len(bad)}. Point each link at "
              f"a heading that exists, or add the heading it names.", file=sys.stderr)
        return 1

    print(f"Documentation pages: the build configuration and the headers parse, "
          f"{checked} links checked — every one lands somewhere.")
    return 0


if __name__ == "__main__":  # pragma: no cover — the entry point, not a rule
    raise SystemExit(main())
