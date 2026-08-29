"""Tests for the clang-tidy lint and clangd symbol-search tools.

Both shell out to real LLVM binaries, so they skip when the binary is absent.
The lint tests exercise the full instrument→lint loop: a deliberate bug is
caught, and the instrumentation's own `__ouro` reserved-identifier noise is
filtered (never reported as a user problem)."""

from __future__ import annotations

import io
import os
import pathlib
import shutil
import time

import pytest

from ouroboros.clangtools import (
    call_hierarchy,
    clangd as clangd_mod,
    describe_symbol,
    document_symbols,
    flags as flags_mod,
    lint as lint_mod,
    lint_file,
    references,
    symbol_search,
)
from ouroboros.clangtools.lint import _CLANG_TIDY_NAMES, _is_instrumentation_noise
from ouroboros.languages.c_lang import CTransformer

# Probe with the SAME binary-name lists the code itself probes. Hardcoding a
# second list here is how a skip silently outlives the thing it guarded: this
# suite used to stop at clangd-20 while the module already looked for clangd-22.
has_clang_tidy = any(shutil.which(b) for b in _CLANG_TIDY_NAMES)
has_clangd = any(shutil.which(b) for b in clangd_mod._CLANGD_NAMES)

needs_clangd = pytest.mark.skipif(not has_clangd, reason="clangd not available")
needs_clang_tidy = pytest.mark.skipif(not has_clang_tidy,
                                      reason="clang-tidy not available")


def live_clangd_children():
    """PIDs of clangd processes that are still OUR live children.

    Reads /proc directly rather than shelling out, and skips zombies ('Z'): a
    zombie has already exited and is merely awaiting a wait(), whereas anything
    else is a real clangd still holding memory and an index lock."""
    alive = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                fields = fh.read().rsplit(b")", 1)[1].split()
            if int(fields[1]) != os.getpid():   # not our child
                continue
            if fields[0].decode() == "Z":       # exited, just unreaped
                continue
            if "clangd" in os.path.basename(os.readlink(f"/proc/{pid}/exe")):
                alive.append(int(pid))
        except (OSError, IndexError, ValueError):
            continue
    return alive

_BUGGY_C = (
    "#include <stdlib.h>\n"
    "int compute(int a, int b) {\n"
    "    int *p = malloc(sizeof(int));\n"
    "    *p = a + b;\n"
    "    int r = *p;\n"
    "    free(p);\n"
    "    if (a = b) { return r * 2; }\n"   # '=' not '==' -> clang-tidy flags it
    "    return r;\n"
    "}\n"
)


def test_noise_filter_unit():
    # our injected identifiers, reserved-id check -> filtered
    assert _is_instrumentation_noise(
        "bugprone-reserved-identifier",
        "declaration uses identifier '__ouro', which is a reserved identifier")
    assert _is_instrumentation_noise(
        "bugprone-reserved-identifier", "identifier '_ouro_result' is reserved")
    # a user's own reserved-id elsewhere is NOT filtered
    assert not _is_instrumentation_noise(
        "bugprone-reserved-identifier", "identifier '__my_thing' is reserved")
    # a real bug is never mistaken for noise
    assert not _is_instrumentation_noise(
        "bugprone-assignment-in-if-condition", "an assignment within an 'if'")


