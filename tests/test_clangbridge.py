"""Tests for the out-of-process libclang boundary shared by C and C++.

What is being guarded here is the *contract*: one native program, one JSON
shape, one set of offsets, for both languages. The backends above it are text
splicers, so anything they get wrong that the parser could have told them shows
up as a wrong number in this file's assertions rather than as broken C.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from ouroboros.languages import clangbridge
from ouroboros.languages.base import CorruptedSourceError
from ouroboros.languages.c_lang import CTransformer, _clang_args
from ouroboros.languages.clangbridge import (
    ClangEmitterError,
    ClangUnit,
    build_emitter,
    emit_ranges,
    emitter_path,
    gate_diagnostics,
    libclang_library,
)
from ouroboros.languages.cpp_lang import CppTransformer, _cxx_args

_CLANG_DIR = Path(clangbridge.__file__).parent / "_clang"


def _c_args() -> list[str]:
    return [*_clang_args(), "-I", str(_CLANG_DIR.parent / "_c")]


def _cpp_args() -> list[str]:
    return [*_cxx_args(), "-I", str(_CLANG_DIR.parent / "_cpp")]


def _emit_c(source: str, filename: str = "probe.c") -> ClangUnit:
    return emit_ranges(source.encode("utf-8"), language="c",
                       filename=filename, args=_c_args())


def _emit_cpp(source: str, filename: str = "probe.cpp") -> ClangUnit:
    return emit_ranges(source.encode("utf-8"), language="cpp",
                       filename=filename, args=_cpp_args())


# --------------------------------------------------------------------------- #
# the contract itself
# --------------------------------------------------------------------------- #


def test_one_emitter_answers_for_both_languages():
    """C and C++ are one contract: the same binary, told which dialect to use.

    Two emitters would have been two contracts to keep in step, which is the
    duplication this boundary exists to end.
    """

    c = _emit_c("int f(int a) { return a; }\n")
    cpp = _emit_cpp("int f(int a) { return a; }\n")
    assert [fn.name for fn in c.functions] == ["f"]
    assert [fn.name for fn in cpp.functions] == ["f"]
    assert c.functions[0].body_start == cpp.functions[0].body_start
    assert c.functions[0].returns[0].arg_start == cpp.functions[0].returns[0].arg_start


def test_offsets_point_at_the_bytes_the_splicer_expects():
    source = "int f(int a) { return a + 1; }\n"
    fn = _emit_c(source).functions[0]
    raw = source.encode("utf-8")
    assert raw[fn.body_start:fn.body_start + 1] == b"{"
    assert raw[fn.body_end - 1:fn.body_end] == b"}"
    assert raw[fn.extent_start:fn.extent_start + 3] == b"int"
    ret = fn.returns[0]
    assert raw[ret.arg_start:ret.arg_end] == b"a + 1"


def test_offsets_are_bytes_not_characters():
    """Non-ASCII source is the case a character-offset boundary gets wrong."""

    source = '/* здравствуй */\nint f(int a) { return a; }\n'
    fn = _emit_c(source).functions[0]
    raw = source.encode("utf-8")
    assert raw[fn.body_start:fn.body_start + 1] == b"{"
    assert fn.body_start != source.index("{"), "byte and character offsets coincide"


def test_parameter_types_come_back_as_printf_specifiers():
    """The type reading the C backend used to do inline, done by the parser."""

    unit = _emit_c(
        "int f(int i, unsigned u, long l, unsigned long ul, long long ll,\n"
        "      double d, long double ld, const char *s, char *m, struct S *p) { return i; }\n"
        "struct S { int x; };\n"
    )
    specs = [(p.name, p.spec, p.is_string) for p in unit.functions[0].params]
    assert specs == [
        ("i", "%d", False), ("u", "%u", False), ("l", "%ld", False),
        ("ul", "%lu", False), ("ll", "%lld", False), ("d", "%f", False),
        ("ld", "%Lf", False), ("s", "%s", True), ("m", "%p", False),
        ("p", "%p", False),
    ]


def test_unprintable_parameter_has_no_specifier():
    unit = _emit_c("struct S { int x; };\nint f(struct S s) { return s.x; }\n")
    param = unit.functions[0].params[0]
    assert param.spec is None and param.is_string is False


def test_unnamed_parameter_comes_back_with_an_empty_name():
    """The backends decide what to call it (C says `_`, C++ drops it)."""

    unit = _emit_c("int f(int, int b) { return b; }\n")
    assert [p.name for p in unit.functions[0].params] == ["", "b"]


@pytest.mark.parametrize(
    ("declaration", "temp_type"),
    [
        ("int f(void)", "int"),
        ("const int f(void)", "int"),
        ("volatile int f(void)", "int"),
        ("const volatile int f(void)", "int"),
        ("const char *f(void)", "const char *"),
        ("unsigned long f(void)", "unsigned long"),
    ],
)
def test_result_temp_type_strips_top_level_qualifiers(declaration, temp_type):
    """`__ouro_result` has to be assignable, so a top-level const is stripped."""

    body = " { return 0; }\n" if "char" not in declaration else ' { return "x"; }\n'
    unit = _emit_c(declaration + body)
    assert unit.functions[0].result.temp_type == temp_type


def test_void_result_has_no_temp_type():
    result = _emit_c("void f(void) { return; }\n").functions[0].result
    assert result.is_void is True and result.temp_type is None and result.spec is None


def test_bare_return_reports_no_argument():
    fn = _emit_c("void f(int a) { if (a) return; return; }\n").functions[0]
    assert [(r.arg_start, r.arg_end) for r in fn.returns] == [(None, None), (None, None)]


def test_struct_result_is_marked_as_a_record():
    """What tells the C++ backend not to route the value through `capture`."""

    unit = _emit_cpp("struct P { int a; };\nP make() { P p; return p; }\n")
    fn = next(f for f in unit.functions if f.name == "make")
    assert fn.result.is_record is True


def test_braced_return_is_marked_as_an_initialiser_list():
    unit = _emit_cpp("struct P { int a, b; };\nP make() { return {1, 2}; }\n")
    fn = next(f for f in unit.functions if f.name == "make")
    assert fn.returns[0].is_init_list is True


def test_returns_inside_a_lambda_belong_to_the_lambda():
    source = textwrap.dedent("""\
        int run(int n) {
            auto f = [](int x) { return x + 1; };
            return f(n);
        }
        """)
    unit = _emit_cpp(source)
    fn = next(f for f in unit.functions if f.name == "run")
    raw = source.encode("utf-8")
    assert [raw[r.arg_start:r.arg_end] for r in fn.returns] == [b"f(n)"]


def test_qualified_name_walks_namespaces_and_classes():
    source = textwrap.dedent("""\
        namespace outer { namespace inner {
        struct Widget { int value() { return 1; } };
        } }
        """)
    unit = _emit_cpp(source)
    fn = next(f for f in unit.functions if f.name == "value")
    assert fn.qualified_name == "outer::inner::Widget::value"


def test_c_reports_no_qualified_name_beyond_the_plain_one():
    fn = _emit_c("int f(void) { return 0; }\n").functions[0]
    assert fn.qualified_name == "f"


def test_constexpr_is_reported_for_cpp_only():
    cpp = _emit_cpp("constexpr int sq(int x) { return x * x; }\n")
    assert cpp.functions[0].is_constexpr is True
    plain = _emit_cpp("int sq(int x) { return x * x; }\n")
    assert plain.functions[0].is_constexpr is False


def test_constructors_and_destructors_are_not_reported():
    """The C++ backend must not wrap special members; they never arrive."""

    source = textwrap.dedent("""\
        struct W {
          W() {}
          ~W() {}
          operator int() const { return 0; }
          int value() { return 1; }
        };
        """)
    assert [f.name for f in _emit_cpp(source).functions] == ["value"]


def test_declarations_from_headers_are_not_reported():
    """Only the file being wrapped is instrumentable; #include'd code is not."""

    unit = _emit_c("#include <stdio.h>\nint f(void) { return 0; }\n")
    assert [f.name for f in unit.functions] == ["f"]


