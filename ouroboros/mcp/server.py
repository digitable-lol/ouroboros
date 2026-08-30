"""FastMCP server wrapping the compiler/sandbox engine.

Tools fall into two groups:

* **Stateless transforms** — ``wrap_code_snippet`` (in-memory) and ``wrap_file``
  (in place on disk). These are the two tools the project brief mandates.
* **Sandbox lifecycle** — ``create_project`` / ``write_file`` / ``read_file`` /
  ``execute`` / ``finish`` map onto the draft→output-tree workflow. Each is
  stateless at the protocol level: the project is re-opened from its ``base``
  path on every call, so no server-side session state can drift. ``finish``
  publishes the draft; it does NOT take the instrumentation back off (there is
  no un-instrumented copy anywhere to restore) — see ``ouroboros/sandbox/sync.py``.

The tool *implementations* live as plain functions returning JSON-able dicts so
they are unit-testable without a live MCP transport; :func:`build_server` just
registers them.
"""

from __future__ import annotations

import base64
import contextlib
import functools
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mcp.types import ToolAnnotations

from ..clangtools import (
    call_hierarchy as clang_call_hierarchy,
    describe_symbol as clang_describe_symbol,
    document_symbols as clang_document_symbols,
    lint_file as clang_lint_file,
    references as clang_references,
    symbol_search as clang_symbol_search,
)
from ..languages import (
    CorruptedSourceError,
    Transformer,
    TreeConfigError,
    supported_languages,
    transformer_for_language,
    transformer_for_path,
)
from ..sandbox import (
    Project,
    SandboxError,
    execute as sandbox_execute,
    finish as sandbox_finish,
    list_files as sandbox_list_files,
    read_file as sandbox_read_file,
    write_file as sandbox_write_file,
)
from ..trace import (
    aggregate as trace_aggregate,
    load as trace_load,
    query as trace_query,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------- #
# Tool implementations (transport-agnostic, JSON-able returns)
# --------------------------------------------------------------------------- #


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically and mode-preservingly.

    ``wrap_file`` / ``wrap_functions`` edit real source trees in place (a kernel
    tree, even). A plain ``write_text`` truncates-then-writes, so a crash mid-write
    leaves a corrupted/empty source file. Instead we write a temp file IN THE SAME
    DIRECTORY (so the rename stays on one filesystem and is therefore atomic),
    fsync it, then ``os.replace`` over the target — a reader sees either the old or
    the complete new file, never a torn one. The original file's mode is preserved
    so instrumenting never silently chmods a source file (e.g. an executable
    script or a tree file with specific perms)."""
    path = Path(path)
    prev_mode: int | None = None
    with contextlib.suppress(FileNotFoundError):
        prev_mode = os.stat(path).st_mode
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=f".{path.name}.", suffix=".ouro-tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        if prev_mode is not None:
            os.chmod(tmp, prev_mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _drop_runtime_asset(tx: Transformer, target: Path, source: str) -> str | None:
    """Place the language's runtime helper next to an IN-PLACE wrapped file so its
    injected ``#include "ouroboros_runtime.h"`` (a quote-include → resolves in the
    file's own directory) actually compiles.

    ``wrap_file`` / ``wrap_functions`` edit files OUTSIDE the sandbox, so unlike
    ``write_file`` (which drops the asset into the draft) nothing else provides
    the helper — without this the instrumented file fails to build with
    ``ouroboros_runtime.h: No such file or directory`` (exactly what broke the
    riscv kernel build). The helper is generated, so we write it FRESH every time
    rather than skip-if-exists: a stale on-disk copy (e.g. an older kernel sink)
    would otherwise silently persist.

    Returns the path written, or ``None`` **only** when this language needs no
    helper at all. A failed write raises ``OSError`` instead of returning ``None``:
    the two used to be the same answer, so a caller could not tell "nothing was
    needed" from "the header the wrapped source includes is missing", and reported
    success for a file that cannot compile. Callers must let that failure through.

    ``source`` is the wrapped text the helper will sit beside. Go needs it: its
    helper joins the file's package rather than being imported, so it has to
    declare the same package name (see ``Transformer.runtime_asset_for``). For
    the other five backends the helper is the same file whatever it sits next
    to, and the argument is ignored."""
    asset = tx.runtime_asset_for(source)
    if asset is None:
        return None
    asset_name, asset_src = asset
    dest = target.parent / asset_name
    _atomic_write(dest, asset_src)
    return str(dest)


def _reports_bad_tree_config[**P](
    fn: Callable[P, dict[str, Any]],
) -> Callable[P, dict[str, Any]]:
    """Turn a broken ``.ouroboros.json`` into an answer, not a crash.

    ``TreeConfigError`` is raised deliberately deep down: a tree whose settings
    cannot be read must stop the run rather than silently instrument against the
    host compiler's flags and drop every function behind an ``#ifdef`` (see
    ``languages/treeflags.py``). That decision is right, and nothing here softens
    it — the operation still fails.

    What this fixes is only HOW it fails at the boundary. Uncaught, the exception
    left the MCP layer as a protocol-level error, so the agent got a stack trace
    instead of ``{ok: false}`` and could not tell "your settings file has a typo"
    from "the server is broken". Measured against a tree with an unparsable
    ``.ouroboros.json``, three of the seventeen tools escaped this way:
    wrap_file, wrap_functions, lint_file.

    Caught HERE and not in the backends on purpose: a backend that swallowed it
    would be back to instrumenting with the wrong flags, which is the bug the
    exception exists to prevent.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except TreeConfigError as e:
            return {
                "ok": False,
                "error": f"the tree's settings cannot be used: {e}",
                "tree_config": e.path,
                "reason": e.reason,
                "hint": "fix or remove the settings file; instrumenting without "
                        "the build's flags would silently skip code behind #ifdef",
            }

    return wrapper


