#!/usr/bin/env python3
"""Считает ответы вслепую и печатает итог с разбросом.

Слепота устроена буквально: разбор и сверка живут в функции `judge`, которая
получает только номер вопроса и текст ответа. Пометки о группе в её входе нет,
и попасть туда ей неоткуда — до сверки записи перемешиваются, а поле «группа»
приклеивается обратно только на счёт.

Групп всегда две: «без» — контрольная группа, и вторая, какая нашлась в ответах
(«с» — целая трасса, «урез» — урезанная).

Запуск: grade.py <случаи.json> <ответы.jsonl>
"""
from __future__ import annotations

import json
import random
import re
import statistics
import sys
from pathlib import Path

ABSTAIN = ("не знаю", "неизвестно", "нельзя определить", "недостаточно")
NUMBER = re.compile(r"-?\d+")


def tidy(text: str) -> str:
    text = text.strip().strip("«»\"'`").strip()
    text = text.rstrip(".")
    return " ".join(text.lower().split())


def extract(raw: str) -> str | None:
    """Последняя строка со словом ОТВЕТ: — то, что после двоеточия."""

    found = None
    for line in raw.splitlines():
        if "ОТВЕТ:" in line.upper() or "ответ:" in line:
            found = line.split(":", 1)[1] if ":" in line else line
    return found


def judge(accepted: list[str], raw: str) -> str:
    """Верно / не знаю / неверно. Видит только принятые ответы и текст."""

    said = extract(raw)
    if said is None:
        return "неверно"
    said = tidy(said)
    if any(word in said for word in ABSTAIN):
        return "не знаю"
    for want in accepted:
        want = tidy(want)
        if said == want:
            return "верно"
        if want in ("да", "нет") and said.startswith(want):
            return "верно"
        if NUMBER.fullmatch(want):
            nums = NUMBER.findall(said)
            if nums == [want]:
                return "верно"
    return "неверно"


def bootstrap(groups: list[list[tuple[str, str]]], arms: tuple[str, str],
              rounds: int = 10000) -> dict:
    """95-процентный промежуток пересборкой по программам."""

    rnd = random.Random(20260830)
    n = len(groups)
    base, other = arms
    shares: dict[str, list[float]] = {base: [], other: [], "разница": [],
                                      "уверенно_" + base: [],
                                      "уверенно_" + other: [],
                                      "уверенно_разница": []}
    for _ in range(rounds):
        picked = [groups[rnd.randrange(n)] for _ in range(n)]
        flat = [item for g in picked for item in g]
        for arm in arms:
            mine = [v for a, v in flat if a == arm]
            shares[arm].append(sum(v == "верно" for v in mine) / len(mine))
            shares["уверенно_" + arm].append(
                sum(v == "неверно" for v in mine) / len(mine))
        shares["разница"].append(shares[other][-1] - shares[base][-1])
        shares["уверенно_разница"].append(
            shares["уверенно_" + other][-1] - shares["уверенно_" + base][-1])
    out = {}
    for key, values in shares.items():
        values.sort()
        out[key] = (values[int(0.025 * rounds)], values[int(0.975 * rounds)])
    return out


def pct(x: float) -> str:
    return f"{100 * x:.1f}".replace(".", ",") + " %"


