#!/usr/bin/env bash
# Опыт: помогает ли запись вызовов разобраться в чужом коде. Одна команда.
#
# Что делается по порядку:
#   1) двенадцать подопытных программ обмазываются, собираются и запускаются
#      дважды — как есть и с записью; вывод обеих сверяется, ключ ответов
#      сверяется с записью (build.py);
#   2) каждой модели из списка задаются шестьдесят вопросов, каждый дважды —
#      без записи и с ней, и так пять раз (ask.py), после чего ответы считаются
#      вслепую (grade.py);
#   3) длинный опыт: те же программы на большом входе, где запись в запрос не
#      влезает и её приходится урезать (long.py);
#   4) опыт на сильной модели: готовятся задания для подчинённых агентов и
#      пересчитываются их ответы, сохранённые в agents/ответы (agents.py).
#      Самих агентов заводит человек или старший агент — из bash это нельзя.
#
# Устройство опыта и заранее объявленная мера — в README.md рядом.
#
# Использование:
#     scripts/measure/trace-help/run.sh [рабочий каталог]
#
# Нужны: python3, node, gcc, g++, elixirc, go и команда ouroboros на PATH.
# Языка, чьего средства сборки нет, опыт не считает и говорит об этом.
set -euo pipefail

cd "$(dirname "$0")"
WORK="${1:-${OUROBOROS_WORK:-./.trace-help-work}}"
mkdir -p "$WORK"
WORK="$(cd "$WORK" && pwd)"

: "${OUROBOROS_MODEL:=qwen2.5:14b-instruct}"
: "${OUROBOROS_MODELS:=qwen3.5:4b qwen2.5:14b-instruct qwen3:32b}"
: "${OUROBOROS_TRIES:=5}"
: "${OUROBOROS_WORKERS:=4}"
: "${OUROBOROS_TRACE_LIMIT:=8000}"
: "${OUROBOROS_MODEL_CMD:=sudo -n -u u ssh -F /home/u/.ssh/config -o BatchMode=yes gpu curl -s -X POST http://127.0.0.1:11434/api/generate -H Content-Type:application/json --data-binary @-}"
export OUROBOROS_MODEL OUROBOROS_TRIES OUROBOROS_WORKERS OUROBOROS_MODEL_CMD
export OUROBOROS_TRACE_LIMIT

if ! command -v ouroboros >/dev/null 2>&1; then
	echo "команды ouroboros нет на PATH — ставить смотрите docs/install.md" >&2
	exit 1
fi

CASES="$WORK/случаи.json"
LONG="$WORK/длинные.json"

echo "== рабочий каталог: $WORK"
echo "== 1. готовим программы и сверяем ключ"
python3 build.py "$WORK/сборка" "$CASES"

for model in $OUROBOROS_MODELS; do
	safe="$(printf '%s' "$model" | tr ':.' '--')"
	answers="$WORK/ответы-$safe.jsonl"
	echo
	echo "== 2. спрашиваем $model (повторов $OUROBOROS_TRIES)"
	echo "   уже собранные ответы не переспрашиваются: сотрите $answers,"
	echo "   чтобы начать заново"
	OUROBOROS_MODEL="$model" python3 ask.py "$CASES" "$answers"
	echo
	echo "== считаем вслепую: $model"
	python3 grade.py "$CASES" "$answers" | tee "$WORK/итог-$safe.md"
done

echo
echo "== 3. длинные программы: запись в запрос не влезает, её урезают"
python3 long.py "$WORK/длинная-сборка" "$LONG"
python3 ask.py "$LONG" "$WORK/ответы-длинные.jsonl"
python3 grade.py "$LONG" "$WORK/ответы-длинные.jsonl" | tee "$WORK/итог-длинные.md"

echo
echo "== 4. сильная модель: подчинённые агенты"
python3 agents.py готовь "$CASES" "$WORK/агенты"
if [ -d agents/ответы ] && [ -f agents/опись.json ]; then
	cp agents/опись.json "$WORK/агенты/опись.json"
	cp agents/ответы/*.txt "$WORK/агенты/ответы/" 2>/dev/null || true
	python3 agents.py собери "$WORK/агенты" "$WORK/ответы-агенты.jsonl"
	python3 grade.py "$CASES" "$WORK/ответы-агенты.jsonl" \
		| tee "$WORK/итог-агенты.md"
else
	echo "   сохранённых ответов агентов нет; задания разложены в"
	echo "   $WORK/агенты/задания — заведите на каждое отдельного агента и"
	echo "   положите его строку в $WORK/агенты/ответы/NNN.txt"
fi

echo
echo "== всё. итоги: $WORK/итог-*.md"
