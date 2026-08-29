"""``symbol_search`` — drive clangd over LSP for cross-file symbol search.

clangd is the LLVM C/C++ language server; it speaks JSON-RPC over stdio (LSP).
We hand-roll a tiny client (no SDK) for one request: ``workspace/symbol``, the
smart project-wide symbol lookup that finds ``wrap_functions`` targets on a big
tree (NetBSD/ROS ≈ 14.7k files) far better than grepping raw text.

Lifecycle is per-call (spawn → initialize → query → shut down), which keeps the
"project re-opened per call" model the rest of the server follows. That is only
affordable because clangd PERSISTS its background index to disk
(``.cache/clangd/index`` under the project): the first query on a fresh tree pays
the indexing cost, every later one reuses the on-disk index. A bounded
``index_timeout`` waits for that first index so results are complete, then gives
up gracefully (partial results are still useful) rather than hanging.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, Any

from .flags import find_tool

# LSP SymbolKind (1-based) → readable name. Only the kinds that matter for C/C++
# navigation; anything else is reported by its numeric kind.
_SYMBOL_KINDS = {
    5: "class", 6: "method", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    22: "enum-member", 23: "struct", 26: "type-parameter",
}

# clangd binary names, newest first — find_tool returns the first on PATH, so a
# newer clangd (which supports e.g. outgoing call hierarchy) is preferred. Bare
# "clangd" comes first so a hand-installed latest (our gpu /usr/local/bin/clangd)
# wins over a distro clangd-NN.
_CLANGD_NAMES = ("clangd", "clangd-22", "clangd-21", "clangd-20", "clangd-19",
                 "clangd-18", "clangd-17")


def _read_message(stream: IO[bytes]) -> dict[str, Any] | None:
    """Read one LSP message (``Content-Length`` header + JSON body) or None on EOF."""
    length = 0
    while True:
        line = stream.readline()
        if not line:
            return None
        text = line.decode("ascii", "replace").strip()
        if not text:  # blank line terminates headers
            break
        if text.lower().startswith("content-length:"):
            length = int(text.split(":", 1)[1].strip())
    if length <= 0:
        return {}
    body = stream.read(length)
    obj: dict[str, Any] = json.loads(body.decode("utf-8"))
    return obj


class _Clangd:
    """A minimal LSP client over a clangd subprocess: framed JSON-RPC, a reader
    thread draining stdout onto a queue, request/notify, and clean shutdown."""

    def __init__(self, binary: str, extra_args: list[str]):
        self._proc = subprocess.Popen(
            [binary, "--background-index", "--limit-results=200", *extra_args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._next_id = 0
        self.index_done = False  # set when clangd reports background indexing finished
        # Why the polite exit failed, if it did. `shutdown` must never RAISE (it
        # runs on error paths, where a second exception would mask the first), but
        # staying silent about it is how an orphaned clangd goes unnoticed — so the
        # reason is recorded here instead of thrown away.
        self.shutdown_error: str | None = None
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        assert self._proc.stdout is not None
        while True:
            msg = _read_message(self._proc.stdout)
            self._inbox.put(msg)
            if msg is None:
                return

    def _send(self, payload: dict[str, Any]) -> None:
        """Write one framed message to clangd.

        A clangd that has died leaves us writing into a closed pipe, which raises
        BrokenPipeError (an OSError). Every caller in this module handles
        ``TimeoutError`` and ``RuntimeError`` and converts them into an error
        dict — an OSError would sail straight past all of them and out of the
        tool. So a dead pipe is reported as the RuntimeError it amounts to: clangd
        is gone.
        """
        assert self._proc.stdin is not None
        data = json.dumps(payload).encode("utf-8")
        try:
            self._proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except (OSError, ValueError) as e:
            # ValueError is what a closed (rather than broken) pipe raises.
            raise RuntimeError(f"clangd is no longer accepting input: {e}") from e

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        self._next_id += 1
        req_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        return self._await_response(req_id, timeout)

    def _handle_incidental(self, msg: dict[str, Any]) -> None:
        """Process a message that isn't our awaited response: reply to clangd's
        server→client requests (e.g. ``window/workDoneProgress/create`` — clangd
        can stall waiting for the reply) and note when background indexing ends."""
        if "method" in msg and "id" in msg:  # server-initiated request
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
            return
        if msg.get("method") == "$/progress":
            params = msg.get("params", {})
            value = params.get("value", {})
            # Bind index-done to clangd's specific background-index token, NOT to
            # any progress `end`. If clangd ever emits another progress stream, its
            # `end` must not flip this — a false `index_complete: True` would be a
            # silent under-report. Conservative: an unrecognised token leaves it
            # False (worst case we say "incomplete" when it finished — safe).
            if params.get("token") == "backgroundIndexProgress" and value.get("kind") == "end":
                self.index_done = True

    def _await_response(self, req_id: int, timeout: float) -> Any:
        """Pump the inbox until the response with ``req_id`` arrives, handling the
        notifications/requests clangd interleaves. Raises on timeout or if clangd
        dies first."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"clangd did not answer within {timeout}s")
            try:
                msg = self._inbox.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"clangd did not answer within {timeout}s") from None
            if msg is None:
                raise RuntimeError("clangd exited before responding")
            if msg.get("id") == req_id and "method" not in msg:  # our response
                if "error" in msg:
                    raise RuntimeError(f"clangd error: {msg['error']}")
                return msg.get("result")
            self._handle_incidental(msg)

    def wait_index(self, timeout: float) -> None:
        """Drain notifications until background indexing reports done (so a
        cross-file query like references/call-hierarchy sees the whole tree) or
        ``timeout`` elapses. Safe to call only when no response is outstanding."""
        deadline = time.monotonic() + timeout
        while not self.index_done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                msg = self._inbox.get(timeout=remaining)
            except queue.Empty:
                return
            if msg is None:
                return
            self._handle_incidental(msg)

    def shutdown(self) -> None:
        """Ask clangd to exit, then GUARANTEE it is gone and reaped.

        The polite path (LSP ``shutdown`` + ``exit``) may legitimately fail —
        clangd can already be dead, or the pipe closed. That is not a reason to
        act as though nothing happened: the cause lands in ``shutdown_error``
        while termination itself is unconditional. Killing without the following
        ``wait`` would leave a zombie child, so the reap is not optional.
        """
        # Attempt the polite exit whenever the channel is still open. Gating on
        # "is the process alive" instead would race: clangd can die between the
        # check and the write. Gating on the pipe also makes a second shutdown
        # (the `_prepare` cleanup path can overlap a caller's own) a no-op.
        if self._proc.stdin is not None and not self._proc.stdin.closed:
            try:
                self.request("shutdown", {}, timeout=5.0)
                self.notify("exit", {})
            except (TimeoutError, RuntimeError, OSError) as e:
                # BrokenPipeError is an OSError; listing it separately added
                # nothing. We narrow to the failures a dying clangd really
                # produces — anything else is a bug and must not be swallowed.
                self.shutdown_error = f"{type(e).__name__}: {e}"
        try:
            self._proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self.shutdown_error = "clangd ignored the exit request; killed"
            self._proc.kill()
            self._proc.wait()  # reap, or the child lingers as a zombie
        # The reader thread ends on its own once stdout hits EOF, which the dead
        # process guarantees. Join before closing so we never yank the pipe out
        # from under a blocked `readline`.
        self._reader.join(timeout=5.0)
        for pipe in (self._proc.stdin, self._proc.stdout):
            assert pipe is not None  # both were created as pipes in __init__
            with contextlib.suppress(OSError):
                pipe.close()


