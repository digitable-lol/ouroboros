"""Проверяет, что живой сайт отдаёт нынешнее дерево, а не вчерашнее.

Зачем. Сборка страниц идёт на стороне GitHub и об отказе никому не сообщает.
29 августа `docs/_config.yml` перестал разбираться как YAML — из-за незакавыченного
значения с двоеточием, — и сборка падала сутки подряд. Всё это время сайт отдавал
предыдущий успешный слепок: обещал пять языков при шести в дереве, выпуск 0.3.0
при 0.4.0 и отвечал 404 на `measurements.html`, страницу из оглавления. Заметили
случайно, попутно.

Отсюда правило: сделано — это когда видно снаружи. Не «сборка зелёная», не
«коммит в ветке». Эта проверка спрашивает сам сайт по HTTP и сравнивает ответ с
деревом.

Что проверяется:

* каждая страница из `docs/` отдаётся живым сайтом с кодом 200 — ровно это
  ловит `measurements.html`, который лежал в дереве и не существовал на сайте;
* `state.json`, который Jekyll кладёт на сайт как есть, совпадает с
  `docs/state.json` в дереве. Точный признак свежести: разошлись — значит сайт
  собран из другого коммита;
* числа в пометках `<!--state:...-->` на живой `index.html` равны числам из
  `docs/state.json`. То есть страница, которую читает человек, показывает
  нынешние числа, а не только файл рядом с ней.

Чего проверка НЕ делает: не судит по состоянию сборки. Сборка бывает зелёной, а
страница всё равно старой; здесь спрашивается только сам сайт.

Коды возврата: 0 — сайт совпадает с деревом; 1 — разошёлся; 2 — до сайта не
достучались (проверка не состоялась, это не то же самое, что «всё хорошо»).

Запуск::

    uv run python scripts/check_pages_live.py                      # спросить сейчас
    uv run python scripts/check_pages_live.py --retries 10 --wait 30

Доводы `--retries` и `--wait` нужны сразу после отправки в ветку: сборка на
стороне GitHub идёт с полминуты до нескольких минут, и до её конца сайт по праву
отдаёт прежнее. Ждать имеет смысл только там; при обычном запросе «а что сейчас
на сайте» ответ нужен сразу, поэтому по умолчанию повторов нет.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Настройка сборки — из неё же берётся адрес сайта, чтобы он не был вписан
#: здесь второй раз и не разошёлся с настоящим.
CONFIG = ROOT / "docs" / "_config.yml"

STATE_FILE = ROOT / "docs" / "state.json"

#: `ключ: значение` верхнего уровня в настройке сборки.
CONFIG_FIELD = re.compile(r"^([a-z_]+):\s*(.*?)\s*$")

#: Пометка с числом состояния — та же, что и в scripts/state_numbers.py.
MARK = re.compile(r"<!--state:([a-z_]+)-->(.*?)<!--/state-->", re.DOTALL)

TIMEOUT = 20


def site_root() -> str:
    """Адрес сайта, собранный из `url` и `baseurl` настройки сборки."""

    fields: dict[str, str] = {}
    for line in CONFIG.read_text(encoding="utf-8").splitlines():
        m = CONFIG_FIELD.match(line)
        if m:
            fields[m.group(1)] = m.group(2).strip("\"'")
    url = fields.get("url", "").rstrip("/")
    baseurl = fields.get("baseurl", "").strip("/")
    if not url:
        raise SystemExit(f"{CONFIG}: нет поля url — неоткуда взять адрес сайта")
    return f"{url}/{baseurl}" if baseurl else url


def fetch(url: str) -> tuple[int, str]:
    """Забирает страницу. Возвращает код ответа и тело."""

    request = urllib.request.Request(url, headers={"User-Agent": "ouroboros-pages-check"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def live_pages() -> list[str]:
    """Адреса, под которыми страницы из `docs/` должны отдаваться сайтом."""

    out = []
    for path in sorted(ROOT.glob("docs/**/*.md")):
        rel = path.relative_to(ROOT / "docs")
        out.append(str(rel.with_suffix(".html")))
    return out


def check() -> int:
    root = site_root()
    bad: list[str] = []
    checked = 0

    # Сначала одна страница — чтобы отличить «сайт отстал» от «сети нет».
    try:
        fetch(f"{root}/index.html")
    except (urllib.error.URLError, OSError) as e:
        print(
            f"До сайта не достучались ({root}): {e}.\n"
            "Проверка НЕ состоялась. Это не то же самое, что «сайт совпадает с деревом».",
            file=sys.stderr,
        )
        return 2

    # 1. Каждая страница из дерева отдаётся живым сайтом.
    for page in live_pages():
        code, _ = fetch(f"{root}/{page}")
        checked += 1
        if code != 200:
            bad.append(f"  - {page}: сайт отвечает {code}, а страница есть в дереве")

    # 2. state.json на сайте совпадает с деревом — точный признак свежести.
    tree_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    code, body = fetch(f"{root}/state.json")
    checked += 1
    if code != 200:
        bad.append(f"  - state.json: сайт отвечает {code}")
    else:
        try:
            live_state = json.loads(body)
        except json.JSONDecodeError as e:
            bad.append(f"  - state.json: сайт отдал не JSON ({e})")
            live_state = None
        if live_state is not None and live_state != tree_state:
            for key in sorted(set(tree_state) | set(live_state)):
                mine, theirs = tree_state.get(key), live_state.get(key)
                if mine != theirs:
                    bad.append(
                        f"  - state.json, поле {key!r}: на сайте {theirs!r}, "
                        f"в дереве {mine!r} — сайт собран не из нынешнего дерева"
                    )

    # 3. Числа в пометках на живой index.html равны числам из дерева.
    code, body = fetch(f"{root}/index.html")
    checked += 1
    if code != 200:
        bad.append(f"  - index.html: сайт отвечает {code}")
    else:
        for key, value in MARK.findall(body):
            expected = tree_state.get(key)
            if expected is not None and str(expected) != value.strip():
                bad.append(
                    f"  - index.html, пометка {key!r}: сайт показывает {value.strip()!r}, "
                    f"в дереве {str(expected)!r}"
                )

    if bad:
        print(f"Живой сайт разошёлся с деревом ({root}):\n", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        print(
            f"\nПроверено запросов: {checked}. Расхождений: {len(bad)}.\n"
            "Смотрите прогоны выкладки: gh run list -R digitable-lol/ouroboros",
            file=sys.stderr,
        )
        return 1

    print(f"Живой сайт отдаёт нынешнее дерево: запросов {checked}, расхождений нет.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--retries", type=int, default=0,
                        help="сколько раз переспросить, пока сайт не догонит дерево")
    parser.add_argument("--wait", type=int, default=30,
                        help="сколько секунд ждать между переспросами")
    args = parser.parse_args()

    for attempt in range(args.retries + 1):
        status = check()
        if status == 0 or attempt == args.retries:
            return status
        print(f"\nЖду {args.wait} с и спрашиваю снова "
              f"(попытка {attempt + 2} из {args.retries + 1}).\n")
        time.sleep(args.wait)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