def tool_wrap_code_snippet(code: str, language: str) -> dict[str, Any]:
    """Instrument a raw ``code`` string for ``language`` in memory."""

    tx = transformer_for_language(language)
    if tx is None:
        return {
            "ok": False,
            "error": f"unsupported language: {language!r}",
            "supported": supported_languages(),
        }
    try:
        result = tx.wrap_source(code)
    except CorruptedSourceError as e:
        return {"ok": False, "error": str(e), "language": e.language}
    return {
        "ok": True,
        "language": result.language,
        "functions_wrapped": result.functions_wrapped,
        "code": result.code,
    }


@_reports_bad_tree_config
def tool_wrap_file(path: str, minimal: bool = False) -> dict[str, Any]:
    """Instrument the file at ``path`` in place; return success/failure metrics.

    ``minimal`` (C only) — emit the stackless, depth-only probe for every function
    (hot/recursive/locked-safe): wrap a WHOLE mechanism file in one call to capture
    its full runtime call tree, without listing names."""

    p = Path(path).expanduser()
    tx = transformer_for_path(str(p))
    if tx is None:
        return {
            "ok": False,
            "error": f"no transformer for extension of {path!r}",
            "supported": supported_languages(),
        }
    try:
        original = p.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"cannot read {path}: {e}"}
    except UnicodeDecodeError:
        # a binary / non-UTF-8 file is not instrumentable -- report cleanly
        # instead of letting UnicodeDecodeError escape the tool and disrupt the
        # MCP (the one uncaught path found by feeding the tools real bad inputs).
        return {"ok": False,
                "error": f"{path} is not valid UTF-8 (binary file?); cannot instrument"}
    try:
        result = tx.wrap_source(original, filename=str(p), minimal=minimal)
    except CorruptedSourceError as e:
        return {"ok": False, "error": str(e), "language": e.language}
    except NotImplementedError as e:
        return {"ok": False, "error": str(e), "language": tx.language}
    # The helper goes down FIRST, on purpose. The wrapped source includes it, so
    # a source file written without it is a file that will not compile; writing
    # the helper first means a failure here leaves the original file untouched
    # instead of leaving the caller a broken tree it was told was fine.
    try:
        runtime_header = (_drop_runtime_asset(tx, p, result.code)
                          if result.functions_wrapped else None)
    except OSError as e:
        return {"ok": False,
                "error": f"cannot write the runtime helper next to {path}: {e}; "
                         f"{path} left unchanged (instrumented code would not build "
                         "without the helper)"}
    try:
        _atomic_write(p, result.code)
    except OSError as e:
        return {"ok": False, "error": f"cannot write {path}: {e}"}
    out: dict[str, Any] = {
        "ok": True,
        "path": str(p),
        "language": result.language,
        "functions_wrapped": result.functions_wrapped,
        "runtime_header": runtime_header,
    }
    if result.warnings:
        out["warnings"] = list(result.warnings)
    return out


