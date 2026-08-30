#!/usr/bin/env python3
"""Тот же опыт, но отвечает не маленькая модель, а сильный подчинённый агент.

Зачем. Короткий опыт снят на одной модели среднего размера. Из него не следует,
что запись вызовов нужна сильной модели: сильная могла бы досчитать всё в уме.
Проверить это можно на самих себе — на подчинённых агентах.

Как устроена слепота. Агент заводится под один-единственный вопрос. Он не знает
ни про опыт, ни про две группы, ни про то, в какой он группе; он видит только
запрос и отвечает одной строкой. Строже, чем бывает у людей: подсмотреть ему
буквально нечего.

Что делает этот файл:

    agents.py готовь <случаи.json> <каталог>
        раскладывает запросы по файлам <каталог>/задания/NNN.txt и пишет опись.
        Порядок перемешан с постоянным зерном, чтобы задания не шли группами.

    agents.py собери <каталог> <ответы.jsonl>
        собирает ответы из <каталог>/ответы/NNN.txt в тот же вид, что у ask.py,
        так что считает их та же слепая grade.py.

Ответы кладёт туда тот, кто заводит агентов. Сами ответы хранятся рядом с
опытом (`agents/ответы/`), поэтому счёт пересчитывается когда угодно.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import ask

NOTE = """Ничего не запускай и не ищи в файловой системе: всё нужное есть ниже.
Ответ дай по тому, что здесь написано.

"""


def prepare(cases_path: Path, out_dir: Path) -> int:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    jobs = []
    for case in cases:
        for q in case["вопросы"]:
            for arm in case.get("группы", ["без", "с"]):
                jobs.append({"вопрос_id": q["id"], "программа": case["имя"],
                             "язык": case["язык"], "группа": arm,
                             "запрос": NOTE + ask.prompt(case, q, arm)})
    random.Random(20260830).shuffle(jobs)

    tasks = out_dir / "задания"
    tasks.mkdir(parents=True, exist_ok=True)
    (out_dir / "ответы").mkdir(parents=True, exist_ok=True)
    index = []
    for n, job in enumerate(jobs, start=1):
        (tasks / f"{n:03d}.txt").write_text(job["запрос"], encoding="utf-8")
        index.append({"номер": n, **{k: v for k, v in job.items()
                                     if k != "запрос"},
                      "знаков_в_запросе": len(job["запрос"])})
    (out_dir / "опись.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"== заданий {len(index)} -> {tasks}")
    print(f"== опись -> {out_dir / 'опись.json'}")
    return 0


def collect(out_dir: Path, answers_path: Path) -> int:
    index = json.loads((out_dir / "опись.json").read_text(encoding="utf-8"))
    lines = []
    missing = []
    for item in index:
        path = out_dir / "ответы" / f"{item['номер']:03d}.txt"
        if not path.exists():
            missing.append(item["номер"])
            continue
        lines.append(json.dumps({
            "вопрос_id": item["вопрос_id"], "программа": item["программа"],
            "язык": item["язык"], "группа": item["группа"], "повтор": 1,
            "знаков_в_запросе": item["знаков_в_запросе"], "секунд": 0.0,
            "ответ": path.read_text(encoding="utf-8").strip(),
        }, ensure_ascii=False))
    if missing:
        print(f"нет ответов на задания: {missing}", file=sys.stderr)
        return 1
    answers_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"== ответов {len(lines)} -> {answers_path}")
    return 0


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2
    what = sys.argv[1]
    if what == "готовь":
        return prepare(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
    if what == "собери":
        return collect(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
    print(f"не знаю такого шага: {what}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
