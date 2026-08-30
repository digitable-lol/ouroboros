"""Tests for the MCP tool implementations (transport-agnostic functions) plus
the FastMCP wiring: server instructions, per-tool titles + behaviour-hint
annotations, and SEP-1303 (input-validation/tool errors surface as isError tool
results, not protocol errors)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import types

from ouroboros.languages import Transformer, WrapResult
from ouroboros.mcp.server import (
    _drop_runtime_asset,
    build_server,
    tool_call_hierarchy,
    tool_create_project,
    tool_describe_symbol,
    tool_document_symbols,
    tool_execute,
    tool_finish,
    tool_lint_file,
    tool_list_files,
    tool_read_file,
    tool_read_trace,
    tool_references,
    tool_symbol_search,
    tool_trace_stats,
    tool_wrap_code_snippet,
    tool_wrap_file,
    tool_wrap_functions,
    tool_write_file,
    transport_from_env,
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


# --------------------------------------------------------------------------- #
# Defect: a failed runtime-helper write was reported as success.
#
# `_drop_runtime_asset` returned None both when the language needs no helper and
# when the helper could not be written, so `wrap_file` answered
# ok=True / runtime_header=None while the source file it had ALREADY overwritten
# contained an include of a header that does not exist. These pin the contract:
# the tool fails loudly and the source file is left untouched.
# --------------------------------------------------------------------------- #


def _block_helper(tmp_path, name: str):
    """Make writing ``name`` fail with a real filesystem error: a directory of
    that name cannot be replaced by a file (os.replace -> IsADirectoryError)."""
    (tmp_path / name).mkdir()


def test_wrap_file_fails_when_runtime_helper_cannot_be_written(tmp_path):
    f = tmp_path / "m.py"
    src = "def g(x):\n    return x\n"
    f.write_text(src, encoding="utf-8")
    _block_helper(tmp_path, "ouroboros_runtime.py")

    res = tool_wrap_file(str(f))

    assert res["ok"] is False
    assert "ouroboros_runtime.py" in res["error"]
    # and the source is NOT left half-instrumented: it still imports nothing
    assert f.read_text(encoding="utf-8") == src


def test_wrap_functions_fails_when_runtime_helper_cannot_be_written(tmp_path):
    f = tmp_path / "m.py"
    src = "def g(x):\n    return x\n\n\ndef h(x):\n    return x\n"
    f.write_text(src, encoding="utf-8")
    _block_helper(tmp_path, "ouroboros_runtime.py")

    res = tool_wrap_functions(str(f), ["g"])

    assert res["ok"] is False
    assert "ouroboros_runtime.py" in res["error"]
    assert f.read_text(encoding="utf-8") == src


def test_wrap_file_reports_the_helper_it_wrote(tmp_path):
    """The success side of the same contract: runtime_header names a file that
    really exists, so `None` can only ever mean "this language needs none"."""
    f = tmp_path / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")

    res = tool_wrap_file(str(f))

    assert res["ok"] is True and res["functions_wrapped"] == 1
    assert res["runtime_header"] == str(tmp_path / "ouroboros_runtime.py")
    assert (tmp_path / "ouroboros_runtime.py").is_file()


def test_wrap_file_without_wrapped_functions_writes_no_helper(tmp_path):
    """Nothing was instrumented -> nothing includes the helper -> None, and no
    stray helper file appears next to the source."""
    f = tmp_path / "empty.py"
    f.write_text("x = 1\n", encoding="utf-8")

    res = tool_wrap_file(str(f))

    assert res["ok"] is True and res["functions_wrapped"] == 0
    assert res["runtime_header"] is None
    assert not (tmp_path / "ouroboros_runtime.py").exists()


# --------------------------------------------------------------------------- #
# The failure answers of every tool. Each one is a path an agent WILL hit (a
# typo'd path, a project that was never created, a file it cannot write) and
# each must come back as a normal ok=False answer, never an exception through
# the transport.
# --------------------------------------------------------------------------- #


class _NoHelperTransformer(Transformer):
    """A language needing no runtime helper — the only honest reason for
    `_drop_runtime_asset` to answer None. Every backend shipped today has a
    helper, so the contract stated in `base.Transformer.runtime_asset` has no
    other way to be exercised."""

    language = "nohelper"
    extensions = (".nohelper",)

    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        return WrapResult(code=source, language=self.language, functions_wrapped=1)


def test_drop_runtime_asset_none_means_no_helper_needed(tmp_path):
    target = tmp_path / "x.nohelper"
    target.write_text("hi", encoding="utf-8")

    assert _drop_runtime_asset(_NoHelperTransformer(), target, "hi") is None
    assert list(tmp_path.iterdir()) == [target]   # and nothing was dropped


def test_wrap_file_unreadable_path(tmp_path):
    d = tmp_path / "adirectory.py"
    d.mkdir()
    res = tool_wrap_file(str(d))
    assert res["ok"] is False and "cannot read" in res["error"]


def test_wrap_file_corrupted_source(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    res = tool_wrap_file(str(f))
    assert res["ok"] is False and res["language"] == "python"


def test_wrap_file_minimal_is_c_only(tmp_path):
    """minimal=True is the kernel-only stackless probe; Python says so instead of
    letting NotImplementedError escape."""
    f = tmp_path / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")
    res = tool_wrap_file(str(f), minimal=True)
    assert res["ok"] is False and res["language"] == "python"
    assert "C-only" in res["error"] or "C only" in res["error"]


def _readonly_dir(tmp_path, name: str = "ro"):
    d = tmp_path / name
    d.mkdir()
    return d


def test_wrap_file_cannot_write_target(tmp_path):
    """Readable but unwritable directory: nothing to wrap, so no helper is in the
    way — the failure is the source write itself."""
    d = _readonly_dir(tmp_path)
    f = d / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    d.chmod(0o500)
    try:
        res = tool_wrap_file(str(f))
    finally:
        d.chmod(0o700)
    assert res["ok"] is False and "cannot write" in res["error"]


def _compdb_miss_tree(tmp_path):
    """A C file under a tree config whose compile database does NOT list it: the
    parse falls back to degraded flags, which the wrap must report as a warning."""
    (tmp_path / ".ouroboros.json").write_text(
        json.dumps({"c": {"compdb": str(tmp_path / "compile_commands.json")}}),
        encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text("[]", encoding="utf-8")
    f = tmp_path / "m.c"
    f.write_text("int visible(void) { return 0; }\n", encoding="utf-8")
    return f


def test_wrap_file_surfaces_warnings(tmp_path):
    res = tool_wrap_file(str(_compdb_miss_tree(tmp_path)))
    assert res["ok"] is True and res["functions_wrapped"] == 1
    assert res["warnings"] and "compile_commands.json" in res["warnings"][0]


def test_wrap_functions_surfaces_warnings(tmp_path):
    res = tool_wrap_functions(str(_compdb_miss_tree(tmp_path)), ["visible"])
    assert res["ok"] is True and res["functions_wrapped"] == 1
    assert res["warnings"] and "compile_commands.json" in res["warnings"][0]


def test_wrap_functions_empty_list(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")
    res = tool_wrap_functions(str(f), [])
    assert res["ok"] is False and "empty" in res["error"]


def test_wrap_functions_unsupported_extension(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("hi", encoding="utf-8")
    res = tool_wrap_functions(str(f), ["g"])
    assert res["ok"] is False and "python" in res["supported"]


def test_wrap_functions_unreadable_path(tmp_path):
    d = tmp_path / "adirectory.py"
    d.mkdir()
    res = tool_wrap_functions(str(d), ["g"])
    assert res["ok"] is False and "cannot read" in res["error"]


def test_wrap_functions_binary_file(tmp_path):
    f = tmp_path / "blob.py"
    f.write_bytes(b"\xff\xfe\x00\x01not utf-8")
    res = tool_wrap_functions(str(f), ["g"])
    assert res["ok"] is False and "UTF-8" in res["error"]


def test_wrap_functions_corrupted_source(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    res = tool_wrap_functions(str(f), ["broken"])
    assert res["ok"] is False and res["language"] == "python"


def test_wrap_functions_minimal_is_c_only(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")
    res = tool_wrap_functions(str(f), ["g"], minimal=True)
    assert res["ok"] is False and res["language"] == "python"


def test_wrap_functions_cannot_write_target(tmp_path):
    """Asking for a name the file does not define wraps nothing, so no helper is
    written and the failure is the source write."""
    d = _readonly_dir(tmp_path)
    f = d / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")
    d.chmod(0o500)
    try:
        res = tool_wrap_functions(str(f), ["not_defined_here"])
    finally:
        d.chmod(0o700)
    assert res["ok"] is False and "cannot write" in res["error"]


def test_read_trace_malformed_cursor_payload(tmp_path):
    """A well-formed base64 token whose payload is not {"i": <int>} is refused as
    a bad cursor, not crashed on."""
    f = tmp_path / "debug.info"
    f.write_text("", encoding="utf-8")
    bad = base64.urlsafe_b64encode(b'{"i": "not-an-int"}').decode("ascii")
    res = tool_read_trace(str(f), cursor=bad)
    assert res["ok"] is False and "invalid cursor" in res["error"]


def test_trace_stats_unreadable_path(tmp_path):
    res = tool_trace_stats(str(tmp_path / "nope.info"))
    assert res["ok"] is False and "cannot read" in res["error"]


def test_trace_stats_invalid_regex(tmp_path):
    f = tmp_path / "debug.info"
    f.write_text("", encoding="utf-8")
    res = tool_trace_stats(str(f), function="(unclosed", regex=True)
    assert res["ok"] is False and "invalid regex" in res["error"]


# ---- sandbox tools called against a base that is not a project ------------- #


def test_create_project_on_a_broken_base(tmp_path):
    """A черновик directory that is not a git repo: create(exist_ok) re-opens and
    the open fails, so the tool reports it instead of pretending it created one."""
    (tmp_path / "site" / "черновик").mkdir(parents=True)
    res = tool_create_project(str(tmp_path / "site"))
    assert res["ok"] is False and "no draft git repo" in res["error"]


def test_sandbox_tools_report_a_missing_project(tmp_path):
    base = str(tmp_path / "never-created")
    assert tool_write_file(base, "m.py", "x = 1\n")["ok"] is False
    assert tool_read_file(base, "m.py")["ok"] is False
    assert tool_list_files(base)["ok"] is False
    assert tool_execute(base, ["true"])["ok"] is False
    assert tool_finish(base)["ok"] is False


def test_read_file_missing_file_in_a_real_project(tmp_path):
    """The OSError half of read_file's handler: the project is fine, the file is not."""
    base = str(tmp_path / "site")
    tool_create_project(base)
    res = tool_read_file(base, "nowhere.py")
    assert res["ok"] is False and "No such file" in res["error"]