# --------------------------------------------------------------------------- #
# Pure helpers. Everything below this comment is a plain value->value function:
# it builds a request or reshapes a reply, and touches neither the subprocess nor
# the network. That is the whole point of the split — the LSP wire shapes clangd
# can legally send (a WorkspaceSymbol with no range, a hover with a list body, a
# definition returned bare instead of in a list) are the fiddly part, and they are
# cheap to test exhaustively here instead of by coaxing a real server into
# producing each one.
# --------------------------------------------------------------------------- #


def _root_uri(root: str) -> str:
    return "file://" + os.path.abspath(os.path.expanduser(root))


def _uri(path: Path) -> str:
    return "file://" + str(path)


def _loc(node: dict[str, Any]) -> dict[str, Any]:
    """An LSP Location or LocationLink -> ``{file, line}`` with a 1-based line.
    A LocationLink names its target under ``targetUri``/``targetSelectionRange``
    rather than ``uri``/``range``, and either may be missing entirely."""
    uri = node.get("uri") or node.get("targetUri") or ""
    rng = node.get("range") or node.get("targetSelectionRange") or node.get("targetRange") or {}
    return {"file": _path_from_uri(uri), "line": _one_based(rng.get("start", {}).get("line"))}


def _path_from_uri(uri: str) -> str:
    """``file:///a/b.c`` -> ``/a/b.c``; anything else is passed through unchanged."""
    return uri[len("file://"):] if uri.startswith("file://") else uri


