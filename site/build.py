#!/usr/bin/env python3
"""Build the Ouroboros landing site.

    python3 site/build.py            build into site/out
    python3 site/build.py --check    build in memory and check, write nothing

There are no dependencies, on purpose: a site that needs a package index to
build is broken exactly when it is needed. Everything here is the standard
library, and the publishing workflow installs nothing.

WHAT THIS REFUSES TO DO. The build *fails* — it does not warn — when:

* a page links to a page or an anchor that does not exist;
* a block of program output names a capture file that was never taken
  (`site/examples/capture.py` takes them);
* a diagram's SVG was drawn from a different text than the .mmd beside it,
  which is what happens when a diagram is edited and never re-rendered;
* a number is asked for by a name that the measured files do not carry.

Each of those is a way for the page to go quietly wrong while still looking
finished, and quiet wrongness is the thing this project exists to argue with.

WHERE THE CONTENT IS. Prose lives in `pages/*.md`, in a small dialect of
Markdown implemented below. Everything that is a *fact* is pulled in at build
time instead of typed:

    {{capture: shop-run.txt}}         a block of real program output
    {{source: site/examples/shop.py}} a file from the tree, shown as code
    {{diagram: pipeline}}             the pre-rendered mermaid SVG, both themes
    {{table: speed}}                  a table printed from the measured JSON
    {{fact: shop.trace_calls}}        one measured number, inline
    {{state: languages}}              one number from docs/state.json

so that a stale page cannot be produced by editing prose alone.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
PAGES = HERE / "pages"
DIAGRAMS = HERE / "diagrams"
CAPTURED = HERE / "examples" / "captured"
DOCS = ROOT / "docs"

# The rule that turns a heading into the address of that heading. It lives in
# `scripts/check_doc_anchors.py`, which checks every link between documentation
# pages against it, and it is imported rather than repeated: a page built by one
# rule and checked by another is a page whose links are checked by nobody.
sys.path.insert(0, str(ROOT / "scripts"))
from check_doc_anchors import anchor_for  # noqa: E402

GITHUB = "https://github.com/digitable-lol/ouroboros"

#: The site, in the order the header shows it. Third field is the tab label.
SITEMAP = [
    ("index", "What it is"),
    ("example", "Examples"),
    ("limits", "Limits"),
]

#: Fenced blocks get a language class; captures get theirs from the extension.
BY_EXTENSION = {".py": "python", ".json": "json", ".jsonl": "json",
                ".txt": "text", ".js": "javascript", ".sh": "bash"}

problems: list[str] = []


def bad(message: str) -> None:
    problems.append(message)


# --------------------------------------------------------------------------- #
# the facts the pages are allowed to quote
# --------------------------------------------------------------------------- #

def read_json(path: Path) -> dict:
    if not path.exists():
        bad(f"missing {path.relative_to(ROOT)} — run site/examples/capture.py")
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Facts:
    """Everything a page may quote as a number, and where it came from."""

    captures: dict[str, str] = field(default_factory=dict)
    measured: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    run: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> Facts:
        f = cls()
        if CAPTURED.exists():
            f.captures = {p.name: p.read_text(encoding="utf-8")
                          for p in sorted(CAPTURED.iterdir()) if p.is_file()}
        f.measured = read_json(CAPTURED / "measurements.json")
        f.run = read_json(CAPTURED / "facts.json")
        f.state = read_json(ROOT / "docs" / "state.json")
        return f

    def dotted(self, key: str) -> str | None:
        node: object = self.run
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return format_number(node)


def format_number(value: object) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, int):
        # A narrow NO-BREAK space, not an ordinary one: a number the browser
        # split across two lines reads as two numbers.
        return f"{value:,}".replace(",", " ")
    return str(value)


def project_facts() -> dict[str, str]:
    """Owner, licence and version — read out of the tree, never typed here.

    A year and a licence typed into a footer template go stale on the 1st of
    January and on the day the project is relicensed, and both go stale in
    silence. `pyproject.toml` and `LICENSE` are where those facts actually
    live, so the footer reads them.
    """

    out = {"version": "", "owner": "", "licence": "", "year": "", "summary": ""}
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for key, pattern in (("version", r'^version = "([^"]+)"'),
                         ("summary", r'^description = "([^"]+)"')):
        m = re.search(pattern, pyproject, re.MULTILINE)
        if m:
            out[key] = m.group(1)
        else:
            bad(f"pyproject.toml has no {key} — the footer has nothing to say")

    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    m = re.search(r"^Copyright \(c\) (\d{4})[,]? (.+?)\s*$", licence, re.MULTILINE)
    if m:
        out["year"], out["owner"] = m.group(1), m.group(2)
    else:
        bad("LICENSE has no `Copyright (c) YEAR, OWNER` line — the footer needs one")
    out["licence"] = licence.splitlines()[0].strip().removesuffix(" License")
    return out


def tree_origin() -> str:
    """Which commit the page was built from. Missing git is not a failure."""

    try:
        rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        date = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cs"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "an unversioned tree"
    if rev.returncode != 0:
        return "an unversioned tree"
    return f"tree <code>{rev.stdout.strip()}</code> of {date.stdout.strip()}"


# --------------------------------------------------------------------------- #
# markdown, the small part of it these pages use
# --------------------------------------------------------------------------- #

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
EM = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def inline(text: str, links: list[str]) -> str:
    """Escape, then put back the four inline forms the pages use."""

    out = html.escape(text, quote=False)
    holes: list[str] = []

    def stash(markup: str) -> str:
        holes.append(markup)
        return f"\x00{len(holes) - 1}\x00"

    out = INLINE_CODE.sub(lambda m: stash(f"<code>{m.group(1)}</code>"), out)

    def link(m: re.Match[str]) -> str:
        href = m.group(2)
        links.append(href)
        extra = "" if href.startswith("#") or "." not in href.split("/")[0] else ""
        if href.startswith("http"):
            extra = ' rel="noopener"'
        return stash(f'<a href="{html.escape(href, quote=True)}"{extra}>{m.group(1)}</a>')

    out = LINK.sub(link, out)
    out = BOLD.sub(r"<strong>\1</strong>", out)
    out = EM.sub(r"<em>\1</em>", out)
    # Backwards, and this is not a detail. A link whose TEXT is inline code —
    # [`run.sh`](...) — parks the code in an earlier hole and the whole link in
    # a later one, so the later hole's markup contains the earlier hole's mark.
    # Filling them in order fills the code hole while it is still hidden inside
    # the unfilled link, and the mark is then never seen again: the page came
    # out reading "the project's own 0". Backwards, the outer hole is opened
    # first and the inner mark is on the surface when its turn comes.
    for i in reversed(range(len(holes))):
        out = out.replace(f"\x00{i}\x00", holes[i])
    if "\x00" in out:
        bad(f"a placeholder survived into the page: {out[:80]!r}")
    return out


def slug(text: str) -> str:
    plain = re.sub(r"<[^>]+>", "", text)
    plain = html.unescape(plain).lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", plain)).strip("-")


def table_html(head: list[str], rows: list[list[str]]) -> str:
    """A table, from cells that are already HTML. The wrapper is what lets a
    wide table scroll on its own instead of widening the page."""

    out = ['<div class="tw"><table><thead><tr>']
    out += [f"<th>{h}</th>" for h in head]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def code_block(text: str, language: str, caption: str = "") -> str:
    body = html.escape(text.rstrip("\n"), quote=False)
    cls = f' class="lang-{language}"' if language else ""
    head = f'<div class="code-title">{html.escape(caption)}</div>' if caption else ""
    return f'<div class="code">{head}<pre{cls}><code>{body}</code></pre></div>'


@dataclass
class Page:
    name: str
    title: str
    tagline: str
    body: str
    toc: list[tuple[str, str]]
    links: list[str]
    anchors: set[str]
    #: Where the page goes, under the documentation root. Empty for the landing,
    #: whose pages are `name.html` beside the index.
    out_rel: str = ""


def front_matter(text: str, where: str,
                 wants: str = "title, tagline") -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        bad(f"{where} has no front matter ({wants})")
        return {}, text
    head, _, rest = text[4:].partition("\n---\n")
    meta = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, rest


class Renderer:
    """One page's worth of the markdown dialect described in the module docstring."""

    def __init__(self, facts: Facts, name: str) -> None:
        self.facts = facts
        self.name = name
        self.out: list[str] = []
        self.toc: list[tuple[str, str]] = []
        self.links: list[str] = []
        self.anchors: set[str] = set()

    # -- directives -------------------------------------------------------- #

    def capture(self, argument: str) -> None:
        filename, _, caption = (a.strip() for a in argument.partition("|"))
        text = self.facts.captures.get(filename)
        if text is None:
            bad(f"{self.name}.md asks for the capture {filename!r}, "
                f"which site/examples/capture.py has not taken")
            return
        language = BY_EXTENSION.get(Path(filename).suffix, "")
        self.out.append(code_block(text, language, caption))

    def source(self, argument: str) -> None:
        rel, _, caption = (a.strip() for a in argument.partition("|"))
        path = ROOT / rel
        if not path.exists():
            bad(f"{self.name}.md shows the file {rel}, which is not in the tree")
            return
        self.out.append(code_block(path.read_text(encoding="utf-8"),
                                   BY_EXTENSION.get(path.suffix, ""), caption or rel))

    def diagram(self, argument: str) -> None:
        name, _, caption = (a.strip() for a in argument.partition("|"))
        source = DIAGRAMS / f"{name}.mmd"
        if not source.exists():
            bad(f"{self.name}.md asks for the diagram {name!r}, which is not in site/diagrams")
            return
        lock = read_json(DIAGRAMS / "rendered.json")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if lock.get(name) != digest:
            bad(f"diagram {name}: the .mmd was edited after the SVG was drawn. "
                f"Re-render with site/render-diagrams.sh")
            return
        for theme in ("light", "dark"):
            if not (DIAGRAMS / f"{name}.{theme}.svg").exists():
                bad(f"diagram {name}: no {theme} SVG. Run site/render-diagrams.sh")
                return
        alt = html.escape(caption or f"{name} diagram", quote=True)
        figure = ['<figure class="diagram">']
        for theme in ("light", "dark"):
            figure.append(f'<img class="{theme}" src="diagrams/{name}.{theme}.svg" alt="{alt}">')
        if caption:
            figure.append(f"<figcaption>{inline(caption, self.links)}</figcaption>")
        figure.append('<details><summary>The mermaid source of this diagram</summary>')
        figure.append(code_block(source.read_text(encoding="utf-8"), "mermaid"))
        figure.append("</details></figure>")
        self.out.append("".join(figure))

    def table(self, argument: str) -> None:
        rows = self.facts.measured.get("rows")
        if not rows:
            bad(f"{self.name}.md asks for the {argument} table, but no measurements "
                f"have been taken (site/examples/capture.py --measure)")
            return
        kind = argument.strip()
        if kind == "speed":
            head = ["language", "plain", "instrumented", "added", "added per call"]
            body = [[label,
                     f"{r['plain_median_s']:.4f} s",
                     f"{r['instrumented_median_s']:.4f} s",
                     f"{r['added_s']:.4f} s",
                     f"{r['added_us_per_call']} µs"] for label, r in rows.items()]
        elif kind == "volume":
            head = ["language", "calls", "lines", "bytes", "bytes per call"]
            body = [[label,
                     format_number(r["calls"]), format_number(r["trace_lines"]),
                     format_number(r["trace_bytes"]), str(r["bytes_per_call"])]
                    for label, r in rows.items()]
        elif kind == "machine":
            head = ["what", "value"]
            body = [[k, v] for k, v in self.facts.measured.get("machine", {}).items()]
        else:
            bad(f"{self.name}.md asks for an unknown table: {kind!r}")
            return
        self.out.append(table_html(
            [html.escape(h) for h in head],
            [[inline(str(c), self.links) for c in row] for row in body]))

    DIRECTIVES = {"capture": capture, "source": source, "diagram": diagram, "table": table}

    # -- inline substitution ------------------------------------------------ #

    def substitute(self, text: str) -> str:
        """`{{fact: a.b}}` and `{{state: key}}` inside a line of prose."""

        def one(m: re.Match[str]) -> str:
            kind, argument = m.group(1), m.group(2).strip()
            if kind == "fact":
                value = self.facts.dotted(argument)
                if value is None:
                    bad(f"{self.name}.md quotes {{{{fact: {argument}}}}}, which is not "
                        f"in site/examples/captured/facts.json")
                    return "?"
                return value
            if kind == "state":
                if argument not in self.facts.state:
                    bad(f"{self.name}.md quotes {{{{state: {argument}}}}}, which is not "
                        f"in docs/state.json")
                    return "?"
                return format_number(self.facts.state[argument])
            if kind == "repo":
                return GITHUB
            if kind == "measured":
                label, _, key = argument.partition(".")
                row = self.facts.measured.get("rows", {}).get(label)
                if not row or key not in row:
                    bad(f"{self.name}.md quotes {{{{measured: {argument}}}}}, which the "
                        f"measurements do not carry")
                    return "?"
                return format_number(row[key])
            bad(f"{self.name}.md uses an unknown substitution: {kind}")
            return "?"

        text = text.replace("{{repo}}", GITHUB)
        return re.sub(r"\{\{(fact|state|measured):([^}]*)\}\}", one, text)

    # -- the block walk ----------------------------------------------------- #

    def render(self, text: str) -> None:
        lines = self.substitute(text).split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            if not line.strip():
                i += 1
                continue

            if line.startswith("```"):
                language = line[3:].strip()
                block, i = [], i + 1
                while i < len(lines) and not lines[i].startswith("```"):
                    block.append(lines[i])
                    i += 1
                self.out.append(code_block("\n".join(block), language))
                i += 1
                continue

            m = re.fullmatch(r"\{\{(\w+):(.*)\}\}", line.strip())
            if m and m.group(1) in self.DIRECTIVES:
                self.DIRECTIVES[m.group(1)](self, m.group(2))
                i += 1
                continue

            if line.startswith(":::"):
                classes = line[3:].strip()
                if classes:
                    self.out.append(f'<section class="{html.escape(classes, quote=True)}">')
                else:
                    self.out.append("</section>")
                i += 1
                continue

            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                body = inline(line[level:].strip(), self.links)
                anchor = slug(body)
                self.anchors.add(anchor)
                if level == 2:
                    self.toc.append((anchor, body))
                self.out.append(f'<h{level} id="{anchor}">{body}</h{level}>')
                i += 1
                continue

            if line.startswith("|"):
                block, i = [], i
                while i < len(lines) and lines[i].startswith("|"):
                    block.append(lines[i])
                    i += 1
                self.out.append(self.markdown_table(block))
                continue

            if line.startswith("> "):
                block, i = [], i
                while i < len(lines) and lines[i].startswith(">"):
                    block.append(lines[i].lstrip(">").strip())
                    i += 1
                inner = "</p><p>".join(
                    inline(p, self.links) for p in "\n".join(block).split("\n\n"))
                self.out.append(f"<blockquote><p>{inner}</p></blockquote>")
                continue

            if re.match(r"^([-*]|\d+\.) ", line):
                ordered = bool(re.match(r"^\d+\. ", line))
                items, i = [], i
                while i < len(lines) and re.match(r"^([-*]|\d+\.) ", lines[i]):
                    item = re.sub(r"^([-*]|\d+\.) ", "", lines[i])
                    i += 1
                    while i < len(lines) and lines[i].startswith("  ") and lines[i].strip():
                        item += " " + lines[i].strip()
                        i += 1
                    items.append(inline(item, self.links))
                tag = "ol" if ordered else "ul"
                self.out.append(f"<{tag}>" + "".join(f"<li>{it}</li>" for it in items)
                                + f"</{tag}>")
                continue

            if line.strip() == "---":
                self.out.append("<hr>")
                i += 1
                continue

            block, i = [], i
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(
                    ("#", "|", ">", "```", ":::", "{{")) and not re.match(
                    r"^([-*]|\d+\.) ", lines[i]):
                block.append(lines[i])
                i += 1
            self.out.append(f"<p>{inline(' '.join(block), self.links)}</p>")

    def markdown_table(self, block: list[str]) -> str:
        def cells(row: str) -> list[str]:
            return [c.strip() for c in row.strip().strip("|").split("|")]

        return table_html([inline(h, self.links) for h in cells(block[0])],
                          [[inline(c, self.links) for c in cells(r)]
                           for r in block[2:]])


