"""Tests for the C++ backend (libclang + RAII ScopeGuard instrumentation)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ouroboros.languages import CorruptedSourceError, transformer_for_path
from ouroboros.languages.cpp_lang import CppTransformer
from ouroboros.sandbox import Project, execute, write_file
from ouroboros.trace import load

has_gxx = shutil.which("g++") is not None


@pytest.fixture
def tx() -> CppTransformer:
    return CppTransformer()


def test_registry_resolves_cpp():
    assert isinstance(transformer_for_path("a.cpp"), CppTransformer)
    assert isinstance(transformer_for_path("a.cc"), CppTransformer)


def test_basic_wrap(tx):
    res = tx.wrap_source("int add(int a, int b) {\n    return a + b;\n}\n", filename="m.cpp")
    assert res.functions_wrapped == 1
    assert '#include "ouroboros_runtime.hpp"' in res.code
    assert '_ouro::Scope __ouro("add"' in res.code
    assert "_ouro::capture(__ouro, (a + b))" in res.code


def test_qualified_name_for_method(tx):
    src = ("namespace ns {\nstruct C {\n  int m(int x) { return x; }\n};\n}\n")
    res = tx.wrap_source(src, filename="m.cpp")
    assert '_ouro::Scope __ouro("ns::C::m"' in res.code


def test_idempotent(tx):
    once = tx.wrap_source("int f(int x) {\n    return x;\n}\n", filename="m.cpp").code
    again = tx.wrap_source(once, filename="m.cpp")
    assert again.functions_wrapped == 0


def test_constructor_is_skipped(tx):
    src = "struct C {\n  int v;\n  C(int x) { v = x; }\n  int get() { return v; }\n};\n"
    res = tx.wrap_source(src, filename="m.cpp")
    # only get() is wrapped, not the constructor
    assert res.functions_wrapped == 1
    assert '"C::get"' in res.code


def test_void_no_capture(tx):
    res = tx.wrap_source("void p(int n) {\n    (void)n;\n}\n", filename="m.cpp")
    assert res.functions_wrapped == 1
    assert "_ouro::capture" not in res.code
    assert '_ouro::Scope __ouro("p"' in res.code


def test_lambda_returns_not_wrapped(tx):
    src = ("int f(int n) {\n"
           "    auto g = [](int z){ return z * 2; };\n"
           "    return g(n);\n}\n")
    res = tx.wrap_source(src, filename="m.cpp")
    # only the outer return is captured; the lambda's return is left alone
    assert res.code.count("_ouro::capture") == 1


def test_corrupted_cpp_raises(tx):
    with pytest.raises(CorruptedSourceError):
        tx.wrap_source("int broken( {\n", filename="bad.cpp")


def test_runtime_asset_is_header(tx):
    name, src = tx.runtime_asset()
    assert name == "ouroboros_runtime.hpp"
    assert '\\"p\\":\\"in\\"' in src and "uncaught_exceptions" in src


@pytest.mark.skipif(not has_gxx, reason="g++ not available")
def test_schema_matches_spec(tmp_path):
    name, header = CppTransformer().runtime_asset()
    (tmp_path / name).write_text(header, encoding="utf-8")
    src = CppTransformer().wrap_source(
        "int add(int a, int b) {\n    return a + b;\n}\n", filename="prog.cpp").code
    src += "\nint main(){ add(2,3); return 0; }\n"
    (tmp_path / "prog.cpp").write_text(src, encoding="utf-8")
    subprocess.run(["g++", "-std=c++17", "prog.cpp", "-o", "app"],
                   cwd=tmp_path, check=True, capture_output=True)
    debug = tmp_path / "debug.info"
    subprocess.run(["./app"], cwd=tmp_path, check=True,
                   env={"OUROBOROS_DEBUG_INFO": str(debug), "PATH": "/usr/bin:/bin"})
    # Parse, then normalize the volatile fields (t, id, d) and compare to the
    # hand-written contract from SPEC.md.
    lines = [json.loads(ln) for ln in
             debug.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # identity is real before it is blanked: a std::thread::id token and ci=-1
    # (a th that regressed to "" would still pass the blanked schema below)
    assert lines[0]["th"] and isinstance(lines[0]["ci"], int)
    for ev in lines:
        for k in ("t", "id", "d", "ci", "th"):
            if k in ev:
                ev[k] = "<X>"
    assert lines == [
        {"p": "in", "t": "<X>", "id": "<X>", "ci": "<X>", "th": "<X>",
         "fn": "add", "a": "2, 3", "k": ""},
        {"p": "out", "id": "<X>", "fn": "add", "r": "5", "d": "<X>"},
    ]


@pytest.mark.skipif(not has_gxx, reason="g++ not available")
def test_cpp_escapes_special_chars(tmp_path):
    """Exercise the `"`/`\\`/control-char paths of the hand-rolled _jesc — the
    integer-arg tests only hit its pass-through branch. A broken escaper emits a
    malformed JSON line (data silently lost) rather than failing a green test."""
    name, header = CppTransformer().runtime_asset()
    (tmp_path / name).write_text(header, encoding="utf-8")
    src = CppTransformer().wrap_source(
        "#include <string>\n"
        "const char *echo(const char *s) {\n    return s;\n}\n"
        "std::string boxed(std::string s) {\n    return s;\n}\n",
        filename="prog.cpp").code
    src += ('\nint main(){ const char *p = "a\\"b\\\\c\\nd";'
            " echo(p); boxed(std::string(p)); return 0; }\n")
    (tmp_path / "prog.cpp").write_text(src, encoding="utf-8")
    subprocess.run(["g++", "-std=c++17", "prog.cpp", "-o", "app"],
                   cwd=tmp_path, check=True, capture_output=True)
    debug = tmp_path / "debug.info"
    subprocess.run(["./app"], cwd=tmp_path, check=True,
                   env={"OUROBOROS_DEBUG_INFO": str(debug), "PATH": "/usr/bin:/bin"})
    loaded = load(debug.read_text(encoding="utf-8"))
    assert loaded.malformed == 0            # quote/backslash/newline didn't break the line
    by_name = {c.name: c for c in loaded.calls}
    assert 'a"b\\c' in by_name["echo"].args                     # arg repr round-tripped
    assert 'a"b\\c' in by_name["echo"].outcome                  # and the result too
    assert "\n" in by_name["echo"].outcome
    assert 'a"b\\c' in by_name["boxed"].args
    # A class type returned BY VALUE is deliberately not captured: routing it
    # through _ouro::capture costs a copy elision, so a program that counted its
    # own constructors would print something it never printed unwrapped. The
    # arguments, duration and outcome kind are still recorded; only the returned
    # object's repr is given up. See cpp_lang._captures_safely.
    assert by_name["boxed"].outcome == "(no value)"


@pytest.mark.skipif(not has_gxx, reason="g++ not available")
def test_end_to_end_via_sandbox(tmp_path):
    proj = Project.create(tmp_path / "site")
    src = (
        "#include <cstdio>\n"
        "int square(int n) {\n    return n * n;\n}\n"
        "int main() {\n    std::printf(\"%d\\n\", square(6));\n    return 0;\n}\n"
    )
    out = write_file(proj, "m.cpp", src)
    assert out.wrapped and out.language == "cpp"
    assert (proj.draft / "ouroboros_runtime.hpp").is_file()

    comp = execute(proj, ["g++", "-std=c++17", "m.cpp", "-o", "m"])
    assert comp.returncode == 0, comp.stderr
    run = execute(proj, ["./m"])
    assert run.returncode == 0 and "36" in run.stdout
    loaded = load(proj.debug_info_path().read_text(encoding="utf-8"))
    assert loaded.malformed == 0
    square = [c for c in loaded.calls if c.name == "square"]
    assert len(square) == 1
    assert square[0].outcome_kind == "result" and square[0].outcome == "36"
