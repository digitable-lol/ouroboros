"""Tests for the CLI front-end.

Two halves, matching the two halves of ``ouroboros/cli.py``:

* the **planning** tests check the mapping from a command line to a tool call
  and the answer built from a tool's reply. They start no process and read no
  file, so they cover the branches of every one of the thirteen subcommands
  that come down to a tool call — including the ten that would otherwise need a
  live clangd to reach at all.
* the **running** tests drive ``main`` end to end: a real trace file written by
  the real runtime, a real draft project with a real git repository in it, a
  real child process, and a real MCP session over real pipes.
"""

from __future__ import annotations

import inspect
import io
import json
import sys

import pytest

from ouroboros import cli
from ouroboros.mcp import server


def _run(argv, stdin=""):
    sys.stdin = io.StringIO(stdin)
    try:
        return cli.main(argv)
    finally:
        sys.stdin = sys.__stdin__


def _parse(argv):
    return cli._build_parser().parse_args(argv)


# --------------------------------------------------------------------------- #
# planning: a command line in, a described call out
# --------------------------------------------------------------------------- #

#: One command line per tool subcommand, with every optional flag given a value
#: that differs from its default — a flag left at its default cannot show that
#: it was routed to the right parameter.
PLANNED = {
    "wrap-file": (
        ["wrap-file", "m.py", "--minimal"],
        server.tool_wrap_file, ("m.py",), {"minimal": True}, cli.COMPACT),
    "wrap-functions": (
        ["wrap-functions", "m.py", "f", "g", "--minimal"],
        server.tool_wrap_functions, ("m.py", ["f", "g"]), {"minimal": True}, cli.COMPACT),
    "trace": (
        ["trace", "debug.info", "-f", "fn", "-c", "sub", "--outcome", "raised",
         "--min-duration", "0.25", "--thread", "7.8", "--regex", "-n", "3",
         "--cursor", "cur", "--limit", "9"],
        server.tool_read_trace, ("debug.info",),
        {"function": "fn", "contains": "sub", "outcome": "raised",
         "min_duration": 0.25, "thread": "7.8", "regex": True,
         "tail": 3, "cursor": "cur", "limit": 9}, cli.READABLE),
    "trace-stats": (
        ["trace-stats", "debug.info", "-f", "fn", "-c", "sub", "--outcome", "result",
         "--min-duration", "0.5", "--thread", "1.2", "--regex"],
        server.tool_trace_stats, ("debug.info",),
        {"function": "fn", "contains": "sub", "outcome": "result",
         "min_duration": 0.5, "thread": "1.2", "regex": True}, cli.READABLE),
    "lint": (
        ["lint", "a.c", "--checks", "bugprone-*"],
        server.tool_lint_file, ("a.c",), {"checks": "bugprone-*"}, cli.READABLE),
    "symbols": (
        ["symbols", "needle", "/root", "--compile-commands-dir", "/build",
         "--limit", "5", "--index-timeout", "1.5"],
        server.tool_symbol_search, ("needle", "/root"),
        {"compile_commands_dir": "/build", "limit": 5, "index_timeout": 1.5},
        cli.READABLE),
    "doc-symbols": (
        ["doc-symbols", "a.c"],
        server.tool_document_symbols, ("a.c",), {}, cli.READABLE),
    "refs": (
        ["refs", "a.c", "f", "--compile-commands-dir", "/build",
         "--limit", "4", "--index-timeout", "2.5"],
        server.tool_references, ("a.c", "f"),
        {"compile_commands_dir": "/build", "limit": 4, "index_timeout": 2.5},
        cli.READABLE),
    "callers": (
        ["callers", "a.c", "f", "--direction", "outgoing",
         "--compile-commands-dir", "/build", "--index-timeout", "3.5"],
        server.tool_call_hierarchy, ("a.c", "f"),
        {"direction": "outgoing", "compile_commands_dir": "/build",
         "index_timeout": 3.5}, cli.READABLE),
    "describe": (
        ["describe", "a.c", "f", "--compile-commands-dir", "/build"],
        server.tool_describe_symbol, ("a.c", "f"),
        {"compile_commands_dir": "/build"}, cli.READABLE),
    "create": (
        ["create", "/site"], server.tool_create_project, ("/site",), {}, cli.COMPACT),
    "finish": (
        ["finish", "/site"], server.tool_finish, ("/site",), {}, cli.COMPACT),
}