# --------------------------------------------------------------------------- #
# the page around the content
# --------------------------------------------------------------------------- #

FAVICON = (
    "data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20"
    "viewBox%3D%220%200%2024%2024%22%3E%3Ccircle%20cx%3D%2212%22%20cy%3D%2212%22%20r%3D%228%22"
    "%20fill%3D%22none%22%20stroke%3D%22%23c2410c%22%20stroke-width%3D%223%22%20"
    "stroke-dasharray%3D%2238%2010%22%2F%3E%3Ccircle%20cx%3D%2219%22%20cy%3D%2212%22%20"
    "r%3D%222.6%22%20fill%3D%22%23c2410c%22%2F%3E%3C%2Fsvg%3E"
)

MARK = ('<svg class="mark" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">'
        '<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="3" '
        'stroke-dasharray="38 10"/><circle cx="19" cy="12" r="2.6" fill="currentColor"/></svg>')

THEME_SCRIPT = """
(function () {
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem('ouroboros-theme');
    if (saved) root.setAttribute('data-theme', saved);
  } catch (e) {}
  var button = document.querySelector('.theme');
  if (!button) return;
  button.addEventListener('click', function () {
    var now = root.getAttribute('data-theme');
    var dark = now ? now === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    var next = dark ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('ouroboros-theme', next); } catch (e) {}
  });
})();
"""


