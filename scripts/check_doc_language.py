"""Сторож языка документации: английский — умолчание, русский — пара с суффиксом.

Правило дерева одно и читается по имени файла. `README.md` — английский,
`README.ru.md` — русский. Суффикса нет — значит английский, и никакого «ну тут
исторически по-русски» быть не может: имя файла обещает язык, и обещание
проверяется машиной.

Зачем машиной. Перевод дерева делается один раз, а разъезжается постепенно: в
английскую страницу дописывают русский абзац, русскую пару забывают завести,
переключатель языка ставят не на тот файл. Каждая такая беда по отдельности
выглядит мелочью и не видна в обзоре правок — их у страницы сотни строк. Вместе
они за месяц возвращают дерево туда, откуда оно ушло.

Что проверяется:

1. **В файле без суффикса нет кириллицы.** Кроме закрытого списка слов, которые
   стоят там намеренно: подпись переключателя и два имени каталогов, которые
   инструмент носил до переименования (`черновик`, `чистовик` —
   `LEGACY_DRAFT_DIRNAME`/`LEGACY_CLEAN_DIRNAME` в
   `ouroboros/sandbox/project.py`). Это не проза, а строка, которую человек
   видит у себя в файловой системе: старый проект инструмент до сих пор
   подхватывает, и английская страница обязана мочь назвать каталог так, как он
   называется. Именительный падеж и только он — «в черновике» и «из чистовика»
   это уже проза, и сторож на ней краснеет.
2. **У каждой страницы `*.ru.md` есть английская пара.** Русская редакция без
   английской — это и есть «русский по умолчанию» с другой стороны.
3. **Пара объявлена с обеих сторон.** Английская страница открывается строкой
   `**English** · [Русский](имя.ru.md)`, русская — `[English](имя.md) · **Русский**`.
   Ссылка обязана вести на существующий файл: переключатель в пустоту хуже, чем
   его отсутствие.
4. **Числа в описании на flang совпадают с `docs/state.json`.** Файл
   `docs/ouroboros.flang` — русское описание инструмента, которое проверяет
   компилятор flang. Про типы и завершение он судит сам; а вот что языков
   восемь, средств MCP семнадцать и версия та самая — знает только дерево.
5. **Ведомость недоделанного.** Страницы вне доли этой работы, которые пока
   существуют только по-русски, перечислены ниже поимённо с причиной. Ведомость
   умеет только сокращаться: если страница из неё стала английской или исчезла,
   сторож требует вычеркнуть строку. Незаписанная русская страница — отказ.

Запуск::

    uv run python scripts/check_doc_language.py
    uv run python scripts/check_doc_language.py --self-test

`--self-test` — отрицательный контроль. Проверка, которая никогда не краснела,
ничего не значит: самопроверка подсовывает сторожу заведомо испорченные страницы
(английская с русским абзацем, русская без пары, переключатель не на тот файл) и
требует, чтобы он на каждой отказал, а на исправной — промолчал.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Где ищем страницы. Чужое (`node_modules`) и собранное не трогаем.
ROOTS = (".", "docs", "bin", "design", "packaging", "skill", "scripts", "bench")
SKIP_PARTS = {"node_modules", ".venv", "__pycache__", ".git", "runs", "runs_debug",
              "task", "task_debug", "fixtures", "fixtures_debug", "programs", "agents"}

CYR = re.compile(r"[А-Яа-яЁё]+")

#: Кириллица, которая стоит в английской странице намеренно. Список закрытый:
#: каждое слово здесь — либо подпись ссылки на русскую пару, либо настоящее имя
#: на диске. Прозы в нём нет и быть не может.
ALLOWED = {
    "Русский",              # подпись переключателя языка
    # Имена каталогов до переименования. Инструмент их до сих пор читает с
    # диска, поэтому английская страница вправе назвать каталог так, как он
    # называется у человека в файловой системе. Падеж только именительный:
    # склонённое слово — уже проза, а не имя, и здесь его нет намеренно.
    "черновик", "чистовик",
}

#: Переключатель языка: первая строка страницы после шапки.
EN_SWITCH = re.compile(r"^\*\*English\*\* · \[Русский\]\(([^)]+)\)\s*$", re.M)
RU_SWITCH = re.compile(r"^\[English\]\(([^)]+)\) · \*\*Русский\*\*\s*$", re.M)

#: Ведомость: страницы, у которых английской редакции ещё нет. Умеет только
#: сокращаться — сторож требует вычеркнуть строку, как только страница
#: переведена или удалена.
PENDING: dict[str, str] = {
    "skill/SKILL.md":
        "навык для ИИ-агента, 555 строк; переводится вместе с навыком, а не с\n"
        "документацией",
    "design/brief.md":
        "исходное задание заказчика — исторический документ, переписывать нельзя",
    "design/example.md": "разбор способа обмазки по языкам, черновик к заданию",
    "design/skill-draft.md": "черновик навыка, живёт при design/brief.md",
    "bin/README.md": "три скрипта выпуска; доля упаковки, не документации",
    "packaging/asdf/README.md": "доля упаковки",
    "scripts/measure/trace-help/README.md":
        "журнал опыта «помогает ли трасса», 582 строки — запись прогонов, а не\n"
        "страница",
    "scripts/measure/trace-help/scale-up-master-prompt.md": "задание к тому же опыту",
}


def pages() -> list[Path]:
    """Все страницы дерева, кроме чужих и собранных."""

    out: dict[Path, None] = {}
    for r in ROOTS:
        base = ROOT / r
        if not base.is_dir():
            continue
        pattern = "*.md" if r == "." else "**/*.md"
        for p in sorted(base.glob(pattern)):
            if set(p.relative_to(ROOT).parts) & SKIP_PARTS:
                continue
            out[p] = None
    return list(out)


def foreign_words(text: str) -> list[str]:
    """Кириллические слова страницы, которых там быть не должно."""

    return [w for w in CYR.findall(text) if w not in ALLOWED]


def page_problems(rel: str, text: str, exists: set[str]) -> list[str]:
    """Беды одной страницы. `exists` — какие пути в дереве есть.

    Отдельной функцией, а не внутри обхода, ровно затем, чтобы самопроверка
    могла подать сюда выдуманную страницу и убедиться, что сторож на ней
    краснеет.
    """

    problems: list[str] = []
    is_ru = rel.endswith(".ru.md")
    stem = rel[: -len(".ru.md")] if is_ru else rel[: -len(".md")]
    en, ru = f"{stem}.md", f"{stem}.ru.md"
    name_en, name_ru = Path(en).name, Path(ru).name

    if is_ru:
        if en not in exists:
            problems.append(
                f"{rel}: русская редакция без английской. Имя без суффикса значит "
                f"английский — заведите {en} или переименуйте страницу"
            )
        m = RU_SWITCH.search(text)
        if m is None:
            problems.append(
                f"{rel}: нет строки переключателя `[English]({name_en}) · **Русский**`"
            )
        elif m.group(1) != name_en:
            problems.append(
                f"{rel}: переключатель ведёт на {m.group(1)!r}, а пара — {name_en!r}"
            )
        return problems

    words = foreign_words(text)
    if words:
        shown = ", ".join(sorted(set(words))[:8])
        problems.append(
            f"{rel}: кириллица в файле без суффикса ({len(words)} слов: {shown}). "
            f"Русский текст живёт в {ru}, а не здесь"
        )

    if ru in exists:
        m = EN_SWITCH.search(text)
        if m is None:
            problems.append(
                f"{rel}: у страницы есть пара {ru}, а строки переключателя "
                f"`**English** · [Русский]({name_ru})` нет"
            )
        elif m.group(1) != name_ru:
            problems.append(
                f"{rel}: переключатель ведёт на {m.group(1)!r}, а пара — {name_ru!r}"
            )
    return problems


def flang_numbers() -> list[str]:
    """Числа русского описания на flang против `docs/state.json`."""

    spec = ROOT / "docs" / "ouroboros.flang"
    state_file = ROOT / "docs" / "state.json"
    if not spec.exists():
        return [f"нет {spec.relative_to(ROOT)} — русского описания на flang"]
    if not state_file.exists():
        return [f"нет {state_file.relative_to(ROOT)}"]

    text = spec.read_text(encoding="utf-8")
    state = json.loads(state_file.read_text(encoding="utf-8"))

    #: имя функции в описании → ключ в state.json
    claims = {"Языков": "languages", "Средств MCP": "mcp_tools", "Версия": "version"}
    problems: list[str] = []
    for fn, key in claims.items():
        block = re.search(
            r"тотальная функция «" + re.escape(fn) + r"».*?(?=\nтотальная функция |\Z)",
            text, re.DOTALL,
        )
        if block is None:
            problems.append(
                f"docs/ouroboros.flang: нет функции «{fn}» — описание перестало "
                f"утверждать {key}"
            )
            continue
        m = re.search(r"ожидается\s+(\"[^\"]*\"|\S+)", block.group(0))
        if m is None:
            problems.append(
                f"docs/ouroboros.flang: у «{fn}» нет примера с ожидаемым значением — "
                "утверждение без примера компилятор не проверяет"
            )
            continue
        said = m.group(1).strip('"')
        want = str(state.get(key))
        if said != want:
            problems.append(
                f"docs/ouroboros.flang: «{fn}» обещает {said!r}, а в docs/state.json "
                f"{key} = {want!r}"
            )
    return problems


def ledger_problems(seen: dict[str, str]) -> list[str]:
    """Ведомость недоделанного: она обязана сокращаться, а не жить вечно."""

    problems: list[str] = []
    for rel, why in sorted(PENDING.items()):
        if rel not in seen:
            problems.append(
                f"{rel}: числится в ведомости PENDING, а такого файла нет — "
                "вычеркните строку"
            )
            continue
        if not foreign_words(seen[rel]):
            problems.append(
                f"{rel}: числится в ведомости PENDING ({why}), а русского в нём уже "
                "нет — вычеркните строку"
            )
    return problems


def selftest() -> int:
    """Отрицательный контроль: сторож обязан краснеть на порче."""

    exists = {"a.md", "a.ru.md", "solo.ru.md"}
    cases: list[tuple[str, str, str, bool]] = [
        (
            "английская страница с русским абзацем",
            "a.md",
            "**English** · [Русский](a.ru.md)\n\n# A\n\nЭто русский абзац.\n",
            True,
        ),
        (
            "английская страница называет каталог прежним именем",
            "a.md",
            "**English** · [Русский](a.ru.md)\n\n# A\n\n"
            "A draft made before the rename is a directory named черновик.\n",
            False,
        ),
        (
            "английская страница склоняет то же слово — это уже проза",
            "a.md",
            "**English** · [Русский](a.ru.md)\n\n# A\n\nThe draft lives in черновике.\n",
            True,
        ),
        (
            "русская страница без английской пары",
            "solo.ru.md",
            "[English](solo.md) · **Русский**\n\n# Соло\n",
            True,
        ),
        (
            "английская страница без переключателя при живой паре",
            "a.md",
            "# A\n\nPlain English page.\n",
            True,
        ),
        (
            "переключатель ведёт не на ту пару",
            "a.ru.md",
            "[English](other.md) · **Русский**\n\n# А\n",
            True,
        ),
        (
            "исправная пара",
            "a.md",
            "**English** · [Русский](a.ru.md)\n\n# A\n\nPlain English page.\n",
            False,
        ),
        (
            "исправная русская половина пары",
            "a.ru.md",
            "[English](a.md) · **Русский**\n\n# А\n\nРусская страница.\n",
            False,
        ),
    ]

    bad = 0
    for name, rel, text, must_fail in cases:
        got = page_problems(rel, text, exists)
        red = bool(got)
        mark = "✓" if red == must_fail else "✗"
        if red != must_fail:
            bad += 1
        want = "отказ" if must_fail else "молчание"
        print(f"  {mark} {name}: ждали {want}, получили "
              f"{'отказ' if red else 'молчание'}")
        if red != must_fail and got:
            for g in got:
                print(f"      {g}")

    # Живой отрицательный контроль: берём настоящую английскую страницу дерева
    # и портим её в памяти. Сторож обязан заметить.
    live = ROOT / "README.md"
    if live.exists():
        text = live.read_text(encoding="utf-8")
        spoiled = text + "\n\nЭтот абзац подсунут самопроверкой.\n"
        if not page_problems("README.md", spoiled, {"README.md", "README.ru.md"}):
            print("  ✗ живой контроль: испорченный README.md прошёл проверку")
            bad += 1
        else:
            print("  ✓ живой контроль: русский абзац в README.md пойман")
        if page_problems("README.md", text, {"README.md", "README.ru.md"}):
            print("  ✗ живой контроль: настоящий README.md не проходит проверку")
            bad += 1
        else:
            print("  ✓ живой контроль: настоящий README.md проходит")

    if bad:
        print(f"\nСамопроверка: {bad} случаев разошлись с ожиданием.")
        return 1
    print(f"\nСамопроверка: {len(cases) + 2} случаев, все сошлись.")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return selftest()

    found = pages()
    seen = {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in found}
    exists = set(seen)

    problems: list[str] = []
    pairs = 0
    for rel, text in sorted(seen.items()):
        if rel in PENDING:
            continue
        problems += page_problems(rel, text, exists)
        if rel.endswith(".ru.md"):
            pairs += 1

    problems += flang_numbers()
    problems += ledger_problems(seen)

    if problems:
        print("Язык документации разошёлся с именами файлов:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nПравило: имя без суффикса — английский, `.ru.md` — русский.")
        return 1

    print(f"Страниц проверено: {len(seen) - len(PENDING)}, из них пар: {pairs}. "
          f"Кириллицы в файлах без суффикса нет.")
    if PENDING:
        print(f"В ведомости PENDING ещё {len(PENDING)} страниц без английской "
              f"редакции — они вне доли этой работы и названы поимённо в сторо́же.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