def _extra_args(compile_commands_dir: str | None) -> list[str]:
    """The clangd command-line arguments implied by ``compile_commands_dir``."""
    if not compile_commands_dir:
        return []
    return [f"--compile-commands-dir={Path(compile_commands_dir).expanduser()}"]


def _kind_name(kind: Any) -> str:
    """An LSP SymbolKind number -> a readable name, or the number as text when it
    is a kind we do not name."""
    return _SYMBOL_KINDS.get(kind, str(kind))


def _one_based(line: Any) -> int | None:
    """An LSP 0-based line -> a 1-based one; None when clangd omitted it."""
    return (line + 1) if isinstance(line, int) else None


def _compdb_seed_candidate(entries: Any, cc_dir: Path) -> Path | None:
    """The file named by the FIRST compile-DB entry, resolved against ``cc_dir``
    when it is relative — or None when the parsed DB has no usable first entry.
    Takes already-parsed JSON and touches no disk, so every shape a real
    ``compile_commands.json`` can have (not a list, empty, entry not an object,
    no ``file`` key, relative path, absolute path) is testable directly."""
    if not isinstance(entries, list) or not entries:
        return None
    first = entries[0]
    if not isinstance(first, dict):
        return None
    name = first.get("file", "")
    if not name:
        return None
    f = Path(name)
    return f if f.is_absolute() else cc_dir / f


def _symbols_from_workspace(raw: Any) -> list[dict[str, Any]]:
    """A ``workspace/symbol`` reply -> our symbol rows. A WorkspaceSymbol may carry
    only ``{uri}`` while a SymbolInformation carries a full range, so the line can
    legitimately be absent; a non-list reply yields no symbols."""
    out: list[dict[str, Any]] = []
    for sym in raw if isinstance(raw, list) else []:
        loc = sym.get("location", {})
        out.append({
            "name": sym.get("name", ""),
            "kind": _kind_name(sym.get("kind")),
            "container": sym.get("containerName") or "",
            "file": _path_from_uri(loc.get("uri", "")),
            "line": _one_based(loc.get("range", {}).get("start", {}).get("line")),
        })
    return out


def _calls_from_hierarchy(raw: Any, direction: str) -> list[dict[str, Any]]:
    """A ``callHierarchy/{in,out}goingCalls`` reply -> call rows. Incoming calls
    name the caller under ``from``, outgoing calls name the callee under ``to``."""
    key = "from" if direction == "incoming" else "to"
    calls: list[dict[str, Any]] = []
    for c in raw if isinstance(raw, list) else []:
        item = c.get(key, {})
        loc = _loc(item)
        calls.append({"name": item.get("name", ""), "kind": _kind_name(item.get("kind")),
                      "file": loc["file"], "line": loc["line"]})
    return calls


def _definition_from(defn: Any) -> dict[str, Any] | None:
    """A ``textDocument/definition`` reply -> ``{file, line}``. clangd may answer
    with a list of locations, a single bare location, or null."""
    if isinstance(defn, list):
        return _loc(defn[0]) if defn else None
    if isinstance(defn, dict):
        return _loc(defn)
    return None