def layout(page: Page, project: dict[str, str], origin: str,
           prefix: str = "") -> str:
    """The page, in the shell every page on the site wears.

    `prefix` is how far up the site root is from the page being written: empty
    for the landing, `../` for a documentation page, `../../` for one in
    `docs/examples`. Every address in the shell is written through it, because
    the same shell is now used from more than one depth.
    """

    tabs = []
    for name, label in SITEMAP:
        here = ' class="here"' if name == page.name else ""
        tabs.append(f'<a{here} href="{prefix}{name}.html">{label}</a>')
    here = ' class="here"' if page.name == "docs" else ""
    tabs.append(f'<a{here} href="{prefix}{DOCS_HOME}/index.html">Documentation</a>')

    # A documentation page in Russian says so, so that a reader machine — a
    # screen reader, a translator — is not told it is reading English.
    lang = "ru" if page.out_rel.endswith(".ru.html") else "en"

    toc = ""
    if len(page.toc) > 1:
        items = "".join(f'<li><a href="#{a}">{t}</a></li>' for a, t in page.toc)
        toc = ('<nav class="toc" aria-label="On this page">'
               f'<div class="toc-title">On this page</div><ul>{items}</ul></nav>')

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page.title)} · Ouroboros</title>
<meta name="description" content="{html.escape(page.tagline or project['summary'], quote=True)}">
<link rel="icon" href="{FAVICON}">
<link rel="stylesheet" href="{prefix}style.css">
</head>
<body>
<a class="skip" href="#content">Skip to the content</a>
<header class="top">
  <a class="brand" href="{prefix}index.html"
     aria-label="Ouroboros, home">{MARK}<span>Ouroboros</span></a>
  <span class="tagline">what your code actually did, one line in and one line out</span>
  <nav class="tabs" aria-label="Sections">{"".join(tabs)}</nav>
  <a class="version" href="{GITHUB}/releases" rel="noopener">{project['version']}</a>
  <a class="gh" href="{GITHUB}" rel="noopener">GitHub</a>
  <button class="theme" type="button" aria-label="Switch theme">◐</button>
