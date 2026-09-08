# site/ — the whole published site

Three landing pages for someone who has not met the tool: what it is and what it
costs (`index`), what it looks like in use (`example`), and what it cannot do
(`limits`). English, because it is the front door.

And the documentation — all thirty pages of `docs/`, English and Russian — which
is built by the same command into `docs/` of the site. It used to be built by
Jekyll, from the same `docs/`, into the same site root that the landing wanted.
A site root holds one site, so one of the two was always going to be missing;
building both here is how they both fit.

Build it:

```sh
python3 site/build.py            # into site/out
python3 site/build.py --check    # check only, write nothing
```

No dependencies — the standard library and nothing else, so it builds with no
network. Open `site/out/index.html`, or serve the directory if you want the
links to behave exactly as they will when published:

```sh
python3 -m http.server -d site/out 8000
```

## Nothing on the page is typed twice

The prose is in `pages/*.md`. Every *fact* on a page is pulled in at build time
instead of written down, which is why the build can refuse:

| in the page | comes from |
|---|---|
| `{{capture: shop-run.txt}}` | `examples/captured/`, taken by a real run |
| `{{source: site/examples/shop.py}}` | the file itself, in the tree |
| `{{diagram: pipeline}}` | `diagrams/*.svg`, drawn from `diagrams/*.mmd` |
| `{{table: speed}}` | `examples/captured/measurements.json` |
| `{{fact: shop.trace_calls}}` | `examples/captured/facts.json` |
| `{{state: mcp_tools}}` | `docs/state.json` |
| version, owner, licence, year | `pyproject.toml` and `LICENSE` |

**The build fails, rather than warning**, when a link points at a page or a
heading that does not exist, when a named capture was never taken, when a
diagram's `.mmd` was edited after its SVG was drawn, or when a page quotes a
number by a name the measured files do not carry.

## Re-taking what the pages quote

```sh
PATH=.venv/bin:$PATH python3 site/examples/capture.py            # the runs (needs pytest)
PATH=.venv/bin:$PATH python3 site/examples/capture.py --measure  # the speed tables, ~5 min
site/render-diagrams.sh                                          # the diagram SVGs
```

`capture.py` wraps and runs the shop program, then runs `add(2, 3)` on all eight
languages through the project's own cross-language test helpers. `--measure`
calls `scripts/measure/run.sh` and renames its fields; it measures nothing
itself.

## Why the diagrams are SVG in the tree

They are written in mermaid (`diagrams/*.mmd`) and rendered to SVG *here*, twice
— once light, once dark — rather than drawn in the reader's browser from a CDN.
A page that renders mermaid at load time shows nothing at all when the CDN is
blocked or scripts are off, and it shows nothing *silently*. Rendering ahead of
time means the published page is plain SVG: visible with JavaScript off, and
reviewable in the diff.

`render-diagrams.sh` needs `npx` and a Chrome or Chromium; it writes
`diagrams/rendered.json`, which is what `build.py` compares against to catch a
diagram that was edited and never re-rendered.

## What the site is made of

| on the site | from | how many |
|---|---|---|
| `index.html`, `example.html`, `limits.html` | `site/pages/*.md` | 3 |
| `docs/…` | `docs/**/*.md` | 30 |
| `docs/state.json` and the other data files | `docs/*.json`, `docs/*.flang` | 4, at the root as well |
| `why.html`, `install.html`, `examples/index.html`, … | redirects into `docs/` | 28 |
| `diagrams/*.svg`, `style.css` | the tree | 7 |

The documentation is written in the Markdown that kramdown used to render, so
that is the dialect `build.py` implements for it: headings, paragraphs, lists
whose items may hold a code block, tables, block quotes, hand-written
`<details>` sections, and HTML comments — `<!--state:version-->` is not
decoration, `scripts/check_pages_live.py` reads those numbers off the live page.
A link to `install.md` is published as a link to `install.html`, which is what
the jekyll-relative-links plugin did and why every address on the site ends in
`.html`. Headings get their anchors from `anchor_for` in
`scripts/check_doc_anchors.py` — the same function that checks every link
between the pages, imported rather than copied, because a page built by one rule
and checked by another is a page whose links nobody checks.

**No address is lost.** The documentation answered on 30 addresses while Jekyll
served it. Two of them, `index.html` and `limits.html`, are names the landing
uses too; the root is what the landing is for, so those two now show the landing
(with the documentation one click away, under `Documentation` in the header).
The other 28 answer with a redirect to `docs/…`. Thirty addresses before,
thirty answering after.

## Publishing

`.github/workflows/pages.yml` builds the site and deploys it, then asks the
published site over HTTP whether it is really there — the root, every
documentation page, every kept address and every diagram file (`check-live.py`).

**One thing has to be done by hand, and only the owner can do it:**

> Settings → Pages → Build and deployment → Source: **GitHub Actions**

Until that is done, two builds publish into one root: the built-in Jekyll one
from `docs/`, and this workflow's from `site/out`. Both report success and the
root goes to whichever finished later, which is Jekyll's — so the landing is on
the site nowhere at all.

**What changes the minute it is switched:**

* the root serves the landing, not `docs/index.md`;
* the documentation is at `docs/…` — `docs/why.html` and so on;
* the 28 old addresses redirect there; `index.html` and `limits.html` become the
  landing's own pages of those names;
* Jekyll stops building anything. `docs/_config.yml` stops mattering — the file
  whose YAML stopped parsing on 29 August and took the whole site down for a day
  is no longer in the path of the site.

**How to see that nothing was lost**, from a checkout, after the switch:

```sh
python3 site/check-live.py          # every address, page and diagram, over HTTP
uv run python scripts/check_pages_live.py   # and the numbers on them match the tree
```

The first is the one that would have caught this: it asks the root for a
sentence that is on the landing and on no documentation page, so a root serving
the documentation cannot pass. Before the switch it goes red and says so; the
minute the source is switched it goes green, with nothing changed in the tree.

`python3 site/check-live.py --self-test` breaks a site eight ways in a temporary
directory — the root serving the documentation, a diagram that did not publish,
a diagram published as a 404 page, a missing documentation page, a documentation
index without its state numbers, an old address gone, an old address answering
with something else, a missing stylesheet — and requires the check to notice
every one. A watchman nobody has seen go red is not a watchman.