@_reports_bad_tree_config
def tool_wrap_functions(path: str, functions: list[str],
                        minimal: bool = False) -> dict[str, Any]:
    """Instrument ONLY the named ``functions`` in the file at ``path`` in place.

    The selective counterpart to :func:`tool_wrap_file`: every other definition
    is left byte-for-byte untouched. This is the mode for hot or kernel files
    where blanket wrapping would flood the sink (printf-per-call on a per-page
    path) or take a per-frame logging struct inside a spinlocked critical
    section. ``functions_requested``/``functions_wrapped`` let the caller see
    which requested names were actually found and instrumented.

    ``minimal`` (C only) — emit the stackless, depth-only probe instead of the
    full per-frame struct: for HOT/RECURSIVE/deeply-locked kernel functions where
    the full struct blows the kernel stack or widens the fault surface. Requires
    the ring sink (``-DOUROBOROS_KERNEL_RING``); dump shows depth-stamped IN-records.
    """

    if not functions:
        return {"ok": False, "error": "functions list is empty; pass >=1 name"}
    p = Path(path).expanduser()
    tx = transformer_for_path(str(p))
    if tx is None:
        return {
            "ok": False,
            "error": f"no transformer for extension of {path!r}",
            "supported": supported_languages(),
        }
    try:
        original = p.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"cannot read {path}: {e}"}
    except UnicodeDecodeError:
        # a binary / non-UTF-8 file is not instrumentable -- report cleanly
        # instead of letting UnicodeDecodeError escape the tool and disrupt the
        # MCP (the one uncaught path found by feeding the tools real bad inputs).
        return {"ok": False,
                "error": f"{path} is not valid UTF-8 (binary file?); cannot instrument"}
    try:
        result = tx.wrap_source(original, filename=str(p), only=set(functions),
                                minimal=minimal)
    except CorruptedSourceError as e:
        return {"ok": False, "error": str(e), "language": e.language}
    except NotImplementedError as e:
        return {"ok": False, "error": str(e), "language": tx.language}
    # Helper first — see the same comment in tool_wrap_file.
    try:
        runtime_header = (_drop_runtime_asset(tx, p, result.code)
                          if result.functions_wrapped else None)
    except OSError as e:
        return {"ok": False,
                "error": f"cannot write the runtime helper next to {path}: {e}; "
                         f"{path} left unchanged (instrumented code would not build "
                         "without the helper)"}
    try:
        _atomic_write(p, result.code)
    except OSError as e:
        return {"ok": False, "error": f"cannot write {path}: {e}"}
    out: dict[str, Any] = {
        "ok": True,
        "path": str(p),
        "language": result.language,
        "functions_requested": sorted(set(functions)),
        "functions_wrapped": result.functions_wrapped,
        "runtime_header": runtime_header,
    }
    if result.warnings:
        out["warnings"] = list(result.warnings)
    return out


# Page size cap: a `limit` larger than this is clamped, so one call can never
# return an unbounded page (the server, not the client, owns the real page size).
_MAX_PAGE = 1000
# Cap on the in_flight list returned alongside a page (hangs are a small signal).
_MAX_IN_FLIGHT = 200