</header>
<div class="shell">
  <main id="content" class="content">{page.body}</main>
  {toc}
</div>
<footer class="bottom">
  <p>Ouroboros is made by {html.escape(project['owner'])} ·
     {html.escape(project['licence'])} licence, © {project['year']} ·
     source at <a href="{GITHUB}" rel="noopener">github.com/digitable-lol/ouroboros</a> ·
     something wrong? <a href="{GITHUB}/issues" rel="noopener">open an issue</a></p>
  <p class="origin">Version {project['version']} · built from {origin} ·
     every block of program output on this site is a real run, taken by
     <code>site/examples/capture.py</code></p>
</footer>
<script>{THEME_SCRIPT}</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# the documentation: docs/*.md, rendered here rather than by Jekyll
# --------------------------------------------------------------------------- #

#: Where the documentation lands on the published site. It cannot stay at the
#: root: `index.html` and `limits.html` are pages of the landing too, and a site
#: root has room for one of each. Every address the documentation used to answer
#: on keeps answering — as a redirect written by `write_docs` below.
DOCS_HOME = "docs"

#: Files the documentation links to, or that a check reads off the site, copied
#: as they are. `state.json` is fetched from the ROOT by
#: `scripts/check_pages_live.py`, so these are written in both places.
DOCS_ASSETS = ("*.json", "*.flang")