def test_macro_generated_function_points_inside_the_macro():
    """The fact the C backend's brace check depends on.

    A function produced by a macro is reported at the EXPANSION location, so its
    body offset lands inside the macro invocation rather than on a `{`.
    """

    source = "#define MAKE(n) int n(int x) { return x; }\nMAKE(twice)\n"
    fn = _emit_c(source).functions[0]
    assert source.encode()[fn.body_start:fn.body_start + 1] != b"{"


def test_body_already_wrapped_is_left_to_the_caller():
    """The emitter reports; it never decides. Wrapping twice adds nothing."""

    tx = CTransformer()
    once = tx.wrap_source("int f(int a) { return a; }\n", filename="f.c")
    twice = tx.wrap_source(once.code, filename="f.c")
    assert once.functions_wrapped == 1
    assert twice.functions_wrapped == 0
    assert twice.code == once.code


# --------------------------------------------------------------------------- #
# diagnostics
# --------------------------------------------------------------------------- #


def test_errors_are_counted_in_full_and_sampled_in_part():
    unit = _emit_c("int f(void) { return undefined_a + undefined_b + undefined_c; }\n")
    assert unit.error_count >= 3
    assert 0 < len(unit.errors) <= 5
    assert all(isinstance(message, str) and message for message in unit.errors)


