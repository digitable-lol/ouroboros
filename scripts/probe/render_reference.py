"""Печатает страницу справочника из снятого JSON.

Вход — `docs/mcp-tools.json`, снятый `tool_reference.py` живым разговором с
сервером. Выход — `docs/mcp-tools.md`.

Страница целиком выводится из снятого файла: ни одного описания средства здесь не
написано руками. Поэтому пересказ не может разойтись с тем, что сервер объявляет
на самом деле, — расхождение чинится пересборкой, а не правкой текста.

Запуск (обычно через ``scripts/probe/build-reference.sh``)::

    uv run python scripts/probe/render_reference.py docs/mcp-tools.json > docs/mcp-tools.md
"""
from __future__ import annotations

import json
import sys
from typing import Any

#: Порядок и названия групп. Средство, не попавшее ни в одну, окажется в
#: «Прочее» — то есть новое средство на странице не потеряется, а бросится в
#: глаза как неразобранное.
GROUPS: list[tuple[str, list[str]]] = [
    ("Дописать запись о вызовах", ["wrap_code_snippet", "wrap_file", "wrap_functions"]),
    ("Прочитать записи", ["read_trace", "trace_stats"]),
    ("Черновик", ["create_project", "write_file", "read_file", "list_files",
                  "execute", "finish"]),
    ("C и C++ через clangd", ["lint_file", "symbol_search", "document_symbols",
                              "references", "call_hierarchy", "describe_symbol"]),
]

#: Как называть подсказки поведения по-русски.
HINTS = {
    "readOnlyHint": "только читает",
    "destructiveHint": "перезаписывает то, что было",
    "idempotentHint": "повторный вызов даёт тот же итог",
    "openWorldHint": "трогает что-то за пределами своих доводов",
}


def fence(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def arguments_table(schema: dict[str, Any]) -> str:
    props = schema.get("properties") or {}
    if not props:
        return "_Доводов нет._"
    required = set(schema.get("required") or [])
    rows = ["| довод | тип | обязателен | по умолчанию |", "|---|---|---|---|"]
    for name, spec in props.items():
        kind = spec.get("type")
        if kind is None and "anyOf" in spec:
            kind = " или ".join(
                str(a.get("type")) for a in spec["anyOf"] if a.get("type")
            )
        default = spec.get("default", "—")
        if default is None:
            default = "`null`"
        elif default != "—":
            default = f"`{json.dumps(default, ensure_ascii=False)}`"
        rows.append(
            f"| `{name}` | {kind or '—'} | {'да' if name in required else 'нет'} "
            f"| {default} |"
        )
    return "\n".join(rows)


def hints_line(annotations: dict[str, Any] | None) -> str:
    if not annotations:
        return "_Подсказок поведения сервер не объявил._"
    on = [HINTS[k] for k, v in annotations.items() if v and k in HINTS]
    off = [HINTS[k] for k, v in annotations.items() if not v and k in HINTS]
    parts = []
    if on:
        parts.append("**да:** " + ", ".join(on))
    if off:
        parts.append("**нет:** " + ", ".join(off))
    return "; ".join(parts) if parts else "_Подсказок поведения сервер не объявил._"


def render(doc: dict[str, Any]) -> str:
    by_name = {t["name"]: t for t in doc["tools"]}
    grouped = {n for _, names in GROUPS for n in names}
    leftover = [n for n in by_name if n not in grouped]

    out: list[str] = []
    out.append("---")
    out.append("title: Справочник средств MCP")
    out.append("---")
    out.append("")
    out.append("# Справочник средств MCP")
    out.append("")
    out.append(
        "**Эта страница собрана прогоном, а не написана.** Её печатает "
        "`scripts/probe/render_reference.py` из файла "
        "[`docs/mcp-tools.json`](mcp-tools.json), который снят живым разговором с "
        "сервером: каждое средство здесь сервер объявил сам, и на каждое сделан "
        "настоящий вызов, ответ на который приведён ниже дословно."
    )
    out.append("")
    out.append("Пересобрать:")
    out.append("")
    out.append("```sh\nscripts/probe/build-reference.sh\n```")
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    out.append(f"| сервер | `{doc['server_name']}` версия `{doc['server_version']}` |")
    out.append(f"| правила разговора | `{doc['protocol_version']}` |")
    out.append(f"| чем запускается | `{doc['entry_point']}` |")
    out.append(f"| средств объявлено | **{doc['declared_tool_count']}** |")
    out.append(f"| снято | {doc['taken_at']} |")
    out.append("")
    missing = doc.get("tools_declared_but_not_called") or []
    if missing:
        out.append(
            "**Объявлено, но не вызвано при съёмке:** "
            + ", ".join(f"`{n}`" for n in missing)
            + ". Для них ниже нет настоящего ответа."
        )
        out.append("")
    else:
        out.append(
            "Средств, объявленных но не вызванных при съёмке, нет: настоящий "
            "ответ есть на каждое."
        )
        out.append("")
    out.append(
        "В путях примеров `<work>` — каталог, в котором шла съёмка, `<python>` — "
        "исполняемый файл Python, которым звали. Длинные строки и списки обрезаны, "
        "обрезка помечена в самом значении."
    )
    out.append("")
    if doc.get("instructions"):
        out.append("## Что сервер говорит о себе при подключении")
        out.append("")
        out.append("```\n" + doc["instructions"].strip() + "\n```")
        out.append("")

    for group, names in GROUPS + ([("Прочее", leftover)] if leftover else []):
        out.append(f"## {group}")
        out.append("")
        for name in names:
            t = by_name.get(name)
            if t is None:
                out.append(f"### `{name}`")
                out.append("")
                out.append("**Сервер такого средства не объявил.**")
                out.append("")
                continue
            out.append(f"### `{name}` — {t.get('title') or '(заголовка нет)'}")
            out.append("")
            if t.get("description"):
                out.append(t["description"].strip())
                out.append("")
            out.append(hints_line(t.get("annotations")))
            out.append("")
            out.append("**Доводы**")
            out.append("")
            out.append(arguments_table(t.get("arguments") or {}))
            out.append("")
            out.append("<details><summary>Настоящий вызов и настоящий ответ</summary>")
            out.append("")
            out.append("Вызвали:")
            out.append("")
            out.append(fence(t.get("example_call")))
            out.append("")
            verdict = "сервер ответил отказом" if t.get("example_is_error") else "ответ"
            out.append(f"Получили ({verdict}):")
            out.append("")
            out.append(fence(t.get("example_answer")))
            out.append("")
            out.append("</details>")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "нужен снятый JSON: "
            "uv run python scripts/probe/render_reference.py docs/mcp-tools.json"
        )
    with open(sys.argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
    sys.stdout.write(render(doc))


main()