#: A line that opens with a tag or a comment and closes with one: `<details>`,
#: `</details>`, `<details><summary>…</summary>`, `<!--schema-facts-->` — the
#: hand-written HTML these pages actually contain. It goes onto the page as
#: written. A line that merely begins with `<` is prose — `<work> is the
#: directory the capture ran in` — and is escaped like any other.
RAW_HTML_LINE = re.compile(r"(?:<!--|</?[A-Za-z])[^>]*>(?:.*>)?$", re.DOTALL)

#: A pipe that ends a table cell, as opposed to one written `\|` inside it.
CELL_BORDER = re.compile(r"(?<!\\)\|")

#: Inline code fenced with two backticks, which is how the tool's own Python
#: docstrings write it — `docs/mcp-tools.md` is printed from them.
DOUBLE_CODE = re.compile(r"``([^`]+)``")

HEADING_LINE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
LIST_ITEM = re.compile(r"^([-*]|\d+\.)\s+")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class DocsRenderer:
    """One documentation page.

    Not an implementation of Markdown — an implementation of the dialect
    `docs/` is actually written in, which a walk over every page says is:
    headings, paragraphs, lists (an item may hold more than one block), pipe
    tables, fenced code, block quotes, hand-written `<details>` blocks, HTML
    comments, and the four inline forms.

    Two things here that the landing never needs:

    * an HTML comment survives onto the page as written. `<!--state:version-->`
      is not decoration — `scripts/check_pages_live.py` reads those numbers off
      the live page and compares them with `docs/state.json`;
    * a link to `install.md` is published as a link to `install.html`. That is
      what the jekyll-relative-links plugin did while Jekyll built these pages,
      and it is why every address on the site ends in `.html`.

    And one thing it does better than what it replaces: what is written between
    `<details>` and `</details>` is Markdown here. kramdown passed it through
    raw, which is why the collapsed sections of the published `mcp-tools.html`
    showed their own ``` fences as text.
    """

    def __init__(self, where: str) -> None:
        self.where = where
        self.out: list[str] = []
        self.toc: list[tuple[str, str]] = []
        self.links: list[str] = []
        self.anchors: set[str] = set()

    # -- inline ------------------------------------------------------------- #

    def link(self, label: str, href: str) -> str:
        self.links.append(href)
        target = href
        if not href.startswith(("http://", "https://", "mailto:", "#")):
            path, sep, fragment = href.partition("#")
            if path.endswith(".md"):
                path = path[: -len(".md")] + ".html"
            target = path + sep + fragment
        extra = ' rel="noopener"' if target.startswith("http") else ""
        return f'<a href="{html.escape(target, quote=True)}"{extra}>{label}</a>'

    def inline(self, text: str) -> str:
        holes: list[str] = []

        def stash(markup: str) -> str:
            holes.append(markup)
            return f"\x00{len(holes) - 1}\x00"

        # Comments before escaping: their angle brackets are their own.
        text = HTML_COMMENT.sub(lambda m: stash(m.group(0)), text)
        out = html.escape(text, quote=False)
        out = DOUBLE_CODE.sub(lambda m: stash(f"<code>{m.group(1)}</code>"), out)
        out = INLINE_CODE.sub(lambda m: stash(f"<code>{m.group(1)}</code>"), out)
        out = LINK.sub(lambda m: stash(self.link(m.group(1), m.group(2))), out)
        out = BOLD.sub(r"<strong>\1</strong>", out)
        out = EM.sub(r"<em>\1</em>", out)
        # Backwards, for the reason given at the top of `inline`: an outer hole
        # can hold an inner hole's mark, and the outer one has to open first.
        for i in reversed(range(len(holes))):
            out = out.replace(f"\x00{i}\x00", holes[i])
        if "\x00" in out:
            bad(f"{self.where}: a placeholder survived into the page: {out[:80]!r}")
        return out

    # -- blocks ------------------------------------------------------------- #

    def blocks(self, lines: list[str]) -> str:
        """Render a run of lines and hand back the HTML, leaving `out` alone.

        Block quotes and list items are rendered by calling this again on what
        is inside them, which is how a fenced block inside a numbered item —
        `docs/with-ai.md` has one — stays a fenced block instead of ending the
        list and starting the numbering over.
        """

        keep, self.out = self.out, []
        self.walk(lines)
        body, self.out = "".join(self.out), keep
        return body

    def walk(self, lines: list[str]) -> None:
        i = 0
        while i < len(lines):
            line = lines[i]

            if not line.strip():
                i += 1
                continue

            if line.startswith("```"):
                language = line[3:].strip()
                block, i = [], i + 1
                while i < len(lines) and not lines[i].startswith("```"):
                    block.append(lines[i])
                    i += 1
                if i >= len(lines):
                    bad(f"{self.where}: a fenced block is opened and never closed")
                self.out.append(code_block("\n".join(block), language))
                i += 1
                continue

            head = HEADING_LINE.match(line)
            if head:
                level, raw = len(head.group(1)), head.group(2)
                anchor = anchor_for(raw, self.anchors)
                self.anchors.add(anchor)
                shown = self.inline(raw)
                if level == 2:
                    self.toc.append((anchor, shown))
                self.out.append(f'<h{level} id="{anchor}">{shown}</h{level}>')
                i += 1
                continue

            if line.startswith("<") and RAW_HTML_LINE.fullmatch(line.strip()):
                self.out.append(line.strip())
                i += 1
                continue

            if line.startswith("|"):
                block, i = [], i
                while i < len(lines) and lines[i].startswith("|"):
                    block.append(lines[i])
                    i += 1
                self.out.append(self.table(block))
                continue

            if line.startswith(">"):
                block, i = [], i
                while i < len(lines) and lines[i].startswith(">"):
                    block.append(re.sub(r"^>[ \t]?", "", lines[i]))
                    i += 1
                self.out.append(f"<blockquote>{self.blocks(block)}</blockquote>")
                continue

            if LIST_ITEM.match(line):
                i = self.list_block(lines, i)
                continue

            block, i = [], i
            while (i < len(lines) and lines[i].strip()
                   and not lines[i].startswith(("#", "|", ">", "```"))
                   and not (lines[i].startswith("<")
                            and RAW_HTML_LINE.fullmatch(lines[i].strip()))
                   and not LIST_ITEM.match(lines[i])):
                block.append(lines[i].strip())
                i += 1
            self.out.append(f"<p>{self.inline(' '.join(block))}</p>")

    def list_block(self, lines: list[str], start: int) -> int:
        """One list, from its first marker to the first line that is neither an
        item nor indented under one. Returns where to carry on from."""

        ordered = bool(re.match(r"^\d+\.\s", lines[start]))
        rendered: list[str] = []
        i = start
        while i < len(lines) and LIST_ITEM.match(lines[i]):
            item = [LIST_ITEM.sub("", lines[i], count=1)]
            i += 1
            while i < len(lines):
                if lines[i].strip():
                    if not lines[i].startswith((" ", "\t")):
                        break
                    item.append(lines[i])
                    i += 1
                    continue
                # A blank line ends the item unless the item goes on below it,
                # indented: that is how a code block sits inside an item.
                ahead = i
                while ahead < len(lines) and not lines[ahead].strip():
                    ahead += 1
                if ahead >= len(lines) or not lines[ahead].startswith((" ", "\t")):
                    break
                item.extend([""] * (ahead - i))
                i = ahead
            rendered.append(self.item(item))
        self.out.append(("<ol>" if ordered else "<ul>")
                        + "".join(f"<li>{it}</li>" for it in rendered)
                        + ("</ol>" if ordered else "</ul>"))
        return i

    def item(self, lines: list[str]) -> str:
        """One list item: a run of prose stays a run of prose, and an item with
        a second block in it goes through the block walk.

        The item is dedented by however far its own continuation lines are
        indented, so that a fenced block written under a numbered item opens at
        the left margin of the item and is seen as a fence.
        """

        indents = [len(ln) - len(ln.lstrip()) for ln in lines[1:] if ln.strip()]
        body = list(lines)
        if indents:
            body = [lines[0]] + [ln[min(indents):] if ln.strip() else ln
                                 for ln in lines[1:]]
        blocky = any(not ln.strip() for ln in body) or any(
            ln.startswith(("```", "|", ">", "#")) or LIST_ITEM.match(ln)
            for ln in body[1:])
        if blocky:
            return self.blocks(body)
        return self.inline(" ".join(ln.strip() for ln in body))

    def table(self, block: list[str]) -> str:
        def cells(row: str) -> list[str]:
            row = row.strip().removeprefix("|").removesuffix("|")
            # A cell may hold a pipe of its own, written `\|` — `--outcome
            # result\|raised` in getting-started.md. Split on the others only,
            # then let the escaped one be an ordinary character again.
            return [c.strip().replace("\\|", "|") for c in CELL_BORDER.split(row)]

        head = [self.inline(h) for h in cells(block[0])]
        rows = [[self.inline(c) for c in cells(r)] for r in block[2:]]
        return table_html(head, rows)


