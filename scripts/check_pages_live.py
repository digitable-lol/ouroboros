"""Checks that the live site serves the current tree, not yesterday's.

Why. The site build runs on GitHub's side and reports its failures to nobody. On
29 August `docs/_config.yml` stopped parsing as YAML — an unquoted value with a
colon in it — and the build failed for a full day. All that time the site kept
serving the previous successful snapshot: it promised five languages while the
tree had six, release 0.3.0 while the tree had 0.4.0, and answered 404 for
`measurements.html`, a page listed in its own table of contents. It was spotted
by accident, in passing.

Hence the rule: done is when it is visible from outside. Not "the build is
green", not "the commit is on the branch". This check asks the site itself over
HTTP and compares the answer with the tree.

What is checked:

* every page from `docs/` is served by the live site with code 200 — that is
  exactly what catches `measurements.html`, which was in the tree and did not
  exist on the site;
* `state.json`, which the build copies to the site as is, matches
  `docs/state.json` in the tree. A precise sign of freshness: if they differ, the
  site was built from a different commit;
* the numbers inside the `<!--state:...-->` marks on the live documentation page
  equal the numbers in `docs/state.json`. That is, the page a human reads shows
  today's numbers, not merely the file sitting next to it. The check makes no
  assumption about where that page lives: while Jekyll built the site out of
  `docs/`, it was the root `index.html`; now the landing page holds the root and
  the documentation lives under `docs/`. Both addresses are asked, and whichever
  carries the marks is the one. A page with no marks anywhere is a failure — else
  the check would quietly degrade into "nothing was compared, so everything
  agrees".

What the check does NOT do: it does not judge by the state of the build. A build
can be green and the page still be old; only the site itself is asked here.

Exit codes: 0 — the site matches the tree; 1 — they differ; 2 — the site could
not be reached (the check did not happen, which is not the same as "all is well").

Run::

    uv run python scripts/check_pages_live.py                      # ask right now
    uv run python scripts/check_pages_live.py --retries 10 --wait 30

The `--retries` and `--wait` options are for the moment right after a push: the
build on GitHub's side takes from half a minute to several minutes, and until it
finishes the site is right to serve the old thing. Waiting only makes sense
there; an ordinary "what is on the site now" wants its answer at once, so there
are no retries by default.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The build configuration — the site address is taken from it, so that it is not
#: written down here a second time and left to drift from the real one.
CONFIG = ROOT / "docs" / "_config.yml"

STATE_FILE = ROOT / "docs" / "state.json"

#: The subdirectory `site/build.py` puts the documentation into on the site.
DOCS_HOME = "docs"

#: A top-level `key: value` in the build configuration.
CONFIG_FIELD = re.compile(r"^([a-z_]+):\s*(.*?)\s*$")

#: A state-number mark — the same one as in scripts/state_numbers.py.
MARK = re.compile(r"<!--state:([a-z_]+)-->(.*?)<!--/state-->", re.DOTALL)

TIMEOUT = 20


def site_root() -> str:
    """The site address, assembled from `url` and `baseurl` of the build config."""

    fields: dict[str, str] = {}
    for line in CONFIG.read_text(encoding="utf-8").splitlines():
        m = CONFIG_FIELD.match(line)
        if m:
            fields[m.group(1)] = m.group(2).strip("\"'")
    url = fields.get("url", "").rstrip("/")
    baseurl = fields.get("baseurl", "").strip("/")
    if not url:
        raise SystemExit(f"{CONFIG}: no url field — nowhere to take the site "
                         f"address from")
    return f"{url}/{baseurl}" if baseurl else url


def fetch(url: str) -> tuple[int, str]:
    """Fetches a page. Returns the response code and the body."""

    request = urllib.request.Request(url, headers={"User-Agent": "ouroboros-pages-check"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def live_pages() -> list[str]:
    """The addresses at which the pages from `docs/` must be served by the site."""

    out = []
    for path in sorted(ROOT.glob("docs/**/*.md")):
        rel = path.relative_to(ROOT / "docs")
        out.append(str(rel.with_suffix(".html")))
    return out


def documentation_index(root: str) -> tuple[str | None, str]:
    """The live documentation page, at whichever address it lives now.

    While Jekyll built the site out of `docs/`, the documentation page was the
    root `index.html`. Since Pages moved to GitHub Actions the landing page holds
    the root and the documentation lives under `docs/`; on the day of the move
    either one is true in turn. The address that carries the state marks is the
    right one: a page without them is not that page, and it must not be counted
    in silence.
    """

    for candidate in (f"{DOCS_HOME}/index.html", "index.html"):
        code, body = fetch(f"{root}/{candidate}")
        if code == 200 and MARK.search(body):
            return candidate, body
    return None, ""


def pages_served(root: str) -> tuple[list[str], int]:
    """1. Every page in the tree is served by the live site."""

    bad = []
    checked = 0
    for page in live_pages():
        code, _ = fetch(f"{root}/{page}")
        checked += 1
        if code != 200:
            bad.append(f"  - {page}: the site answers {code}, yet the page is in "
                       f"the tree")
    return bad, checked


def state_matches(root: str, tree_state: dict[str, object]) -> tuple[list[str], int]:
    """2. `state.json` on the site matches the tree — a precise sign of freshness."""

    bad: list[str] = []
    code, body = fetch(f"{root}/state.json")
    if code != 200:
        return [f"  - state.json: the site answers {code}"], 1
    try:
        live_state = json.loads(body)
    except json.JSONDecodeError as e:
        return [f"  - state.json: the site served something that is not JSON ({e})"], 1
    if live_state != tree_state:
        for key in sorted(set(tree_state) | set(live_state)):
            mine, theirs = tree_state.get(key), live_state.get(key)
            if mine != theirs:
                bad.append(
                    f"  - state.json, field {key!r}: on the site {theirs!r}, "
                    f"in the tree {mine!r} — the site was built from a "
                    f"different tree"
                )
    return bad, 1


def marks_match(root: str, tree_state: dict[str, object]) -> tuple[list[str], int]:
    """3. The numbers in the marks on the live page equal those in the tree."""

    where, body = documentation_index(root)
    if where is None:
        return ([f"  - no documentation page with state marks at either "
                 f"{DOCS_HOME}/index.html or index.html — there is nothing to "
                 f"compare the numbers against"], 2)
    bad = []
    for key, value in MARK.findall(body):
        expected = tree_state.get(key)
        if expected is not None and str(expected) != value.strip():
            bad.append(
                f"  - {where}, mark {key!r}: the site shows {value.strip()!r}, "
                f"the tree says {str(expected)!r}"
            )
    return bad, 2


def check() -> int:
    root = site_root()
    tree_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    bad: list[str] = []
    checked = 0

    # Every request is inside this one `try`, not just the first. A network that
    # drops halfway through used to leave an uncaught URLError, i.e. a traceback
    # and exit 1 — the gate reporting "the site parted ways with the tree" when
    # what happened was that nobody could ask it. Unreachable is code 2, and it
    # has to stay code 2 whichever request hits the wall.
    try:
        for found, n in (pages_served(root),
                         state_matches(root, tree_state),
                         marks_match(root, tree_state)):
            bad += found
            checked += n
    except (urllib.error.URLError, OSError) as e:
        print(
            f"The site could not be reached ({root}): {e}.\n"
            "The check did NOT happen. That is not the same as "
            "'the site matches the tree'.",
            file=sys.stderr,
        )
        return 2

    if bad:
        print(f"The live site parted ways with the tree ({root}):\n", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        print(
            f"\nRequests made: {checked}. Mismatches: {len(bad)}.\n"
            "Look at the deploy runs: gh run list -R digitable-lol/ouroboros",
            file=sys.stderr,
        )
        return 1

    print(f"The live site serves the current tree: {checked} requests, no mismatches.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--retries", type=int, default=0,
                        help="how many times to ask again while the site catches up")
    parser.add_argument("--wait", type=int, default=30,
                        help="how many seconds to wait between attempts")
    args = parser.parse_args()

    # The site is asked once, and again only while there are retries left. Written
    # as "ask, then retry" rather than "loop retries+1 times" because the second
    # shape returned an unassigned variable when --retries was negative.
    attempts = max(0, args.retries) + 1
    status = check()
    for attempt in range(2, attempts + 1):
        if status == 0:
            break
        print(f"\nWaiting {args.wait} s and asking again "
              f"(attempt {attempt} of {attempts}).\n")
        time.sleep(args.wait)
        status = check()
    return status


if __name__ == "__main__":  # pragma: no cover — the entry point, not a rule
    raise SystemExit(main())
