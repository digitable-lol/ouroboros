"""Ищет слова, в которых смешаны кириллица и латиница.

Такое слово почти всегда — опечатка от раскладки: русское слово, в котором одна
или две буквы набраны латиницей. Читается как обычное, а найти его глазами
в двух тысячах строк нельзя: буквы `а`, `е`, `о`, `с`, `р`, `х` в двух алфавитах выглядят одинаково.
Поиском по слову оно тоже не находится — набирают-то его правильно.

Правило дерева: либо кириллица целиком, либо английские слова. Смешанного слова
не бывает.

Что не считается бедой: латинское слово рядом с русским (`поле a`, `git`), имена
из кода (`write_file`), русское окончание через дефис у латинского имени
(`JSON-строка`) — дефис делит слово на части, и каждая часть однородна.

Запуск: uv run python scripts/check_no_mixed_script.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CYR = re.compile(r"[А-Яа-яЁё]")
LAT = re.compile(r"[A-Za-z]")
#: Слово — буквы подряд. Дефис и подчёркивание словоразделители: они соединяют
#: разнородные части намеренно (`JSON-строка`, `write_file`).
WORD = re.compile("[A-Za-z" + "А-Яа-яЁё]+")  # разбито, чтобы не ловить само себя

#: Управляющая последовательность в строке кода: перевод строки перед русским
#: словом приклеивает свою латинскую букву к слову и даёт мнимую находку.
#: Убираем такие последовательности до разбора, иначе проверка ловит свой вывод.
ESCAPE = re.compile(r"\\.")

#: Пометка «здесь смешанное слово стоит намеренно» — для примеров в описаниях.
OPT_OUT = "смешанные-алфавиты: нарочно"

#: Где смотрим. Исходники и страницы; чужое и собранное не трогаем.
ROOTS = ("docs", "scripts", "ouroboros", "tests")
EXTS = {".md", ".py", ".sh", ".toml"}
SKIP_PARTS = {".venv", "node_modules", "__pycache__", "_js", ".probe-work"}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    targets = [root / "README.md", root / "ARCHITECTURE.md", root / "SPEC.md"]
    for r in ROOTS:
        targets += [p for p in (root / r).rglob("*") if p.suffix in EXTS]

    bad: list[str] = []
    for path in sorted(set(targets)):
        if not path.is_file() or SKIP_PARTS & set(path.parts):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if OPT_OUT in line:
                continue
            for m in WORD.finditer(ESCAPE.sub(" ", line)):
                w = m.group()
                if CYR.search(w) and LAT.search(w):
                    bad.append(f"{path.relative_to(root)}:{n}: {w!r} в строке: {line.strip()[:90]}")

    if bad:
        print("Слова со смешанными алфавитами (кириллица и латиница в одном слове):\n")
        for b in bad:
            print(f"  - {b}")
        print(f"\nВсего: {len(bad)}. Либо кириллица целиком, либо английское слово.")
        return 1
    print("Слов со смешанными алфавитами нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