def build_docs() -> list[Page]:
    """Every `docs/**/*.md`, rendered. The page order is the file order."""

    pages: list[Page] = []
    for path in sorted(DOCS.glob("**/*.md")):
        rel = path.relative_to(DOCS)
        where = f"docs/{rel.as_posix()}"
        meta, text = front_matter(path.read_text(encoding="utf-8"), where, "title")
        renderer = DocsRenderer(where)
        body = renderer.blocks(text.split("\n"))
        pages.append(Page(name="docs",
                          title=meta.get("title", rel.stem),
                          tagline="",
                          body=body,
                          toc=renderer.toc,
                          links=renderer.links,
                          anchors=renderer.anchors,
                          out_rel=rel.with_suffix(".html").as_posix()))
    if not any(page.out_rel == "index.html" for page in pages):
        bad("docs/index.md is not in the tree, and every page on the site has a "
            "Documentation tab pointing at docs/index.html")
    check_docs_links(pages)
    return pages


def check_docs_links(pages: list[Page]) -> None:
    """A link between documentation pages has to land somewhere on the site.

    `scripts/check_doc_anchors.py` asks the same question of the tree; this one
    asks it of the site that is about to be published, where the answer can be
    different — a page can be in the tree and not on the site, which is exactly
    what happened to `measurements.html` for a whole day.
    """

    known = {page.out_rel: page.anchors for page in pages}
    assets = {path.name for pattern in DOCS_ASSETS for path in DOCS.glob(pattern)}
    for page in pages:
        here = PurePosixPath(page.out_rel).parent
        for href in page.links:
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            target, _, anchor = href.partition("#")
            if not target:
                if anchor not in page.anchors:
                    bad(f"docs/{page.out_rel}: links to #{anchor}, "
                        f"and the page has no such heading")
                continue
            if target.endswith(".md"):
                target = target[: -len(".md")] + ".html"
            resolved = str(PurePosixPath(os.path.normpath(here / target)))
            if resolved in assets or PurePosixPath(resolved).name in assets:
                continue
            if resolved not in known:
                bad(f"docs/{page.out_rel}: links to {href}, and the site will "
                    f"have no {resolved}")
                continue
            if anchor and anchor not in known[resolved]:
                bad(f"docs/{page.out_rel}: links to {href}, and {resolved} "
                    f"has no such heading")


