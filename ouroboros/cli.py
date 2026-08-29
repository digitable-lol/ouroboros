"""``ouroboros`` CLI — the standalone Executor.

This is the thin command-line front-end over the same engine the MCP server
exposes. It is handy for scripting and for driving the transformer without an
MCP client.

Commands::

    ouroboros wrap-file <path> [--stdout]
    ouroboros wrap-snippet --language python [< code]
    ouroboros create <base>
    ouroboros write <base> <rel_path> [< content]
    ouroboros execute <base> -- <command...>
    ouroboros finish <base>
    ouroboros languages

The module is split in two, the same way ``languages/toolchain.py`` is:

* the **planning** half turns the parsed arguments into a description of the
  call to make, and a tool's answer into text plus an exit status. It opens no
  file, reads no stream and starts no process, so every branch in it is
  reachable from a literal in a test.
* the **running** half is the only part that reads stdin, opens files, calls
  the tools and writes to the streams.

Thirteen of the subcommands are the same shape — take the arguments, hand them
to one tool, print the answer, exit 0 when it says ``ok`` — so that shape is
written once, as data, in `TOOL_COMMANDS`. Before, it was written out thirteen
times, and an argument that reached the wrong parameter would have been caught
by nothing short of running the command against a real clangd.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .languages import (
    CorruptedSourceError,
    TreeConfigError,
    supported_languages,
    transformer_for_language,
)
from .mcp.server import (
    tool_call_hierarchy,
    tool_create_project,
    tool_describe_symbol,
    tool_document_symbols,
    tool_finish,
    tool_lint_file,
    tool_read_trace,
    tool_references,
    tool_symbol_search,
    tool_trace_stats,
    tool_wrap_file,
    tool_wrap_functions,
)
from .sandbox import Project, execute as sandbox_execute, write_file as sandbox_write_file


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ouroboros", description="Ouroboros-Logger Executor")
    sub = p.add_subparsers(dest="command", required=True)

    wf = sub.add_parser("wrap-file", help="instrument a file in place")
    wf.add_argument("path")
    wf.add_argument("--stdout", action="store_true",
                    help="print result instead of editing in place")
    wf.add_argument("--minimal", action="store_true",
                    help="C only: stackless depth-only probe for EVERY function "
                         "(hot/recursive/locked-safe; wrap a whole mechanism file)")

    wfn = sub.add_parser("wrap-functions",
                         help="instrument ONLY the named functions in a file in place")
    wfn.add_argument("path")
    wfn.add_argument("functions", nargs="+", help="function names to instrument")
    wfn.add_argument("--minimal", action="store_true",
                     help="C only: stackless depth-only probe for hot/recursive/locked "
                          "kernel functions (no per-frame struct; needs the ring sink)")

    ws = sub.add_parser("wrap-snippet", help="instrument code from stdin, print to stdout")
    ws.add_argument("--language", "-l", required=True)

    tr = sub.add_parser("trace", help="query a debug.info JSONL trace structurally")
    tr.add_argument("path")
    tr.add_argument("--function", "-f", help="substring of the qualified name")
    tr.add_argument("--contains", "-c", help="substring of args/kwargs/outcome")
    tr.add_argument("--outcome", choices=["result", "raised", "unknown"])
    tr.add_argument("--min-duration", type=float,
                    help="only calls whose real duration >= this many seconds (slow calls)")
    tr.add_argument("--thread", help="exact thread token (`th`, e.g. kernel pid.lid)")
    tr.add_argument("--regex", action="store_true",
                    help="treat --function/--contains as regular expressions")
    tr.add_argument("--tail", "-n", type=int, help="keep only the last N matches")
    tr.add_argument("--cursor", help="opaque next_cursor from a prior page (read in parts)")
    tr.add_argument("--limit", type=int, default=200, help="page-size hint (<=1000)")

    ts = sub.add_parser("trace-stats",
                        help="aggregate a trace: call counts + REAL per-call durations")
    ts.add_argument("path")
    ts.add_argument("--function", "-f", help="substring of the qualified name")
    ts.add_argument("--contains", "-c", help="substring of args/kwargs/outcome")
    ts.add_argument("--outcome", choices=["result", "raised", "unknown"])
    ts.add_argument("--min-duration", type=float, help="only calls >= this many seconds")
    ts.add_argument("--thread", help="exact thread token (`th`, e.g. kernel pid.lid)")
    ts.add_argument("--regex", action="store_true",
                    help="treat --function/--contains as regular expressions")

    ln = sub.add_parser("lint", help="static-analyse a C/C++ file with clang-tidy")
    ln.add_argument("path")
    ln.add_argument("--checks", help="clang-tidy check set (overrides the default)")

    sy = sub.add_parser("symbols", help="cross-file C/C++ symbol search via clangd")
    sy.add_argument("query")
    sy.add_argument("root", help="project directory to search")
    sy.add_argument("--compile-commands-dir", help="dir holding compile_commands.json")
    sy.add_argument("--limit", type=int, default=100, help="max symbols to return")
    sy.add_argument("--index-timeout", type=float, default=60.0,
                    help="seconds to wait for clangd's background index (large trees)")

    ds = sub.add_parser("doc-symbols", help="list symbols defined in one C/C++ file (clangd)")
    ds.add_argument("path")

    rf = sub.add_parser("refs", help="find references/call sites of a C/C++ symbol (clangd)")
    rf.add_argument("path")
    rf.add_argument("symbol")
    rf.add_argument("--compile-commands-dir", help="dir holding compile_commands.json")
    rf.add_argument("--limit", type=int, default=200)
    rf.add_argument("--index-timeout", type=float, default=60.0)

    ch = sub.add_parser("callers", help="call hierarchy of a C/C++ function (clangd)")
    ch.add_argument("path")
    ch.add_argument("symbol")
    ch.add_argument("--direction", choices=["incoming", "outgoing"], default="incoming")
    ch.add_argument("--compile-commands-dir", help="dir holding compile_commands.json")
    ch.add_argument("--index-timeout", type=float, default=60.0)

    de = sub.add_parser("describe", help="definition + hover of a C/C++ symbol (clangd)")
    de.add_argument("path")
    de.add_argument("symbol")
    de.add_argument("--compile-commands-dir", help="dir holding compile_commands.json")

    cr = sub.add_parser("create", help="create a draft project")
    cr.add_argument("base")

    wr = sub.add_parser("write", help="wrap-on-save a file into the draft (content on stdin)")
    wr.add_argument("base")
    wr.add_argument("rel_path")

    ex = sub.add_parser("execute", help="run a command inside the draft")
    ex.add_argument("base")
    ex.add_argument("argv", nargs=argparse.REMAINDER, help="-- command and args")

    fi = sub.add_parser("finish", help="sync draft -> clean")
    fi.add_argument("base")

    sub.add_parser("languages", help="list supported languages")
    sub.add_parser("mcp", help="run the MCP server over stdio")
    return p


# --------------------------------------------------------------------------- #
# planning — arguments in, a description of the call and the printed answer out
# --------------------------------------------------------------------------- #

#: Every tool takes keyword arguments and answers with one JSON-ready dict.
ToolFn = Callable[..., dict[str, Any]]

#: How the answer is printed. ``COMPACT`` is one line, for an answer a program
#: reads; ``READABLE`` is indented, for an answer a person reads. Which one each
#: command uses is part of its contract — scripts pipe ``create`` and ``finish``
#: into ``jq``, while ``trace`` output is read on a terminal.
COMPACT: int | None = None
READABLE: int | None = 2


@dataclass(frozen=True)
class ToolCall:
    """Which tool to call, with what, and how to print its answer.

    Building one calls nothing: it is a description, so a test can check that
    ``--limit 5`` reaches the tool as ``limit=5`` without starting clangd.
    """

    tool: ToolFn
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    indent: int | None


#: The subcommands that come down to a single tool call: name -> (tool,
#: positional arguments, keyword arguments, indent). The strings are the names
#: argparse stores the values under AND the tool's own parameter names — they
#: are deliberately the same word, and ``tests/test_cli.py`` checks both halves
#: of that claim against the parser and against ``inspect.signature``.
TOOL_COMMANDS: dict[str, tuple[ToolFn, tuple[str, ...], tuple[str, ...], int | None]] = {
    "wrap-file": (tool_wrap_file, ("path",), ("minimal",), COMPACT),
    "wrap-functions": (tool_wrap_functions, ("path", "functions"), ("minimal",), COMPACT),
    "trace": (tool_read_trace, ("path",),
              ("function", "contains", "outcome", "min_duration", "thread",
               "regex", "tail", "cursor", "limit"), READABLE),
    "trace-stats": (tool_trace_stats, ("path",),
                    ("function", "contains", "outcome", "min_duration", "thread",
                     "regex"), READABLE),
    "lint": (tool_lint_file, ("path",), ("checks",), READABLE),
    "symbols": (tool_symbol_search, ("query", "root"),
                ("compile_commands_dir", "limit", "index_timeout"), READABLE),
    "doc-symbols": (tool_document_symbols, ("path",), (), READABLE),
    "refs": (tool_references, ("path", "symbol"),
             ("compile_commands_dir", "limit", "index_timeout"), READABLE),
    "callers": (tool_call_hierarchy, ("path", "symbol"),
                ("direction", "compile_commands_dir", "index_timeout"), READABLE),
    "describe": (tool_describe_symbol, ("path", "symbol"),
                 ("compile_commands_dir",), READABLE),
    "create": (tool_create_project, ("base",), (), COMPACT),
    "finish": (tool_finish, ("base",), (), COMPACT),
}

#: The subcommands that do NOT come down to one tool call, and why. Kept next to
#: `TOOL_COMMANDS` so that the two together must cover the parser — which is
#: what makes the end of `_run` reachable-by-construction rather than a
#: ``pragma: no cover`` on a line nobody can prove is dead.
SPECIAL_COMMANDS: frozenset[str] = frozenset({
    "mcp",           # hands the process over to the MCP server; never returns normally
    "languages",     # answers from the registry, no tool involved
    "wrap-snippet",  # reads the code from stdin
    "write",         # reads the content from stdin
    "execute",       # streams the child's own stdout/stderr through, verbatim
})

#: Printed under a `TreeConfigError`. Saying only that the settings file is
#: broken is not enough: the reason this stops the run rather than falling back
#: on the host compiler's flags is that the fallback loses every function behind
#: an ``#ifdef``, silently.
BAD_TREE_CONFIG_HINT = (
    "fix or remove the settings file; instrumenting without the build's "
    "flags would silently skip code behind #ifdef"
)


def plan(args: argparse.Namespace) -> ToolCall | None:
    """The call this command comes down to, or ``None`` if it is not one call.

    ``wrap-file --stdout`` is the one command that leaves the table depending on
    a flag: without ``--stdout`` it is `tool_wrap_file`, which rewrites the file
    in place, and with it the file must not be touched at all.
    """

    if args.command == "wrap-file" and args.stdout:
        return None
    entry = TOOL_COMMANDS.get(args.command)
    if entry is None:
        return None
    tool, positional, keyword, indent = entry
    return ToolCall(
        tool=tool,
        args=tuple(getattr(args, name) for name in positional),
        kwargs={name: getattr(args, name) for name in keyword},
        indent=indent,
    )


def report(result: Mapping[str, Any], indent: int | None) -> tuple[str, int]:
    """A tool's answer as the text to print and the status to exit with.

    The status comes from the answer's own ``ok``, so a failure the tool
    reported as data still leaves the shell a non-zero status to branch on.
    """

    return json.dumps(result, ensure_ascii=False, indent=indent), 0 if result["ok"] else 1


def command_after_dashdash(argv: Sequence[str]) -> list[str]:
    """The command for ``execute``, with the ``--`` argparse leaves in place.

    ``nargs=REMAINDER`` keeps the separator as the first element, so
    ``execute base -- python x.py`` arrives as ``["--", "python", "x.py"]`` and
    would try to run a program literally named ``--``.
    """

    if argv and argv[0] == "--":
        return list(argv[1:])
    return list(argv)


# --------------------------------------------------------------------------- #
# running — the only half that touches streams, files and processes
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    """Run one subcommand and return its exit status.

    A broken ``.ouroboros.json`` is caught here, once, rather than in each
    subcommand: it is a mistake in the user's tree, and the answer to it is a
    sentence naming the file — not the Python stack trace the user got before,
    which buried that sentence under eight frames of our internals. Caught at the
    top and nowhere lower on purpose; see ``_reports_bad_tree_config`` in
    ``mcp/server.py`` for why no backend may swallow it.
    """

    try:
        return _run(_build_parser().parse_args(argv))
    except TreeConfigError as e:
        print(f"{e}", file=sys.stderr)
        print(BAD_TREE_CONFIG_HINT, file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    call = plan(args)
    if call is not None:
        text, status = report(call.tool(*call.args, **call.kwargs), call.indent)
        print(text)
        return status

    if args.command == "mcp":
        from .mcp.server import main as mcp_main

        mcp_main()
        return 0

    if args.command == "languages":
        print(json.dumps({"languages": supported_languages()}))
        return 0

    if args.command == "wrap-snippet":
        tx = transformer_for_language(args.language)
        if tx is None:
            print(f"unsupported language: {args.language}", file=sys.stderr)
            return 2
        source = sys.stdin.read()
        try:
            result = tx.wrap_source(source)
        except CorruptedSourceError as e:
            print(str(e), file=sys.stderr)
            return 1
        sys.stdout.write(result.code)
        return 0

    if args.command == "wrap-file":  # --stdout: print, leave the file alone
        from .languages import transformer_for_path

        t = transformer_for_path(args.path)
        if t is None:
            print(f"no transformer for {args.path}", file=sys.stderr)
            return 2
        try:
            with open(args.path, encoding="utf-8") as fh:
                out = t.wrap_source(fh.read(), filename=args.path, minimal=args.minimal)
        except CorruptedSourceError as e:
            print(str(e), file=sys.stderr)
            return 1
        sys.stdout.write(out.code)
        return 0

    if args.command == "write":
        content = sys.stdin.read()
        try:
            proj = Project.open(args.base)
            outcome = sandbox_write_file(proj, args.rel_path, content)
        except CorruptedSourceError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "rel_path": outcome.rel_path,
                    "functions_wrapped": outcome.functions_wrapped,
                    "wrapped": outcome.wrapped,
                },
                ensure_ascii=False,
            )
        )
        return 0

    # `execute` is the last one: the parser accepts nothing outside
    # TOOL_COMMANDS | SPECIAL_COMMANDS, and test_cli.py holds it to that, so
    # there is no unreachable tail here to hide behind a pragma.
    argv_cmd = command_after_dashdash(args.argv)
    if not argv_cmd:
        print("no command given after --", file=sys.stderr)
        return 2
    proj = Project.open(args.base)
    exec_result = sandbox_execute(proj, argv_cmd)
    sys.stdout.write(exec_result.stdout)
    sys.stderr.write(exec_result.stderr)
    return exec_result.returncode


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