@pytest.mark.parametrize("command", sorted(PLANNED))
def test_every_flag_reaches_the_parameter_of_the_same_name(command):
    """The point of the table: `--limit 5` must arrive as `limit=5`.

    Before, each subcommand spelled its own call out by hand, and a value handed
    to the neighbouring parameter — `limit` where `index_timeout` belongs, both
    numbers — would have been caught only by running the command against a live
    clangd, which no test did.
    """

    argv, tool, args, kwargs, indent = PLANNED[command]

    call = cli.plan(_parse(argv))

    assert call == cli.ToolCall(tool=tool, args=args, kwargs=kwargs, indent=indent)


@pytest.mark.parametrize("command", sorted(cli.TOOL_COMMANDS))
def test_the_names_in_the_table_are_real_parameters_of_the_tool(command):
    """A renamed tool parameter must break here, not at the user's terminal."""

    tool, positional, keyword, _ = cli.TOOL_COMMANDS[command]
    params = inspect.signature(tool).parameters

    assert list(params)[:len(positional)] == list(positional)
    assert set(keyword) <= set(params)
    # and the table must not quietly drop one the tool can take
    optional = {n for n, p in params.items() if p.default is not inspect.Parameter.empty}
    assert optional == set(keyword), (optional ^ set(keyword))


def test_every_subcommand_is_either_a_tool_call_or_a_named_special():
    """No subcommand may fall off the end of `_run` unhandled.

    This is what lets `_run` end with `execute` instead of a `return 2` that no
    test can reach and no reader can prove is dead.
    """

    sub = next(a for a in cli._build_parser()._actions if a.choices and a.dest == "command")

    assert set(sub.choices) == set(cli.TOOL_COMMANDS) | cli.SPECIAL_COMMANDS


def test_wrap_file_leaves_the_table_when_asked_for_stdout():
    """`--stdout` must not reach `tool_wrap_file`: that one rewrites the file."""

    assert cli.plan(_parse(["wrap-file", "m.py"])).tool is server.tool_wrap_file
    assert cli.plan(_parse(["wrap-file", "m.py", "--stdout"])) is None


@pytest.mark.parametrize("command", sorted(cli.SPECIAL_COMMANDS - {"mcp", "languages"}))
def test_the_special_commands_are_not_planned_as_tool_calls(command):
    argv = {"wrap-snippet": ["wrap-snippet", "-l", "python"],
            "write": ["write", "/site", "m.py"],
            "execute": ["execute", "/site", "--", "true"]}[command]

    assert cli.plan(_parse(argv)) is None


@pytest.mark.parametrize("ok,status", [(True, 0), (False, 1)])
def test_the_exit_status_follows_the_answer(ok, status):
    """A failure the tool reported as data still has to reach the shell."""

    text, got = cli.report({"ok": ok, "reason": "nope"}, cli.COMPACT)

    assert got == status
    assert json.loads(text)["ok"] is ok


def test_compact_is_one_line_and_readable_is_not():
    result = {"ok": True, "items": [1, 2]}

    compact, _ = cli.report(result, cli.COMPACT)
    readable, _ = cli.report(result, cli.READABLE)

    assert compact.count("\n") == 0
    assert readable.count("\n") > 0
    assert json.loads(compact) == json.loads(readable) == result


def test_non_ascii_in_an_answer_is_printed_as_itself():
    """The draft directory is named in Russian; escaping it makes the answer
    unreadable for the person the answer is for."""

    text, _ = cli.report({"ok": True, "path": "черновик/m.py"}, cli.COMPACT)

    assert "черновик" in text


@pytest.mark.parametrize("argv,expected", [
    (["--", "python", "x.py"], ["python", "x.py"]),
    (["python", "x.py"], ["python", "x.py"]),
    (["--"], []),
    ([], []),
    (["--", "sh", "-c", "echo -- done"], ["sh", "-c", "echo -- done"]),
])
def test_only_the_leading_separator_is_dropped(argv, expected):
    """argparse keeps the `--` as the first element, and a program named `--`
    does not exist; a later one belongs to the child's own command line."""

    assert cli.command_after_dashdash(argv) == expected


# --------------------------------------------------------------------------- #
# running: real files, a real draft, a real child process, a real MCP session
# --------------------------------------------------------------------------- #