REDIRECT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} · Ouroboros</title>
<link rel="canonical" href="{target}">
<meta http-equiv="refresh" content="0; url={target}">
<link rel="icon" href="{favicon}">
</head>
<body>
<p>This page now lives at <a href="{target}">{target}</a>.</p>
</body>
</html>
"""


def write_docs(docs: list[Page], project: dict[str, str], origin: str) -> int:
    """Write the documentation into `docs/` of the site, its data files beside
    it, and a redirect at every address the documentation answered on before.

    Returns how many redirects were written. The two addresses that get none
    are `index.html` and `limits.html`: the landing has pages of those names,
    the root has room for one of each, and the landing is what the root is for.
    """

    for page in docs:
        target = OUT / DOCS_HOME / page.out_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        depth = 1 + page.out_rel.count("/")
        target.write_text(layout(page, project, origin, "../" * depth),
                          encoding="utf-8")

    for pattern in DOCS_ASSETS:
        for path in sorted(DOCS.glob(pattern)):
            shutil.copy(path, OUT / DOCS_HOME / path.name)
            shutil.copy(path, OUT / path.name)

    landing = {f"{name}.html" for name, _ in SITEMAP}
    written = 0
    for page in docs:
        if page.out_rel in landing:
            continue
        stub = OUT / page.out_rel
        stub.parent.mkdir(parents=True, exist_ok=True)
        up = "../" * page.out_rel.count("/")
        stub.write_text(REDIRECT.format(title=html.escape(page.title),
                                        target=f"{up}{DOCS_HOME}/{page.out_rel}",
                                        favicon=FAVICON), encoding="utf-8")
        written += 1
    return written


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #

def build() -> list[Page]:
    facts = Facts.load()
    pages: list[Page] = []
    for name, _ in SITEMAP:
        path = PAGES / f"{name}.md"
        if not path.exists():
            bad(f"no page source {path.relative_to(ROOT)}")
            continue
        meta, text = front_matter(path.read_text(encoding="utf-8"), f"{name}.md")
        renderer = Renderer(facts, name)
        renderer.render(text)
        pages.append(Page(name=name,
                          title=meta.get("title", name),
                          tagline=meta.get("tagline", ""),
                          body="".join(renderer.out),
                          toc=renderer.toc,
                          links=renderer.links,
                          anchors=renderer.anchors))
    check_links(pages)
    return pages


def check_links(pages: list[Page]) -> None:
    """Every internal link has to land somewhere. A dead one fails the build.

    A renamed heading leaves links that still look like links and answer 404 —
    the sort of rot nobody notices from inside the tree, which is why this is a
    refusal and not a warning.
    """

    known = {p.name: p.anchors for p in pages}
    for page in pages:
        for href in page.links:
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            target, _, anchor = href.partition("#")
            name = page.name if not target else Path(target).stem
            if name not in known:
                bad(f"{page.name}.md links to {href}, and there is no such page")
                continue
            if anchor and anchor not in known[name]:
                bad(f"{page.name}.md links to {href}, and {name}.html has no such heading")


def write(pages: list[Page], docs: list[Page]) -> int:
    project = project_facts()
    origin = tree_origin()
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "diagrams").mkdir(parents=True)
    for page in pages:
        (OUT / f"{page.name}.html").write_text(layout(page, project, origin), encoding="utf-8")
    shutil.copy(HERE / "style.css", OUT / "style.css")
    for svg in sorted(DIAGRAMS.glob("*.svg")):
        shutil.copy(svg, OUT / "diagrams" / svg.name)
    # GitHub Pages runs Jekyll over an uploaded tree unless told not to; the
    # underscore-free names here would survive it, but the marker costs nothing
    # and removes a whole class of surprise.
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    return write_docs(docs, project, origin)


def main(argv: list[str]) -> int:
    pages = build()
    docs = build_docs()
    if problems:
        print("The site was NOT built. What is wrong:\n", file=sys.stderr)
        for problem in problems:
            print(f"  * {problem}", file=sys.stderr)
        return 1
    if "--check" in argv:
        print(f"checked: {len(pages)} landing pages and {len(docs)} documentation "
              f"pages, every link, every heading and every number resolves")
        return 0
    redirects = write(pages, docs)
    total = sum(path.stat().st_size for path in OUT.glob("**/*.html"))
    print(f"built {len(pages)} landing pages and {len(docs)} documentation pages "
          f"into {OUT.relative_to(ROOT)}/, plus {redirects} redirects from the "
          f"addresses the documentation had ({total // 1024} KiB of HTML)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
