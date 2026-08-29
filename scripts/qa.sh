#!/usr/bin/env bash
# Ouroboros code-quality gate.
#
# STANDING RULE: any edit to the MCP / engine code under ouroboros/, however
# minor, must pass all three gates below before it is considered done. The
# Elixir lesson — "gone-green != warning-free" — applies: do NOT suppress a
# finding (no blanket `noqa` / `type: ignore`); fix the real defect. The one
# legitimate relaxation is the `clang.*` mypy override (libclang ships no type
# stubs — a real third-party-import gap). Discriminating test for any other
# relaxation you are tempted by: would this error still fire if libclang had
# stubs? Yes -> it is your bug, fix it.
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

echo "== all gates passed =="
