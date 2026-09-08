#!/usr/bin/env bash
# Turns every site/diagrams/*.mmd into the two SVG files the site shows.
#
# Why the SVG is checked in rather than drawn in the reader's browser. A page
# that loads mermaid from a CDN and renders on the fly shows nothing at all when
# the CDN is blocked, when the reader has JavaScript off, or when the pinned
# version moves under us — and the failure is silent, an empty box where the
# diagram was. Rendering here instead means the published page carries plain
# SVG: it is visible with JavaScript off, needs no network beyond the page
# itself, and what is reviewed in the diff is what the reader sees.
#
# The mermaid source stays the source of truth: `site/build.py` hashes each
# .mmd and REFUSES to build when the SVG beside it was made from a different
# text. So an edited diagram that was never re-rendered cannot reach the site.
#
# Two files per diagram, light and dark, because the SVG carries its own colours
# and the page has both themes; the stylesheet shows one and hides the other.
#
# Usage:
#     site/render-diagrams.sh
#
# Needs: node (npx), and a Chrome/Chromium for mermaid-cli to draw in. Point
# CHROME at one if it is not in the usual place.
set -euo pipefail

cd "$(dirname "$0")"
DIAGRAMS="$PWD/diagrams"
VERSION="${MERMAID_CLI_VERSION:-11.17.0}"

CHROME="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
if [ -z "$CHROME" ]; then
	echo "no Chrome/Chromium found — mermaid-cli draws in one. Set CHROME=/path/to/chrome" >&2
	exit 1
fi

CONF="$(mktemp -d)"
trap 'rm -rf "$CONF"' EXIT
cat > "$CONF/puppeteer.json" <<JSON
{"executablePath": "$CHROME", "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
JSON

export PUPPETEER_SKIP_DOWNLOAD=true PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true

for src in "$DIAGRAMS"/*.mmd; do
	name="$(basename "$src" .mmd)"
	for theme in default dark; do
		case "$theme" in default) suffix=light ;; *) suffix=dark ;; esac
		echo "== $name.$suffix"
		npx --yes "@mermaid-js/mermaid-cli@$VERSION" \
			--input "$src" \
			--output "$DIAGRAMS/$name.$suffix.svg" \
			--puppeteerConfigFile "$CONF/puppeteer.json" \
			--theme "$theme" \
			--backgroundColor transparent \
			--quiet
	done
done

echo
echo "== recording which text each SVG was drawn from"
python3 - "$DIAGRAMS" <<'PY'
import hashlib, json, sys
from pathlib import Path

diagrams = Path(sys.argv[1])
lock = {p.stem: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(diagrams.glob("*.mmd"))}
(diagrams / "rendered.json").write_text(
    json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
for name, digest in lock.items():
    print(f"  {name}: {digest[:12]}")
PY
