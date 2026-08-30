#!/usr/bin/env python3
"""Задаёт модели вопросы о программах — с трассой и без — и складывает ответы.

Каждый вопрос задаётся дважды: контрольной группе (исходник, команда, вывод) и
опытной группе (то же плюс трасса). Всё, кроме куска с трассой, в двух запросах
одинаково, включая зерно случайности.

Групп у случая может быть и другая пара — какая, написано в самом случае, в поле
«группы». Длинный опыт вместо целой трассы даёт урезанную: группа «урез».

Запуск: ask.py <случаи.json> <куда писать ответы.jsonl>
Настройки — переменными OUROBOROS_MODEL_CMD, OUROBOROS_MODEL, OUROBOROS_TRIES,
OUROBOROS_TRACE_LIMIT (сколько знаков трассы влезает в запрос группы «урез»).
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODEL = os.environ.get("OUROBOROS_MODEL", "qwen2.5:14b-instruct")
TRIES = int(os.environ.get("OUROBOROS_TRIES", "5"))
WORKERS = int(os.environ.get("OUROBOROS_WORKERS", "3"))
CMD = os.environ.get("OUROBOROS_MODEL_CMD", "")
TRACE_LIMIT = int(os.environ.get("OUROBOROS_TRACE_LIMIT", "8000"))

CUT_NOTE = """Запись целиком в запрос не поместилась: из середины вырезаны
строки, сколько именно — сказано в самой записи на месте выреза."""

LEGEND = """Формат записи: по одному объекту JSON в строке. Строка с "p":"in" —
вход в вызов: "fn" — имя функции, "a" — её доводы, "id" — номер вызова. Строка с
"p":"out" — выход из того же вызова (тот же "id"): "r" — что вернули, "x" — что
бросили, "d" — сколько заняло. Функции, которой в записи нет, на этом запуске не
вызывали."""

HEAD = """Ты разбираешь чужую программу. Ниже её исходник, команда, которой её
запустили, и то, что она напечатала.{ещё}

=== исходник ===
{исходник}
=== команда запуска ===
{команда}
=== что программа напечатала ===
{вывод}
{трасса}
Вопрос про этот самый запуск: {вопрос}

Ответь ровно одной строкой вида
ОТВЕТ: <значение>
Значение — короткое: число, слово, «да» или «нет». Если определить нельзя,
напиши «ОТВЕТ: не знаю». Ничего, кроме этой строки, не пиши."""


def lines_of(trace: str) -> list[str]:
    return [l for l in trace.splitlines() if l.strip()]


def cut_bounds(lines: list[str], limit: int) -> tuple[int, int]:
    """Какие строки записи выброшены: с i-й по j-ю включительно.

    Набираем поочерёдно с начала и с конца, пока влезает: края записи важнее
    середины. Если выбрасывать нечего, возвращается пустой промежуток.
    """

    used = 0
    i, j = 0, len(lines) - 1
    while i <= j:
        if used + len(lines[i]) + 1 > limit:
            break
        used += len(lines[i]) + 1
        i += 1
        if i > j or used + len(lines[j]) + 1 > limit:
            break
        used += len(lines[j]) + 1
        j -= 1
    return i, j


def cut(trace: str, limit: int) -> tuple[str, int]:
    """Урезает запись до бюджета: оставляет начало и конец, середину вырезает.

    Возвращает урезанный текст и число выброшенных строк. Так делает всякое
    средство, которому запись не влезает в окно.
    """

    lines = lines_of(trace)
    i, j = cut_bounds(lines, limit)
    dropped = j - i + 1
    if dropped <= 0:
        return "\n".join(lines), 0
    middle = f"... здесь вырезано {dropped} строк ..."
    return "\n".join(lines[:i] + [middle] + lines[j + 1:]), dropped


def prompt(case: dict, question: dict, arm: str) -> str:
    more = ""
    trace = ""
    if arm == "с":
        more = "\nИ ещё запись всех вызовов функций, снятая на этом же запуске."
        trace = ("=== запись вызовов, тот же запуск ===\n" + LEGEND + "\n\n"
                 + case["трасса"])
    elif arm == "урез":
        more = ("\nИ ещё запись всех вызовов функций, снятая на этом же запуске,"
                "\nурезанная до размера запроса.")
        short, _ = cut(case["трасса"], TRACE_LIMIT)
        trace = ("=== запись вызовов, тот же запуск ===\n" + LEGEND + "\n"
                 + CUT_NOTE + "\n\n" + short)
    return HEAD.format(ещё=more, исходник=case["исходник"], команда=case["команда"],
                       вывод=case["вывод"], трасса=trace, вопрос=question["вопрос"])


def call(text: str, seed: int) -> tuple[str, float]:
    body = json.dumps({
        "model": MODEL,
        "prompt": text,
        "stream": False,
        # Внутреннее рассуждение выключено у всех моделей, иначе новые тратят на
        # него весь предел ответа и до строки ОТВЕТ не доходят. Модели, которые
        # рассуждать не умеют, это поле просто не замечают.
        "think": False,
        "options": {"temperature": 0.8, "seed": seed, "num_ctx": 16384,
                    "num_predict": 300},
    }, ensure_ascii=False)
    started = time.perf_counter()
    done = subprocess.run(shlex.split(CMD), input=body, capture_output=True, text=True)
    spent = time.perf_counter() - started
    if done.returncode != 0:
        raise RuntimeError(f"модель не ответила: {done.stderr[-500:]}")
    return json.loads(done.stdout)["response"], spent


def main() -> int:
    if not CMD:
        print("не задан OUROBOROS_MODEL_CMD", file=sys.stderr)
        return 2
    cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_path = Path(sys.argv[2])

    done_keys = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done_keys.add((r["вопрос_id"], r["группа"], r["повтор"]))

    jobs = []
    for repeat in range(1, TRIES + 1):
        for case in cases:
            for q in case["вопросы"]:
                for arm in case.get("группы", ["без", "с"]):
                    if (q["id"], arm, repeat) in done_keys:
                        continue
                    jobs.append((case, q, arm, repeat))

    print(f"== модель {MODEL}, повторов {TRIES}, запросов {len(jobs)}")
    fh = out_path.open("a", encoding="utf-8")
    counter = [0]

    def work(job):
        case, q, arm, repeat = job
        text = prompt(case, q, arm)
        answer, spent = call(text, repeat)
        return {
            "вопрос_id": q["id"], "программа": case["имя"], "язык": case["язык"],
            "группа": arm, "повтор": repeat, "знаков_в_запросе": len(text),
            "секунд": round(spent, 3), "ответ": answer,
        }

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for rec in pool.map(work, jobs):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            counter[0] += 1
            if counter[0] % 25 == 0:
                print(f"   {counter[0]}/{len(jobs)}", flush=True)
    fh.close()
    print("== готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