def test_gate_raises_on_the_first_error_when_strict():
    unit = ClangUnit(functions=(), error_count=2, errors=("first bad thing", "second"))
    with pytest.raises(CorruptedSourceError) as excinfo:
        gate_diagnostics(unit, "c", "x.c", strict=True)
    assert "first bad thing" in str(excinfo.value)


def test_gate_records_the_residual_when_not_strict(caplog):
    unit = ClangUnit(functions=(), error_count=7, errors=("a", "b", "c", "d"))
    with caplog.at_level("INFO", logger="ouroboros.clang"):
        gate_diagnostics(unit, "c", "tree.c", strict=False)
    assert "7 clang/gcc diagnostic(s) in tree.c" in caplog.text
    assert "a; b; c" in caplog.text and "; d" not in caplog.text


def test_gate_says_nothing_when_the_parse_was_clean(caplog):
    with caplog.at_level("INFO", logger="ouroboros.clang"):
        gate_diagnostics(ClangUnit((), 0, ()), "c", "x.c", strict=True)
    assert caplog.text == ""


def test_a_buffer_libclang_cannot_open_at_all_is_corrupted_source(tmp_path):
    """`ok: false` from the emitter means the source, not the toolchain."""

    fake = tmp_path / "fake-emitter"
    fake.write_text('#!/bin/sh\nprintf \'{"ok":false,"error":"no translation unit"}\'\n')
    fake.chmod(0o755)
    with pytest.raises(CorruptedSourceError) as excinfo:
        emit_ranges(b"int f(void) {}", language="c", filename="x.c", args=[],
                    emitter=str(fake))
    assert "no translation unit" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# the helper as a program: building it, running it, failing to
# --------------------------------------------------------------------------- #


def test_emitter_is_built_once_and_reused():
    first = emitter_path()
    assert Path(first).is_file() and os.access(first, os.X_OK)
    assert emitter_path() == first


def test_emitter_path_can_be_pointed_at_a_prebuilt_binary(monkeypatch):
    monkeypatch.setenv("OUROBOROS_CLANG_EMITTER", "/somewhere/prebuilt")
    emitter_path.cache_clear()
    try:
        assert emitter_path() == "/somewhere/prebuilt"
    finally:
        emitter_path.cache_clear()


