"""Tests for the MCP tool implementations (transport-agnostic functions) plus
the FastMCP wiring: server instructions, per-tool titles + behaviour-hint
annotations, and SEP-1303 (input-validation/tool errors surface as isError tool
results, not protocol errors)."""

from __future__ import annotations

import asyncio
import sys

from mcp import types

from ouroboros.mcp.server import (
    build_server,
    tool_create_project,
    tool_execute,
    tool_finish,
    tool_list_files,
    tool_read_file,
    tool_wrap_code_snippet,
    tool_wrap_file,
    tool_write_file,
)


def test_wrap_code_snippet_python():
    res = tool_wrap_code_snippet("def f(a):\n    return a\n", "python")
    assert res["ok"] is True
    assert res["functions_wrapped"] == 1
    assert "_ouro_log" in res["code"]


def test_wrap_code_snippet_unsupported_language():
    res = tool_wrap_code_snippet("x", "cobol")
    assert res["ok"] is False
    assert "python" in res["supported"]


def test_wrap_code_snippet_corrupted():
    res = tool_wrap_code_snippet("def broken(:\n", "python")
    assert res["ok"] is False
    assert res["language"] == "python"


def test_wrap_file_in_place(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")
    res = tool_wrap_file(str(f))
    assert res["ok"] is True
    assert res["functions_wrapped"] == 1
    assert "_ouro_log" in f.read_text(encoding="utf-8")


def test_wrap_file_unsupported_ext(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("hi", encoding="utf-8")
    res = tool_wrap_file(str(f))
    assert res["ok"] is False


def test_sandbox_lifecycle_via_tools(tmp_path):
    base = str(tmp_path / "site")
    created = tool_create_project(base)
    assert created["ok"] is True

    written = tool_write_file(base, "main.py", "def hi(n):\n    return n\n\nprint(hi('x'))\n")
    assert written["ok"] is True and written["wrapped"] is True

    listed = tool_list_files(base)
    assert "main.py" in listed["files"]

    read = tool_read_file(base, "main.py")
    assert "_ouro_log" in read["content"]

    executed = tool_execute(base, [sys.executable, "main.py"])
    assert executed["ok"] is True and executed["returncode"] == 0

    finished = tool_finish(base)
    assert finished["ok"] is True
    assert "main.py" in finished["synced"]


def test_write_file_rejects_corrupted(tmp_path):
    base = str(tmp_path / "site")
    tool_create_project(base)
    res = tool_write_file(base, "bad.py", "def broken(:\n")
    assert res["ok"] is False
    assert res.get("rejected") is True


def test_build_server_registers_tools():
    server = build_server()
    # FastMCP exposes a name; constructing it must not raise.
    assert server.name == "ouroboros-logger"


def test_server_advertises_instructions():
    """The 2025-11-25 surface: the server hands the client a description so an
    agent can reason about the toolset before the first call."""
    server = build_server()
    assert server.instructions and "instrument" in server.instructions.lower()


# Expected behaviour hints per tool (readOnly, openWorld). The mutating tools
# are checked for destructive/idempotent honesty too. These ARE the contract a
# host reasons about, so assert on them — a silent drift (e.g. execute losing
# openWorldHint) would otherwise pass unnoticed.
_READ_ONLY_TOOLS = {"wrap_code_snippet", "read_trace", "trace_stats",
                    "read_file", "list_files", "lint_file", "document_symbols"}


def test_every_tool_has_title_and_annotations():
    tools = {t.name: t for t in build_server()._tool_manager.list_tools()}
    assert set(tools) >= _READ_ONLY_TOOLS
    for name, t in tools.items():
        assert t.title, f"{name} has no title"
        ann = t.annotations
        assert ann is not None, f"{name} has no annotations"
        if name in _READ_ONLY_TOOLS:
            assert ann.readOnlyHint is True and ann.openWorldHint is False
        else:
            assert ann.readOnlyHint is False
    # execute runs arbitrary commands -> the only open-world tool.
    assert tools["execute"].annotations.openWorldHint is True
    assert tools["read_trace"].annotations.openWorldHint is False
    # finish rmtree's the clean tree before rebuilding it -> destructive.
    assert tools["finish"].annotations.destructiveHint is True
    # the clang tooling: lint is read-only; symbol_search writes only clangd's
    # own index cache (not read-only) but is additive + idempotent.
    assert tools["lint_file"].annotations.readOnlyHint is True
    assert tools["symbol_search"].annotations.readOnlyHint is False
    assert tools["symbol_search"].annotations.idempotentHint is True
    # clangd navigation tools: document_symbols needs no index (read-only); the
    # cross-file ones write only the index cache (idempotent, not read-only).
    assert tools["document_symbols"].annotations.readOnlyHint is True
    for name in ("references", "call_hierarchy", "describe_symbol"):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.idempotentHint is True


def _call_tool(name: str, arguments: dict[str, object]) -> types.CallToolResult:
    """Drive a tool through the real CallToolRequest handler (the layer that
    turns errors into isError results), so SEP-1303 is verified end to end."""
    server = build_server()
    handler = server._mcp_server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return asyncio.run(handler(req)).root


def test_invalid_args_is_error_result_not_protocol_error():
    """SEP-1303: a call missing a required argument comes back as an isError
    tool RESULT (the model can see and recover), not a raised protocol error."""
    res = _call_tool("wrap_code_snippet", {"code": "x"})  # missing `language`
    assert res.isError is True
    assert "validation error" in res.content[0].text.lower()


def test_handled_failure_is_normal_result_with_ok_false():
    """An expected, handled failure (unsupported language) is NOT an error at the
    protocol layer — it is a normal result whose payload says ok=False."""
    res = _call_tool("wrap_code_snippet", {"code": "x", "language": "cobol"})
    assert res.isError is False
    assert res.structuredContent is not None and res.structuredContent["ok"] is False