def _encode_cursor(last_index: int) -> str:
    """Opaque forward cursor: the ``index`` of the last record on this page.
    Modeled on the MCP cursor convention (opaque token; callers echo it back
    verbatim, never parse it). Stable under appends — keying on record identity,
    not a positional offset, so it survives the trace growing between calls."""
    raw = json.dumps({"i": last_index}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> int:
    """Inverse of :func:`_encode_cursor`; raises ValueError on a bad token."""
    raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    obj = json.loads(raw)
    if not isinstance(obj, dict) or not isinstance(obj.get("i"), int):
        raise ValueError("cursor payload malformed")
    index: int = obj["i"]
    return index


def tool_read_trace(path: str, function: str | None = None,
                    contains: str | None = None, outcome: str | None = None,
                    min_duration: float | None = None, thread: str | None = None,
                    regex: bool = False,
                    tail: int | None = None, cursor: str | None = None,
                    limit: int = 200) -> dict[str, Any]:
    """Query an Ouroboros ``debug.info`` trace (the JSONL ``in``/``out`` lines the
    runtime emits) STRUCTURALLY instead of grepping it raw.

    The read side of the instrument→run→observe loop. Returns COMPLETION records
    (one per finished call, with its real ``duration``). The two "what went wrong"
    signals: ``min_duration`` finds the SLOW calls (≥ N seconds), and ``in_flight``
    surfaces the HUNG/crashed ones (entered but never completed) — returned
    alongside the page, capped, not part of the cursor stream.

    Filters: ``function`` (qualified name), ``contains`` (args/kwargs/outcome),
    ``outcome`` ('result'/'raised'/'unknown'), ``min_duration`` (seconds),
    ``thread`` (exact ``th`` token — isolate one thread out of a concurrent trace;
    each record carries ``cpu``/``thread``), and ``regex`` (treat function/contains
    as regular expressions).

    Reading in parts (pagination modeled on the MCP opaque-cursor convention — this
    is a tool result, not protocol-level pagination): a page carries ``next_cursor``
    when more matches remain; pass it back verbatim as ``cursor`` for the next page;
    its ABSENCE means end-of-results. Treat the cursor as opaque (don't parse it).
    ``limit`` is the page-size hint (clamped to 1000); ``tail`` first windows
    to the last N matches (the recent calls before a failure), then you can page
    within that window; ``tail`` must be >= 0, and ``tail=0`` returns no records.
    Reads UTF-8 with replacement so a noisy kernel capture parses.
    """
    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": f"cannot read {path}: {e}"}
    try:
        last_index = _decode_cursor(cursor) if cursor is not None else None
    except (ValueError, TypeError):
        return {"ok": False, "error": f"invalid cursor: {cursor!r}"}
    try:
        loaded = trace_load(text)
        matched = trace_query(loaded.calls, function=function, contains=contains,
                              outcome=outcome, min_duration=min_duration,
                              thread=thread, regex=regex)
    except re.error as e:
        return {"ok": False, "error": f"invalid regex: {e}"}

    total_matched = len(matched)
    # `matched[-tail:]` is a trap at tail == 0: `matched[0:]` is the WHOLE list,
    # so "keep the last 0 matches" used to hand back every match. Spell the three
    # cases out instead of leaning on slice semantics.
    if tail is None:
        window = matched
    elif tail < 0:
        return {"ok": False, "error": f"tail must be >= 0; got {tail}"}
    elif tail == 0:
        window = []
    else:
        window = matched[-tail:]
    # Forward page: records after the cursor's index (records keep insertion order,
    # so `index` is monotonic — "index > last" is a clean, append-stable cut).
    candidates = window if last_index is None else [r for r in window if r.index > last_index]
    page_size = max(1, min(limit, _MAX_PAGE))
    page = candidates[:page_size]
    has_more = len(candidates) > len(page)
    next_cursor = _encode_cursor(page[-1].index) if (has_more and page) else None

    in_flight = loaded.in_flight[:_MAX_IN_FLIGHT]
    return {
        "ok": True,
        "path": str(p),
        "calls_parsed": len(loaded.calls),  # completed calls (paired in+out)
        "malformed": loaded.malformed,      # non-JSON / unknown lines skipped
        "matched": total_matched,           # completion records passing the filters
        "returned": len(page),
        "next_cursor": next_cursor,         # absent/null => end of results
        "in_flight": in_flight,             # entered but never completed (hang/crash)
        "in_flight_truncated": len(loaded.in_flight) > len(in_flight),
        "records": [r.as_dict() for r in page],
    }


def tool_trace_stats(path: str, function: str | None = None,
                     contains: str | None = None, outcome: str | None = None,
                     min_duration: float | None = None, thread: str | None = None,
                     regex: bool = False) -> dict[str, Any]:
    """Aggregate a debug.info trace instead of listing records: per-function call
    counts (split by outcome result/raised/unknown) and REAL per-call durations
    (``duration_seconds`` min/max/mean/total, read from each call's ``d`` field),
    plus ``by_thread`` (per-thread call counts + the CPUs each thread ran on — the
    concurrency view), ``in_flight`` (calls that entered but never completed) and
    the entry ``timespan``. Same filters as ``read_trace`` (incl.
    ``min_duration``/``thread``/``regex``) to scope the stats (e.g. only ``pmap``
    functions, only one thread, or only calls ≥ 0.1s)."""
    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": f"cannot read {path}: {e}"}
    try:
        loaded = trace_load(text)
        matched = trace_query(loaded.calls, function=function, contains=contains,
                              outcome=outcome, min_duration=min_duration,
                              thread=thread, regex=regex)
    except re.error as e:
        return {"ok": False, "error": f"invalid regex: {e}"}
    stats = trace_aggregate(matched, loaded.in_flight)
    return {"ok": True, "path": str(p), "calls_parsed": len(loaded.calls),
            "malformed": loaded.malformed, **stats}


def tool_create_project(base: str) -> dict[str, Any]:
    try:
        proj = Project.create(base, exist_ok=True)
    except SandboxError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "base": str(proj.base),
        "draft": str(proj.draft),
        "clean": str(proj.clean),
    }


def tool_write_file(base: str, rel_path: str, content: str) -> dict[str, Any]:
    try:
        proj = Project.open(base)
        outcome = sandbox_write_file(proj, rel_path, content)
    except CorruptedSourceError as e:
        return {"ok": False, "error": str(e), "language": e.language, "rejected": True}
    except SandboxError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "rel_path": outcome.rel_path,
        "language": outcome.language,
        "functions_wrapped": outcome.functions_wrapped,
        "wrapped": outcome.wrapped,
        "committed": outcome.committed,
    }