def main() -> int:
    cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    answers = [json.loads(l) for l in Path(sys.argv[2]).read_text(
        encoding="utf-8").splitlines() if l.strip()]

    accepted = {}
    kinds = {}
    program_of = {}
    marks = {}
    for case in cases:
        for q in case["вопросы"]:
            accepted[q["id"]] = q["ответ"]
            kinds[q["id"]] = q["проверка"]["вид"]
            program_of[q["id"]] = case["имя"]
            if q.get("метка"):
                marks[q["id"]] = q["метка"]

    # --- слепой кусок: сюда идут только номер вопроса и текст --------------
    blind = [(i, r["вопрос_id"], r["ответ"]) for i, r in enumerate(answers)]
    random.Random(1).shuffle(blind)
    verdicts = {i: judge(accepted[qid], text) for i, qid, text in blind}
    # --- дальше уже можно смотреть, кто где ------------------------------
    for i, r in enumerate(answers):
        r["итог"] = verdicts[i]

    seen = {r["группа"] for r in answers}
    other = next(a for a in ("с", "урез") if a in seen)
    arms = ("без", other)
    titles_arm = {"без": "без трассы (контрольная группа)", "с": "с трассой",
                  "урез": "с урезанной трассой"}

    print()
    print(f"ответов всего: {len(answers)}; вопросов: {len(accepted)}; "
          f"программ: {len(cases)}; "
          f"повторов: {max(r['повтор'] for r in answers)}")

    def share(rows, verdict):
        return sum(r["итог"] == verdict for r in rows) / len(rows) if rows else 0.0

    print()
    print("### главное")
    print()
    print("| группа | верных | уверенно неверных | «не знаю» | ответов |")
    print("|---|---|---|---|---|")
    for arm in arms:
        rows = [r for r in answers if r["группа"] == arm]
        print(f"| {titles_arm[arm]} | {pct(share(rows, 'верно'))} | "
              f"{pct(share(rows, 'неверно'))} | {pct(share(rows, 'не знаю'))} | "
              f"{len(rows)} |")

    groups = []
    for case in cases:
        ids = {q["id"] for q in case["вопросы"]}
        groups.append([(r["группа"], r["итог"]) for r in answers
                       if r["вопрос_id"] in ids])
    band = bootstrap(groups, arms)
    print()
    print("95-процентные промежутки (пересборка по программам, 10 000 раз):")
    print()
    wide = max(len(titles_arm[a]) for a in arms) + 7
    for arm in arms:
        name = "верных " + titles_arm[arm]
        print(f"  {name:{wide}s} {pct(band[arm][0])} … {pct(band[arm][1])}")
    print(f"  {'разница':{wide}s} {pct(band['разница'][0])} … "
          f"{pct(band['разница'][1])}")
    print(f"  уверенно неверных: разница {pct(band['уверенно_разница'][0])} … "
          f"{pct(band['уверенно_разница'][1])}")

    print()
    print("### разброс между повторами (доля верных в каждом повторе)")
    print()
    print(f"| повтор | {titles_arm[arms[0]]} | {titles_arm[arms[1]]} |")
    print("|---|---|---|")
    per_repeat = {}
    for rep in sorted({r["повтор"] for r in answers}):
        line = []
        for arm in arms:
            rows = [r for r in answers if r["повтор"] == rep and r["группа"] == arm]
            line.append(share(rows, "верно"))
        per_repeat[rep] = line
        print(f"| {rep} | {pct(line[0])} | {pct(line[1])} |")
    for idx, title in enumerate(titles_arm[a] for a in arms):
        vals = [v[idx] for v in per_repeat.values()]
        if len(vals) > 1:
            print(f"  {title}: размах {pct(min(vals))} … {pct(max(vals))}, "
                  f"стандартное отклонение {pct(statistics.stdev(vals))}")

    print()
    print("### по языкам")
    print()
    print(f"| язык | верных {titles_arm[arms[0]]} | верных {titles_arm[arms[1]]}"
          " | ответов на группу |")
    print("|---|---|---|---|")
    for lang in sorted({r["язык"] for r in answers}):
        line = []
        for arm in arms:
            rows = [r for r in answers if r["язык"] == lang and r["группа"] == arm]
            line.append((share(rows, "верно"), len(rows)))
        print(f"| {lang} | {pct(line[0][0])} | {pct(line[1][0])} | {line[0][1]} |")

    print()
    print("### по виду вопроса")
    print()
    print(f"| о чём вопрос | верных {titles_arm[arms[0]]} | "
          f"верных {titles_arm[arms[1]]} | ответов на группу |")
    print("|---|---|---|---|")
    titles = {"число": "сколько раз вызвана функция",
              "возврат": "что вернул такой-то вызов",
              "звался": "вызывалась ли функция вообще",
              "довод": "с чем позвали функцию",
              "бросил": "кто бросил исключение"}
    for kind in ("число", "возврат", "звался", "довод", "бросил"):
        ids = {q for q, k in kinds.items() if k == kind}
        if not ids:
            continue
        line = []
        for arm in arms:
            rows = [r for r in answers
                    if r["вопрос_id"] in ids and r["группа"] == arm]
            line.append((share(rows, "верно"), len(rows)))
        print(f"| {titles[kind]} | {pct(line[0][0])} | {pct(line[1][0])} | "
              f"{line[0][1]} |")

    if marks:
        print()
        print("### где в записи лежит ответ")
        print()
        print(f"| ответ лежит | верных {titles_arm[arms[0]]} | "
              f"верных {titles_arm[arms[1]]} | ответов на группу |")
        print("|---|---|---|---|")
        order = []
        for q in accepted:
            m = marks.get(q)
            if m and m not in order:
                order.append(m)
        for mark in order:
            ids = {q for q, m in marks.items() if m == mark}
            line = []
            for arm in arms:
                rows = [r for r in answers
                        if r["вопрос_id"] in ids and r["группа"] == arm]
                line.append((share(rows, "верно"), len(rows)))
            print(f"| {mark} | {pct(line[0][0])} | {pct(line[1][0])} | "
                  f"{line[0][1]} |")

    print()
    print("### цена")
    print()
    src = sum(len(c["исходник"]) for c in cases)
    trc = sum(len(c["трасса"]) for c in cases)
    print(f"знаков в исходниках {len(cases)} программ: {src}")
    print(f"знаков в их трассах: {trc} — это "
          f"{trc / src:.2f}".replace(".", ",") + " исходника")
    ratios = sorted(len(c["трасса"]) / len(c["исходник"]) for c in cases)
    print(f"на программу: от {ratios[0]:.2f} до {ratios[-1]:.2f}, "
          f"посередине {statistics.median(ratios):.2f}".replace(".", ","))
    for arm in arms:
        rows = [r for r in answers if r["группа"] == arm]
        letters = statistics.median(r["знаков_в_запросе"] for r in rows)
        secs = statistics.median(r["секунд"] for r in rows)
        print(f"{titles_arm[arm]}: запрос {int(letters)} знаков, "
              f"ответ {secs:.2f} с".replace(".", ","))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