def _hover_text(hov: Any) -> str:
    """A ``textDocument/hover`` reply -> plain text. ``contents`` is a MarkupContent
    object, a list of marked strings, a bare string, or null, depending on the
    server and the LSP version it speaks."""
    contents = hov.get("contents") if isinstance(hov, dict) else None
    if isinstance(contents, dict):
        text = contents.get("value", "")
    elif isinstance(contents, list):
        text = "\n".join(c.get("value", "") if isinstance(c, dict) else str(c)
                         for c in contents)
    else:
        text = contents or ""
    return str(text).strip()


def _is_unsupported_method(error_text: str) -> bool:
    """Whether a clangd error means "this build has no such request". Older builds
    (clangd 18) implement incoming call hierarchy but not outgoing, and answer
    JSON-RPC ``-32601``; that is a version limit, not a defect on our side."""
    return "-32601" in error_text or "method not found" in error_text.lower()


_LANG_ID = {".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
            ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp"}


def _seed_file(root: Path,
               compile_commands_dir: str | None) -> tuple[Path | None, str | None]:
    """A source file to ``didOpen`` so clangd starts indexing, plus a warning when
    the compile database could not be used.

    clangd indexes the whole ``compile_commands.json`` in the background only once
    a file is opened — so we open the FIRST compile-DB entry (or, failing that, the
    first source file under ``root``); that one open kicks off the tree-wide index
    that ``workspace/symbol`` then answers from.

    A malformed or unreadable ``compile_commands.json`` still falls back to any
    source file under ``root``, but it is REPORTED rather than passed over in
    silence: seeding from an arbitrary file indexes far less of the tree, and a
    user whose build file is broken should learn that from the result instead of
    wondering why their symbol is missing."""
    cc_dir = Path(compile_commands_dir).expanduser() if compile_commands_dir else root
    cc = cc_dir / "compile_commands.json"
    warning: str | None = None
    if cc.is_file():
        try:
            entries = json.loads(cc.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            warning = f"ignored unusable compile database {cc}: {e}"
            entries = None
        if entries is not None:
            candidate = _compdb_seed_candidate(entries, cc_dir)
            if candidate is None:
                warning = f"compile database {cc} named no usable first entry"
            elif candidate.is_file():
                return candidate, warning
            else:
                warning = f"compile database {cc} names a missing file: {candidate}"
    for ext in _LANG_ID:
        found = next(iter(sorted(root.rglob(f"*{ext}"))), None)
        if found is not None:
            return found, warning
    return None, warning


def symbol_search(query: str, root: str, compile_commands_dir: str | None = None,
                  limit: int = 100, index_timeout: float = 60.0) -> dict[str, Any]:
    """Search C/C++ symbols matching ``query`` across the tree at ``root`` via
    clangd's ``workspace/symbol``.

    ``compile_commands_dir`` points clangd at the build's ``compile_commands.json``
    (defaults to clangd's own search up from ``root``). ``index_timeout`` bounds
    the wait for the first background index. Returns ``{ok, query, root, matched,
    returned, symbols}``; each symbol is ``{name, kind, container, file, line}``.
    """
    binary = find_tool(*_CLANGD_NAMES)
    if binary is None:
        return {"ok": False, "error": "clangd not found on PATH"}
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        return {"ok": False, "error": f"root is not a directory: {root}"}

    client = _Clangd(binary, _extra_args(compile_commands_dir))
    seed_warning: str | None = None
    try:
        client.request("initialize", {
            "processId": os.getpid(),
            "rootUri": _root_uri(root),
            "capabilities": {
                "workspace": {"symbol": {}, "workDoneProgress": True},
                "window": {"workDoneProgress": True},
            },
        }, timeout=index_timeout)
        client.notify("initialized", {})
        # Open one file so clangd starts indexing the whole compile DB (it won't
        # without an open file); workspace/symbol then answers tree-wide.
        seed, seed_warning = _seed_file(root_path, compile_commands_dir)
        if seed is not None:
            ext = seed.suffix.lower()
            client.notify("textDocument/didOpen", {"textDocument": {
                "uri": "file://" + str(seed),
                "languageId": _LANG_ID.get(ext, "c"),
                "version": 1,
                "text": seed.read_text(encoding="utf-8", errors="replace"),
            }})
        # workspace/symbol answers from the index, which builds in the background.
        # Retry until clangd reports indexing done (each request() also pumps the
        # progress notifications that flip index_done) or we get a hit — bounded by
        # index_timeout. A still-empty result after indexing is a genuine no-match.
        deadline = time.monotonic() + index_timeout
        result = client.request("workspace/symbol", {"query": query}, timeout=index_timeout)
        while not result and not client.index_done and time.monotonic() < deadline:
            time.sleep(0.4)
            result = client.request("workspace/symbol", {"query": query},
                                    timeout=max(1.0, deadline - time.monotonic()))
    except (TimeoutError, RuntimeError) as e:
        client.shutdown()
        return {"ok": False, "error": str(e)}
    client.shutdown()

    symbols = _symbols_from_workspace(result)
    out: dict[str, Any] = {
        "ok": True,
        "query": query,
        "root": str(root_path),
        # False = indexing didn't finish within index_timeout, so results may be
        # PARTIAL (a fresh huge tree). The on-disk index cache warms, so a retry
        # (or a larger index_timeout) completes it — never a silent under-report.
        "index_complete": client.index_done,
        "matched": len(symbols),
        "returned": min(len(symbols), limit),
        "symbols": symbols[:limit],
    }
    if seed_warning is not None:
        out["seed_warning"] = seed_warning
    return out


# --------------------------------------------------------------------------- #
# Navigation tools (document symbols, references, call hierarchy, describe).
# All share one flow: connect → open the file → (cross-file: wait for index) →
# resolve the symbol's position from the file's own symbols → issue the request.
# Resolving a name to a position means callers pass a SYMBOL NAME, never raw
# line/col — the position is an LSP implementation detail we hide.
# --------------------------------------------------------------------------- #


def _flatten_symbols(nodes: Any) -> list[dict[str, Any]]:
    """clangd returns hierarchical ``DocumentSymbol[]`` (name/kind/range/
    selectionRange/children) or flat ``SymbolInformation[]``. Flatten either into
    rows carrying the name-position (``selectionRange``) used to drive requests."""
    out: list[dict[str, Any]] = []
    for n in nodes if isinstance(nodes, list) else []:
        if "location" in n:  # SymbolInformation
            rng = n["location"].get("range", {})
        else:  # DocumentSymbol
            rng = n.get("selectionRange") or n.get("range") or {}
        out.append({
            "name": n.get("name", ""),
            "kind": _kind_name(n.get("kind")),
            "line": (rng.get("start", {}).get("line", 0)) + 1,
            "_pos": rng.get("start", {"line": 0, "character": 0}),
        })
        out.extend(_flatten_symbols(n.get("children", [])))
    return out


def _connect(binary: str, root: str, compile_commands_dir: str | None,
             timeout: float) -> _Clangd:
    """Spawn clangd and complete the LSP handshake (initialize + initialized).

    The subprocess exists from the moment ``_Clangd`` is constructed, so a
    handshake that fails must take it down here — otherwise the caller receives an
    exception with no object to shut down, and clangd survives as an orphan."""
    client = _Clangd(binary, _extra_args(compile_commands_dir))
    try:
        client.request("initialize", {
            "processId": os.getpid(),
            "rootUri": _root_uri(root),
            "capabilities": {
                "textDocument": {"documentSymbol": {"hierarchicalDocumentSymbolSupport": True}},
                "window": {"workDoneProgress": True},
            },
        }, timeout=timeout)
        client.notify("initialized", {})
    except BaseException:
        client.shutdown()
        raise
    return client


def _open(client: _Clangd, path: Path) -> str:
    uri = _uri(path)
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": uri,
        "languageId": _LANG_ID.get(path.suffix.lower(), "c"),
        "version": 1,
        "text": path.read_text(encoding="utf-8", errors="replace"),
    }})
    return uri