def tool_read_file(base: str, rel_path: str) -> dict[str, Any]:
    try:
        proj = Project.open(base)
        return {"ok": True, "content": sandbox_read_file(proj, rel_path)}
    except (SandboxError, OSError) as e:
        return {"ok": False, "error": str(e)}


def tool_list_files(base: str) -> dict[str, Any]:
    try:
        proj = Project.open(base)
        return {"ok": True, "files": sandbox_list_files(proj)}
    except SandboxError as e:
        return {"ok": False, "error": str(e)}


def tool_execute(base: str, command: list[str], timeout: float | None = None) -> dict[str, Any]:
    try:
        proj = Project.open(base)
        res = sandbox_execute(proj, command, timeout=timeout)
    except SandboxError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "returncode": res.returncode,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "debug_info": str(proj.debug_info_path()),
    }


def tool_finish(base: str) -> dict[str, Any]:
    """Copy the draft into the output tree (``чистовик``), instrumentation and all.

    ``instrumentation_removed`` is in the answer because the operation's name
    used to suggest the opposite. It is always False, and cannot be otherwise:
    ``write_file`` wraps a buffer before saving it, so no un-instrumented copy of
    the code exists anywhere to restore — see ``ouroboros/sandbox/sync.py``."""
    try:
        proj = Project.open(base)
        result = sandbox_finish(proj)
    except SandboxError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "clean": str(proj.clean),
        "synced": result.synced,
        # Named, not counted. The rule that drops these cannot tell a compiler's
        # output from a file the program was asked to produce, so the author has
        # to be able to see a name they wanted and say so.
        "skipped": [{"path": p, "reason": r} for p, r in result.skipped],
        "instrumentation_removed": False,
        "note": "The copy is instrumented, exactly like the draft: this step "
                "publishes the draft, it does not un-instrument it. Left behind: "
                ".git, debug.info, tool caches, and anything that looks built "
                "(compiled binaries, object files, crash dumps) — each one listed "
                "in `skipped` with the reason. If something you wanted is in that "
                "list, copy it across yourself.",
    }


@_reports_bad_tree_config
def tool_lint_file(path: str, checks: str | None = None) -> dict[str, Any]:
    """Static-analyse a C/C++ file with clang-tidy (see ``clangtools.lint``)."""
    return clang_lint_file(path, checks=checks)


def tool_symbol_search(query: str, root: str,
                       compile_commands_dir: str | None = None,
                       limit: int = 100, index_timeout: float = 60.0) -> dict[str, Any]:
    """Cross-file C/C++ symbol search via clangd (see ``clangtools.clangd``)."""
    return clang_symbol_search(query, root, compile_commands_dir=compile_commands_dir,
                               limit=limit, index_timeout=index_timeout)


def tool_document_symbols(path: str) -> dict[str, Any]:
    """List the symbols defined in one C/C++ file (see ``clangtools.clangd``)."""
    return clang_document_symbols(path)


def tool_references(path: str, symbol: str,
                    compile_commands_dir: str | None = None,
                    limit: int = 200, index_timeout: float = 60.0) -> dict[str, Any]:
    """Find every call/use site of a C/C++ symbol (see ``clangtools.clangd``)."""
    return clang_references(path, symbol, compile_commands_dir=compile_commands_dir,
                            limit=limit, index_timeout=index_timeout)


def tool_call_hierarchy(path: str, symbol: str, direction: str = "incoming",
                        compile_commands_dir: str | None = None,
                        index_timeout: float = 60.0) -> dict[str, Any]:
    """Callers/callees of a C/C++ function (see ``clangtools.clangd``)."""
    return clang_call_hierarchy(path, symbol, direction=direction,
                                compile_commands_dir=compile_commands_dir,
                                index_timeout=index_timeout)


def tool_describe_symbol(path: str, symbol: str,
                         compile_commands_dir: str | None = None) -> dict[str, Any]:
    """Definition + hover (type/signature/doc) of a C/C++ symbol."""
    return clang_describe_symbol(path, symbol, compile_commands_dir=compile_commands_dir)


# --------------------------------------------------------------------------- #
# FastMCP wiring
# --------------------------------------------------------------------------- #


