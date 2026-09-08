# site/ — the landing page

Three pages for someone who has not met the tool: what it is and what it costs
(`index`), what it looks like in use (`example`), and what it cannot do
(`limits`). English, because it is the front door.

Build it:

```sh
python3 site/build.py            # into site/out
python3 site/build.py --check    # check only, write nothing
```

No dependencies — the standard library and nothing else, so it builds with no
network. Open `site/out/index.html`, or serve the directory if you want the
links to behave exactly as they will when published.

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

## Publishing

`.github/workflows/pages.yml` builds and deploys `site/out`, then asks the
published site over HTTP whether its pages and every diagram image are really
there (`check-live.py`).

**It does not work until Pages is switched to "GitHub Actions" by hand**, and
that switch takes the site root away from the Jekyll site currently built from
`docs/`. The head of the workflow file explains the trade-off.
