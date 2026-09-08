#!/usr/bin/env python3
"""Ask the published site whether the landing, the documentation and the
diagrams are really on it.

    python3 site/check-live.py
    python3 site/check-live.py --base http://127.0.0.1:8000 --retries 20 --wait 30
    python3 site/check-live.py --self-test

WHY THIS EXISTS AT ALL. GitHub builds and serves the site itself, and when that
goes wrong nobody is told: the previous successful snapshot keeps being served.
This project has already lost a day to exactly that. So the check is not "did
the build pass" — it is "does the thing on the internet have the pages and the
pictures in it".

WHY IT ASKS FOR A SENTENCE AND NOT JUST A PAGE. The version of this check that
asked every page for a 200 and an `<main>` was green for weeks while the landing
was on the site nowhere at all. Two builds were publishing into one root — the
built-in Jekyll one from `docs/` and this one from `site/out` — and the root
answered with whichever finished last, which was Jekyll's. Every address the
check asked for existed, so the check was happy; the site simply was not the
site. Hence `LANDING_MARK`: a sentence that is on the landing and, as the check
itself makes sure before it asks anything, on no documentation page. A root that
cannot show that sentence is not the landing, whatever it answers.

WHAT IT ASKS FOR

* the root — the address a reader types — shows the landing;
* every landing page answers, with our title and a body;
* every documentation page answers under `docs/`, and the documentation index
  still carries the `<!--state:…-->` numbers `scripts/check_pages_live.py`
  reads off it;
* every address the documentation had while Jekyll served it still answers, and
  the redirect at it names where the page went;
* every diagram in `site/diagrams` is fetched by its own address and its bytes
  start an SVG document — the diagrams are the one part of this site that is a
  separate file the page has to fetch, and a diagram that failed to publish
  leaves the page looking finished with a hole where the picture was;
* every `<img>` a landing page names is fetched, for the same reason.

Exit codes, following `scripts/check_pages_live.py` in this tree:

    0  the site is there and matches
    1  the site answered, and what it answered is wrong
    2  the site could not be reached at all (no network) — not a verdict
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOCS = ROOT / "docs"
DEFAULT_BASE = "https://digitable-lol.github.io/ouroboros/"

#: Where the documentation lives on the site. The same name `site/build.py`
#: writes it under; a page cannot be checked at an address nobody publishes to.
DOCS_HOME = "docs"

#: A sentence off the landing that no documentation page says. This is the whole
#: difference between "the site answers" and "the site is ours": see the head of
#: this file. `premises()` refuses to run the check if the sentence has left the
#: landing or turned up in the documentation.
LANDING_MARK = "Wrapping happens once"

IMG = re.compile(r'<img[^>]+src="([^"]+)"')
TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
STATE_MARK = re.compile(r"<!--state:([a-z_]+)-->")


def fetch(url: str, timeout: float = 30.0) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "ouroboros-site-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# --------------------------------------------------------------------------- #
# what the site is supposed to hold, taken from the tree rather than typed here
# --------------------------------------------------------------------------- #

def landing_pages() -> list[str]:
    """The pages the landing generator makes."""

    return sorted(p.stem + ".html" for p in (HERE / "pages").glob("*.md"))


def docs_pages() -> list[str]:
    """The documentation, by its address under `docs/` on the site."""

    return sorted(p.relative_to(DOCS).with_suffix(".html").as_posix()
                  for p in DOCS.glob("**/*.md"))


def diagram_files() -> list[str]:
    """Every rendered diagram, by file name. Both themes of each are separate
    files, and either of them can be the one that fails to publish."""

    return sorted(p.name for p in (HERE / "diagrams").glob("*.svg"))


def old_addresses() -> list[tuple[str, str]]:
    """`(address as it was, where the page is now)` for every documentation page
    whose address the landing did not take. Those two — `index.html` and
    `limits.html` — are pages of the landing as well, and the root has room for
    one of each."""

    landing = set(landing_pages())
    return [(page, f"{DOCS_HOME}/{page}") for page in docs_pages() if page not in landing]


def premises() -> list[str]:
    """What this check believes before it asks the site anything.

    A check that cannot go red is worth nothing, and this one leans on a single
    sentence being in one place and not in another. If that stops being true —
    the landing was rewritten, the documentation quoted it — the check would go
    on passing while meaning nothing. So it is checked, and it is checked first.
    """

    wrong: list[str] = []
    landing = (HERE / "pages" / "index.md").read_text(encoding="utf-8")
    if LANDING_MARK not in landing:
        wrong.append(f"site/pages/index.md no longer says {LANDING_MARK!r}, so this "
                     f"check has nothing to tell the landing by — pick another "
                     f"sentence off the landing and put it in LANDING_MARK")
    for path in sorted(DOCS.glob("**/*.md")):
        if LANDING_MARK in path.read_text(encoding="utf-8"):
            wrong.append(f"{path.relative_to(ROOT)} says {LANDING_MARK!r} too, so the "
                         f"sentence no longer tells the landing from the documentation")
    return wrong


# --------------------------------------------------------------------------- #
# the questions
# --------------------------------------------------------------------------- #

def check(base: str) -> list[str]:
    wrong: list[str] = premises()
    if wrong:
        return wrong

    seen_images = 0

    # 1. The root: what a reader gets for typing the address.
    status, body = fetch(base)
    if status != 200:
        wrong.append(f"{base} answered {status}")
    elif LANDING_MARK not in body.decode("utf-8", "replace"):
        wrong.append(f"{base} does not say {LANDING_MARK!r} — the root is not the "
                     f"landing. Whatever else is being served there, the landing "
                     f"is not what a reader gets")

    # 2. The landing.
    for name in landing_pages():
        url = base + name
        status, body = fetch(url)
        if status != 200:
            wrong.append(f"{url} answered {status}")
            continue
        text = body.decode("utf-8", "replace")
        title = TITLE.search(text)
        if not title or "Ouroboros" not in title.group(1):
            wrong.append(f"{url} has no Ouroboros title — is this our page?")
        if "<main" not in text:
            wrong.append(f"{url} has no main content")
        for src in IMG.findall(text):
            if src.startswith("data:"):
                continue
            seen_images += 1
            image_url = src if src.startswith("http") else base + src
            image_status, image_body = fetch(image_url)
            if image_status != 200:
                wrong.append(f"{image_url} answered {image_status} — "
                             f"the page on {name} shows an empty box there")
            elif not image_body.lstrip()[:200].lower().startswith((b"<svg", b"<?xml")):
                wrong.append(f"{image_url} is not an SVG document "
                             f"({len(image_body)} bytes, starts {image_body[:40]!r})")

    # 3. The documentation, at the address it is published under now.
    for page in docs_pages():
        url = f"{base}{DOCS_HOME}/{page}"
        status, body = fetch(url)
        if status != 200:
            wrong.append(f"{url} answered {status} — the documentation is in the "
                         f"tree and not on the site")
            continue
        text = body.decode("utf-8", "replace")
        if "<main" not in text:
            wrong.append(f"{url} has no main content")
        if page == "index.html" and not STATE_MARK.search(text):
            wrong.append(f"{url} carries no <!--state:…--> numbers — either this is "
                         f"not the documentation index, or the numbers "
                         f"scripts/check_pages_live.py compares are gone")

    # 4. The addresses the documentation had before the landing took the root.
    for was, now in old_addresses():
        url = base + was
        status, body = fetch(url)
        if status != 200:
            wrong.append(f"{url} answered {status} — that address answered before "
                         f"the landing moved in, and it is in somebody's history")
        elif now not in body.decode("utf-8", "replace"):
            wrong.append(f"{url} answers, but says nothing about {now} — a page "
                         f"that neither is the documentation nor points at it")

    # 5. Every diagram, by its own address. Not only the ones a page names: a
    #    diagram that no page shows today is shown by the next page written, and
    #    a missing file is easier to see now than then.
    for name in diagram_files():
        url = f"{base}diagrams/{name}"
        status, body = fetch(url)
        if status != 200:
            wrong.append(f"{url} answered {status} — a diagram is missing from the "
                         f"site, and a page that shows it has a hole in it")
        elif not body.lstrip()[:200].lower().startswith((b"<svg", b"<?xml")):
            wrong.append(f"{url} is not an SVG document "
                         f"({len(body)} bytes, starts {body[:40]!r})")

    status, _ = fetch(base + "style.css")
    if status != 200:
        wrong.append(f"{base}style.css answered {status} — the site is unstyled")

    if seen_images == 0:
        wrong.append("no diagrams were found on any landing page — the site has "
                     "lost its pictures")
    elif not wrong:
        print(f"  {len(landing_pages())} landing pages, {len(docs_pages())} "
              f"documentation pages, {len(old_addresses())} kept addresses, "
              f"{len(diagram_files())} diagrams, {seen_images} images on pages")
    return wrong


# --------------------------------------------------------------------------- #
# the check, checked
# --------------------------------------------------------------------------- #

def skeleton(root: Path) -> None:
    """A site this check has to be happy with: every address it asks for, with
    nothing in it but what it looks at."""

    def page(rel: str, body: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"<!doctype html><title>Page · Ouroboros</title>"
                        f"<main>{body}</main>", encoding="utf-8")

    page("index.html", f"{LANDING_MARK}<img src=\"diagrams/{diagram_files()[0]}\">")
    for name in landing_pages():
        if name != "index.html":
            page(name, "a landing page")
    for rel in docs_pages():
        page(f"{DOCS_HOME}/{rel}",
             "<!--state:tests-->999<!--/state-->" if rel == "index.html" else "a page")
    for was, now in old_addresses():
        page(was, f'moved to <a href="{now}">{now}</a>')
    (root / "diagrams").mkdir(exist_ok=True)
    for name in diagram_files():
        (root / "diagrams" / name).write_text("<svg xmlns='http://www.w3.org/2000/svg'/>",
                                              encoding="utf-8")
    (root / "style.css").write_text("body{}", encoding="utf-8")


def serve(root: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = partial(QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass


def self_test() -> int:
    """Break the site on purpose and require this check to say so.

    A check nobody has ever seen go red is a check nobody has any reason to
    believe. Each case below is a way the site has gone wrong or can: the root
    serving the documentation instead of the landing is not hypothetical — it is
    what the published site did for as long as two builds shared one root.
    """

    problems = premises()
    if problems:
        print("The check cannot be trusted before it is run:\n", file=sys.stderr)
        for problem in problems:
            print(f"  * {problem}", file=sys.stderr)
        return 1

    work = Path(tempfile.mkdtemp(prefix="ouroboros-check-live-"))
    try:
        root = work / "site"
        root.mkdir()
        skeleton(root)
        server, base = serve(root)
        try:
            wrong = check(base)
            if wrong:
                print("A site with everything in place was called wrong:\n", file=sys.stderr)
                for problem in wrong:
                    print(f"  * {problem}", file=sys.stderr)
                return 1
            print("a site with everything in place: passes")

            sabotage: list[tuple[str, object]] = [
                ("the root serves the documentation index, not the landing",
                 lambda: (root / "index.html").write_text(
                     "<!doctype html><title>Ouroboros</title><main>Four steps</main>",
                     encoding="utf-8")),
                ("a diagram did not publish",
                 lambda: (root / "diagrams" / diagram_files()[0]).unlink()),
                ("a diagram published as a 404 page instead of an SVG",
                 lambda: (root / "diagrams" / diagram_files()[-1]).write_text(
                     "<!doctype html><title>404</title>", encoding="utf-8")),
                ("a documentation page did not publish",
                 lambda: (root / DOCS_HOME / docs_pages()[0]).unlink()),
                ("the documentation index lost its state numbers",
                 lambda: (root / DOCS_HOME / "index.html").write_text(
                     "<!doctype html><title>x · Ouroboros</title><main>x</main>",
                     encoding="utf-8")),
                ("an address the documentation used to answer on is gone",
                 lambda: (root / old_addresses()[0][0]).unlink()),
                ("an old address answers, but with something else entirely",
                 lambda: (root / old_addresses()[-1][0]).write_text(
                     "<!doctype html><title>x · Ouroboros</title><main>hello</main>",
                     encoding="utf-8")),
                ("the stylesheet did not publish",
                 lambda: (root / "style.css").unlink()),
            ]
            failures = 0
            for what, break_it in sabotage:
                shutil.rmtree(root)
                root.mkdir()
                skeleton(root)
                break_it()  # type: ignore[operator]
                wrong = check(base)
                if wrong:
                    print(f"{what}: caught — {wrong[0]}")
                else:
                    print(f"{what}: NOT CAUGHT — the check passed a broken site",
                          file=sys.stderr)
                    failures += 1
            if failures:
                print(f"\n{failures} of {len(sabotage)} breakages went unnoticed.",
                      file=sys.stderr)
                return 1
            print(f"\nAll {len(sabotage)} breakages were caught, and an intact site "
                  f"passed. The check can go red.")
            return 0
        finally:
            server.shutdown()
            server.server_close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--retries", type=int, default=0,
                        help="how many times to ask again while a deploy finishes")
    parser.add_argument("--wait", type=float, default=30.0, help="seconds between tries")
    parser.add_argument("--self-test", action="store_true",
                        help="break a site on purpose and require this check to notice")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    base = args.base if args.base.endswith("/") else args.base + "/"

    for attempt in range(args.retries + 1):
        try:
            wrong = check(base)
        except OSError as e:
            print(f"could not reach {base}: {e}", file=sys.stderr)
            return 2
        if not wrong:
            print(f"{base} is up to date.")
            return 0
        if attempt < args.retries:
            print(f"not there yet ({len(wrong)} problems), asking again in {args.wait:g}s")
            time.sleep(args.wait)

    print("The published site does not match what it should be:\n", file=sys.stderr)
    for problem in wrong:
        print(f"  * {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
