"""Печатает страницу справочника из снятого JSON — в двух редакциях.

Вход — `docs/mcp-tools.json`, снятый `tool_reference.py` живым разговором с
сервером. Выход — `docs/mcp-tools.md` (английская) или `docs/mcp-tools.ru.md`
(русская), по ключу `--lang`.

Страница целиком выводится из снятого файла: ни одного описания средства здесь не
написано руками. Поэтому пересказ не может разойтись с тем, что сервер объявляет
на самом деле, — расхождение чинится пересборкой, а не правкой текста.

Двумя редакциями, а не одной, по той же причине. Правило дерева — английский по
умолчанию, русский парой с суффиксом `.ru`; страница, которую печатает машина,
подчиняется ему так же, как написанная руками. Слова обеих редакций лежат ниже в
одной таблице: перевести страницу можно только здесь, и обе редакции всегда
собраны из одного и того же снятого JSON.

Запуск (обычно через ``scripts/probe/build-reference.sh``)::

    R=scripts/probe/render_reference.py
    uv run python $R docs/mcp-tools.json > docs/mcp-tools.md
    uv run python $R docs/mcp-tools.json --lang ru > docs/mcp-tools.ru.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: Порядок групп и какие средства в какую попадают. Средство, не попавшее ни в
#: одну, окажется в последней — то есть новое средство на странице не потеряется,
#: а бросится в глаза как неразобранное.
GROUPS: list[tuple[str, list[str]]] = [
    ("instrument", ["wrap_code_snippet", "wrap_file", "wrap_functions"]),
    ("read", ["read_trace", "trace_stats"]),
    ("draft", ["create_project", "write_file", "read_file", "list_files",
               "execute", "finish"]),
    ("clangd", ["lint_file", "symbol_search", "document_symbols",
                "references", "call_hierarchy", "describe_symbol"]),
]

#: Все слова страницы, по редакциям. Ключи те же, значения разные — и это
#: единственное место, где редакции отличаются.
WORDS: dict[str, dict[str, Any]] = {
    "en": {
        "title": "MCP tool reference",
        "switch": "**English** · [Русский](mcp-tools.ru.md)",
        "intro": (
            "**This page is recorded, not written.** "
            "`scripts/probe/render_reference.py` prints it from "
            "[`docs/mcp-tools.json`](mcp-tools.json), which is captured in a live "
            "conversation with the server: every tool here was declared by the "
            "server itself, every one of them was actually called, and the answer "
            "is quoted below word for word."
        ),
        "rebuild": "Rebuild:",
        "server": "server",
        "server_value": "`{name}` version `{version}`",
        "protocol": "protocol",
        "entry": "started by",
        "declared": "tools declared",
        "taken": "captured",
        "missing": (
            "**Declared but not called during capture:** {names}. "
            "There is no real answer for them below."
        ),
        "nothing_missing": (
            "No tool was declared and left uncalled: every one of them has a real "
            "answer."
        ),
        "paths": (
            "In the example paths `<work>` is the directory the capture ran in and "
            "`<python>` is the Python executable it was called with. Long strings "
            "and lists are trimmed, and the trimming is marked inside the value "
            "itself."
        ),
        "instructions": "## What the server says about itself on connect",
        "groups": {
            "instrument": "Add call logging",
            "read": "Read the records",
            "draft": "Draft workspace",
            "clangd": "C and C++ through clangd",
            "other": "Other",
        },
        "hints": {
            "readOnlyHint": "reads only",
            "destructiveHint": "overwrites what was there",
            "idempotentHint": "calling again gives the same result",
            "openWorldHint": "touches something beyond its own arguments",
        },
        "hints_yes": "**yes:**",
        "hints_no": "**no:**",
        "no_hints": "_The server declared no behaviour hints._",
        "no_args": "_No arguments._",
        "args_head": "| argument | type | required | default |",
        "args_title": "**Arguments**",
        "yes": "yes",
        "no": "no",
        "or": " or ",
        "not_declared": "**The server did not declare this tool.**",
        "no_title": "(no title)",
        "details": "<details><summary>The real call and the real answer</summary>",
        "called": "Called:",
        "got": "Got ({verdict}):",
        "verdict_error": "the server answered with a refusal",
        "verdict_ok": "answer",
    },
    "ru": {
        "title": "Справочник средств MCP",
        "switch": "[English](mcp-tools.md) · **Русский**",
        "intro": (
            "**Эта страница собрана прогоном, а не написана.** Её печатает "
            "`scripts/probe/render_reference.py` из файла "
            "[`docs/mcp-tools.json`](mcp-tools.json), который снят живым разговором с "
            "сервером: каждое средство здесь сервер объявил сам, и на каждое сделан "
            "настоящий вызов, ответ на который приведён ниже дословно."
        ),
        "rebuild": "Пересобрать:",
        "server": "сервер",
        "server_value": "`{name}` версия `{version}`",
        "protocol": "правила разговора",
        "entry": "чем запускается",
        "declared": "средств объявлено",
        "taken": "снято",
        "missing": (
            "**Объявлено, но не вызвано при съёмке:** {names}. "
            "Для них ниже нет настоящего ответа."
        ),
        "nothing_missing": (
            "Средств, объявленных но не вызванных при съёмке, нет: настоящий "
            "ответ есть на каждое."
        ),
        "paths": (
            "В путях примеров `<work>` — каталог, в котором шла съёмка, `<python>` — "
            "исполняемый файл Python, которым звали. Длинные строки и списки обрезаны, "
            "обрезка помечена в самом значении."
        ),
        "instructions": "## Что сервер говорит о себе при подключении",
        "groups": {
            "instrument": "Дописать запись о вызовах",
            "read": "Прочитать записи",
            "draft": "Черновик",
            "clangd": "C и C++ через clangd",
            "other": "Прочее",
        },
        "hints": {
            "readOnlyHint": "только читает",
            "destructiveHint": "перезаписывает то, что было",
            "idempotentHint": "повторный вызов даёт тот же итог",
            "openWorldHint": "трогает что-то за пределами своих доводов",
        },
        "hints_yes": "**да:**",
        "hints_no": "**нет:**",
        "no_hints": "_Подсказок поведения сервер не объявил._",
        "no_args": "_Доводов нет._",
        "args_head": "| довод | тип | обязателен | по умолчанию |",
        "args_title": "**Доводы**",
        "yes": "да",
        "no": "нет",
        "or": " или ",
        "not_declared": "**Сервер такого средства не объявил.**",
        "no_title": "(заголовка нет)",
        "details": "<details><summary>Настоящий вызов и настоящий ответ</summary>",
        "called": "Вызвали:",
        "got": "Получили ({verdict}):",
        "verdict_error": "сервер ответил отказом",
        "verdict_ok": "ответ",
    },
}


def fence(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def arguments_table(schema: dict[str, Any], w: dict[str, Any]) -> str:
    props = schema.get("properties") or {}
    if not props:
        return str(w["no_args"])
    required = set(schema.get("required") or [])
    rows = [w["args_head"], "|---|---|---|---|"]
    for name, spec in props.items():
        kind = spec.get("type")
        if kind is None and "anyOf" in spec:
            kind = w["or"].join(
                str(a.get("type")) for a in spec["anyOf"] if a.get("type")
            )
        default = spec.get("default", "—")
        if default is None:
            default = "`null`"
        elif default != "—":
            default = f"`{json.dumps(default, ensure_ascii=False)}`"
        rows.append(
            f"| `{name}` | {kind or '—'} | {w['yes'] if name in required else w['no']} "
            f"| {default} |"
        )
    return "\n".join(rows)


def hints_line(annotations: dict[str, Any] | None, w: dict[str, Any]) -> str:
    if not annotations:
        return str(w["no_hints"])
    hints = w["hints"]
    on = [hints[k] for k, v in annotations.items() if v and k in hints]
    off = [hints[k] for k, v in annotations.items() if not v and k in hints]
    parts = []
    if on:
        parts.append(w["hints_yes"] + " " + ", ".join(on))
    if off:
        parts.append(w["hints_no"] + " " + ", ".join(off))
    return "; ".join(parts) if parts else w["no_hints"]


def render(doc: dict[str, Any], lang: str = "en") -> str:
    w = WORDS[lang]
    by_name = {t["name"]: t for t in doc["tools"]}
    grouped = {n for _, names in GROUPS for n in names}
    leftover = [n for n in by_name if n not in grouped]

    out: list[str] = []
    out.append("---")
    out.append(f"title: {w['title']}")
    out.append("---")
    out.append("")
    out.append(w["switch"])
    out.append("")
    out.append(f"# {w['title']}")
    out.append("")
    out.append(w["intro"])
    out.append("")
    out.append(w["rebuild"])
    out.append("")
    out.append("```sh\nscripts/probe/build-reference.sh\n```")
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    value = w["server_value"].format(name=doc["server_name"],
                                     version=doc["server_version"])
    out.append(f"| {w['server']} | {value} |")
    out.append(f"| {w['protocol']} | `{doc['protocol_version']}` |")
    out.append(f"| {w['entry']} | `{doc['entry_point']}` |")
    out.append(f"| {w['declared']} | **{doc['declared_tool_count']}** |")
    out.append(f"| {w['taken']} | {doc['taken_at']} |")
    out.append("")
    missing = doc.get("tools_declared_but_not_called") or []
    if missing:
        out.append(w["missing"].format(
            names=", ".join(f"`{n}`" for n in missing)))
    else:
        out.append(w["nothing_missing"])
    out.append("")
    out.append(w["paths"])
    out.append("")
    if doc.get("instructions"):
        out.append(w["instructions"])
        out.append("")
        out.append("```\n" + doc["instructions"].strip() + "\n```")
        out.append("")

    order = GROUPS + ([("other", leftover)] if leftover else [])
    for key, names in order:
        out.append(f"## {w['groups'][key]}")
        out.append("")
        for name in names:
            t = by_name.get(name)
            if t is None:
                out.append(f"### `{name}`")
                out.append("")
                out.append(w["not_declared"])
                out.append("")
                continue
            out.append(f"### `{name}` — {t.get('title') or w['no_title']}")
            out.append("")
            if t.get("description"):
                out.append(t["description"].strip())
                out.append("")
            out.append(hints_line(t.get("annotations"), w))
            out.append("")
            out.append(w["args_title"])
            out.append("")
            out.append(arguments_table(t.get("arguments") or {}, w))
            out.append("")
            out.append(w["details"])
            out.append("")
            out.append(w["called"])
            out.append("")
            out.append(fence(t.get("example_call")))
            out.append("")
            verdict = w["verdict_error"] if t.get("example_is_error") else w["verdict_ok"]
            out.append(w["got"].format(verdict=verdict))
            out.append("")
            out.append(fence(t.get("example_answer")))
            out.append("")
            out.append("</details>")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    args = list(sys.argv[1:])
    lang = "en"
    if "--lang" in args:
        i = args.index("--lang")
        lang = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if lang not in WORDS:
        raise SystemExit(f"--lang: жду одно из {', '.join(WORDS)}, дано {lang!r}")
    if not args:
        raise SystemExit(
            "нужен снятый JSON: "
            "uv run python scripts/probe/render_reference.py docs/mcp-tools.json"
        )
    doc = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    sys.stdout.write(render(doc, lang))


main()
