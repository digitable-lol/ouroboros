"""Tests for the C++ backend (libclang + RAII ScopeGuard instrumentation)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from ouroboros.languages import CorruptedSourceError, cpp_lang, transformer_for_path
from ouroboros.languages.clangbridge import clang_resource_dir_args
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
    # The escaper is exercised through std::string, which carries its own length
    # and is therefore safe to read. A `const char *` is NOT read for content any
    # more — the type does not promise NUL-termination, and reading to the first
    # zero runs off the end of a pointer-to-one-char. Both its argument and its
    # result now render as an address.
    assert 'a"b\\c' in by_name["boxed"].args
    assert "\n" in by_name["boxed"].args
    assert re.fullmatch(r"0x[0-9a-f]+", by_name["echo"].args)
    assert re.fullmatch(r"0x[0-9a-f]+", by_name["echo"].outcome)
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


# --------------------------------------------------------------------------- #
# Where the libstdc++ headers come from.
#
# `_cxx_args` asks g++ where it looks for system headers and hands the answer to
# the parser as `-isystem`. Without it a self-contained C++ file that includes
# anything from the standard library does not parse, and the corruption gate
# blames the file. The three cases below are the ones the host this runs on
# never produces: g++ answering in a shape the reader does not fully recognise,
# naming a directory that is not there, or not being on the machine at all.
# --------------------------------------------------------------------------- #

@pytest.fixture
def fresh_cxx_args():
    """`_cxx_args` caches its one answer for the process; these cases replace it,
    so the cache is emptied on the way in and on the way out."""
    cpp_lang._cxx_args.cache_clear()
    yield
    cpp_lang._cxx_args.cache_clear()


def _fake_gxx(stderr, monkeypatch):
    """Answer for g++ only; everything else (the clang resource-dir probe next
    door) still goes to the real subprocess."""
    real_run = subprocess.run

    def run(argv, **kwargs):
        if argv[0] != "g++":
            return real_run(argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", run)


def test_only_real_directories_from_the_search_list_are_passed_on(
        fresh_cxx_args, monkeypatch, tmp_path):
    """g++ prints paths it merely considered, and a tree can be moved or cleaned
    between builds. A `-isystem` pointing at nothing is not inert: clang counts
    it, and a stale one hides the failure that the header search found nothing."""

    real = tmp_path / "real-include"
    real.mkdir()
    gone = tmp_path / "removed-include"           # never created
    _fake_gxx(
        'ignoring nonexistent directory "/nowhere"\n'
        '#include "..." search starts here:\n'
        "#include <...> search starts here:\n"
        f" {real}\n"
        f" {gone}\n"
        "End of search list.\n", monkeypatch)

    args = cpp_lang._cxx_args()

    assert args[args.index(str(real)) - 1] == "-isystem"
    assert str(gone) not in args
    assert "#include <...> search starts here:" not in args     # the heading is not a path


def test_a_search_list_with_no_terminator_is_still_used(
        fresh_cxx_args, monkeypatch, tmp_path):
    """"End of search list." ends the list; a build that stops early, a g++ whose
    output was truncated, or a future wording leaves it out. The directories
    already read are still the right ones — dropping them would silently strip
    every libstdc++ path and turn ordinary C++ into "corrupted source"."""

    real = tmp_path / "real-include"
    real.mkdir()
    _fake_gxx("#include <...> search starts here:\n"
              f" {real}\n", monkeypatch)                        # nothing follows

    args = cpp_lang._cxx_args()

    assert ["-isystem", str(real)] == args[-2:]
    assert args[:3] == ["-x", "c++", "-std=c++17"]              # base flags intact


def test_no_gxx_on_the_machine_leaves_the_base_flags(fresh_cxx_args, monkeypatch):
    """A machine with clang but no g++ still has to wrap plain C++. Asking a g++
    that is not there raises, and that must cost the header paths, not the wrap."""

    real_run = subprocess.run

    def missing(argv, **kwargs):
        if argv[0] != "g++":
            return real_run(argv, **kwargs)
        raise FileNotFoundError(2, "No such file or directory", "g++")

    monkeypatch.setattr(subprocess, "run", missing)

    args = cpp_lang._cxx_args()

    # Exactly the self-contained base, and nothing g++ would have contributed.
    assert args == ["-x", "c++", "-std=c++17", "-ferror-limit=0",
                    *clang_resource_dir_args()]
