"""Печатает таблицу «что попадает в поля записи» из настоящего прогона пяти языков.

Зачем. Эта таблица в `docs/languages.md` однажды уже стала ложью — и не от
правки самой страницы, а от чужой правки в обработчиках языков. До неё C, C++ и
Elixir писали в поле `a` строку `a=2, b=3`; правка привела все пять к значениям
без имён, проверки это закрепили, а страница продолжала обещать имена. Ни одна
проверка такого не ловит: страница и код не связаны ничем, кроме внимательности.

Как здесь. Таблица не пишется, а печатается — из того же прогона, которым живёт
`tests/test_schema_parity.py`: настоящий обработчик, настоящий компилятор,
настоящий `debug.info`. Между пометками в странице текст принадлежит машине.

    uv run python scripts/schema_facts.py --measure   # прогнать и переписать
    uv run python scripts/schema_facts.py             # сверить

Замер идёт минуты (собираются C, C++ и Elixir) и потому вызывается руками.
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
PAGE = ROOT / "docs" / "languages.md"

MARK = re.compile(
    r"(<!--schema-facts-->)(.*?)(<!--/schema-facts-->)", re.DOTALL
)

#: Как называть языки в таблице.
TITLES = {"python": "Python", "javascript": "JavaScript",
          "c": "C", "cpp": "C++", "elixir": "Elixir"}


def measure() -> dict[str, Any]:
    """Гоняет один и тот же вызов на пяти языках и смотрит, что записалось."""

    from test_schema_parity import _LANGS, _records, _skip_unless_available

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
        entry = next(r for r in recs if r["p"] == "in")
        out[lang] = {"a": entry["a"], "k": entry["k"]}
        print(f"  {lang}: a={entry['a']!r} k={entry['k']!r}")
    return out


def render(facts: dict[str, Any]) -> str:
    rows = ["| язык | поле `a` (по позиции) | поле `k` (именованные) |",
            "|---|---|---|"]
    for lang, title in TITLES.items():
        f = facts.get(lang)
        if f is None or "unavailable" in (f or {}):
            rows.append(f"| {title} | не снято на этой машине | не снято |")
            continue
        a = f"`{f['a']}`" if f["a"] else "пусто"
        k = f"`{f['k']}`" if f["k"] else "пусто"
        rows.append(f"| {title} | {a} | {k} |")
    return "\n" + "\n".join(rows) + "\n"


def apply(table: str) -> bool:
    text = PAGE.read_text(encoding="utf-8")
    if not MARK.search(text):
        raise SystemExit(f"в {PAGE.name} нет пометок <!--schema-facts-->")
    new = MARK.sub(lambda m: m.group(1) + table + m.group(3), text)
    if new == text:
        return False
    PAGE.write_text(new, encoding="utf-8")
    return True


def main() -> int:
    if "--measure" in sys.argv:
        print("== гоняю один вызов на пяти языках ==")
        facts = measure()
        FACTS.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
        changed = apply(render(facts))
        print(f"записано в {FACTS.relative_to(ROOT)}")
        print("страница обновлена" if changed else "страница уже совпадала")
        return 0

    if not FACTS.exists():
        print(f"нет {FACTS.relative_to(ROOT)} — прогоните с --measure")
        return 1
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    want = render(facts)
    got = MARK.search(PAGE.read_text(encoding="utf-8"))
    if got is None:
        print(f"в {PAGE.name} нет пометок <!--schema-facts-->")
        return 1
    if got.group(2) != want:
        print("Таблица полей записи разошлась со снятым:\n")
        print("  в странице:\n" + got.group(2).rstrip())
        print("\n  снято прогоном:\n" + want.rstrip())
        print("\nПочинка: uv run python scripts/schema_facts.py --measure")
        return 1
    print("Таблица полей записи совпадает со снятым прогоном.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