# Server description handed to the client at initialize. Tells an agent the
# loop the toolset is built around (instrument → run → observe) and which tools
# mutate the filesystem, so a host can reason about them before the first call.
_INSTRUCTIONS = """\
Ouroboros-Logger: guaranteed function-level logging instrumentation for code.

The loop is instrument -> run -> observe:
  1. instrument: wrap_code_snippet (in memory), wrap_file (whole file in place),
     or wrap_functions (only named functions — for hot/kernel paths). Or work in
     a sandbox: create_project, then write_file (wrap-on-save), execute, finish.
  2. run: execute the instrumented code (execute, or run it yourself); every
     wrapped call appends `in`/`out` JSONL records to a debug.info trace.
  3. observe: read_trace (structural query + pagination) and trace_stats
     (per-function counts + real durations). min_duration finds slow calls;
     in_flight surfaces hung/crashed ones.

Choosing WHAT to instrument in a large C/C++ tree — six clangd/clang-tidy tools,
listed here because a tool absent from these instructions does not get chosen:
symbol_search (find a name across the tree), document_symbols (what one file
defines), references (who uses it), call_hierarchy (who calls whom, transitively),
describe_symbol (where it is defined, with what signature), lint_file (clang-tidy
findings). Use them BEFORE wrap_functions to pick the functions worth wrapping,
instead of wrapping a whole hot file. They need `clangd` and `clang-tidy` on PATH
and, for anything cross-file, a compile_commands.json; without those they return
{ok: false} explaining what is missing, so it is safe to try one and read the answer.

Filesystem effects: wrap_file/wrap_functions overwrite the target file in place;
write_file/finish mutate the sandbox tree; execute runs arbitrary commands. The
read_* / list_files / trace / clangd tools never write. See SPEC.md for the trace
schema.
"""