def test_languages(capsys):
    assert _run(["languages"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "python" in out["languages"]


def test_wrap_snippet(capsys):
    rc = _run(["wrap-snippet", "-l", "python"], stdin="def f(x):\n    return x\n")
    assert rc == 0
    out = capsys.readouterr().out
    assert "_ouro_log" in out


def test_wrap_snippet_corrupted(capsys):
    rc = _run(["wrap-snippet", "-l", "python"], stdin="def broken(:\n")
    assert rc == 1
    assert capsys.readouterr().err.strip()


def test_wrap_snippet_unknown_language(capsys):
    rc = _run(["wrap-snippet", "-l", "cobol"], stdin="x\n")
    assert rc == 2
    assert "cobol" in capsys.readouterr().err


def test_wrap_file_stdout(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")
    rc = _run(["wrap-file", str(f), "--stdout"])
    assert rc == 0
    assert "_ouro_log" in capsys.readouterr().out
    # --stdout must not modify the file
    assert "_ouro_log" not in f.read_text(encoding="utf-8")


def test_wrap_file_stdout_unknown_extension(tmp_path, capsys):
    f = tmp_path / "notes.txt"
    f.write_text("hi\n", encoding="utf-8")
    rc = _run(["wrap-file", str(f), "--stdout"])
    assert rc == 2
    assert "no transformer" in capsys.readouterr().err


def test_wrap_file_stdout_corrupted(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    rc = _run(["wrap-file", str(f), "--stdout"])
    assert rc == 1
    assert capsys.readouterr().err.strip()


def test_wrap_file_in_place(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("def g(x):\n    return x\n", encoding="utf-8")
    rc = _run(["wrap-file", str(f)])
    assert rc == 0
    assert "_ouro_log" in f.read_text(encoding="utf-8")


def test_wrap_file_reports_a_failure_as_a_non_zero_status(tmp_path, capsys):
    f = tmp_path / "notes.txt"
    f.write_text("hi\n", encoding="utf-8")

    rc = _run(["wrap-file", str(f)])

    assert rc == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_wrap_functions_touches_only_the_named_one(tmp_path, capsys):
    f = tmp_path / "m.py"
    f.write_text("def a(x):\n    return x\n\n\ndef b(x):\n    return x\n", encoding="utf-8")

    rc = _run(["wrap-functions", str(f), "a"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["functions_wrapped"] == 1
    body = f.read_text(encoding="utf-8")
    assert "_ouro_log\ndef a" in body and "_ouro_log\ndef b" not in body


@pytest.fixture
def trace_file(tmp_path, monkeypatch):
    """A real debug.info, written by the real runtime helper."""

    import importlib

    import ouroboros.runtime as runtime

    path = tmp_path / "debug.info"
    monkeypatch.setenv("OUROBOROS_DEBUG_INFO", str(path))
    importlib.reload(runtime)

    @runtime.log
    def add(a, b):
        return a + b

    @runtime.log
    def boom():
        raise ValueError("nope")

    add(1, 2)
    add(3, 4)
    with pytest.raises(ValueError):
        boom()
    return path


def test_trace_reads_a_real_trace(trace_file, capsys):
    rc = _run(["trace", str(trace_file)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "\n" in out.strip()          # printed for a person, indented
    answer = json.loads(out)
    assert answer["ok"] is True
    assert answer["calls_parsed"] == 3
    assert [r["name"].split(".")[-1] for r in answer["records"]] == ["add", "add", "boom"]


def test_trace_filters_reach_the_backend(trace_file, capsys):
    rc = _run(["trace", str(trace_file), "--outcome", "raised"])

    assert rc == 0
    answer = json.loads(capsys.readouterr().out)
    assert [r["name"].split(".")[-1] for r in answer["records"]] == ["boom"]
    assert answer["records"][0]["outcome"] == "ValueError: nope"


def test_trace_of_a_missing_file_exits_non_zero(tmp_path, capsys):
    rc = _run(["trace", str(tmp_path / "nope.info")])

    assert rc == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_trace_stats_aggregates_a_real_trace(trace_file, capsys):
    rc = _run(["trace-stats", str(trace_file)])

    assert rc == 0
    answer = json.loads(capsys.readouterr().out)
    by_name = {f["name"].split(".")[-1]: f for f in answer["by_function"]}
    assert by_name["add"]["count"] == 2 and by_name["add"]["result"] == 2
    assert by_name["boom"]["raised"] == 1


def test_lint_runs_clang_tidy_on_a_real_file(tmp_path, capsys):
    src = tmp_path / "a.c"
    src.write_text("int f(int x) { return x + 1; }\n", encoding="utf-8")

    rc = _run(["lint", str(src)])

    assert rc == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] is True
    assert isinstance(answer["diagnostics"], list)


def test_doc_symbols_runs_clangd_on_a_real_file(tmp_path, capsys):
    src = tmp_path / "a.c"
    src.write_text("int f(int x) { return x + 1; }\n", encoding="utf-8")

    rc = _run(["doc-symbols", str(src)])

    assert rc == 0
    answer = json.loads(capsys.readouterr().out)
    assert "f" in [s["name"] for s in answer["symbols"]]


def test_full_workflow(tmp_path, capsys):
    base = str(tmp_path / "site")
    assert _run(["create", base]) == 0
    capsys.readouterr()

    rc = _run(["write", base, "main.py"], stdin="def hi(n):\n    return n\n\nprint(hi('x'))\n")
    assert rc == 0
    written = json.loads(capsys.readouterr().out)
    assert written["wrapped"] is True and written["functions_wrapped"] == 1

    rc = _run(["execute", base, "--", sys.executable, "main.py"])
    assert rc == 0
    assert "x" in capsys.readouterr().out

    rc = _run(["finish", base])
    assert rc == 0
    finished = json.loads(capsys.readouterr().out)
    assert "main.py" in finished["synced"]


def test_write_refuses_unparseable_content(tmp_path, capsys):
    """The draft's wrap-on-save gate, seen from the command line."""

    base = str(tmp_path / "site")
    _run(["create", base])
    capsys.readouterr()

    rc = _run(["write", base, "main.py"], stdin="def broken(:\n")

    assert rc == 1
    assert capsys.readouterr().err.strip()
    assert not (tmp_path / "site" / "черновик" / "main.py").exists()


def test_execute_requires_command(tmp_path):
    base = str(tmp_path / "site")
    _run(["create", base])
    assert _run(["execute", base, "--"]) == 2


def test_execute_passes_the_child_status_and_streams_through(tmp_path, capsys):
    base = str(tmp_path / "site")
    _run(["create", base])
    capsys.readouterr()

    rc = _run(["execute", base, "--", sys.executable, "-c",
               "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"])

    captured = capsys.readouterr()
    assert rc == 3
    assert "out" in captured.out and "err" in captured.err


def test_a_broken_settings_file_is_a_sentence_not_a_stack_trace(tmp_path, capsys):
    """`.ouroboros.json` is the user's mistake, and the answer to it names the
    file and says why the run stopped instead of guessing the build's flags."""

    (tmp_path / ".ouroboros.json").write_text("{ not json }", encoding="utf-8")
    src = tmp_path / "a.c"
    src.write_text("int f(int x) { return x + 1; }\n", encoding="utf-8")

    rc = _run(["wrap-file", str(src), "--stdout"])

    err = capsys.readouterr().err
    assert rc == 1
    assert ".ouroboros.json" in err and "not valid JSON" in err
    assert "#ifdef" in err                      # says what it would have cost
    assert "Traceback" not in err
    assert src.read_text(encoding="utf-8") == "int f(int x) { return x + 1; }\n"


def test_mcp_subcommand_serves_a_real_session_over_stdio(tmp_path, monkeypatch):
    """`ouroboros mcp` hands the process to the server: a real client request in
    on stdin, a real JSON-RPC answer out on stdout, and exit 0 at end of input.

    Run in this process on real pipes rather than as a child, so what is
    measured is this code path and not a fresh interpreter's.
    """

    monkeypatch.delenv("OUROBOROS_MCP_TRANSPORT", raising=False)
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "test", "version": "0"}}}
    inp, outp = tmp_path / "in", tmp_path / "out"
    inp.write_text(json.dumps(request) + "\n", encoding="utf-8")

    saved_in, saved_out = sys.stdin, sys.stdout
    try:
        sys.stdin = inp.open(encoding="utf-8")
        sys.stdout = outp.open("w", encoding="utf-8")
        rc = cli.main(["mcp"])
    finally:
        for stream in (sys.stdin, sys.stdout):
            if not stream.closed:
                stream.close()
        sys.stdin, sys.stdout = saved_in, saved_out

    assert rc == 0
    answer = json.loads(outp.read_text(encoding="utf-8").splitlines()[0])
    assert answer["id"] == 1
    assert answer["result"]["serverInfo"]["name"] == "ouroboros-logger"
    assert answer["result"]["capabilities"]["tools"] is not None


def test_mcp_subcommand_reads_the_transport_from_the_environment(monkeypatch):
    """A typo in OUROBOROS_MCP_TRANSPORT stops at the entry point."""

    monkeypatch.setenv("OUROBOROS_MCP_TRANSPORT", "carrier-pigeon")

    with pytest.raises(SystemExit, match="carrier-pigeon"):
        cli.main(["mcp"])


def test_the_installed_command_is_this_main():
    """`pyproject.toml` points the `ouroboros` script at `ouroboros.cli:main`;
    an installed entry point that no longer resolves is invisible to every test
    that imports the module directly."""

    from importlib.metadata import entry_points

    script = {e.name: e for e in entry_points(group="console_scripts")}["ouroboros"]

    assert script.load() is cli.main
