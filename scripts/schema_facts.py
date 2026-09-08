"""Печатает таблицу «что попадает в поля записи» из настоящего прогона всех языков.

Зачем. Эта таблица в `docs/languages.md` однажды уже стала ложью — и не от
правки самой страницы, а от чужой правки в обработчиках языков. До неё C, C++ и
Elixir писали в поле `a` строку `a=2, b=3`; правка привела всех к значениям
без имён, проверки это закрепили, а страница продолжала обещать имена. Ни одна
проверка такого не ловит: страница и код не связаны ничем, кроме внимательности.

Как здесь. Таблица не пишется, а печатается — из того же прогона, которым живёт
`tests/test_schema_parity.py`: настоящий обработчик, настоящий компилятор,
настоящий `debug.info`. Между пометками в странице текст принадлежит машине.

    uv run python scripts/schema_facts.py --measure   # прогнать и переписать
    uv run python scripts/schema_facts.py             # сверить

Замер идёт минуты (собираются C, C++, Elixir и Go) и потому вызывается руками.
Сверка стоит секунды и висит в `scripts/qa.sh`.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

FACTS = ROOT / "docs" / "schema-facts.json"
#: Обе редакции страницы: имя без суффикса — английская, с `.ru` — русская. Таблицу
#: печатает машина, поэтому подписи столбцов у каждой редакции свои и хранятся
#: здесь, а не в странице: иначе перевод страницы молча разошёлся бы со снятым.
PAGES = {
    ROOT / "docs" / "languages.md": {
        "head": "| language | field `a` (positional) | field `k` (keyword) |",
        "empty": "empty",
        "unavailable": "not measured on this machine",
        "unavailable_short": "not measured",
    },
    ROOT / "docs" / "languages.ru.md": {
        "head": "| язык | поле `a` (по позиции) | поле `k` (именованные) |",
        "empty": "пусто",
        "unavailable": "не снято на этой машине",
        "unavailable_short": "не снято",
    },
}

MARK = re.compile(
    r"(<!--schema-facts-->)(.*?)(<!--/schema-facts-->)", re.DOTALL
)

#: Как называть языки в таблице.
TITLES = {"python": "Python", "javascript": "JavaScript",
          "c": "C", "cpp": "C++", "elixir": "Elixir",
          "go": "Go", "java": "Java", "csharp": "C#"}


def measure() -> dict[str, Any]:
    """Гоняет один и тот же вызов на всех языках и смотрит, что записалось."""

    from test_schema_parity import _ADD, _LANGS, _records, _skip_unless_available

    out: dict[str, Any] = {}
    for lang in _LANGS:
        try:
            _skip_unless_available(lang)
        except Exception as e:  # pytest.skip.Exception и подобные
            out[lang] = {"unavailable": str(e)[:120]}
            print(f"  {lang}: пропущен — {e}")
            continue
        with tempfile.TemporaryDirectory(dir="/srv/tmp") as td:
            recs = _records(lang, Path(td))
        # The record for `add(2, 3)` specifically. Java has no file level to
        # append a driver to, so its `main` is instrumented along with the rest
        # and is the FIRST `in` line — taking that one would print `main`'s own
        # arguments into a table that says it is about `add`.
        entry = next(r for r in recs if r["p"] == "in" and r["fn"] == _ADD[lang])
        out[lang] = {"a": entry["a"], "k": entry["k"]}
        print(f"  {lang}: a={entry['a']!r} k={entry['k']!r}")
    return out


def render(facts: dict[str, Any], words: dict[str, str]) -> str:
    rows = [words["head"], "|---|---|---|"]
    for lang, title in TITLES.items():
        f = facts.get(lang)
        if f is None or "unavailable" in (f or {}):
            rows.append(
                f"| {title} | {words['unavailable']} | {words['unavailable_short']} |")
            continue
        a = f"`{f['a']}`" if f["a"] else words["empty"]
        k = f"`{f['k']}`" if f["k"] else words["empty"]
        rows.append(f"| {title} | {a} | {k} |")
    return "\n" + "\n".join(rows) + "\n"


def apply(facts: dict[str, Any]) -> bool:
    changed = False
    for page, words in PAGES.items():
        text = page.read_text(encoding="utf-8")
        if not MARK.search(text):
            raise SystemExit(f"в {page.name} нет пометок <!--schema-facts-->")
        table = render(facts, words)
        new = MARK.sub(lambda m: m.group(1) + table + m.group(3), text)
        if new != text:
            page.write_text(new, encoding="utf-8")
            changed = True
    return changed


def main() -> int:
    if "--measure" in sys.argv:
        print("== гоняю один вызов на всех языках ==")
        facts = measure()
        FACTS.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
        changed = apply(facts)
        print(f"записано в {FACTS.relative_to(ROOT)}")
        print("страница обновлена" if changed else "страница уже совпадала")
        return 0

    if not FACTS.exists():
        print(f"нет {FACTS.relative_to(ROOT)} — прогоните с --measure")
        return 1
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    for page, words in PAGES.items():
        want = render(facts, words)
        got = MARK.search(page.read_text(encoding="utf-8"))
        if got is None:
            print(f"в {page.name} нет пометок <!--schema-facts-->")
            return 1
        if got.group(2) != want:
            print(f"Таблица полей записи в {page.name} разошлась со снятым:\n")
            print("  в странице:\n" + got.group(2).rstrip())
            print("\n  снято прогоном:\n" + want.rstrip())
            print("\nПочинка: uv run python scripts/schema_facts.py --measure")
            return 1
    print(f"Таблица полей записи совпадает со снятым прогоном в {len(PAGES)} редакциях.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