# One shared annotation instance for the read side and the pure in-memory
# transform: they touch no state (readOnly); the mutating tools are marked
# individually and honestly — see each call. Sharing one instance across tools
# is safe because annotations are never mutated after registration (the type is
# a plain pydantic model, NOT frozen — so the safety is by convention, not by
# the type).
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def build_server() -> FastMCP:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("ouroboros-logger", instructions=_INSTRUCTIONS)

    @mcp.tool(
        title="Wrap code snippet",
        # Pure in-memory transform: returns instrumented code, writes nothing.
        annotations=_READ_ONLY,
    )
    def wrap_code_snippet(code: str, language: str) -> dict[str, Any]:
        """Wrap a raw code string with function-level logging instrumentation."""
        return tool_wrap_code_snippet(code, language)

    @mcp.tool(
        title="Instrument file in place",
        # Overwrites the target file in place (not additive -> destructive), but
        # re-wrapping an already-wrapped file is a no-op (idempotent).
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                    idempotentHint=True, openWorldHint=False),
    )
    def wrap_file(path: str, minimal: bool = False) -> dict[str, Any]:
        """Instrument a source file in place; returns success/failure metrics.

        Set ``minimal=True`` (C only) for the stackless depth-only probe on every
        function — wrap a whole mechanism file to capture its full runtime call tree."""
        return tool_wrap_file(path, minimal=minimal)

    @mcp.tool(
        title="Instrument named functions in place",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                    idempotentHint=True, openWorldHint=False),
    )
    def wrap_functions(path: str, functions: list[str],
                       minimal: bool = False) -> dict[str, Any]:
        """Instrument ONLY the named functions in a file in place (selective mode
        for hot/kernel files where wrapping the whole file would flood the sink).

        Set ``minimal=True`` (C only) for the stackless, depth-only probe — for
        HOT/RECURSIVE/deeply-locked kernel functions where the full per-frame
        struct blows the kernel stack or widens the fault surface."""
        return tool_wrap_functions(path, functions, minimal=minimal)

    @mcp.tool(
        title="Query trace records",
        annotations=_READ_ONLY,
    )
    def read_trace(path: str, function: str | None = None,
                   contains: str | None = None, outcome: str | None = None,
                   min_duration: float | None = None, thread: str | None = None,
                   regex: bool = False,
                   tail: int | None = None, cursor: str | None = None,
                   limit: int = 200) -> dict[str, Any]:
        """Query a debug.info trace (the JSONL in/out call records the runtime emits)
        structurally. Filter by function/contains/outcome, min_duration (slow calls,
        seconds), thread (exact `th` token — one thread out of a concurrent trace;
        each record carries cpu/thread), or regex. Read in parts: a page carries
        next_cursor when more matches remain — pass it back as cursor for the next
        page; its absence means end. limit is the page-size hint (≤1000); tail windows
        to the last N first (tail must be >= 0; tail=0 returns nothing). Slow calls
        (min_duration) + hung calls (in_flight) cover "what went wrong". The read
        side of instrument -> run -> observe."""
        return tool_read_trace(path, function=function, contains=contains,
                               outcome=outcome, min_duration=min_duration,
                               thread=thread, regex=regex,
                               tail=tail, cursor=cursor, limit=limit)

    @mcp.tool(
        title="Aggregate trace statistics",
        annotations=_READ_ONLY,
    )
    def trace_stats(path: str, function: str | None = None,
                    contains: str | None = None, outcome: str | None = None,
                    min_duration: float | None = None, thread: str | None = None,
                    regex: bool = False) -> dict[str, Any]:
        """Aggregate a debug.info trace: per-function call counts (by outcome) and
        REAL per-call durations (min/max/mean/total from each call's `d`), plus
        by_thread (per-thread counts + CPUs each thread ran on), in_flight and the
        entry timespan. Same filters as read_trace, incl. min_duration (seconds),
        thread, and regex."""
        return tool_trace_stats(path, function=function, contains=contains,
                                outcome=outcome, min_duration=min_duration,
                                thread=thread, regex=regex)

    @mcp.tool(
        title="Create draft project",
        # Re-creating an existing project base is a no-op (exist_ok), and it only
        # adds a draft/clean scaffold — never deletes anything.
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    def create_project(base: str) -> dict[str, Any]:
        """Create a draft (черновик) git project under the given base path."""
        return tool_create_project(base)

    @mcp.tool(
        title="Wrap-on-save into draft",
        # Writes into the isolated draft sandbox and commits each save; scoped to
        # the draft (not destructive to anything outside it), and each commit is a
        # distinct revision (not idempotent).
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=False, openWorldHint=False),
    )
    def write_file(base: str, rel_path: str, content: str) -> dict[str, Any]:
        """Wrap-on-save a file into the draft and commit it (rejects unparseable code)."""
        return tool_write_file(base, rel_path, content)

    @mcp.tool(
        title="Read file from draft",
        annotations=_READ_ONLY,
    )
    def read_file(base: str, rel_path: str) -> dict[str, Any]:
        """Read a file from the draft."""
        return tool_read_file(base, rel_path)

    @mcp.tool(
        title="List files in draft",
        annotations=_READ_ONLY,
    )
    def list_files(base: str) -> dict[str, Any]:
        """List tracked files in the draft."""
        return tool_list_files(base)

    @mcp.tool(
        title="Execute command in draft",
        # Runs an arbitrary command -> open-world side effects, not idempotent.
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                    idempotentHint=False, openWorldHint=True),
    )
    def execute(base: str, command: list[str], timeout: float | None = None) -> dict[str, Any]:
        """Run a command in the draft; runtime info is funneled to debug.info."""
        return tool_execute(base, command, timeout)

    @mcp.tool(
        # NOT "clean up the code": this copies the draft, it does not take the
        # instrumentation back off. The title says copy so the name `finish` and
        # the folder name `чистовик` stop implying a step that does not exist.
        title="Copy draft into the output tree",
        # Rebuilds the output tree from the draft: it rmtree's the existing tree
        # first, so content there but not in the draft is lost (destructive);
        # re-copying yields the same state (idempotent).
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                    idempotentHint=True, openWorldHint=False),
    )
    def finish(base: str) -> dict[str, Any]:
        """Copy the draft (черновик) into the output tree (чистовик).

        The copy KEEPS the logging instrumentation — there is no un-instrument
        step, and this is not one. write_file wraps code before saving it, so no
        un-instrumented copy of the source exists anywhere to restore. Left
        behind: .git, debug.info, and build output (__pycache__, *.pyc, *.beam,
        tool caches). Wipes the output tree first, then rebuilds it."""
        return tool_finish(base)

    @mcp.tool(
        title="Lint C/C++ file (clang-tidy)",
        # Reads the file and runs clang-tidy; writes nothing.
        annotations=_READ_ONLY,
    )
    def lint_file(path: str, checks: str | None = None) -> dict[str, Any]:
        """Static-analyse a C/C++ file with clang-tidy — real bugs the parse gate
        can't see (use-after-free, `if (a = b)`, dead stores, perf traps). Uses the
        same compile_commands.json as the instrumenter; filters the `__ouro`
        reserved-identifier noise our own instrumentation injects. `checks` overrides
        the default clang-tidy check set."""
        return tool_lint_file(path, checks=checks)

    @mcp.tool(
        title="Search C/C++ symbols (clangd)",
        # Reads the tree; the only write is clangd's own on-disk index cache
        # (.cache/clangd), never user content — so not read-only, but additive and
        # idempotent (same query → same result).
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    def symbol_search(query: str, root: str,
                      compile_commands_dir: str | None = None,
                      limit: int = 100, index_timeout: float = 60.0) -> dict[str, Any]:
        """Smart cross-file C/C++ symbol search via clangd's workspace/symbol — find
        functions/types/vars by name across a whole tree (to pick wrap_functions
        targets) instead of grepping. `root` is the project dir; optional
        `compile_commands_dir` points clangd at the build's compile_commands.json.
        First call on a fresh tree pays background-index cost (cached to disk after);
        raise `index_timeout` (seconds) for a very large tree. The result's
        `index_complete` is False if indexing didn't finish (results may be partial)."""
        return tool_symbol_search(query, root, compile_commands_dir=compile_commands_dir,
                                  limit=limit, index_timeout=index_timeout)

    # clangd's index cache (.cache/clangd) is the only write these make — never
    # user content; document_symbols needs no index so it is purely read-only.
    clangd_nav = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                 idempotentHint=True, openWorldHint=False)

    @mcp.tool(
        title="List symbols in C/C++ file (clangd)",
        annotations=_READ_ONLY,
    )
    def document_symbols(path: str) -> dict[str, Any]:
        """List every symbol defined in ONE C/C++ file (functions, types, vars) — the
        per-file menu of wrap_functions candidates. Needs no project index, so it is
        fast. Returns name/kind/line for each."""
        return tool_document_symbols(path)

    @mcp.tool(
        title="Find references to C/C++ symbol (clangd)",
        annotations=clangd_nav,
    )
    def references(path: str, symbol: str, compile_commands_dir: str | None = None,
                   limit: int = 200, index_timeout: float = 60.0) -> dict[str, Any]:
        """Every call/use site of `symbol` (defined in `path`) across the tree — who
        calls it / how hot it is, to choose instrumentation targets. Cross-file, so it
        waits for clangd's background index. `compile_commands_dir` points at the build's
        compile_commands.json; raise `index_timeout` for a large tree. `index_complete`
        in the result is False if indexing didn't finish (results may be partial)."""
        return tool_references(path, symbol, compile_commands_dir=compile_commands_dir,
                               limit=limit, index_timeout=index_timeout)

    @mcp.tool(
        title="C/C++ call hierarchy (clangd)",
        annotations=clangd_nav,
    )
    def call_hierarchy(path: str, symbol: str, direction: str = "incoming",
                       compile_commands_dir: str | None = None,
                       index_timeout: float = 60.0) -> dict[str, Any]:
        """Callers (direction='incoming') or callees ('outgoing') of a C/C++ function —
        the sharpest tool for choosing what to wrap_functions along a call path. `symbol`
        is defined in `path`; cross-file, waits for the background index (raise
        `index_timeout` for a large tree). NOTE: 'outgoing' needs clangd ≥ ~19; older
        builds report a clean unsupported error."""
        return tool_call_hierarchy(path, symbol, direction=direction,
                                   compile_commands_dir=compile_commands_dir,
                                   index_timeout=index_timeout)

    @mcp.tool(
        title="Describe C/C++ symbol (clangd)",
        annotations=clangd_nav,
    )
    def describe_symbol(path: str, symbol: str,
                        compile_commands_dir: str | None = None) -> dict[str, Any]:
        """Definition location + hover (type/signature/doc) of a C/C++ `symbol` in
        `path` — navigate to where it's defined and see its signature."""
        return tool_describe_symbol(path, symbol,
                                    compile_commands_dir=compile_commands_dir)

    return mcp


