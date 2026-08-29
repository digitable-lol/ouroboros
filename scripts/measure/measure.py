#!/usr/bin/env python3
"""Замер: считает время прогона и объём записей.

Запуск:
    measure.py <имя> <повторов> <файл записей или "-"> <рабочий каталог> -- <команда...>
Печатает JSON: медиана, минимум, максимум времени в секундах.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from typing import Any


def run(cmd: list[str], cwd: str, env: dict[str, str],
        repeats: int, trace: str) -> list[float]:
    """Запускает команду `repeats` раз и возвращает время каждого прогона.

    Файл записей стирается ПЕРЕД каждым повтором: иначе объём насчитается за все
    повторы сразу — на этом легко ошибиться и получить впятеро больше строк, чем
    делает одна программа."""

    times: list[float] = []
    for _ in range(repeats):
        if trace != "-" and os.path.exists(trace):
            os.remove(trace)
        t0 = time.perf_counter()
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
        t1 = time.perf_counter()
        if p.returncode != 0:
            print("ОШИБКА", p.returncode, p.stdout[-2000:], p.stderr[-2000:], file=sys.stderr)
            sys.exit(1)
        times.append(t1 - t0)
    return times


def main() -> int:
    name = sys.argv[1]
    repeats = int(sys.argv[2])
    trace = sys.argv[3]           # путь к debug.info, либо "-" если записей нет
    cwd = sys.argv[4]
    sep = sys.argv.index("--")
    cmd = sys.argv[sep + 1:]

    env = dict(os.environ)
    if trace != "-":
        env["OUROBOROS_DEBUG_INFO"] = trace
    times = run(cmd, cwd, env, repeats, trace)
    out: dict[str, Any] = {
        "имя": name,
        "повторов": repeats,
        "медиана_с": round(statistics.median(times), 6),
        "минимум_с": round(min(times), 6),
        "максимум_с": round(max(times), 6),
    }
    if trace != "-" and os.path.exists(trace):
        size = os.path.getsize(trace)
        with open(trace, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        n_in = sum(1 for line in lines if '"p":"in"' in line)
        n_out = sum(1 for line in lines if '"p":"out"' in line)
        out.update({
            "байт_записей": size,
            "строк": len(lines),
            "строк_входа": n_in,
            "строк_выхода": n_out,
        })
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