def test_a_missing_helper_is_a_toolchain_fault_not_a_source_fault():
    with pytest.raises(ClangEmitterError) as excinfo:
        emit_ranges(b"int f(void) {}", language="c", filename="x.c", args=[],
                    emitter="/nonexistent/emitter")
    assert "cannot run" in str(excinfo.value)


def test_a_helper_that_dies_reports_its_own_error(tmp_path):
    fake = tmp_path / "dying-emitter"
    fake.write_text("#!/bin/sh\necho 'libclang is missing something' >&2\nexit 3\n")
    fake.chmod(0o755)
    with pytest.raises(ClangEmitterError) as excinfo:
        emit_ranges(b"int f(void) {}", language="c", filename="x.c", args=[],
                    emitter=str(fake))
    assert "exited with 3" in str(excinfo.value)
    assert "libclang is missing something" in str(excinfo.value)


def test_a_helper_that_prints_rubbish_is_reported_as_such(tmp_path):
    fake = tmp_path / "noisy-emitter"
    fake.write_text("#!/bin/sh\necho not json\n")
    fake.chmod(0o755)
    with pytest.raises(ClangEmitterError) as excinfo:
        emit_ranges(b"int f(void) {}", language="c", filename="x.c", args=[],
                    emitter=str(fake))
    assert "not JSON" in str(excinfo.value)


def test_a_helper_that_never_finishes_is_given_up_on(tmp_path, monkeypatch):
    fake = tmp_path / "hanging-emitter"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setattr(clangbridge, "EMIT_TIMEOUT", 1)
    with pytest.raises(ClangEmitterError) as excinfo:
        emit_ranges(b"int f(void) {}", language="c", filename="x.c", args=[],
                    emitter=str(fake))
    assert "did not finish" in str(excinfo.value)


def test_building_without_a_c_compiler_says_so(monkeypatch, tmp_path):
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setattr(clangbridge.shutil, "which", lambda _name: None)
    with pytest.raises(ClangEmitterError) as excinfo:
        build_emitter(tmp_path / "out")
    assert "no C compiler found" in str(excinfo.value)


def test_a_compiler_that_fails_reports_the_command_and_its_output(monkeypatch, tmp_path):
    monkeypatch.setenv("CC", "false")
    with pytest.raises(ClangEmitterError) as excinfo:
        build_emitter(tmp_path / "out")
    assert "building the C/C++ range emitter failed" in str(excinfo.value)


def test_libclang_shared_object_is_found():
    library = libclang_library()
    assert Path(library).is_file()
    assert "libclang" in Path(library).name


def test_libclang_search_reports_absence_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(clangbridge.sys, "path", [])
    monkeypatch.setattr(clangbridge.Path, "glob", lambda _self, _pattern: iter(()))
    libclang_library.cache_clear()
    try:
        with pytest.raises(ClangEmitterError) as excinfo:
            libclang_library()
        assert "no libclang shared library found" in str(excinfo.value)
    finally:
        libclang_library.cache_clear()


# --------------------------------------------------------------------------- #
# the vendored libclang declarations, held to the real header
# --------------------------------------------------------------------------- #


def _system_clang_c() -> tuple[str, str] | None:
    """A matched (include dir, libclang) pair from the host's llvm installation.

    Matched on purpose: the control build has to parse with the SAME libclang
    the vendored build is pointed at, or a difference in output would say
    nothing about the declarations under test.
    """

    roots = []
    if shutil.which("llvm-config"):
        out = subprocess.run(["llvm-config", "--prefix"], capture_output=True,
                             text=True, timeout=30).stdout.strip()
        if out:
            roots.append(Path(out))
    roots += sorted(Path("/").glob("usr/lib/llvm-*"), reverse=True)
    roots += [Path("/usr"), Path("/usr/local"), Path("/opt/homebrew/opt/llvm")]
    for root in roots:
        include = root / "include"
        if not (include / "clang-c" / "Index.h").is_file():
            continue
        for name in ("libclang.so", "libclang.so.1", "libclang.dylib"):
            library = root / "lib" / name
            if library.is_file():
                return str(include), str(library)
    return None