def test_finish_answer_says_the_copy_stays_instrumented(tmp_path):
    """The decision recorded in the answer itself: finish publishes the draft, it
    does not take the instrumentation off — and cannot, since no un-instrumented
    copy of the source was ever saved."""
    base = str(tmp_path / "site")
    tool_create_project(base)
    tool_write_file(base, "m.py", "def f():\n    return 1\n")

    res = tool_finish(base)

    assert res["ok"] is True
    assert res["instrumentation_removed"] is False
    published = Path(res["clean"]) / "m.py"
    assert "_ouro_log" in published.read_text(encoding="utf-8")


# ---- the clang/clangd wrappers --------------------------------------------- #


def test_clang_wrappers_pass_failures_through(tmp_path):
    """Six one-line delegations to ouroboros.clangtools. Drive each through the
    server wrapper so a mis-wired argument shows up here, not in the field."""
    py = tmp_path / "x.py"
    py.write_text("x = 1\n", encoding="utf-8")
    c = tmp_path / "x.c"
    c.write_text("int f(void) { return 0; }\n", encoding="utf-8")

    assert tool_lint_file(str(py))["ok"] is False            # not a C/C++ file
    assert tool_document_symbols(str(py))["ok"] is False
    assert tool_symbol_search("f", str(tmp_path / "nope"))["ok"] is False
    assert tool_references("/nonexistent/x.c", "f")["ok"] is False
    assert tool_call_hierarchy(str(c), "f", direction="sideways")["ok"] is False
    assert tool_describe_symbol("/nonexistent/x.c", "f")["ok"] is False