def test_lint_rejects_non_c_file(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    res = lint_file(str(f))
    assert res["ok"] is False and "C/C++" in res["error"]


def test_lint_missing_file():
    res = lint_file("/nonexistent/nope.c")
    assert res["ok"] is False and "no such file" in res["error"]


@pytest.mark.skipif(not has_clang_tidy, reason="clang-tidy not available")
def test_lint_catches_real_bug(tmp_path):
    f = tmp_path / "demo.c"
    f.write_text(_BUGGY_C, encoding="utf-8")
    res = lint_file(str(f))
    assert res["ok"] is True and res["language"] == "c"
    checks = {d["check"] for d in res["diagnostics"]}
    # the `if (a = b)` assignment is the headline bug clang-tidy should surface
    assert any("assignment-in-if-condition" in c or "parentheses" in c for c in checks), \
        res["diagnostics"]


@pytest.mark.skipif(not has_clang_tidy, reason="clang-tidy not available")
def test_lint_filters_instrumentation_noise(tmp_path):
    """After instrumenting, the `__ouro` reserved-id diagnostics must be filtered
    out (counted, not reported) — we never report our own wrapper as a problem."""
    f = tmp_path / "demo.c"
    f.write_text(_BUGGY_C, encoding="utf-8")
    wrapped = CTransformer().wrap_source(_BUGGY_C, filename=str(f))
    f.write_text(wrapped.code, encoding="utf-8")
    # drop the runtime header next to it so the injected #include resolves
    name, src = CTransformer().runtime_asset()
    (tmp_path / name).write_text(src, encoding="utf-8")

    res = lint_file(str(f))
    assert res["ok"] is True
    # no surviving diagnostic mentions our injected identifiers
    assert not any("ouro" in d["message"] for d in res["diagnostics"]), res["diagnostics"]
    # every reported diagnostic is in the linted file — never in the runtime header
    # (clang-tidy 22's analyzer flags our header's snprintf/_ouro_enter; the
    # file-scope filter drops those regardless of clang-tidy version)
    assert all(d["file"].endswith("demo.c") for d in res["diagnostics"]), res["diagnostics"]
    # and the real bug still comes through
    checks = {d["check"] for d in res["diagnostics"]}
    assert any("assignment-in-if-condition" in c or "parentheses" in c for c in checks)


@pytest.mark.skipif(not has_clangd, reason="clangd not available")
def test_symbol_search_finds_function(tmp_path):
    (tmp_path / "lib.c").write_text(
        "int ouro_demo_add(int a, int b) { return a + b; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        f'[{{"directory": "{tmp_path}", "command": "clang -c lib.c", '
        f'"file": "{tmp_path / "lib.c"}"}}]\n', encoding="utf-8")
    res = symbol_search("ouro_demo_add", str(tmp_path),
                        compile_commands_dir=str(tmp_path), index_timeout=30.0)
    assert res["ok"] is True, res
    assert "index_complete" in res  # honesty flag: indexing finished or partial
    assert any(s["name"] == "ouro_demo_add" for s in res["symbols"]), res["symbols"]


def test_symbol_search_bad_root(tmp_path):
    res = symbol_search("x", str(tmp_path / "nope"))
    assert res["ok"] is False


# ---- navigation tools (clangd) --------------------------------------------- #

# Two files so cross-file references/call-hierarchy have something to find:
# main() calls helper(), defined in lib.c.
def _nav_tree(tmp_path):
    (tmp_path / "lib.c").write_text(
        "int ouro_helper(int x) { return x + 1; }\n", encoding="utf-8")
    (tmp_path / "main.c").write_text(
        '#include "lib.h"\nint ouro_main(void) { return ouro_helper(41); }\n',
        encoding="utf-8")
    (tmp_path / "lib.h").write_text("int ouro_helper(int x);\n", encoding="utf-8")
    cc = (f'[{{"directory":"{tmp_path}","command":"clang -c lib.c","file":"{tmp_path}/lib.c"}},'
          f'{{"directory":"{tmp_path}","command":"clang -c main.c","file":"{tmp_path}/main.c"}}]\n')
    (tmp_path / "compile_commands.json").write_text(cc, encoding="utf-8")
    return tmp_path


def test_document_symbols_non_c(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert document_symbols(str(f))["ok"] is False


def test_references_missing_file():
    assert references("/nonexistent/x.c", "f")["ok"] is False


@pytest.mark.skipif(not has_clangd, reason="clangd not available")
def test_document_symbols_lists_functions(tmp_path):
    t = _nav_tree(tmp_path)
    res = document_symbols(str(t / "main.c"))
    assert res["ok"] is True
    names = {s["name"] for s in res["symbols"]}
    assert "ouro_main" in names


@pytest.mark.skipif(not has_clangd, reason="clangd not available")
def test_describe_symbol_hover_and_definition(tmp_path):
    t = _nav_tree(tmp_path)
    res = describe_symbol(str(t / "lib.c"), "ouro_helper", compile_commands_dir=str(t))
    assert res["ok"] is True
    assert res["definition"] and res["definition"]["file"].endswith("lib.c")
    assert "ouro_helper" in res["hover"]  # signature shows the name


@pytest.mark.skipif(not has_clangd, reason="clangd not available")
def test_references_finds_call_site(tmp_path):
    t = _nav_tree(tmp_path)
    res = references(str(t / "lib.c"), "ouro_helper", compile_commands_dir=str(t))
    assert res["ok"] is True
    assert "index_complete" in res
    # the call in main.c is a reference
    assert any(r["file"].endswith("main.c") for r in res["references"]), res


@pytest.mark.skipif(not has_clangd, reason="clangd not available")
def test_call_hierarchy_incoming(tmp_path):
    t = _nav_tree(tmp_path)
    res = call_hierarchy(str(t / "lib.c"), "ouro_helper", direction="incoming",
                         compile_commands_dir=str(t))
    assert res["ok"] is True
    # ouro_main calls ouro_helper -> incoming caller is ouro_main
    assert any(c["name"] == "ouro_main" for c in res["calls"]), res


def test_call_hierarchy_bad_direction(tmp_path):
    f = tmp_path / "x.c"
    f.write_text("int f(void){return 0;}\n", encoding="utf-8")
    assert call_hierarchy(str(f), "f", direction="sideways")["ok"] is False


@pytest.mark.skipif(not has_clangd, reason="clangd not available")
def test_call_hierarchy_outgoing_never_crashes(tmp_path):
    """outgoingCalls is unsupported by older clangd (18) — the tool must report
    that cleanly, never surface a raw -32601 protocol error or raise."""
    t = _nav_tree(tmp_path)
    res = call_hierarchy(str(t / "main.c"), "ouro_main", direction="outgoing",
                         compile_commands_dir=str(t))
    assert "ok" in res
    if not res["ok"]:
        assert "call hierarchy" in res["error"]  # the friendly version-limit message
    else:
        assert isinstance(res["calls"], list)


# --------------------------------------------------------------------------- #
# Process lifecycle. These are the regression tests for two defects: a clangd
# left running after a failed setup, and a shutdown that reported success no
# matter what happened.
# --------------------------------------------------------------------------- #


@needs_clangd
def test_failed_setup_leaves_no_running_clangd(tmp_path):
    """A setup that fails after clangd is already spawned must still take it down.

    `_prepare` used to return its error dict straight from the `except`, with the
    live client only referenced by a local that then went out of scope — so every
    timed-out call orphaned a clangd process that kept running, holding its index.
    A 1 ms timeout makes the handshake fail deterministically.
    """
    src = tmp_path / "demo.c"
    src.write_text("int demo_fn(int a) { return a + 1; }\n", encoding="utf-8")
    before = live_clangd_children()

    res = document_symbols(str(src), timeout=0.001)

    assert res["ok"] is False and "did not answer" in res["error"]
    # Give a would-be orphan every chance to show up before we look.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        leaked = [pid for pid in live_clangd_children() if pid not in before]
        if not leaked:
            break
        time.sleep(0.1)
    assert not leaked, f"clangd left running after a failed setup: {leaked}"


@needs_clangd
def test_successful_call_leaves_no_running_clangd(tmp_path):
    """The happy path must not leak either — the client is handed to the caller,
    which is responsible for shutting it down once it has its answer."""
    t = _nav_tree(tmp_path)
    before = live_clangd_children()
    assert document_symbols(str(t / "main.c"))["ok"] is True
    assert [pid for pid in live_clangd_children() if pid not in before] == []


@needs_clangd
def test_shutdown_records_why_the_polite_exit_failed(tmp_path):
    """When clangd cannot answer the LSP `shutdown`, the reason is recorded.

    The old handler caught four exception types and did nothing with them, so a
    server that had already died looked exactly like a clean exit. Killing clangd
    underneath the client makes the request fail for real — no substitute process,
    just a genuinely dead one.
    """
    binary = clangd_mod.find_tool(*clangd_mod._CLANGD_NAMES)
    client = clangd_mod._Clangd(binary, [])
    client._proc.kill()
    client._proc.wait()          # dead and reaped before we even ask it to exit

    client.shutdown()

    assert client.shutdown_error is not None, "a failed exit was reported as success"
    assert client._proc.poll() is not None


@needs_clangd
def test_shutdown_is_silent_when_the_exit_is_clean(tmp_path):
    """The mirror of the test above: a clangd that exits properly records nothing,
    so `shutdown_error` means something when it is set."""
    binary = clangd_mod.find_tool(*clangd_mod._CLANGD_NAMES)
    client = clangd_mod._Clangd(binary, [])
    client.request("initialize", {"processId": os.getpid(), "rootUri": None,
                                  "capabilities": {}}, timeout=30.0)
    client.notify("initialized", {})

    client.shutdown()

    assert client.shutdown_error is None
    assert client._proc.poll() is not None


@needs_clangd
def test_shutdown_twice_is_harmless(tmp_path):
    """`_prepare`'s cleanup can run on a client a caller also shuts down. The
    second call must not raise on the pipes the first one closed."""
    binary = clangd_mod.find_tool(*clangd_mod._CLANGD_NAMES)
    client = clangd_mod._Clangd(binary, [])
    client.shutdown()
    client.shutdown()            # must not raise
    assert client._proc.poll() is not None


@needs_clangd
def test_shutdown_reaps_the_child(tmp_path):
    """Killing without waiting leaves a zombie. Whichever way clangd goes down,
    the child must be reaped, not merely dead."""
    binary = clangd_mod.find_tool(*clangd_mod._CLANGD_NAMES)
    client = clangd_mod._Clangd(binary, [])
    pid = client._proc.pid
    client.shutdown()
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)   # already reaped -> no such child left


# --------------------------------------------------------------------------- #
# Pure helpers. These need no clangd: they are the value->value half of the
# module — building a request, reshaping a reply. The LSP shapes below are the
# ones the protocol genuinely permits and clangd genuinely varies between, which
# is exactly why they are worth pinning here rather than hoping a live server
# happens to produce each one.
# --------------------------------------------------------------------------- #


def test_read_message_parses_a_framed_body():
    body = b'{"id": 1, "result": {"x": 2}}'
    stream = io.BytesIO(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    assert clangd_mod._read_message(stream) == {"id": 1, "result": {"x": 2}}


def test_read_message_ignores_other_headers():
    body = b'{"ok": true}'
    stream = io.BytesIO(
        b"Content-Type: application/vscode-jsonrpc\r\n"
        b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    assert clangd_mod._read_message(stream) == {"ok": True}


def test_read_message_returns_none_at_eof():
    assert clangd_mod._read_message(io.BytesIO(b"")) is None


def test_read_message_without_a_length_yields_an_empty_message():
    """Headers that never declare a Content-Length describe a zero-byte body; the
    reader must not then try to read a negative or unbounded amount."""
    assert clangd_mod._read_message(io.BytesIO(b"\r\n")) == {}


def test_path_from_uri_strips_only_the_file_scheme():
    assert clangd_mod._path_from_uri("file:///a/b.c") == "/a/b.c"
    assert clangd_mod._path_from_uri("/a/b.c") == "/a/b.c"
    assert clangd_mod._path_from_uri("") == ""


def test_extra_args_only_when_a_compile_dir_is_given():
    assert clangd_mod._extra_args(None) == []
    assert clangd_mod._extra_args("") == []
    assert clangd_mod._extra_args("/build") == ["--compile-commands-dir=/build"]


def test_extra_args_expands_a_home_relative_dir():
    got = clangd_mod._extra_args("~/build")
    assert got == [f"--compile-commands-dir={os.path.expanduser('~/build')}"]
    assert "~" not in got[0]


def test_kind_name_maps_known_kinds_and_passes_others_through():
    assert clangd_mod._kind_name(12) == "function"
    assert clangd_mod._kind_name(23) == "struct"
    assert clangd_mod._kind_name(99) == "99"      # a kind we deliberately do not name
    assert clangd_mod._kind_name(None) == "None"  # clangd omitted it entirely


def test_one_based_converts_lines_and_keeps_absence_as_none():
    assert clangd_mod._one_based(0) == 1
    assert clangd_mod._one_based(41) == 42
    assert clangd_mod._one_based(None) is None
    assert clangd_mod._one_based("7") is None     # a string is not a line number


def test_loc_reads_both_location_and_locationlink_shapes():
    plain = {"uri": "file:///a.c", "range": {"start": {"line": 4}}}
    assert clangd_mod._loc(plain) == {"file": "/a.c", "line": 5}
    link = {"targetUri": "file:///b.c", "targetSelectionRange": {"start": {"line": 0}}}
    assert clangd_mod._loc(link) == {"file": "/b.c", "line": 1}
    # a LocationLink with only the wider targetRange
    wide = {"targetUri": "file:///c.c", "targetRange": {"start": {"line": 9}}}
    assert clangd_mod._loc(wide) == {"file": "/c.c", "line": 10}
    assert clangd_mod._loc({}) == {"file": "", "line": None}


def test_symbols_from_workspace_handles_both_reply_shapes():
    raw = [
        # SymbolInformation: carries a full range
        {"name": "f", "kind": 12, "containerName": "N",
         "location": {"uri": "file:///a.c", "range": {"start": {"line": 2}}}},
        # WorkspaceSymbol: uri only, no range -> line is legitimately unknown
        {"name": "g", "kind": 23, "location": {"uri": "file:///b.c"}},
    ]
    assert clangd_mod._symbols_from_workspace(raw) == [
        {"name": "f", "kind": "function", "container": "N", "file": "/a.c", "line": 3},
        {"name": "g", "kind": "struct", "container": "", "file": "/b.c", "line": None},
    ]


def test_symbols_from_workspace_on_a_non_list_reply():
    """clangd answers null when it has nothing; that is no symbols, not a crash."""
    assert clangd_mod._symbols_from_workspace(None) == []
    assert clangd_mod._symbols_from_workspace({}) == []
    assert clangd_mod._symbols_from_workspace([]) == []


def test_calls_from_hierarchy_reads_from_for_incoming_and_to_for_outgoing():
    incoming = [{"from": {"name": "caller", "kind": 12,
                          "uri": "file:///m.c", "range": {"start": {"line": 1}}}}]
    assert clangd_mod._calls_from_hierarchy(incoming, "incoming") == [
        {"name": "caller", "kind": "function", "file": "/m.c", "line": 2}]
    # the same payload read as outgoing finds nothing under "to" — the key is
    # load-bearing, so a swap would be caught here
    assert clangd_mod._calls_from_hierarchy(incoming, "outgoing") == [
        {"name": "", "kind": "None", "file": "", "line": None}]
    outgoing = [{"to": {"name": "callee", "kind": 6,
                        "uri": "file:///n.c", "range": {"start": {"line": 0}}}}]
    assert clangd_mod._calls_from_hierarchy(outgoing, "outgoing") == [
        {"name": "callee", "kind": "method", "file": "/n.c", "line": 1}]


def test_calls_from_hierarchy_on_a_non_list_reply():
    assert clangd_mod._calls_from_hierarchy(None, "incoming") == []


def test_definition_from_accepts_list_bare_and_null():
    loc = {"uri": "file:///a.c", "range": {"start": {"line": 6}}}
    assert clangd_mod._definition_from([loc]) == {"file": "/a.c", "line": 7}
    assert clangd_mod._definition_from(loc) == {"file": "/a.c", "line": 7}
    assert clangd_mod._definition_from([]) is None     # searched, found nothing
    assert clangd_mod._definition_from(None) is None   # server declined to answer


def test_hover_text_reads_every_contents_shape():
    assert clangd_mod._hover_text({"contents": {"value": " int f()\n"}}) == "int f()"
    assert clangd_mod._hover_text({"contents": [{"value": "a"}, {"value": "b"}]}) == "a\nb"
    assert clangd_mod._hover_text({"contents": ["plain", {"value": "x"}]}) == "plain\nx"
    assert clangd_mod._hover_text({"contents": "  bare  "}) == "bare"
    assert clangd_mod._hover_text({"contents": None}) == ""
    assert clangd_mod._hover_text(None) == ""          # no hover at all


def test_is_unsupported_method_recognises_the_version_limit():
    assert clangd_mod._is_unsupported_method("clangd error: {'code': -32601}")
    assert clangd_mod._is_unsupported_method("Method not found")
    assert not clangd_mod._is_unsupported_method("clangd did not answer within 5s")


def test_flatten_symbols_walks_children_and_flat_replies():
    hierarchical = [{
        "name": "S", "kind": 23, "selectionRange": {"start": {"line": 0, "character": 7}},
        "children": [{"name": "m", "kind": 6,
                      "selectionRange": {"start": {"line": 1, "character": 4}}}],
    }]
    got = clangd_mod._flatten_symbols(hierarchical)
    assert [(s["name"], s["kind"], s["line"]) for s in got] == [
        ("S", "struct", 1), ("m", "method", 2)]
    # the position drives later requests, so it must be the NAME position
    assert got[1]["_pos"] == {"line": 1, "character": 4}

    flat = [{"name": "f", "kind": 12,
             "location": {"range": {"start": {"line": 3, "character": 0}}}}]
    assert clangd_mod._flatten_symbols(flat)[0]["line"] == 4


def test_flatten_symbols_falls_back_to_range_and_tolerates_junk():
    """A DocumentSymbol without selectionRange still has range; a reply that is
    not a list yields nothing rather than raising."""
    assert clangd_mod._flatten_symbols(
        [{"name": "f", "kind": 12, "range": {"start": {"line": 2}}}])[0]["line"] == 3
    assert clangd_mod._flatten_symbols(None) == []
    # a symbol with no position at all defaults to the top of the file
    bare = clangd_mod._flatten_symbols([{"name": "f", "kind": 12}])[0]
    assert bare["line"] == 1 and bare["_pos"] == {"line": 0, "character": 0}


def test_root_uri_and_uri_build_file_urls():
    assert clangd_mod._root_uri("/a/b") == "file:///a/b"
    assert clangd_mod._root_uri("~").startswith("file://")
    assert "~" not in clangd_mod._root_uri("~")
    assert clangd_mod._uri(pathlib.Path("/a/b.c")) == "file:///a/b.c"


# --------------------------------------------------------------------------- #
# Seeding. A compile database that cannot be used must SAY so: seeding from an
# arbitrary source file indexes far less of the tree, and the old code fell back
# in silence, so a broken build file looked like a missing symbol.
# --------------------------------------------------------------------------- #


def test_compdb_seed_candidate_resolves_relative_against_the_db_dir():
    cc_dir = pathlib.Path("/build")
    assert clangd_mod._compdb_seed_candidate(
        [{"file": "src/a.c"}], cc_dir) == pathlib.Path("/build/src/a.c")
    assert clangd_mod._compdb_seed_candidate(
        [{"file": "/abs/a.c"}], cc_dir) == pathlib.Path("/abs/a.c")


@pytest.mark.parametrize("entries", [
    None,               # the JSON was not a database at all
    [],                 # a database with no entries
    ["not-an-object"],  # an entry that is not an object
    [{}],               # an entry with no "file"
    [{"file": ""}],     # an entry with an empty "file"
])
def test_compdb_seed_candidate_rejects_unusable_entries(entries):
    assert clangd_mod._compdb_seed_candidate(entries, pathlib.Path("/build")) is None


def test_seed_file_prefers_the_first_compile_db_entry(tmp_path):
    (tmp_path / "z.c").write_text("int z;\n", encoding="utf-8")
    (tmp_path / "a.c").write_text("int a;\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        f'[{{"directory":"{tmp_path}","command":"clang -c z.c","file":"z.c"}}]',
        encoding="utf-8")
    seed, warning = clangd_mod._seed_file(tmp_path, str(tmp_path))
    assert seed == tmp_path / "z.c"     # the DB's choice, not the alphabetical one
    assert warning is None


def test_seed_file_reports_an_unreadable_compile_db(tmp_path):
    (tmp_path / "a.c").write_text("int a;\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text("{ not json", encoding="utf-8")
    seed, warning = clangd_mod._seed_file(tmp_path, str(tmp_path))
    assert seed == tmp_path / "a.c"     # still seeded, so the tool keeps working
    assert warning is not None and "unusable compile database" in warning


def test_seed_file_reports_a_db_naming_a_missing_file(tmp_path):
    (tmp_path / "a.c").write_text("int a;\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        f'[{{"directory":"{tmp_path}","command":"clang -c gone.c","file":"gone.c"}}]',
        encoding="utf-8")
    seed, warning = clangd_mod._seed_file(tmp_path, str(tmp_path))
    assert seed == tmp_path / "a.c"
    assert warning is not None and "names a missing file" in warning


def test_seed_file_reports_a_db_with_no_usable_entry(tmp_path):
    (tmp_path / "a.c").write_text("int a;\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text("[]", encoding="utf-8")
    seed, warning = clangd_mod._seed_file(tmp_path, str(tmp_path))
    assert seed == tmp_path / "a.c"
    assert warning is not None and "no usable first entry" in warning


def test_seed_file_falls_back_to_any_source_when_there_is_no_db(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.cpp").write_text("int d;\n", encoding="utf-8")
    seed, warning = clangd_mod._seed_file(tmp_path, None)
    assert seed == tmp_path / "sub" / "deep.cpp"   # found by recursive search
    assert warning is None


def test_seed_file_finds_nothing_in_a_tree_with_no_sources(tmp_path):
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")
    assert clangd_mod._seed_file(tmp_path, None) == (None, None)


@needs_clangd
def test_symbol_search_surfaces_a_broken_compile_database(tmp_path):
    """The warning has to reach the caller, not just exist inside the helper."""
    (tmp_path / "lib.c").write_text(
        "int ouro_warn_demo(int a) { return a; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text("{ not json", encoding="utf-8")
    res = symbol_search("ouro_warn_demo", str(tmp_path), index_timeout=20.0)
    assert res["ok"] is True
    assert "unusable compile database" in res["seed_warning"]


@needs_clangd
def test_symbol_search_is_quiet_when_the_compile_database_is_good(tmp_path):
    """The mirror: no warning key at all when nothing went wrong, so its presence
    always means something."""
    (tmp_path / "lib.c").write_text(
        "int ouro_quiet_demo(int a) { return a; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        f'[{{"directory":"{tmp_path}","command":"clang -c lib.c",'
        f'"file":"{tmp_path / "lib.c"}"}}]', encoding="utf-8")
    res = symbol_search("ouro_quiet_demo", str(tmp_path),
                        compile_commands_dir=str(tmp_path), index_timeout=30.0)
    assert res["ok"] is True and "seed_warning" not in res


# --------------------------------------------------------------------------- #
# clang-tidy output parsing. All of the judgement (what is a diagnostic, whose
# file it belongs to, what is our own noise, what is a duplicate) lives in
# parse_diagnostics and needs no subprocess.
# --------------------------------------------------------------------------- #


def test_parse_diagnostics_reads_a_normal_row(tmp_path):
    target = tmp_path / "a.c"
    target.write_text("int a;\n", encoding="utf-8")
    out = f"{target}:7:3: warning: dead store to 'x' [clang-analyzer-deadcode.DeadStores]"
    diags, filtered, elsewhere = lint_mod.parse_diagnostics(out, target.resolve())
    assert diags == [{"file": str(target), "line": 7, "col": 3, "severity": "warning",
                      "check": "clang-analyzer-deadcode.DeadStores",
                      "message": "dead store to 'x'"}]
    assert (filtered, elsewhere) == (0, 0)


def test_parse_diagnostics_keeps_errors_and_rows_without_a_check(tmp_path):
    target = tmp_path / "a.c"
    target.write_text("int a;\n", encoding="utf-8")
    out = (f"{target}:1:1: error: expected ';'\n"
           f"{target}:2:1: warning: something [some-check]\n")
    diags, _, _ = lint_mod.parse_diagnostics(out, target.resolve())
    assert [d["severity"] for d in diags] == ["error", "warning"]
    assert diags[0]["check"] == ""     # clang-tidy omits the bracket for plain errors


def test_parse_diagnostics_ignores_lines_that_are_not_diagnostics(tmp_path):
    target = tmp_path / "a.c"
    target.write_text("int a;\n", encoding="utf-8")
    out = ("2 warnings generated.\n"
           "        if (a = b) { return r * 2; }\n"
           "            ~~^~~\n"
           "Error while processing /some/file.c.\n")
    assert lint_mod.parse_diagnostics(out, target.resolve()) == ([], 0, 0)


def test_parse_diagnostics_drops_rows_from_other_files(tmp_path):
    """A diagnostic the analyzer found inside an #included header is not the
    linted file's problem — counted, never reported."""
    target = tmp_path / "a.c"
    target.write_text("int a;\n", encoding="utf-8")
    other = tmp_path / "ouroboros_runtime.h"
    other.write_text("int h;\n", encoding="utf-8")
    out = (f"{other}:3:1: warning: in the header [clang-analyzer-x]\n"
           f"{target}:4:1: warning: in the file [clang-analyzer-y]\n")
    diags, _, elsewhere = lint_mod.parse_diagnostics(out, target.resolve())
    assert [d["message"] for d in diags] == ["in the file"]
    assert elsewhere == 1


def test_parse_diagnostics_filters_only_our_own_reserved_identifiers(tmp_path):
    target = tmp_path / "a.c"
    target.write_text("int a;\n", encoding="utf-8")
    out = (f"{target}:1:1: warning: identifier '__ouro' is reserved "
           f"[bugprone-reserved-identifier]\n"
           f"{target}:2:1: warning: identifier '__user_thing' is reserved "
           f"[bugprone-reserved-identifier]\n")
    diags, filtered, _ = lint_mod.parse_diagnostics(out, target.resolve())
    assert filtered == 1
    assert [d["message"] for d in diags] == [
        "identifier '__user_thing' is reserved"]   # the user's own stays reportable


def test_parse_diagnostics_dedups_identical_rows(tmp_path):
    """clang-tidy prints the same finding on stdout and stderr on some builds; we
    scan both, so the same (file, line, col, check) must collapse to one row."""
    target = tmp_path / "a.c"
    target.write_text("int a;\n", encoding="utf-8")
    row = f"{target}:5:2: warning: same finding [check-a]"
    diags, _, _ = lint_mod.parse_diagnostics(row + "\n" + row, target.resolve())
    assert len(diags) == 1


def test_parse_diagnostics_keeps_distinct_findings_at_one_position(tmp_path):
    """Dedup must key on the check too, or a second real finding on the same line
    would vanish."""
    target = tmp_path / "a.c"
    target.write_text("int a;\n", encoding="utf-8")
    out = (f"{target}:5:2: warning: first [check-a]\n"
           f"{target}:5:2: warning: second [check-b]\n")
    diags, _, _ = lint_mod.parse_diagnostics(out, target.resolve())
    assert len(diags) == 2


def test_same_file_matches_through_a_relative_path(tmp_path, monkeypatch):
    target = tmp_path / "a.c"
    target.write_text("int a;\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert lint_mod._same_file("a.c", target.resolve())
    assert lint_mod._same_file("./a.c", target.resolve())
    assert not lint_mod._same_file("other.c", target.resolve())


def test_summarise_counts_by_severity():
    rows = [{"severity": "error"}, {"severity": "warning"}, {"severity": "warning"}]
    assert lint_mod.summarise(rows) == {"error": 1, "warning": 2}
    assert lint_mod.summarise([]) == {"error": 0, "warning": 0}


# --------------------------------------------------------------------------- #
# Flag and binary resolution.
# --------------------------------------------------------------------------- #


def test_find_tool_takes_the_first_name_present():
    assert flags_mod.find_tool("definitely-no-such-tool-xyz", "sh").endswith("sh")


def test_find_tool_returns_none_when_nothing_is_installed():
    assert flags_mod.find_tool("no-such-tool-abc", "no-such-tool-def") is None


def test_language_for_uses_the_extension_when_no_tree_config_applies(tmp_path):
    (tmp_path / "a.c").write_text("int a;\n", encoding="utf-8")
    (tmp_path / "b.cpp").write_text("int b;\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("x = 1\n", encoding="utf-8")
    assert flags_mod.language_for(str(tmp_path / "a.c")) == "c"
    assert flags_mod.language_for(str(tmp_path / "b.cpp")) == "cpp"
    assert flags_mod.language_for(str(tmp_path / "c.py")) is None


def test_language_for_lets_the_compile_database_override_the_extension(tmp_path):
    """A header compiled as C++ by the build is C++, whatever its extension says.
    That override is the whole reason the compdb is consulted first."""
    header = tmp_path / "thing.h"
    header.write_text("int t;\n", encoding="utf-8")
    compdb = tmp_path / "compile_commands.json"
    compdb.write_text(
        f'[{{"directory":"{tmp_path}","command":"clang++ -std=c++17 -c thing.h",'
        f'"file":"{header}"}}]', encoding="utf-8")
    (tmp_path / ".ouroboros.json").write_text(
        f'{{"cpp": {{"compdb": "{compdb}"}}}}', encoding="utf-8")
    assert flags_mod.language_for(str(header)) == "cpp"


def test_compile_flags_include_the_runtime_header_dirs(tmp_path):
    """The injected `#include "ouroboros_runtime.h"` has to resolve, or every
    linted instrumented file drowns in phantom 'header not found' errors."""
    src = tmp_path / "a.c"
    src.write_text("int a;\n", encoding="utf-8")
    got = flags_mod.compile_flags_for(str(src), "c")
    assert str(tmp_path) in got                    # the file's own directory
    assert str(flags_mod._C_DIR) in got            # the bundled C runtime dir
    assert got.count("-I") >= 2


def test_compile_flags_for_cpp_ask_for_the_cxx_dir(tmp_path):
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n", encoding="utf-8")
    got = flags_mod.compile_flags_for(str(src), "cpp")
    assert str(flags_mod._CPP_DIR) in got
    assert str(flags_mod._C_DIR) not in got        # the C dir would be the wrong one


def test_compile_flags_for_cpp_use_the_trees_own_flags(tmp_path):
    """When an .ouroboros.json covers the file we must parse it the way the build
    does — the tree's flags, forced to C++, not the self-contained defaults."""
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n", encoding="utf-8")
    (tmp_path / ".ouroboros.json").write_text(
        '{"cpp": {"cflags": ["-DFROM_THE_TREE=1"]}}', encoding="utf-8")
    got = flags_mod.compile_flags_for(str(src), "cpp")
    assert "-DFROM_THE_TREE=1" in got
    assert got[:2] == ["-x", "c++"]


# --------------------------------------------------------------------------- #
# The environment-failure paths: the tool this module wraps is simply not
# installed. Emptying PATH is the real condition, not a stand-in for it.
# --------------------------------------------------------------------------- #


def test_lint_reports_a_missing_clang_tidy(tmp_path, monkeypatch):
    src = tmp_path / "a.c"
    src.write_text("int a;\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))      # a directory with no binaries
    res = lint_file(str(src))
    assert res["ok"] is False and res["error"] == "clang-tidy not found on PATH"


def test_symbol_search_reports_a_missing_clangd(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    res = symbol_search("x", str(tmp_path))
    assert res["ok"] is False and res["error"] == "clangd not found on PATH"


def test_navigation_reports_a_missing_clangd(tmp_path, monkeypatch):
    src = tmp_path / "a.c"
    src.write_text("int a;\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))
    res = document_symbols(str(src))
    assert res["ok"] is False and res["error"] == "clangd not found on PATH"


def test_lint_reports_a_clang_tidy_that_will_not_start(tmp_path, monkeypatch):
    """find_tool sees a name on PATH but exec fails — a broken install, reported
    as such instead of raising out of the tool."""
    fake = tmp_path / "clang-tidy"
    fake.write_text("", encoding="utf-8")          # present but not executable
    fake.chmod(0o644)
    src = tmp_path / "a.c"
    src.write_text("int a;\n", encoding="utf-8")
    monkeypatch.setattr(lint_mod, "find_tool", lambda *names: str(fake))
    res = lint_file(str(src))
    assert res["ok"] is False and "failed to run" in res["error"]


@needs_clang_tidy
def test_lint_reports_its_own_timeout(tmp_path):
    src = tmp_path / "a.c"
    src.write_text("int a;\n", encoding="utf-8")
    res = lint_file(str(src), timeout=0.001)
    assert res["ok"] is False and "timed out" in res["error"]


@needs_clang_tidy
def test_lint_honours_a_caller_supplied_check_list(tmp_path):
    """A caller's `checks` string reaches clang-tidy and shapes the report. The
    leading `-*` is clang-tidy's own syntax for clearing its built-in defaults —
    without it `--checks=X` ADDS to them, which is why the default set has to be
    switched off explicitly to prove the argument took effect."""
    src = tmp_path / "demo.c"
    src.write_text(_BUGGY_C, encoding="utf-8")
    narrow = "-*,bugprone-assignment-in-if-condition"
    res = lint_file(str(src), checks=narrow)
    assert res["ok"] is True and res["checks"] == narrow
    assert {d["check"] for d in res["diagnostics"]} == {
        "bugprone-assignment-in-if-condition"}
    # and the default run does find more than that, so the narrowing is real
    wide = {d["check"] for d in lint_file(str(src))["diagnostics"]}
    assert wide > {"bugprone-assignment-in-if-condition"}


# --------------------------------------------------------------------------- #
# Protocol-level failures against a REAL clangd: a server that has exited, and
# deadlines that have already passed. No substitute server is involved — the
# process is genuinely started and genuinely killed.
# --------------------------------------------------------------------------- #


def _fresh_client():
    binary = clangd_mod.find_tool(*clangd_mod._CLANGD_NAMES)
    return clangd_mod._Clangd(binary, [])


@needs_clangd
def test_request_reports_a_clangd_that_died_as_a_runtime_error():
    """Writing to a dead clangd raises BrokenPipeError, an OSError — which none of
    the tools catch, so it would escape as an unhandled crash instead of becoming
    an `{ok: False}` result. It has to arrive as RuntimeError like every other
    "clangd let us down" condition."""
    client = _fresh_client()
    client._proc.kill()
    client._proc.wait()
    with pytest.raises(RuntimeError):
        client.request("initialize", {"processId": os.getpid(), "rootUri": None,
                                      "capabilities": {}}, timeout=30.0)
    client.shutdown()


@needs_clangd
def test_a_dead_clangd_becomes_an_error_result_not_a_crash(tmp_path):
    """The same defect seen from the outside: the public tool must return a
    result, not raise, when clangd is not there any more."""
    t = _nav_tree(tmp_path)
    real_prepare = clangd_mod._prepare

    def prepare_then_kill(*args, **kwargs):
        out = real_prepare(*args, **kwargs)
        if isinstance(out, tuple):      # a live session was handed over
            out[0]._proc.kill()
            out[0]._proc.wait()
        return out

    # The handshake and the session are real; we kill the real server midway,
    # which is the one condition we cannot ask a healthy clangd to produce.
    monkey = pytest.MonkeyPatch()
    monkey.setattr(clangd_mod, "_prepare", prepare_then_kill)
    try:
        res = document_symbols(str(t / "main.c"))
    finally:
        monkey.undo()
    assert res["ok"] is False and "clangd" in res["error"]


@needs_clangd
def test_request_reports_an_already_expired_deadline():
    """A deadline in the past must fail immediately, not block on the queue."""
    client = _fresh_client()
    with pytest.raises(TimeoutError, match="did not answer"):
        client.request("initialize", {"processId": os.getpid(), "rootUri": None,
                                      "capabilities": {}}, timeout=0)
    client.shutdown()


@needs_clangd
def test_request_surfaces_an_error_reply_from_clangd():
    """clangd answering with a JSON-RPC error is a RuntimeError carrying the code,
    which is what the call-hierarchy version check later reads."""
    client = _fresh_client()
    client.request("initialize", {"processId": os.getpid(), "rootUri": None,
                                  "capabilities": {}}, timeout=30.0)
    client.notify("initialized", {})
    with pytest.raises(RuntimeError, match="clangd error"):
        client.request("no/such/method", {}, timeout=30.0)
    client.shutdown()


@needs_clangd
def test_wait_index_gives_up_on_an_expired_deadline():
    client = _fresh_client()
    client.wait_index(0)                  # already past the deadline
    assert client.index_done is False     # honest: unfinished, not assumed done
    client.shutdown()


@needs_clangd
def test_wait_index_gives_up_when_nothing_arrives():
    """An idle clangd with no file open sends no progress at all; the bounded wait
    has to end anyway, reporting the index as incomplete rather than hanging."""
    client = _fresh_client()
    client.request("initialize", {"processId": os.getpid(), "rootUri": None,
                                  "capabilities": {}}, timeout=30.0)
    client.notify("initialized", {})
    started = time.monotonic()
    client.wait_index(0.3)
    assert time.monotonic() - started < 10.0
    assert client.index_done is False
    client.shutdown()


@needs_clangd
def test_wait_index_stops_when_clangd_exits():
    client = _fresh_client()
    client._proc.kill()
    client._proc.wait()
    client.wait_index(30.0)               # returns on EOF, not after 30 seconds
    assert client.index_done is False
    client.shutdown()


@needs_clangd
def test_handle_incidental_answers_server_initiated_requests():
    """clangd stalls waiting for a reply to window/workDoneProgress/create, so an
    unanswered server request is a hang, not a nuisance."""
    client = _fresh_client()
    client.request("initialize", {"processId": os.getpid(), "rootUri": None,
                                  "capabilities": {}}, timeout=30.0)
    client.notify("initialized", {})
    client._handle_incidental({"id": 7, "method": "window/workDoneProgress/create",
                               "params": {"token": "t"}})
    client.shutdown()
    assert client.shutdown_error is None  # the reply went out on a healthy pipe


def test_index_done_binds_only_to_the_background_index_token():
    """A progress `end` from some other stream must NOT flip index_done — that
    would silently promote partial results to 'complete'."""
    client = clangd_mod._Clangd.__new__(clangd_mod._Clangd)   # no subprocess needed
    client.index_done = False
    client._handle_incidental({"method": "$/progress", "params": {
        "token": "someOtherThing", "value": {"kind": "end"}}})
    assert client.index_done is False
    client._handle_incidental({"method": "$/progress", "params": {
        "token": "backgroundIndexProgress", "value": {"kind": "end"}}})
    assert client.index_done is True


def test_index_done_ignores_progress_that_is_not_an_end():
    client = clangd_mod._Clangd.__new__(clangd_mod._Clangd)
    client.index_done = False
    client._handle_incidental({"method": "$/progress", "params": {
        "token": "backgroundIndexProgress", "value": {"kind": "begin"}}})
    assert client.index_done is False
    client._handle_incidental({"method": "textDocument/publishDiagnostics",
                               "params": {}})     # an ordinary notification
    assert client.index_done is False


# --------------------------------------------------------------------------- #
# Whole-tool failure paths.
# --------------------------------------------------------------------------- #


@needs_clangd
def test_symbol_search_reports_a_handshake_that_timed_out(tmp_path):
    (tmp_path / "a.c").write_text("int a;\n", encoding="utf-8")
    before = live_clangd_children()
    res = symbol_search("a", str(tmp_path), index_timeout=0.001)
    assert res["ok"] is False and "did not answer" in res["error"]
    assert [pid for pid in live_clangd_children() if pid not in before] == []


@needs_clangd
def test_symbol_search_copes_with_a_tree_that_has_no_sources(tmp_path):
    """Nothing to seed the index with; the search still answers instead of
    failing, it just finds nothing."""
    (tmp_path / "README.md").write_text("no code here\n", encoding="utf-8")
    res = symbol_search("anything", str(tmp_path), index_timeout=10.0)
    assert res["ok"] is True and res["symbols"] == [] and res["matched"] == 0


@needs_clangd
def test_symbol_search_limit_caps_what_is_returned(tmp_path):
    (tmp_path / "lib.c").write_text(
        "".join(f"int ouro_many_{i}(void) {{ return {i}; }}\n" for i in range(5)),
        encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        f'[{{"directory":"{tmp_path}","command":"clang -c lib.c",'
        f'"file":"{tmp_path / "lib.c"}"}}]', encoding="utf-8")
    res = symbol_search("ouro_many_", str(tmp_path), compile_commands_dir=str(tmp_path),
                        limit=2, index_timeout=30.0)
    assert res["ok"] is True and res["matched"] >= 3
    assert res["returned"] == 2 and len(res["symbols"]) == 2


@needs_clangd
def test_navigation_reports_a_symbol_the_file_does_not_define(tmp_path):
    t = _nav_tree(tmp_path)
    before = live_clangd_children()
    res = references(str(t / "lib.c"), "no_such_symbol_at_all", compile_commands_dir=str(t))
    assert res["ok"] is False and "symbol not found" in res["error"]
    # the failed lookup must not strand the clangd it started
    assert [pid for pid in live_clangd_children() if pid not in before] == []


@needs_clangd
def test_resolve_falls_back_to_a_substring_match(tmp_path):
    """Callers pass a name, not a line and column. An exact match is preferred,
    but a partial name still resolves rather than reporting 'not found'."""
    t = _nav_tree(tmp_path)
    res = describe_symbol(str(t / "lib.c"), "helper", compile_commands_dir=str(t))
    assert res["ok"] is True
    assert "ouro_helper" in res["hover"]


@needs_clangd
def test_call_hierarchy_on_something_that_is_not_callable(tmp_path):
    """A struct has no call hierarchy. clangd answers with no items, which is an
    empty answer, not a failure."""
    src = tmp_path / "types.c"
    src.write_text("struct OuroThing { int a; };\nint ouro_use(void){return 0;}\n",
                   encoding="utf-8")
    res = call_hierarchy(str(src), "OuroThing", direction="incoming")
    assert res["ok"] is True and res["calls"] == []


@needs_clangd
def test_call_hierarchy_reports_a_missing_file():
    assert call_hierarchy("/nonexistent/x.c", "f")["ok"] is False


@needs_clangd
def test_describe_symbol_reports_a_missing_file():
    assert describe_symbol("/nonexistent/x.c", "f")["ok"] is False


def test_document_symbols_reports_a_missing_file():
    res = document_symbols("/nonexistent/x.c")
    assert res["ok"] is False and "no such file" in res["error"]


@needs_clangd
def test_await_response_reports_the_readers_end_of_stream():
    """The reader thread signals a dead clangd by putting None on the inbox. The
    response pump must turn that into an error, not wait out the whole timeout."""
    client = _fresh_client()
    client._proc.kill()
    client._proc.wait()
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="exited before responding"):
        client._await_response(1, timeout=30.0)
    assert time.monotonic() - started < 10.0   # it noticed, it did not wait
    client.shutdown()


def _kill_clangd_after_prepare(monkeypatch):
    """Let a REAL session start, then kill the real clangd behind it.

    Every navigation tool has a handler for "clangd stopped answering after the
    handshake". A healthy server cannot be asked to produce that on cue, and the
    handshake, the file open and the symbol resolution here are all genuine — the
    only thing arranged is the server's death, which is the exact condition under
    test.
    """
    real_prepare = clangd_mod._prepare

    def prepare_then_kill(*args, **kwargs):
        out = real_prepare(*args, **kwargs)
        if isinstance(out, tuple):
            out[0]._proc.kill()
            out[0]._proc.wait()
        return out

    monkeypatch.setattr(clangd_mod, "_prepare", prepare_then_kill)


@needs_clangd
@pytest.mark.parametrize("tool", ["document_symbols", "references", "call_hierarchy",
                                  "describe_symbol"])
def test_every_tool_survives_clangd_dying_mid_session(tmp_path, monkeypatch, tool):
    t = _nav_tree(tmp_path)
    before = live_clangd_children()
    _kill_clangd_after_prepare(monkeypatch)
    calls = {
        "document_symbols": lambda: document_symbols(str(t / "lib.c")),
        "references": lambda: references(str(t / "lib.c"), "ouro_helper",
                                         compile_commands_dir=str(t)),
        "call_hierarchy": lambda: call_hierarchy(str(t / "lib.c"), "ouro_helper",
                                                 compile_commands_dir=str(t)),
        "describe_symbol": lambda: describe_symbol(str(t / "lib.c"), "ouro_helper",
                                                   compile_commands_dir=str(t)),
    }
    res = calls[tool]()
    assert res["ok"] is False and res["error"]
    # and the corpse is cleaned up rather than left for the OS
    assert [pid for pid in live_clangd_children() if pid not in before] == []


@needs_clangd
def test_prepare_reports_a_session_that_breaks_during_setup(tmp_path, monkeypatch):
    """The failure lands inside `_prepare` itself, between a successful handshake
    and the symbol lookup — the window the leak fix has to cover."""
    t = _nav_tree(tmp_path)
    before = live_clangd_children()
    real_connect = clangd_mod._connect

    def connect_then_kill(*args, **kwargs):
        client = real_connect(*args, **kwargs)
        client._proc.kill()
        client._proc.wait()
        return client

    monkeypatch.setattr(clangd_mod, "_connect", connect_then_kill)
    res = describe_symbol(str(t / "lib.c"), "ouro_helper", compile_commands_dir=str(t))
    assert res["ok"] is False and "clangd" in res["error"]
    assert [pid for pid in live_clangd_children() if pid not in before] == []


# An older clangd, if one is installed alongside the current one. clangd 18
# implements incoming call hierarchy but not outgoing, which is the only way to
# exercise the version-limit branch against a server that really lacks the
# method rather than one told to pretend.
_OLD_CLANGD = next((p for p in (shutil.which(n) for n in
                                ("clangd-18", "clangd-17")) if p), None)


@pytest.mark.skipif(_OLD_CLANGD is None,
                    reason="no clangd older than outgoingCalls support installed")
def test_outgoing_call_hierarchy_on_a_clangd_that_lacks_it(tmp_path, monkeypatch):
    """The friendly message must come from a real -32601, so that what we tell the
    user ("your clangd is too old") matches what an old clangd actually does."""
    t = _nav_tree(tmp_path)
    monkeypatch.setattr(clangd_mod, "find_tool", lambda *names: _OLD_CLANGD)
    res = call_hierarchy(str(t / "main.c"), "ouro_main", direction="outgoing",
                         compile_commands_dir=str(t), index_timeout=60.0)
    assert res["ok"] is False
    assert res["error"] == ("this clangd build does not support outgoing call "
                            "hierarchy (needs a newer clangd)")
    assert "-32601" not in res["error"]   # the raw protocol code stays hidden


@pytest.mark.skipif(_OLD_CLANGD is None, reason="no older clangd installed")
def test_incoming_call_hierarchy_still_works_on_the_older_clangd(tmp_path, monkeypatch):
    """The counterpart: the older build is not simply broken, so the version
    message is about one missing method, not a blanket failure."""
    t = _nav_tree(tmp_path)
    monkeypatch.setattr(clangd_mod, "find_tool", lambda *names: _OLD_CLANGD)
    res = call_hierarchy(str(t / "lib.c"), "ouro_helper", direction="incoming",
                         compile_commands_dir=str(t), index_timeout=60.0)
    assert res["ok"] is True
    assert any(c["name"] == "ouro_main" for c in res["calls"]), res
