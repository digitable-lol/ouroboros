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

# Документация ссылается на исходник с точностью до строки. Такие номера тихо
# протухают от любой правки выше по файлу, поэтому их проверяет машина, а не
# внимательность: см. scripts/check_doc_links.py.
echo "== ссылки документации на строки исходника =="
uv run python scripts/check_doc_links.py

# Ссылки между самими страницами ломаются так же тихо: раздел переименовали, а
# ссылка на него осталась и выглядит исправной. Jekyll на это не ругается.
echo "== ссылки между страницами документации =="
uv run python scripts/check_doc_anchors.py

# Числа состояния (сколько проверок, какое покрытие, сколько средств) вписаны в
# страницы машиной и сверяются машиной. Оба раза, когда они разошлись с делом,
# их вписал человек и никто не пересчитал: см. scripts/state_numbers.py.
echo "== числа состояния в README и ARCHITECTURE =="
uv run python scripts/state_numbers.py

# Слово, где часть букв набрана не тем алфавитом, читается как обычное и глазами
# не находится: `а`, `е`, `о`, `с`, `р`, `х` в двух алфавитах выглядят одинаково.
echo "== слова со смешанными алфавитами =="
uv run python scripts/check_no_mixed_script.py

# Таблица полей записи в docs/languages.md печатается прогоном пяти языков.
# Она однажды стала ложью от правки в обработчиках, а не в странице: см.
# scripts/schema_facts.py.
echo "== таблица полей записи =="
uv run python scripts/schema_facts.py

# Сделано — это когда видно снаружи. Сборка страниц идёт на стороне GitHub и об
# отказе не сообщает никому: 29 августа она упала на разборе docs/_config.yml и
# сутки отдавала предыдущий слепок — пять языков при шести, выпуск 0.3.0 при
# 0.4.0, 404 на странице из оглавления. Здесь спрашивается сам сайт по HTTP.
# Код 2 — до сайта не достучались (нет сети); это не отказ гейта, но и не
# «всё хорошо»: то же самое ежедневно проверяет .github/workflows/pages-live.yml,
# где сеть есть всегда.
echo "== живой сайт отдаёт нынешнее дерево =="
live_status=0
uv run python scripts/check_pages_live.py || live_status=$?
if [ "$live_status" -eq 1 ]; then
    exit 1
elif [ "$live_status" -eq 2 ]; then
    echo "   ВНИМАНИЕ: сайт не опрошен, снаружи не проверено."
fi

echo "== all gates passed =="
