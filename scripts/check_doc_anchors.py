"""Проверяет страницы документации: ссылки между ними и шапку каждой.

Зачем. `scripts/check_doc_links.py` сторожит ссылки на СТРОКИ ИСХОДНИКА. За
ссылками между самими страницами не следил никто, а ломаются они тем же тихим
способом: раздел переименовали — ссылка на него осталась и продолжает выглядеть
исправной. При сборке страниц Jekyll на такое не ругается, читатель просто
попадает в начало страницы вместо нужного места.

Что проверяется:

* ссылка на файл (`limits.md`, `../SPEC.md`) — файл существует;
* ссылка с меткой (`limits.md#где-обмазка-меняет-поведение-программы`) — в том
  файле есть заголовок, дающий такую метку;
* шапка страницы (то, что между двумя `---` в начале) — разбирается как YAML.
  Ловится самая частая беда: значение без кавычек, внутри которого стоит
  двоеточие с пробелом. Для страницы это значит «заголовок пропал», для файла
  навыка — что навык не загрузится вовсе, и ни там, ни там ошибки не видно;
* `docs/_config.yml` — по тому же правилу. Файл другой, беда та же, и мимо она
  прошла именно потому, что проверка стояла только на шапках: 29 августа
  незакавыченное `description:` с двоеточием остановило сборку страниц на сутки,
  и всё это время сайт молча отдавал предыдущий слепок.

Метка вычисляется так же, как её делает kramdown, на котором собран сайт:
заголовок переводится в строчные буквы, обратные кавычки и выделение снимаются,
знаки препинания выбрасываются, пробелы становятся дефисами. Повторяющиеся
метки получают номер (`-1`, `-2`), как и у kramdown.

Чего проверка НЕ делает: не ходит по внешним ссылкам (`http://`, `https://`) —
для этого нужна сеть, а гейт должен работать без неё.

Запуск: uv run python scripts/check_doc_anchors.py   (из корня хранилища)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Что считаем документацией.
GLOBS = ("docs/**/*.md", "*.md", "skill/*.md")

#: `[текст](цель)` — цель без пробелов.
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

#: Заголовок Markdown.
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

#: Строка вида `ключ: значение` в шапке страницы или в настройке сборки.
FRONT_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*): (.*)$")

#: Настройка, по которой GitHub собирает сайт. Ломается так же тихо, как шапка.
CONFIG = ROOT / "docs" / "_config.yml"


def anchors(path: Path) -> set[str]:
    """Метки, которые kramdown сделает из заголовков этого файла."""

    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = HEADING.match(line)
        if not m:
            continue
        text = re.sub(r"`([^`]*)`", r"\1", m.group(2))
        text = re.sub(r"\*\*?([^*]*)\*\*?", r"\1", text)
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"[^\w\s\-]", "", text.lower(), flags=re.UNICODE)
        anchor = text.strip().replace(" ", "-")
        base, n = anchor, 1
        while anchor in out:
            anchor = f"{base}-{n}"
            n += 1
        out.add(anchor)
    return out


def front_matter_problems(path: Path) -> list[str]:
    """Беды в шапке страницы, которые молча ломают разбор YAML.

    Проверяется одна, зато самая частая: значение без кавычек, внутри которого
    стоит двоеточие с пробелом. YAML читает такое как вложенное отображение и
    отказывается разбирать всю шапку. Для страницы это значит «нет заголовка»,
    а для файла навыка — что навык не загрузится вовсе, и ни там, ни там ошибки
    не видно: ровно на этом сломалась шапка skill/SKILL.md.

    Своей проверкой, а не через YAML: тянуть постороннюю библиотеку ради одной
    строки не стоит, а эта беда ловится правилом в три строки.
    """

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return []
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return [f"{path}: шапка открыта, но не закрыта"]

    return unquoted_colon_problems(path, parts[1], first_lineno=2, what="шапку")


def config_problems() -> list[str]:
    """Та же беда в `docs/_config.yml` — настройке, по которой собирается сайт.

    Правило то же, что и для шапки страницы, а файл другой, и ровно поэтому
    беда прошла мимо: проверка шапок уже стояла, когда 29 августа незакавыченное
    `description:` с двоеточием остановило сборку страниц на сутки. Здесь у
    поломки цена выше: не «у страницы пропал заголовок», а весь сайт замирает на
    предыдущем слепке и продолжает отдавать вчерашний день без единой жалобы.
    """

    if not CONFIG.exists():
        return []
    return unquoted_colon_problems(CONFIG, CONFIG.read_text(encoding="utf-8"),
                                   first_lineno=1, what="настройку сборки")


def unquoted_colon_problems(path: Path, text: str, first_lineno: int, what: str) -> list[str]:
    """Значения без кавычек, внутри которых стоит двоеточие с пробелом.

    YAML читает такое как вложенное отображение и отказывается разбирать файл
    целиком. Своей проверкой, а не через YAML: тянуть постороннюю библиотеку
    ради одного правила не стоит, а эта беда ловится тремя строками.
    """

    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), first_lineno):
        m = FRONT_FIELD.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if not value or value[0] in "\"'[{|>":
            continue  # в кавычках, список, отображение или блок — разберётся
        if ": " in value:
            problems.append(
                f"{path}:{lineno}: значение поля {key!r} не в кавычках и содержит "
                f"двоеточие с пробелом — YAML такую {what} не разберёт; возьмите "
                "значение в двойные кавычки"
            )
    return problems


def pages() -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file():
                seen[p] = None
    return list(seen)


def main() -> int:
    cache: dict[Path, set[str]] = {}
    bad: list[str] = []
    checked = 0

    for problem in config_problems():
        bad.append(f"  - {problem.replace(str(ROOT) + '/', '')}")

    for page in pages():
        for problem in front_matter_problems(page):
            bad.append(f"  - {problem.replace(str(ROOT) + '/', '')}")

    for page in pages():
        rel = page.relative_to(ROOT)
        for target in LINK.findall(page.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#!")):
                continue
            path_part, _, fragment = target.partition("#")
            checked += 1

            if path_part:
                dest = (page.parent / path_part).resolve()
                if not dest.exists():
                    bad.append(f"  - {rel}: нет файла {path_part}")
                    continue
            else:
                dest = page  # ссылка внутрь той же страницы

            if fragment and dest.suffix == ".md":
                if dest not in cache:
                    cache[dest] = anchors(dest)
                if fragment not in cache[dest]:
                    where = dest.relative_to(ROOT)
                    bad.append(f"  - {rel}: в {where} нет заголовка с меткой #{fragment}")

    if bad:
        print("Беды в страницах документации:\n", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        print(f"\nПроверено ссылок: {checked}. Бед: {len(bad)}.", file=sys.stderr)
        return 1

    print(f"Страницы документации: настройка сборки и шапки разбираются, "
          f"ссылок проверено {checked} — все ведут в место.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