@pytest.mark.skipif(shutil.which("clangd") is None, reason="clangd not available")
def test_describe_symbol_wrapper_forwards_path_and_symbol(tmp_path):
    """A success case, so a swapped path/symbol pair could not pass: the wrapper
    must find the real definition of a real symbol."""
    src = tmp_path / "lib.c"
    src.write_text("int ouro_helper(int x) { return x + 1; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path), "command": "clang -c lib.c",
                     "file": str(src)}]), encoding="utf-8")

    res = tool_describe_symbol(str(src), "ouro_helper",
                               compile_commands_dir=str(tmp_path))

    assert res["ok"] is True
    assert res["definition"]["file"].endswith("lib.c")
    assert "ouro_helper" in res["hover"]


@pytest.mark.skipif(shutil.which("clangd") is None, reason="clangd not available")
def test_document_symbols_wrapper_lists_a_real_file(tmp_path):
    src = tmp_path / "lib.c"
    src.write_text("int ouro_helper(int x) { return x + 1; }\n", encoding="utf-8")

    res = tool_document_symbols(str(src))

    assert res["ok"] is True
    assert "ouro_helper" in {s["name"] for s in res["symbols"]}


@pytest.mark.skipif(shutil.which("clang-tidy") is None, reason="clang-tidy not available")
def test_lint_file_wrapper_forwards_the_checks_argument(tmp_path):
    """`checks` must reach clang-tidy: restricting to one unrelated check leaves
    the bug that the default set reports unreported."""
    src = tmp_path / "demo.c"
    src.write_text("int f(int a, int b) {\n    if (a = b) { return 1; }\n    return 0;\n}\n",
                   encoding="utf-8")

    default = tool_lint_file(str(src))
    narrowed = tool_lint_file(str(src), checks="-*,llvm-namespace-comment")

    assert default["ok"] is True and narrowed["ok"] is True
    assert any("assignment-in-if-condition" in d["check"] or "parentheses" in d["check"]
               for d in default["diagnostics"]), default["diagnostics"]
    assert narrowed["diagnostics"] == []


