"""Собирает справочник средств сервера MCP живым разговором по стандартному
вводу-выводу: объявление (имя, заголовок, описание, схема доводов, схема ответа,
подсказки поведения) плюс настоящий ответ на настоящий вызов каждого средства.

Ничего не берётся чтением исходников — только то, что сервер сказал сам.

Запуск (обычно через ``scripts/probe/build-reference.sh``)::

    uv run python scripts/probe/tool_reference.py <рабочий-каталог> > docs/mcp-tools.json

Рабочий каталог создаётся заново на каждом прогоне: в нём заводится маленький
набор образцов (файл на Python, файл на C, готовый ``debug.info``), по которому
зовутся все семнадцать средств.

Пути в выводе заменяются на ``<work>`` и ``<python>``, чтобы два прогона на
разных машинах давали один и тот же текст и разница в ``git diff`` показывала
изменение сервера, а не каталога, в котором его снимали.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

#: Длиннее этого строки в примерах ответов обрезаются — справочник читают глазами.
_MAX_STR = 220


def sample_calls(root: Path) -> dict[str, dict[str, Any]]:
    """Готовит образцы и возвращает вызов для каждого средства."""

    base = str(root / "site")
    py = root / "m.py"
    py.write_text("def square(n):\n    return n * n\n", encoding="utf-8")
    py2 = root / "m2.py"
    py2.write_text("def square(n):\n    return n * n\n", encoding="utf-8")
    trace = root / "debug.info"
    trace.write_text(
        json.dumps({"p": "in", "t": "2026-06-15T10:00:00.001", "id": "abc",
                    "fn": "square", "a": "6", "k": ""}) + "\n"
        + json.dumps({"p": "out", "id": "abc", "fn": "square", "r": "36",
                      "d": 0.000012}) + "\n", encoding="utf-8")
    lib = root / "lib.c"
    lib.write_text("int ouro_helper(int x) { return x + 1; }\n", encoding="utf-8")
    (root / "compile_commands.json").write_text(
        json.dumps([{"directory": str(root), "command": "clang -c lib.c",
                     "file": str(lib)}]), encoding="utf-8")
    return {
        "create_project": {"base": base},
        "write_file": {"base": base, "rel_path": "main.py",
                       "content": "def square(n):\n    return n * n\n\nprint(square(6))\n"},
        "read_file": {"base": base, "rel_path": "main.py"},
        "list_files": {"base": base},
        "execute": {"base": base, "command": [sys.executable, "main.py"]},
        "finish": {"base": base},
        "wrap_code_snippet": {"code": "def square(n):\n    return n * n\n",
                              "language": "python"},
        "wrap_file": {"path": str(py)},
        "wrap_functions": {"path": str(py2), "functions": ["square"]},
        "read_trace": {"path": str(trace)},
        "trace_stats": {"path": str(trace)},
        "lint_file": {"path": str(lib)},
        "symbol_search": {"query": "ouro_helper", "root": str(root),
                          "compile_commands_dir": str(root)},
        "document_symbols": {"path": str(lib)},
        "references": {"path": str(lib), "symbol": "ouro_helper",
                       "compile_commands_dir": str(root)},
        "call_hierarchy": {"path": str(lib), "symbol": "ouro_helper",
                           "direction": "incoming", "compile_commands_dir": str(root)},
        "describe_symbol": {"path": str(lib), "symbol": "ouro_helper",
                            "compile_commands_dir": str(root)},
    }


def stabilise(value: Any, root: str, python: str) -> Any:
    """Заменяет пути прогона на постоянные обозначения.

    Без этого справочник, пересобранный в другом каталоге, отличался бы от
    прежнего в каждой строке с путём, и разница перестала бы что-либо значить.
    """

    if isinstance(value, str):
        return value.replace(root, "<work>").replace(python, "<python>")
    if isinstance(value, list):
        return [stabilise(v, root, python) for v in value]
    if isinstance(value, dict):
        return {k: stabilise(v, root, python) for k, v in value.items()}
    return value


def trim(value: Any) -> Any:
    """Обрезает длинные строки и списки, чтобы пример ответа читался."""

    if isinstance(value, str):
        if len(value) <= _MAX_STR:
            return value
        return value[:_MAX_STR] + f"... (+{len(value) - _MAX_STR} символов)"
    if isinstance(value, list):
        head = [trim(v) for v in value[:2]]
        return head + ([f"... ещё {len(value) - 2}"] if len(value) > 2 else [])
    if isinstance(value, dict):
        return {k: trim(v) for k, v in value.items()}
    return value


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "нужен рабочий каталог: "
            "uv run python scripts/probe/tool_reference.py <каталог>"
        )
    root = Path(sys.argv[1]).resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    calls = sample_calls(root)
    command = os.environ.get("OUROBOROS_MCP_COMMAND", "ouroboros-mcp")
    params = StdioServerParameters(command=command, args=[], env={**os.environ})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            listed = await session.list_tools()
            declared = {t.name: t for t in listed.tools}
            missing = sorted(set(declared) - set(calls))
            entries = []
            for name in calls:
                if name not in declared:
                    entries.append({"name": name, "error": "сервер такого не объявил"})
                    continue
                t = declared[name]
                res = await session.call_tool(name, calls[name])
                entries.append({
                    "name": name,
                    "title": t.title,
                    "description": t.description,
                    "annotations": t.annotations.model_dump(exclude_none=True)
                    if t.annotations else None,
                    "arguments": t.inputSchema,
                    "answer_schema": t.outputSchema,
                    "example_call": trim(calls[name]),
                    "example_answer": trim(res.structuredContent),
                    "example_is_error": res.isError,
                })

    out = {
        "taken_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "how": "живой разговор с `ouroboros-mcp` по стандартному вводу-выводу",
        "server_name": init.serverInfo.name,
        "server_version": init.serverInfo.version,
        "protocol_version": init.protocolVersion,
        "entry_point": "ouroboros-mcp (pyproject [project.scripts])",
        "instructions": init.instructions,
        "declared_tool_count": len(declared),
        "tools_declared_but_not_called": missing,
        "tools": stabilise(entries, str(root), sys.executable),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


asyncio.run(main())
