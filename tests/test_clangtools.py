"""Tests for the clang-tidy lint and clangd symbol-search tools.

Both shell out to real LLVM binaries, so they skip when the binary is absent.
The lint tests exercise the full instrument→lint loop: a deliberate bug is
caught, and the instrumentation's own `__ouro` reserved-identifier noise is
filtered (never reported as a user problem)."""

from __future__ import annotations

import shutil

import pytest

from ouroboros.clangtools import (
    call_hierarchy,
    describe_symbol,
    document_symbols,
    lint_file,
    references,
    symbol_search,
)
from ouroboros.clangtools.lint import _is_instrumentation_noise
from ouroboros.languages.c_lang import CTransformer

has_clang_tidy = any(shutil.which(b) for b in
                     ("clang-tidy", "clang-tidy-20", "clang-tidy-19",
                      "clang-tidy-18", "clang-tidy-17"))
has_clangd = any(shutil.which(b) for b in
                 ("clangd", "clangd-20", "clangd-19", "clangd-18", "clangd-17"))

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