_CROSS_CHECK_SOURCES = [
    ("c", "probe.c",
     "struct S { int x; };\n"
     "static const char *label(int i, unsigned u, long l, const char *s,\n"
     "                         double d, struct S *p) {\n"
     "    if (i) return s;\n"
     "    return \"x\";\n"
     "}\n"
     "const int fixed(void) { return 1; }\n"
     "void quiet(void) { return; }\n"),
    ("cpp", "probe.cpp",
     "namespace ns { struct W {\n"
     "  int value(int a) { return a; }\n"
     "  W() {}\n"
     "};\n"
     "constexpr int sq(int x) { return x * x; }\n"
     "struct P { int a, b; };\n"
     "P make() { return {1, 2}; }\n"
     "int run(int n) { auto f = [](int x) { return x + 1; }; return f(n); }\n"
     "}\n"),
]


def test_vendored_header_matches_the_real_one(tmp_path):
    """The vendored libclang declarations are checked, not trusted.

    ``libclang_api.h`` restates a slice of libclang's ABI so the emitter builds
    on a host that has the shared object but not the llvm development headers —
    which is every host that got libclang from the `libclang` wheel. Restating an
    ABI by hand is only safe if something proves the restatement right, so this
    builds the emitter BOTH ways — against the vendored declarations and against
    the host's real ``<clang-c/Index.h>`` — and requires the two binaries to
    print the same bytes.
    """

    pair = _system_clang_c()
    if pair is None:
        pytest.skip("no system clang-c/Index.h to check against")
    include, library = pair

    vendored = tmp_path / "emit-vendored"
    control = tmp_path / "emit-control"
    build_emitter(vendored)
    build_emitter(control, system_header=include, library=library)

    for language, filename, source in _CROSS_CHECK_SOURCES:
        args = _c_args() if language == "c" else _cpp_args()
        runs = []
        for binary in (vendored, control):
            proc = subprocess.run([str(binary), language, filename, *args],
                                  input=source.encode("utf-8"),
                                  capture_output=True, timeout=120,
                                  env={**os.environ,
                                       "OUROBOROS_LIBCLANG": library})
            assert proc.returncode == 0, proc.stderr.decode()
            runs.append(proc.stdout)
        assert json.loads(runs[0])["functions"], f"{filename} produced no functions"
        assert runs[0] == runs[1], (
            f"the vendored declarations disagree with {include} for {filename}"
        )


# --------------------------------------------------------------------------- #
# the backends above the boundary are splicers and nothing else
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("transformer", [CTransformer, CppTransformer])
def test_backends_reach_the_parser_only_through_the_boundary(transformer, monkeypatch):
    """Every parse a backend does goes out of the process.

    If a backend ever grew a second, in-process route to libclang, the
    boundary would be decorative. Blocking the one door stops both backends.
    """

    def refuse(*_args, **_kwargs):
        raise AssertionError("the backend parsed without going through emit_ranges")

    monkeypatch.setattr(clangbridge, "emit_ranges", refuse)
    with pytest.raises(AssertionError):
        transformer().wrap_source("int f(int a) { return a; }\n")


def test_cpp_refuses_the_minimal_probe():
    with pytest.raises(NotImplementedError):
        CppTransformer().wrap_source("int f(void) { return 0; }\n", minimal=True)


