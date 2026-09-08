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

# Язык страницы читается по её имени: без суффикса — английский, `.ru.md` —
# русский. Разъезжается это молча и по одному абзацу за раз, поэтому сверяет
# машина. Сначала самопроверка: сторож, который никогда не краснел, ничего не
# значит — она подсовывает ему заведомо испорченные страницы и требует отказа.
echo "== сторож языка документации: самопроверка =="
uv run python scripts/check_doc_language.py --self-test

echo "== английский по умолчанию, русский парой .ru =="
uv run python scripts/check_doc_language.py

# Русское описание инструмента написано на flang и проверяется компилятором:
# типы, доказанное завершение всех функций и примеры внутри самих функций.
# Компилятор ставится одной строкой: npm i -g @digitable-lol/flang
echo "== русское описание на flang =="
if command -v flang >/dev/null 2>&1; then
    flang check docs/ouroboros.flang
    flang test docs/ouroboros.flang
else
    echo "   ВНИМАНИЕ: flang не найден, описание НЕ проверено."
    echo "   Это не «всё хорошо»: то же самое проверяет"
    echo "   .github/workflows/docs-language.yml, где компилятор ставится всегда."
fi

# Мозг чтения трассы написан на flang: что считать годной строкой, что с чем
# парно, из чего складывается завершённый вызов, кто ещё в полёте и как режется
# длинное значение — решается там, а Python вокруг только читает файлы и
# раскладывает JSON. Компилятор судит типы, доказывает завершение каждой функции
# и гоняет примеры, записанные внутри самих функций.
echo "== мозг чтения трассы на flang =="
if command -v flang >/dev/null 2>&1; then
    flang check ouroboros/brain/trace_brain.flang
    flang test ouroboros/brain/trace_brain.flang
else
    echo "   ВНИМАНИЕ: flang не найден, мозг НЕ проверен."
    echo "   Компилятор ставится одной строкой: npm i -g @digitable-lol/flang"
fi

# Напечатанное лежит в дереве, чтобы установка обходилась одним pip. Плата за
# это одна: печать может тихо отстать от исходника. Поэтому её сверяет машина.
# Код 2 — не с чем сверять (компилятора нет или он другой версии); это не отказ
# гейта, но и не «всё хорошо»: сверка идёт только там, где стоит тот же
# компилятор, которым печать сделана.
echo "== напечатанный мозг совпадает со свежей печатью =="
brain_status=0
uv run python scripts/emit_brain.py --check || brain_status=$?
if [ "$brain_status" -eq 1 ]; then
    exit 1
elif [ "$brain_status" -eq 2 ]; then
    echo "   ВНИМАНИЕ: печать не сверена."
fi

# Имена команд в рецепте Homebrew и в плагине asdf выглядят правдоподобно и
# глазами не проверяются: в рецепте однажды стояла строка
# assert_path_exists bin/"ouroboros-mcp-router" — имени, которого не было никогда.
# Сверяется с [project.scripts] и с разборщиком командной строки.
echo "== имена в упаковке =="
uv run python scripts/check_packaging_names.py

# Проверка, которая ничего не ловит, выглядит ровно как проверка, которая всё прошла.
# Правила сверки выпуска смотрят на выложенное, то есть проверить их на настоящей
# порче нельзя — поэтому порчу им подсовывают нарочно, и каждое обязано покраснеть.
echo "== правила сверки выпуска работают =="
uv run python scripts/check_release_published.py --self-test

# Человек ставит не из дерева, а из хранилища формул и из тегов. У соседнего flang
# кран отстал на шесть выпусков, и `brew install` всё это время молча раздавал 0.7.3 —
# ни одна проверка в дереве такого не видит. Здесь спрашивается выложенное.
# Код 2 — не достучались; это не отказ гейта, но и не «всё хорошо»: то же самое
# ежедневно проверяет .github/workflows/packaging-live.yml, где сеть есть всегда.
echo "== выложенное совпадает с выпуском =="
release_status=0
uv run python scripts/check_release_published.py || release_status=$?
if [ "$release_status" -eq 1 ]; then
    exit 1
elif [ "$release_status" -eq 2 ]; then
    echo "   ВНИМАНИЕ: выложенное не опрошено, снаружи не проверено."
fi

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
