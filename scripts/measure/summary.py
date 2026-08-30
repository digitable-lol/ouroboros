#!/usr/bin/env python3
"""Печатает сводные таблицы по сырым замерам, снятым scripts/measure/run.sh.

Читает файл, где на каждый замер по строке JSON, и печатает две таблицы в том
виде, в каком они стоят в docs/measurements.md: время работы и объём записей.

Запуск: python3 scripts/measure/summary.py <файл замеров>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: (как назвать в таблице, замер без обмазки, замер с обмазкой, сколько вызовов).
#:
#: Число вызовов стоит здесь, а не выводится из числа записей, нарочно: у
#: короткого вида для C записей вдвое меньше (только строка входа), и деление на
#: них дало бы вдвое завышенную цену вызова.
PAIRS = [
    ("Python", "python-без", "python-с", 20002),
    ("JavaScript", "js-без", "js-с", 20002),
    ("C", "c-без", "c-с", 20002),
    ("C++", "cpp-без", "cpp-с", 20003),
    ("Elixir", "elixir-без", "elixir-с", 20002),
    ("Java", "java-без", "java-с", 20003),
    ("Go", "go-без", "go-с", 20002),
    ("C, короткий вид (`--minimal`)", "c-без", "c-краткий", 20002),
    ("Go, без номера горутины", "go-без", "go-без-th", 20002),
]


def read(path: Path) -> dict[str, dict[str, Any]]:
    """Замеры из файла: имя замера -> что о нём известно."""

    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("{"):
            record = json.loads(line)
            out[record["имя"]] = record
    return out


def comma(text: str) -> str:
    """Десятичная запятая вместо точки — как принято в русском тексте."""

    return text.replace(".", ",")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    measured = read(Path(sys.argv[1]))

    print()
    print("### время работы (медиана из повторов, секунды)")
    print()
    print("| язык | без обмазки | с обмазкой | добавка | добавка на вызов |")
    print("|---|---|---|---|---|")
    for title, plain, wrapped, calls in PAIRS:
        if plain not in measured or wrapped not in measured:
            continue
        before = float(measured[plain]["медиана_с"])
        after = float(measured[wrapped]["медиана_с"])
        print(comma(f"| {title} | {before:.4f} с | {after:.4f} с | "
                    f"{after - before:.4f} с | "
                    f"{(after - before) / calls * 1e6:.1f} мкс |"))

    print()
    print("### объём записей")
    print()
    print("| язык | строк | всего байт | байт на запись | байт на вызов |")
    print("|---|---|---|---|---|")
    for title, _plain, wrapped, calls in PAIRS:
        record = measured.get(wrapped)
        if not record or "байт_записей" not in record:
            continue
        lines = int(record["строк"])
        size = int(record["байт_записей"])
        print(comma(f"| {title} | {lines} | {size} | {size / lines:.1f} | "
                    f"{size / calls:.1f} |"))

    print()
    print("Разброс между повторами (минимум и максимум, секунды) — чтобы было")
    print("видно, где замер устойчив, а где нет:")
    print()
    for name, record in measured.items():
        low = float(record["минимум_с"])
        high = float(record["максимум_с"])
        print(f"  {name:14s} {low:.4f} … {high:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
