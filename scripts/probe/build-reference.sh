#!/usr/bin/env bash
# Пересобирает справочник средств MCP: docs/mcp-tools.json и docs/mcp-tools.md.
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

echo "== печатаю страницу из снятого файла =="
uv run python scripts/probe/render_reference.py docs/mcp-tools.json > docs/mcp-tools.md

rm -rf "$WORK"

COUNT=$(python3 -c "import json;print(json.load(open('docs/mcp-tools.json'))['declared_tool_count'])")
echo "== готово: средств объявлено $COUNT =="
echo "   docs/mcp-tools.json  — снятое как есть"
echo "   docs/mcp-tools.md    — страница, напечатанная из него"