# --------------------------------------------------------------------------- #
# Every registered tool, driven through the real CallToolRequest handler.
#
# The registered functions are thin forwarders to the tool_* implementations,
# and a forwarder wired to the wrong implementation (or a tool quietly dropped
# from the server) is exactly the kind of drift nothing else here would catch.
# The table is also the checked list of what the server declares: 17 tools, no
# more and no fewer.
# --------------------------------------------------------------------------- #


def _every_tool_call(tmp_path) -> dict[str, tuple[dict[str, object], bool]]:
    """Name -> (arguments, expected ok) for all 17 tools. Cheap arguments only:
    the point is reaching each implementation, not re-testing it."""
    base = str(tmp_path / "site")
    py = tmp_path / "m.py"
    py.write_text("def f():\n    return 1\n", encoding="utf-8")
    py2 = tmp_path / "m2.py"
    py2.write_text("def f():\n    return 1\n", encoding="utf-8")
    trace = tmp_path / "debug.info"
    trace.write_text(
        json.dumps({"p": "in", "t": "2026-06-15T10:00:00.001", "id": "abc",
                    "fn": "f", "a": "", "k": ""}) + "\n"
        + json.dumps({"p": "out", "id": "abc", "fn": "f", "r": "1", "d": 0.0}) + "\n",
        encoding="utf-8")
    c = tmp_path / "x.c"
    c.write_text("int f(void) { return 0; }\n", encoding="utf-8")
    return {
        "wrap_code_snippet": ({"code": "def f():\n    return 1\n",
                               "language": "python"}, True),
        "wrap_file": ({"path": str(py)}, True),
        "wrap_functions": ({"path": str(py2), "functions": ["f"]}, True),
        "read_trace": ({"path": str(trace)}, True),
        "trace_stats": ({"path": str(trace)}, True),
        "create_project": ({"base": base}, True),
        "write_file": ({"base": base, "rel_path": "a.py",
                        "content": "def g():\n    return 2\n"}, True),
        "read_file": ({"base": base, "rel_path": "a.py"}, True),
        "list_files": ({"base": base}, True),
        "execute": ({"base": base, "command": [sys.executable, "-c", "print(1)"]}, True),
        "finish": ({"base": base}, True),
        # the clang/clangd side: arguments chosen so each answers without needing
        # a toolchain — the wiring is what is under test here.
        "lint_file": ({"path": str(py)}, False),            # not a C/C++ file
        "symbol_search": ({"query": "f", "root": str(tmp_path / "nope")}, False),
        "document_symbols": ({"path": str(py)}, False),
        "references": ({"path": "/nonexistent/x.c", "symbol": "f"}, False),
        "call_hierarchy": ({"path": str(c), "symbol": "f",
                            "direction": "sideways"}, False),
        "describe_symbol": ({"path": "/nonexistent/x.c", "symbol": "f"}, False),
    }


