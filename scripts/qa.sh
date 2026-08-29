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

# Документация ссылается на исходник с точностью до строки. Такие номера тихо
# протухают от любой правки выше по файлу, поэтому их проверяет машина, а не
# внимательность: см. scripts/check_doc_links.py.
echo "== ссылки документации на строки исходника =="
uv run python scripts/check_doc_links.py

# Числа состояния (сколько проверок, какое покрытие, сколько средств) вписаны в
# страницы машиной и сверяются машиной. Оба раза, когда они разошлись с делом,
# их вписал человек и никто не пересчитал: см. scripts/state_numbers.py.
echo "== числа состояния в README и ARCHITECTURE =="
uv run python scripts/state_numbers.py

# Слово, где часть букв набрана не тем алфавитом, читается как обычное и глазами
# не находится: `а`, `е`, `о`, `с`, `р`, `х` в двух алфавитах выглядят одинаково.
echo "== слова со смешанными алфавитами =="
uv run python scripts/check_no_mixed_script.py

echo "== all gates passed =="
