#!/usr/bin/env python3
"""Ask the published site whether it is actually there, and whether the
diagrams on it are actually visible.

    python3 site/check-live.py
    python3 site/check-live.py --base http://127.0.0.1:8000 --retries 20 --wait 30

Why this exists at all. GitHub builds and serves the site itself, and when that
goes wrong nobody is told: the previous successful snapshot keeps being served.
This project has already lost a day to exactly that. So the check is not "did
the build pass" — it is "does the thing on the internet have the pages and the
pictures in it".

The diagrams are the part worth checking twice. They are the one piece of this
site that is a separate file the page has to fetch: if a diagram fails to
publish, the page still renders, still looks finished, and simply has a hole
where the picture was. So every `<img>` a page names is fetched, and its bytes
have to start an SVG document. A 404 page is not an SVG, and neither is an empty
file.

Exit codes, following `scripts/check_pages_live.py` in this tree:

    0  the site is there and matches
    1  the site answered, and what it answered is wrong
    2  the site could not be reached at all (no network) — not a verdict
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BASE = "https://digitable-lol.github.io/ouroboros/"

IMG = re.compile(r'<img[^>]+src="([^"]+)"')
TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def fetch(url: str, timeout: float = 30.0) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "ouroboros-site-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def pages() -> list[str]:
    """The pages the generator makes, taken from the sources, not typed here."""

    return sorted(p.stem + ".html" for p in (HERE / "pages").glob("*.md"))


def check(base: str) -> list[str]:
    wrong: list[str] = []
    seen_images = 0

    for name in pages():
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

    status, _ = fetch(base + "style.css")
    if status != 200:
        wrong.append(f"{base}style.css answered {status} — the site is unstyled")

    if seen_images == 0:
        wrong.append("no diagrams were found on any page — the site has lost its pictures")
    elif not wrong:
        print(f"  {len(pages())} pages, {seen_images} diagram images, all fetched")
    return wrong


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--retries", type=int, default=0,
                        help="how many times to ask again while a deploy finishes")
    parser.add_argument("--wait", type=float, default=30.0, help="seconds between tries")
    args = parser.parse_args(argv)
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