def test_ouroboros_instruments_its_own_range_emitter(tmp_path):
    """The tool eats its own tail: it wraps the C program it parses C with.

    `emitter.c` is ordinary C, so the C backend must be able to instrument it —
    and the instrumented copy must still answer exactly what the plain one
    answers, because instrumentation is not allowed to change what a program
    does. That makes this both a self-hosting check and a second, independent
    equivalence check on a real 600-line C file rather than a test snippet.
    """

    if shutil.which("cc") is None and shutil.which("gcc") is None:
        pytest.skip("no C compiler")

    transformer = CTransformer()
    source = (_CLANG_DIR / "emitter.c").read_text(encoding="utf-8")
    result = transformer.wrap_source(source, filename=str(_CLANG_DIR / "emitter.c"))
    assert result.functions_wrapped > 10, "the emitter's own functions were not found"

    (tmp_path / "emitter.c").write_text(result.code, encoding="utf-8")
    (tmp_path / "libclang_api.h").write_text(
        (_CLANG_DIR / "libclang_api.h").read_text(encoding="utf-8"), encoding="utf-8")
    helper_name, helper_text = transformer.runtime_asset()
    (tmp_path / helper_name).write_text(helper_text, encoding="utf-8")

    instrumented = tmp_path / "emit-instrumented"
    build = subprocess.run(
        [shutil.which("cc") or "gcc", "-O2", "-o", str(instrumented),
         str(tmp_path / "emitter.c"), "-I", str(tmp_path)],
        capture_output=True, text=True, timeout=300)
    assert build.returncode == 0, build.stderr

    probe = "static int add(int a, const char *s) { if (a) return a + 1; return 0; }\n"
    env = {**os.environ, "OUROBOROS_LIBCLANG": libclang_library(),
           "OUROBOROS_DEBUG_INFO": str(tmp_path / "debug.info")}
    answers = []
    for binary in (emitter_path(), str(instrumented)):
        proc = subprocess.run([binary, "c", "probe.c", *_c_args()],
                              input=probe.encode("utf-8"), capture_output=True,
                              timeout=120, env=env)
        assert proc.returncode == 0, proc.stderr.decode()
        answers.append(proc.stdout)
    assert answers[0] == answers[1], "instrumentation changed what the emitter says"
    # Read the trace as BYTES. A `const char *` argument is logged with a
    # guarded `%s`, which assumes a NUL-terminated string; `buf_putc` passes the
    # address of a single stack char, so the record for that call carries
    # whatever followed it on the stack and the file is not valid UTF-8. That is
    # the C backend's standing rule for `const char *`, not a fault of this test.
    trace = (tmp_path / "debug.info").read_bytes()
    assert trace.count(b'"p":"in"') > 5, (
        "the instrumented emitter recorded nothing about its own run"
    )
    # Every call is closed except one: `main` ends with `_Exit`, which skips the
    # cleanup attribute that writes the closing record. That is the mechanism
    # working, not failing — the record closes on scope exit, and `_Exit` leaves
    # no scope.
    assert trace.count(b'"p":"in"') == trace.count(b'"p":"out"') + 1, (
        "exactly one record — main's — should be left open"
    )


# --------------------------------------------------------------------------- #
# what the splicers do with what they are told
# --------------------------------------------------------------------------- #


def test_macro_generated_function_is_left_alone_by_the_c_backend():
    """The brace check in the shared wrap loop, seen from above.

    Splicing at an offset inside a macro invocation would cut the macro's name
    in half and leave a file that does not compile. The function is skipped
    instead, so the wrap touches only what it can safely touch.
    """

    source = ("#define MAKE(n) int n(int x) { return x; }\n"
              "MAKE(twice)\n"
              "int plain(int x) { return x; }\n")
    result = CTransformer().wrap_source(source, filename="macro.c")
    assert result.functions_wrapped == 1
    assert "MAKE(twice)\n" in result.code, "the macro invocation was cut"
    assert result.code.count("_ouro_enter") == 1


def test_unprintable_argument_is_written_as_an_ellipsis():
    source = "struct S { int x; };\nint f(struct S s, int n) { return n; }\n"
    result = CTransformer().wrap_source(source, filename="rec.c")
    assert '"<...>, %d", n' in result.code


