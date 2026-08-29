"""Проверяет, что ссылки из документации на строки исходника ведут туда, куда обещано.

Документация ссылается на исходник с точностью до строки — `ouroboros/…py:123` и
такой же `#L123` в ссылке на GitHub. Такие номера тихо протухают: правка выше по
файлу сдвигает всё, и ссылка продолжает выглядеть исправной, ведя на случайную
строку. Ровно это и случилось, когда сервер MCP подрос на 63 строки: четыре
ссылки в четырёх страницах стали указывать мимо, и ни одна проверка этого не
заметила.

Как здесь устроена проверка. Ниже перечислены **опоры** — те места исходника, на
которые документации вообще разрешено ссылаться, и опознаются они по куску
текста самой строки, а не по номеру. Проверка находит текущий номер каждой
опоры, а затем требует, чтобы каждая ссылка из документации указывала на номер
какой-нибудь опоры. Сдвинулся исходник — проверка падает и печатает новые
номера, которые надо проставить.

Чего проверка не делает: она не знает, какую именно опору имела в виду
конкретная страница, поэтому при расхождении показывает все подходящие. Этого
достаточно, чтобы поломка не проехала молча, а починка занимала минуту.

Добавили в документацию ссылку на новое место — допишите опору сюда, иначе
проверка откажет: список опор намеренно закрытый.

Запуск: uv run python scripts/check_doc_links.py   (из корня хранилища)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

#: (файл исходника, кусок строки-опоры). Кусок должен встречаться в файле ровно
#: один раз — проверка это тоже требует, иначе опора не опора.
ANCHORS: list[tuple[str, str]] = [
    ("ouroboros/runtime.py", "_repr = reprlib.Repr()"),
    ("ouroboros/runtime.py", 'return os.environ.get("OUROBOROS_DEBUG_INFO"'),
    ("ouroboros/runtime.py", "def _cpu() -> int:"),
    ("ouroboros/runtime.py", "t0 = time.perf_counter()"),
    ("ouroboros/languages/base.py", "class CorruptedSourceError(Exception):"),
    ("ouroboros/sandbox/sync.py", "never carried into the output tree"),
    ("ouroboros/mcp/server.py", "_READ_ONLY = ToolAnnotations("),
    ("ouroboros/mcp/server.py", "def transport_from_env("),
]

#: `ouroboros/путь.py:12` или `ouroboros/путь.py:12-34` в тексте страницы.
REF = re.compile(r"(ouroboros/[A-Za-z0-9_/]*\.py):(\d+)(?:-(\d+))?")

#: `…/blob/main/ouroboros/путь.py#L12` или `#L12-L34` в ссылке.
URL = re.compile(r"blob/main/(ouroboros/[A-Za-z0-9_/]*\.py)#L(\d+)(?:-L(\d+))?")


def anchor_lines(root: Path) -> tuple[dict[str, dict[int, str]], list[str]]:
    """Текущие номера опор: файл -> {номер: кусок}. Плюс список бед."""

    found: dict[str, dict[int, str]] = {}
    problems: list[str] = []
    for rel, needle in ANCHORS:
        path = root / rel
        if not path.exists():
            problems.append(f"опора указывает на несуществующий файл: {rel}")
            continue
        hits = [
            i
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if needle in line
        ]
        if len(hits) != 1:
            problems.append(
                f"опора {rel!r} / {needle!r} встречается {len(hits)} раз "
                "(нужен ровно один) — поправьте кусок в ANCHORS"
            )
            continue
        found.setdefault(rel, {})[hits[0]] = needle
    return found, problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    anchors, problems = anchor_lines(root)

    docs = sorted(root.glob("**/*.md"))
    docs = [d for d in docs if ".venv" not in d.parts and "node_modules" not in d.parts]

    checked = 0
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern in (REF, URL):
                for m in pattern.finditer(line):
                    rel, start = m.group(1), int(m.group(2))
                    checked += 1
                    valid = anchors.get(rel, {})
                    if not valid:
                        problems.append(
                            f"{doc.relative_to(root)}:{lineno} ссылается на {rel}:{start}, "
                            f"но для {rel} не объявлено ни одной опоры в "
                            "scripts/check_doc_links.py"
                        )
                    elif start not in valid:
                        where = ", ".join(
                            f"{n} ({needle!r})" for n, needle in sorted(valid.items())
                        )
                        problems.append(
                            f"{doc.relative_to(root)}:{lineno} ссылается на {rel}:{start}, "
                            f"а там сейчас не опора. Опоры в этом файле: {where}"
                        )

    # Номер в тексте и номер в ссылке должны совпадать — обычная описка при правке.
    for doc in docs:
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            text_refs = {(m.group(1), m.group(2), m.group(3)) for m in REF.finditer(line)}
            url_refs = {(m.group(1), m.group(2), m.group(3)) for m in URL.finditer(line)}
            # То, что нашлось по ссылке, находится и по тексту; сравниваем
            # только остаток, иначе разной выглядела бы каждая строка.
            if url_refs and not url_refs <= text_refs:
                problems.append(
                    f"{doc.relative_to(root)}:{lineno}: номер в тексте и номер в "
                    f"ссылке разошлись — {sorted(text_refs)} против {sorted(url_refs)}"
                )

    # Одна и та же беда ловится и по тексту, и по ссылке — показываем один раз,
    # сохраняя порядок находок.
    problems = list(dict.fromkeys(problems))

    if problems:
        print("Ссылки документации на исходник разошлись с исходником:\n")
        for p in problems:
            print(f"  - {p}")
        print(f"\nПроверено ссылок: {checked}. Бед: {len(problems)}.")
        return 1

    print(f"Ссылки документации на исходник: проверено {checked}, все ведут на опоры.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