def _resolve(client: _Clangd, uri: str, symbol: str,
             timeout: float) -> dict[str, Any] | None:
    """The LSP position of ``symbol``'s name in the open file, via documentSymbol
    (exact name first, then substring), or None if not found."""
    syms = _flatten_symbols(
        client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}}, timeout))
    pos = next((s["_pos"] for s in syms if s["name"] == symbol), None)
    if pos is None:
        pos = next((s["_pos"] for s in syms if symbol in s["name"]), None)
    return pos


def _prepare(path: str, symbol: str | None, *, need_index: bool,
             compile_commands_dir: str | None,
             timeout: float) -> tuple[_Clangd, str, dict[str, Any] | None] | dict[str, Any]:
    """Shared setup for the navigation tools: validate the file, connect, open it,
    optionally wait for the index, and resolve ``symbol`` to a position. Returns
    ``(client, uri, position)`` on success or an ``{ok: False}`` error dict."""
    p = Path(path).expanduser()
    if not p.is_file():
        return {"ok": False, "error": f"cannot read {path}: no such file"}
    if p.suffix.lower() not in _LANG_ID:
        return {"ok": False, "error": f"not a C/C++ file: {path}"}
    binary = find_tool(*_CLANGD_NAMES)
    if binary is None:
        return {"ok": False, "error": "clangd not found on PATH"}
    root = compile_commands_dir or str(p.parent)
    try:
        client = _connect(binary, root, compile_commands_dir, timeout)
    except (TimeoutError, RuntimeError) as e:
        # `_connect` already killed its own client before re-raising.
        return {"ok": False, "error": str(e)}
    # From here a live clangd exists, so EVERY exit must shut it down. `handed_off`
    # flips only once the client becomes the caller's responsibility; the `finally`
    # covers the error returns below and any exception we do not convert to a dict
    # (an OSError from reading the file, KeyboardInterrupt) — each of which used to
    # leave clangd running with nobody holding a reference to it.
    handed_off = False
    try:
        uri = _open(client, p)
        if need_index:
            client.wait_index(timeout)
        position = _resolve(client, uri, symbol, timeout) if symbol is not None else None
        if symbol is not None and position is None:
            return {"ok": False, "error": f"symbol not found in file: {symbol!r}"}
        handed_off = True
        return client, uri, position
    except (TimeoutError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}
    finally:
        if not handed_off:
            client.shutdown()