@pytest.mark.parametrize(
    ("transformer", "source", "filename", "marker"),
    [
        (CTransformer, "void f(int a) { if (a) return; return; }\n", "b.c",
         "__ouro_result"),
        (CppTransformer, "void f(int a) { if (a) return; return; }\n", "b.cpp",
         "_ouro::capture"),
    ],
)
def test_a_bare_return_is_not_rewritten(transformer, source, filename, marker):
    """`return;` carries no value, so there is nothing to capture — and the
    scope guard reports the exit anyway."""

    result = transformer().wrap_source(source, filename=filename)
    assert result.functions_wrapped == 1
    assert marker not in result.code


def test_the_shared_wrap_loop_demands_the_two_hooks():
    """`ClangTransformer` is a skeleton: a subclass that supplies no text is a
    programming error, not a silent no-op."""

    class Bare(clangbridge.ClangTransformer):
        language = "c"
        extensions = (".c",)
        runtime_dir = _CLANG_DIR
        runtime_name = "emitter.c"
        include_line = "#include <nothing.h>"
        default_filename = "bare.c"

    with pytest.raises(NotImplementedError):
        Bare().default_args()
    with pytest.raises(NotImplementedError):
        Bare().instrument(_emit_c("int f(void) { return 0; }\n").functions[0],
                          minimal=False)


# --------------------------------------------------------------------------- #
# building the emitter, in the states a fresh machine is in
# --------------------------------------------------------------------------- #


def test_a_cold_cache_builds_the_emitter_and_a_warm_one_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(clangbridge, "_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.delenv("OUROBOROS_CLANG_EMITTER", raising=False)
    emitter_path.cache_clear()
    try:
        built = emitter_path()
        assert Path(built).is_file()
        assert Path(built).parent == tmp_path / "cache"
        stamp = Path(built).stat().st_mtime_ns

        emitter_path.cache_clear()
        assert emitter_path() == built
        assert Path(built).stat().st_mtime_ns == stamp, "it was rebuilt needlessly"

        # and it works
        unit = emit_ranges(b"int f(int a) { return a; }\n", language="c",
                           filename="x.c", args=_c_args(), emitter=built)
        assert [fn.name for fn in unit.functions] == ["f"]
    finally:
        emitter_path.cache_clear()


def test_a_compiler_that_cannot_be_started_says_so(monkeypatch, tmp_path):
    def refuse(*_args, **_kwargs):
        raise OSError("no such thing")

    monkeypatch.setattr(clangbridge.subprocess, "run", refuse)
    with pytest.raises(ClangEmitterError) as excinfo:
        build_emitter(tmp_path / "out")
    assert "cannot run the C compiler" in str(excinfo.value)


def test_the_control_build_needs_a_library_named(tmp_path):
    with pytest.raises(ClangEmitterError) as excinfo:
        build_emitter(tmp_path / "out", system_header="/usr/include")
    assert "needs a library" in str(excinfo.value)


def test_libclang_is_also_found_without_the_python_package(monkeypatch):
    """The fallback for an install that got libclang from the distribution."""

    monkeypatch.setattr(clangbridge.sys, "path", [])
    libclang_library.cache_clear()
    try:
        found = Path(libclang_library())
        assert found.is_file() and "libclang" in found.name
    finally:
        libclang_library.cache_clear()


def test_resource_dir_lookup_survives_a_missing_clang(monkeypatch):
    monkeypatch.setattr(clangbridge.subprocess, "run",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("gone")))
    clangbridge.clang_resource_dir_args.cache_clear()
    try:
        assert clangbridge.clang_resource_dir_args() == []
    finally:
        clangbridge.clang_resource_dir_args.cache_clear()


def test_resource_dir_lookup_ignores_a_clang_that_answers_nothing(monkeypatch):
    class Empty:
        stdout = "\n"

    monkeypatch.setattr(clangbridge.subprocess, "run", lambda *_a, **_k: Empty())
    clangbridge.clang_resource_dir_args.cache_clear()
    try:
        assert clangbridge.clang_resource_dir_args() == []
    finally:
        clangbridge.clang_resource_dir_args.cache_clear()
