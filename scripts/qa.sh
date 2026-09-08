#!/usr/bin/env bash
# Ouroboros code-quality gate.
#
# STANDING RULE: any edit to the MCP / engine code under ouroboros/, however
# minor, must pass all three gates below before it is considered done. The
# Elixir lesson — "gone-green != warning-free" — applies: do NOT suppress a
# finding (no blanket `noqa` / `type: ignore`); fix the real defect. There are
# now no per-module relaxations at all: the last one, the `clang.*` mypy
# override, went away when libclang moved out of the process — the backends no
# longer pass untyped cursors around, they read JSON.
#
# Usage: scripts/qa.sh          (run from the repo root)
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== ruff (lint, ouroboros + tests) =="
uv run ruff check ouroboros tests

echo "== mypy (strict, ouroboros) =="
uv run mypy ouroboros

echo "== pytest =="
uv run pytest

# The documentation cites the source down to the line. Such numbers go stale
# silently from any edit higher up the file, so a machine checks them rather than
# somebody's attention: see scripts/check_doc_links.py.
echo "== documentation links into source lines =="
uv run python scripts/check_doc_links.py

# Links between the pages themselves break just as quietly: a section is renamed
# and the link to it stays behind, looking correct. Jekyll does not complain.
echo "== links between documentation pages =="
uv run python scripts/check_doc_anchors.py

# The state numbers (how many tests, what coverage, how many tools) are written
# into the pages by a machine and checked by a machine. Both times they parted
# ways with reality a human had written them in and nobody recounted: see
# scripts/state_numbers.py.
echo "== state numbers in README and ARCHITECTURE =="
uv run python scripts/state_numbers.py

# A word with some of its letters typed in the wrong alphabet reads like the
# ordinary word and is not findable by eye: `а`, `е`, `о`, `с`, `р`, `х` look the
# same in the two alphabets.
echo "== words with mixed alphabets =="
uv run python scripts/check_no_mixed_script.py

# A page's language is read off its name: no suffix means English, `.ru.md` means
# Russian. That drifts silently, one paragraph at a time, so a machine checks it.
# The self-test comes first: a guard that has never gone red means nothing — the
# self-test feeds it knowingly broken pages and demands a refusal.
echo "== documentation language guard: self-test =="
uv run python scripts/check_doc_language.py --self-test

echo "== English by default, Russian as a .ru pair =="
uv run python scripts/check_doc_language.py

# The Russian description of the tool is written in flang and checked by the
# compiler: types, proven termination of every function, and examples inside the
# functions themselves. The compiler installs in one line:
# npm i -g @digitable-lol/flang
echo "== Russian description in flang =="
if command -v flang >/dev/null 2>&1; then
    flang check docs/ouroboros.flang
    flang test docs/ouroboros.flang
else
    echo "   WARNING: flang not found, the description was NOT checked."
    echo "   This is not 'all is well': the same thing is checked by"
    echo "   .github/workflows/docs-language.yml, where the compiler is always installed."
fi

# The brain that reads a trace is written in flang: what counts as a usable line,
# what pairs with what, what makes a finished call, who is still in flight and how
# a long value is cut — all of that is decided there, while the Python around it
# only reads files and lays out JSON. The compiler judges the types, proves that
# every function terminates and runs the examples written inside the functions.
echo "== the trace-reading brain in flang =="
if command -v flang >/dev/null 2>&1; then
    flang check ouroboros/brain/trace_brain.flang
    flang test ouroboros/brain/trace_brain.flang
else
    echo "   WARNING: flang not found, the brain was NOT checked."
    echo "   The compiler installs in one line: npm i -g @digitable-lol/flang"
fi

# The emitted code lives in the tree so that installing takes one pip. There is a
# single price for that: the emission can quietly fall behind its source. So a
# machine compares them. Code 2 means there is nothing to compare against (no
# compiler, or a different version); that is not a gate failure, but it is not
# 'all is well' either: the comparison only happens where the very compiler that
# made the emission is installed.
echo "== the emitted brain matches a fresh emission =="
brain_status=0
uv run python scripts/emit_brain.py --check || brain_status=$?
if [ "$brain_status" -eq 1 ]; then
    exit 1
elif [ "$brain_status" -eq 2 ]; then
    echo "   WARNING: the emission was not compared."
fi

# Command names in the Homebrew formula and in the asdf plugin look plausible and
# cannot be verified by eye: the formula once carried the line
# assert_path_exists bin/"ouroboros-mcp-router" — a name that never existed.
# They are checked against [project.scripts] and against the argument parser.
echo "== names in the packaging =="
uv run python scripts/check_packaging_names.py

# A check that catches nothing looks exactly like a check that found nothing wrong.
# The release rules look at what is published, so they cannot be tried on real
# breakage — breakage is therefore fed to them on purpose, and every rule must go red.
echo "== the release rules do work =="
uv run python scripts/check_release_published.py --self-test

# A person installs not from the tree but from the tap and from the tags. In the
# neighbouring flang the tap fell six releases behind, and all that time
# `brew install` silently handed out 0.7.3 — no check inside a tree sees that.
# Here the published thing is asked. Code 2 means unreachable; that is not a gate
# failure, but it is not 'all is well' either: the same thing is checked daily by
# .github/workflows/packaging-live.yml, where the network is always there.
echo "== the published release matches the tree =="
release_status=0
uv run python scripts/check_release_published.py || release_status=$?
if [ "$release_status" -eq 1 ]; then
    exit 1
elif [ "$release_status" -eq 2 ]; then
    echo "   WARNING: the published release was not polled, not checked from outside."
fi

# The record-field table in docs/languages.md is printed by a run of five
# languages. It once became a lie through an edit in the backends, not in the
# page: see scripts/schema_facts.py.
echo "== the record-field table =="
uv run python scripts/schema_facts.py

# Done is when it is visible from outside. The site build runs on GitHub's side
# and reports its failures to nobody: on 29 August it failed parsing
# docs/_config.yml and served the previous snapshot for a full day — five
# languages while there were six, release 0.3.0 while there was 0.4.0, a 404 on a
# page listed in the table of contents. Here the site itself is asked over HTTP.
# Code 2 means the site was unreachable (no network); that is not a gate failure,
# but it is not 'all is well' either: the same thing is checked daily by
# .github/workflows/pages-live.yml, where the network is always there.
echo "== the live site serves the current tree =="
live_status=0
uv run python scripts/check_pages_live.py || live_status=$?
if [ "$live_status" -eq 1 ]; then
    exit 1
elif [ "$live_status" -eq 2 ]; then
    echo "   WARNING: the site was not polled, not checked from outside."
fi

echo "== all gates passed =="