def document_symbols(path: str, timeout: float = 30.0) -> dict[str, Any]:
    """List every symbol defined in a single C/C++ file (functions, types, vars) —
    the per-file menu of ``wrap_functions`` candidates. Needs no project index, so
    it is fast. Returns ``{ok, path, count, symbols:[{name, kind, line}]}``."""
    prepared = _prepare(path, None, need_index=False, compile_commands_dir=None,
                        timeout=timeout)
    if isinstance(prepared, dict):
        return prepared
    client, uri, _ = prepared
    try:
        raw = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}},
                             timeout)
    except (TimeoutError, RuntimeError) as e:
        client.shutdown()
        return {"ok": False, "error": str(e)}
    client.shutdown()
    syms = [{"name": s["name"], "kind": s["kind"], "line": s["line"]}
            for s in _flatten_symbols(raw)]
    return {"ok": True, "path": str(Path(path).expanduser()), "count": len(syms),
            "symbols": syms}


def references(path: str, symbol: str, compile_commands_dir: str | None = None,
               limit: int = 200, index_timeout: float = 60.0) -> dict[str, Any]:
    """Every call/use site of ``symbol`` (defined in ``path``) across the tree, via
    ``textDocument/references`` — who calls it / how hot it is. Cross-file, so it
    waits for the background index. Returns ``{ok, symbol, matched, returned,
    references:[{file, line}]}``."""
    prepared = _prepare(path, symbol, need_index=True,
                        compile_commands_dir=compile_commands_dir, timeout=index_timeout)
    if isinstance(prepared, dict):
        return prepared
    client, uri, position = prepared
    try:
        raw = client.request("textDocument/references", {
            "textDocument": {"uri": uri}, "position": position,
            "context": {"includeDeclaration": False},
        }, index_timeout)
    except (TimeoutError, RuntimeError) as e:
        client.shutdown()
        return {"ok": False, "error": str(e)}
    index_complete = client.index_done
    client.shutdown()
    refs = [_loc(r) for r in (raw if isinstance(raw, list) else [])]
    return {"ok": True, "symbol": symbol, "index_complete": index_complete,
            "matched": len(refs), "returned": min(len(refs), limit),
            "references": refs[:limit]}