def test_every_registered_tool_reaches_its_implementation(tmp_path):
    table = _every_tool_call(tmp_path)
    declared = {t.name for t in build_server()._tool_manager.list_tools()}

    assert declared == set(table), (declared ^ set(table))
    assert len(declared) == 17

    # create_project must run before the tools that use its base
    order = ["create_project", "write_file"]
    order += [n for n in table if n not in order]
    for name in order:
        args, expected_ok = table[name]
        res = _call_tool(name, args)
        assert res.isError is False, f"{name}: {res.content}"
        assert res.structuredContent is not None, name
        assert res.structuredContent["ok"] is expected_ok, (name, res.structuredContent)


def test_bad_transport_env_var_fails_fast():
    """OUROBOROS_MCP_TRANSPORT is validated against the three the SDK accepts, so a
    typo stops at the entry point with a readable message instead of failing deep
    inside the server. Run as a real process — this is the entry point's job."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "from ouroboros.mcp.server import main; main()"],
        env={**os.environ, "OUROBOROS_MCP_TRANSPORT": "carrier-pigeon"},
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode != 0
    assert "carrier-pigeon" in proc.stderr
    assert "stdio" in proc.stderr


@pytest.mark.parametrize("value,expected",
                         [(None, "stdio"), ("stdio", "stdio"), ("sse", "sse"),
                          ("streamable-http", "streamable-http")])
def test_transport_from_env_accepts_the_three_sdk_transports(monkeypatch, value, expected):
    monkeypatch.delenv("OUROBOROS_MCP_TRANSPORT", raising=False)
    if value is not None:
        monkeypatch.setenv("OUROBOROS_MCP_TRANSPORT", value)
    assert transport_from_env() == expected


def test_transport_from_env_rejects_anything_else(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MCP_TRANSPORT", "carrier-pigeon")
    with pytest.raises(SystemExit, match="carrier-pigeon"):
        transport_from_env()


# --------------------------------------------------------------------------- #
# A broken .ouroboros.json must reach the caller as an ANSWER.
#
# TreeConfigError is raised deep on purpose: a tree whose settings cannot be read
# must stop, not quietly instrument with the host compiler's flags and lose every
# function behind an #ifdef. Nothing below softens that — the operation still
# fails. What is pinned here is that it fails as {ok: false} rather than as an
# exception escaping the MCP layer, which is what the agent used to get.
# --------------------------------------------------------------------------- #

@pytest.fixture
def broken_tree(tmp_path):
    (tmp_path / ".ouroboros.json").write_text("{ not json }", encoding="utf-8")
    src = tmp_path / "a.c"
    src.write_text("int f(int x) { return x + 1; }\n", encoding="utf-8")
    return tmp_path, src


@pytest.mark.parametrize("call", ["wrap_file", "wrap_functions", "lint_file"])
def test_broken_tree_config_is_an_answer_not_a_crash(broken_tree, call):
    _, src = broken_tree
    fn = {
        "wrap_file": lambda: tool_wrap_file(str(src)),
        "wrap_functions": lambda: tool_wrap_functions(str(src), ["f"]),
        "lint_file": lambda: tool_lint_file(str(src)),
    }[call]

    res = fn()

    assert res["ok"] is False
    assert res["tree_config"].endswith(".ouroboros.json")
    assert "not valid JSON" in res["reason"]
    # The answer has to say what to do, not just that something is wrong.
    assert "#ifdef" in res["hint"]


def test_broken_tree_config_leaves_the_source_untouched(broken_tree):
    """Failing loudly is only right if it also fails safely."""

    _, src = broken_tree
    before = src.read_text(encoding="utf-8")

    assert tool_wrap_file(str(src))["ok"] is False

    assert src.read_text(encoding="utf-8") == before


def test_a_good_tree_still_works_after_the_guard(tmp_path):
    """The guard must not swallow the ordinary path."""

    (tmp_path / ".ouroboros.json").write_text("{}", encoding="utf-8")
    src = tmp_path / "a.c"
    src.write_text("int f(int x) { return x + 1; }\n", encoding="utf-8")

    res = tool_wrap_file(str(src))

    assert res["ok"] is True
    assert res["functions_wrapped"] == 1


def test_finish_answer_names_what_it_left_behind(tmp_path):
    base = tmp_path / "site"
    tool_create_project(str(base))
    tool_write_file(str(base), "prog.c", "int add(int a, int b) { return a + b; }\n")
    (base / "черновик" / "prog").write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64)

    res = tool_finish(str(base))

    assert res["ok"] is True
    assert "prog" not in res["synced"]
    assert [s["path"] for s in res["skipped"]] == ["prog"]
    assert res["skipped"][0]["reason"]
    assert res["instrumentation_removed"] is False