def transport_from_env() -> Literal["stdio", "sse", "streamable-http"]:
    """Read ``OUROBOROS_MCP_TRANSPORT`` (default ``stdio``) and check it.

    The SDK accepts exactly these three; validating here stops a typo from
    failing deep inside the server with an unreadable message. Separate from
    :func:`main` so the check can be made without starting anything. The equality
    chain also narrows the value to the ``Literal`` ``run`` expects (no cast)."""
    transport = os.environ.get("OUROBOROS_MCP_TRANSPORT", "stdio")
    if transport == "sse":
        return "sse"
    if transport == "streamable-http":
        return "streamable-http"
    if transport == "stdio":
        return "stdio"
    raise SystemExit(
        f"OUROBOROS_MCP_TRANSPORT={transport!r} is not one of "
        "'stdio', 'sse', 'streamable-http'"
    )


def main() -> None:
    """Start the server on the transport the environment asks for.

    This carried a ``no cover`` note for a long time, saying ``run()`` blocks so
    the entry point cannot be measured. It does not block forever: over stdio it
    returns when the client closes the input, which is how an ordinary session
    ends. The note hid the entry point, and with it the fact that nothing
    checked the chosen transport ever reached ``run``. A whole session now goes
    through here in ``tests/test_cli.py`` — a real request in, a real answer
    out, exit 0 at end of input.
    """

    build_server().run(transport=transport_from_env())


if __name__ == "__main__":  # pragma: no cover
    main()