def call_hierarchy(path: str, symbol: str, direction: str = "incoming",
                   compile_commands_dir: str | None = None,
                   index_timeout: float = 60.0) -> dict[str, Any]:
    """Callers (``direction="incoming"``) or callees (``"outgoing"``) of ``symbol``,
    via ``textDocument/prepareCallHierarchy`` + ``callHierarchy/{in,out}goingCalls``.
    The sharpest tool for choosing what to ``wrap_functions`` along a call path.
    Returns ``{ok, symbol, direction, calls:[{name, kind, file, line}]}``.

    NOTE: ``outgoing`` requires a clangd that implements ``outgoingCalls`` — older
    builds (e.g. clangd 18) support only ``incoming`` and return a clean
    "does not support" error for ``outgoing``."""
    if direction not in ("incoming", "outgoing"):
        return {"ok": False, "error": "direction must be 'incoming' or 'outgoing'"}
    prepared = _prepare(path, symbol, need_index=True,
                        compile_commands_dir=compile_commands_dir, timeout=index_timeout)
    if isinstance(prepared, dict):
        return prepared
    client, uri, position = prepared
    try:
        items = client.request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": uri}, "position": position}, index_timeout)
        if not items:
            index_complete = client.index_done
            client.shutdown()
            return {"ok": True, "symbol": symbol, "direction": direction,
                    "index_complete": index_complete, "calls": []}
        method = f"callHierarchy/{direction}Calls"
        raw = client.request(method, {"item": items[0]}, index_timeout)
    except (TimeoutError, RuntimeError) as e:
        client.shutdown()
        # Older clangd (e.g. 18) implements incomingCalls but NOT outgoingCalls →
        # -32601 method not found. Report that honestly instead of a raw protocol
        # error, so the caller knows it's a clangd-version limit, not a bug.
        if _is_unsupported_method(str(e)):
            return {"ok": False, "error": f"this clangd build does not support "
                    f"{direction} call hierarchy (needs a newer clangd)"}
        return {"ok": False, "error": str(e)}
    index_complete = client.index_done
    client.shutdown()
    return {"ok": True, "symbol": symbol, "direction": direction,
            "index_complete": index_complete,
            "calls": _calls_from_hierarchy(raw, direction)}


def describe_symbol(path: str, symbol: str,
                    compile_commands_dir: str | None = None,
                    timeout: float = 30.0) -> dict[str, Any]:
    """The definition location + hover (type/signature/doc) of ``symbol`` in
    ``path``, via ``textDocument/definition`` and ``textDocument/hover``. Returns
    ``{ok, symbol, definition:{file,line}, hover}``."""
    prepared = _prepare(path, symbol, need_index=False,
                        compile_commands_dir=compile_commands_dir, timeout=timeout)
    if isinstance(prepared, dict):
        return prepared
    client, uri, position = prepared
    try:
        defn = client.request("textDocument/definition", {
            "textDocument": {"uri": uri}, "position": position}, timeout)
        hov = client.request("textDocument/hover", {
            "textDocument": {"uri": uri}, "position": position}, timeout)
    except (TimeoutError, RuntimeError) as e:
        client.shutdown()
        return {"ok": False, "error": str(e)}
    client.shutdown()
    return {"ok": True, "symbol": symbol, "definition": _definition_from(defn),
            "hover": _hover_text(hov)}
