#!/usr/bin/env python3
"""Длинный опыт: что будет, когда запись вызовов в запрос не влезает.

Короткий опыт (build.py + ask.py + grade.py) отдаёт модели запись целиком —
она там в несколько десятков строк. На настоящем коде так не бывает. Здесь те же
программы запускаются на большом входе: запись получается в сотни килобайт, а в
запрос кладётся её урезанный вид — начало и конец, середина вырезана.

Группы две: «без» — исходник, команда и вывод; «урез» — то же плюс урезанная
запись. Целой записи нет ни у кого: она не влезает.

Вопросы к каждой программе размечены по тому, где лежит ответ: в уцелевшей
части записи, в вырезанной середине, или его вообще нельзя достать из куска
(«сколько раз вызвана функция» требует всей записи целиком).

Правильные ответы здесь, в отличие от короткого опыта, не переписаны руками:
триста позиций руками не пересчитать. Они взяты из ПОЛНОЙ записи тем же кодом,
что и там (`build.from_trace`), и ни один из них не выведен из ответов модели.

Запуск: long.py <рабочий каталог> <куда писать>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import ask
import build

HERE = Path(__file__).resolve().parent

# Сколько знаков записи влезает в запрос. То же значение читает ask.py.
LIMIT = ask.TRACE_LIMIT


def items_orders(n: int) -> list[str]:
    """Позиции заказа: имя, разряд, количество, цена."""

    kinds = ["книги", "еда", "техника"]
    out = []
    for i in range(n):
        qty = 1 + (i * 7) % 14
        price = 40 + (i * 37) % 900
        out.append(f"поз{i}:{kinds[i % 3]}:{qty}:{price}")
    return out


def items_cart(n: int) -> list[str]:
    out = ["ZIMA"]
    for i in range(n):
        out.append(f"тов{i}:{1 + (i * 3) % 5}:{20 + (i * 53) % 400}")
    return out


def items_router(n: int) -> list[str]:
    """Пути запросов. Все начинаются с /users, чтобы часть веток не сработала."""

    out = []
    for i in range(n):
        out.append(["/users", f"/users/{i}", f"/users/имя{i}"][i % 3])
    return out


def items_tokens(n: int) -> list[str]:
    ops = ["+", "-", "*"]
    out = ["7"]
    for i in range(n):
        out.append(ops[i % 3])
        out.append(str(1 + (i * 11) % 9))
    return out


# Что за программы берём, на каком входе и о чём спрашиваем.
#   «счёт»     — функции для вопроса «сколько раз вызвана»;
#   «значения» — функции для вопросов «что вернул такой-то вызов». Взяты только
#                те, что возвращают число или «да»/«нет»: если бы возврат был
#                составным, группа с записью списывала бы его вид буква в букву,
#                а контрольная группа не могла бы угадать даже расстановку кавычек, и
#                разница мерила бы не знание, а вид записи;
#   «нет»      — функция, которой на этом запуске не было вовсе.
LONG = [
    {"имя": "orders", "доводы": items_orders(300),
     "счёт": ["line_total", "tax"],
     "значения": ["line_total", "tax", "bulk_discount"],
     "нет": "loyalty_bonus"},
    {"имя": "cart", "доводы": items_cart(500),
     "счёт": ["parseLine", "priceOf"],
     "значения": ["priceOf"],
     "нет": "giftFor"},
    {"имя": "tokens", "доводы": items_tokens(700),
     "счёт": ["is_op", "to_int"],
     "значения": ["to_int", "apply", "is_op"],
     "нет": "complain"},
    {"имя": "router", "доводы": items_router(300),
     "счёт": ["splitPath", "isNumber"],
     "значения": ["isNumber"],
     "нет": "matchPosts"},
]


def call_lines(trace: list[dict], fn: str, n: int) -> tuple[int, int]:
    """Номера строк входа и выхода n-го по счёту вызова функции (с единицы)."""

    ins = [i for i, r in enumerate(trace)
           if r.get("p") == "in" and r.get("fn") == fn]
    idx = ins[n - 1 if n > 0 else n]
    ident = trace[idx]["id"]
    outs = [i for i, r in enumerate(trace)
            if r.get("p") == "out" and r.get("id") == ident]
    return idx, (outs[0] if outs else idx)


def ask_questions(name: str, spec: dict, trace: list[dict],
                  survived: set[int]) -> list[dict]:
    """Девять вопросов к одной длинной программе, с меткой места ответа."""

    qs: list[dict] = []

    def add(qid, text, check, mark):
        qs.append({"id": qid, "вопрос": text, "проверка": check, "метка": mark,
                   "ответ": [build.from_trace(trace, check)]})

    def count(fn: str) -> int:
        return len([r for r in trace if r.get("p") == "in" and r.get("fn") == fn])

    # 1-2. Сколько раз вызвана — по куску записи не сосчитать никак.
    for k, fn in enumerate(spec["счёт"], start=1):
        add(f"{name}-д{k}",
            f"Сколько раз за этот запуск вызвана функция {fn}?",
            {"вид": "число", "fn": fn}, "сколько раз вызвана — нужна вся запись")

    # 3. Функция, которой на этом запуске не было.
    add(f"{name}-д3",
        f"Вызывалась ли за этот запуск функция {spec['нет']}? Ответь «да» или «нет».",
        {"вид": "звался", "fn": spec["нет"]}, "функции в записи нет вовсе")

    # 4-9. Возвраты: по два вызова из начала, из середины и с конца.
    vals = spec["значения"]
    picks: list[tuple[str, int]] = []
    for place, offsets in (("начало", (2, 5)), ("середина", (0, 1)),
                           ("конец", (-1, -4))):
        for m, off in enumerate(offsets):
            fn = vals[m % len(vals)]
            total = count(fn)
            n = off if place != "середина" else max(3, total // 2 + off)
            picks.append((fn, n))

    for k, (fn, n) in enumerate(picks, start=4):
        which = "последний" if n == -1 else (
            f"{-n}-й с конца" if n < 0 else f"{n}-й по счёту")
        line_in, line_out = call_lines(trace, fn, n)
        alive = line_in in survived and line_out in survived
        mark = ("что вернул вызов — вызов уцелел в урезанной записи" if alive
                else "что вернул вызов — вызов вырезан")
        add(f"{name}-д{k}", f"Что вернул {which} вызов функции {fn}?",
            {"вид": "возврат", "fn": fn, "n": n}, mark)
    return qs


def records(raw: str) -> tuple[list[dict], set[int]]:
    """Записи полной трассы и номера тех из них, что уцелели после урезания.

    Номер записи — это её место в файле, а урезание выбрасывает промежуток
    строк подряд, так что уцелевшие считаются точно, а не по совпадению текста.
    """

    lines = [l for l in ask.lines_of(raw) if l.startswith("{")]
    i, j = ask.cut_bounds(lines, LIMIT)
    alive = set(range(0, i)) | set(range(j + 1, len(lines)))
    return [json.loads(l) for l in lines], alive


def main() -> int:
    work = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    cases = []
    for spec in LONG:
        name = spec["имя"]
        case = build.prepare(name, work, args=spec["доводы"], questions=[],
                             into="длинный-" + name)
        if case is None:
            continue
        trace, alive = records(case["трасса"])
        case["вопросы"] = ask_questions(name, spec, trace, alive)
        case["группы"] = ["без", "урез"]
        case["имя"] = "длинный-" + name
        short, dropped = ask.cut(case["трасса"], LIMIT)
        print(f"   запись {len(case['трасса'])} знаков, "
              f"в запрос влезает {len(short)}, вырезано {dropped} строк "
              f"из {len(trace)}")
        cases.append(case)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    total = sum(len(c["вопросы"]) for c in cases)
    print(f"== длинных программ {len(cases)}, вопросов {total} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
