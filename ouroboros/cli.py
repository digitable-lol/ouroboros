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
"""

from __future__ import annotations

import argparse
import json
import sys

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
        print(
            "fix or remove the settings file; instrumenting without the build's "
            "flags would silently skip code behind #ifdef",
            file=sys.stderr,
        )
        return 1


def _run(args: argparse.Namespace) -> int:

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

    if args.command == "wrap-file":
        if args.stdout:
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
        res = tool_wrap_file(args.path, minimal=args.minimal)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1

    if args.command == "wrap-functions":
        res = tool_wrap_functions(args.path, args.functions, minimal=args.minimal)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1

    if args.command == "trace":
        res = tool_read_trace(args.path, function=args.function,
                              contains=args.contains, outcome=args.outcome,
                              min_duration=args.min_duration, thread=args.thread,
                              regex=args.regex,
                              tail=args.tail, cursor=args.cursor, limit=args.limit)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "trace-stats":
        res = tool_trace_stats(args.path, function=args.function,
                               contains=args.contains, outcome=args.outcome,
                               min_duration=args.min_duration, thread=args.thread,
                               regex=args.regex)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "lint":
        res = tool_lint_file(args.path, checks=args.checks)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "symbols":
        res = tool_symbol_search(args.query, args.root,
                                 compile_commands_dir=args.compile_commands_dir,
                                 limit=args.limit, index_timeout=args.index_timeout)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "doc-symbols":
        res = tool_document_symbols(args.path)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "refs":
        res = tool_references(args.path, args.symbol,
                              compile_commands_dir=args.compile_commands_dir,
                              limit=args.limit, index_timeout=args.index_timeout)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "callers":
        res = tool_call_hierarchy(args.path, args.symbol, direction=args.direction,
                                  compile_commands_dir=args.compile_commands_dir,
                                  index_timeout=args.index_timeout)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "describe":
        res = tool_describe_symbol(args.path, args.symbol,
                                   compile_commands_dir=args.compile_commands_dir)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.command == "create":
        res = tool_create_project(args.base)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1

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

    if args.command == "execute":
        argv_cmd = args.argv
        if argv_cmd and argv_cmd[0] == "--":
            argv_cmd = argv_cmd[1:]
        if not argv_cmd:
            print("no command given after --", file=sys.stderr)
            return 2
        proj = Project.open(args.base)
        exec_result = sandbox_execute(proj, argv_cmd)
        sys.stdout.write(exec_result.stdout)
        sys.stderr.write(exec_result.stderr)
        return exec_result.returncode

    if args.command == "finish":
        res = tool_finish(args.base)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1

    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
