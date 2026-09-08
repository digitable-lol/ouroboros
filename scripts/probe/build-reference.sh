#!/usr/bin/env bash
# Пересобирает справочник средств MCP: docs/mcp-tools.json и обе редакции
# страницы — docs/mcp-tools.md (английская) и docs/mcp-tools.ru.md (русская).
#
# Справочник снимается ЖИВЫМ разговором с сервером — запускается настоящий
# `ouroboros-mcp`, у него спрашивается список средств, и каждое зовётся
# по-настоящему. Ни одна строка справочника не выведена чтением исходника.
#
# Так было найдено, что прежняя документация описывала три средства, которых
# сервер не выкладывает: живого списка никто не снимал. Пересборка этой командой
# — способ больше в это не попадать.
#
# Использование: scripts/probe/build-reference.sh   (из корня хранилища)
set -euo pipefail

cd "$(dirname "$0")/../.."

# Рабочий каталог для образцов. Не в /tmp: на этом дереве временные файлы
# держат рядом с хранилищем, чтобы прогон было видно и можно было разобрать.
WORK="${OUROBOROS_PROBE_WORK:-$PWD/.probe-work}"

echo "== снимаю справочник живым разговором с ouroboros-mcp =="
echo "   рабочий каталог: $WORK"
uv run python scripts/probe/tool_reference.py "$WORK" > docs/mcp-tools.json

# Обе редакции печатаются из ОДНОГО снятого файла и в одном прогоне: иначе они
# разъедутся молча — сначала пересоберут английскую, а русскую забудут.
echo "== печатаю обе редакции страницы из снятого файла =="
uv run python scripts/probe/render_reference.py docs/mcp-tools.json > docs/mcp-tools.md
uv run python scripts/probe/render_reference.py docs/mcp-tools.json --lang ru > docs/mcp-tools.ru.md

rm -rf "$WORK"

COUNT=$(python3 -c "import json;print(json.load(open('docs/mcp-tools.json'))['declared_tool_count'])")
echo "== готово: средств объявлено $COUNT =="
echo "   docs/mcp-tools.json    — снятое как есть"
echo "   docs/mcp-tools.md      — страница, напечатанная из него (английская)"
echo "   docs/mcp-tools.ru.md   — она же по-русски"
