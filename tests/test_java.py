"""Tests for the Java backend (try/catch/finally body instrumentation).

The parser is the one inside the JDK, so these tests need a JDK and nothing
else. What they hold down, beyond "it produced some text", is every decision the
backend makes that a reader of the output could not check by eye: which
declarations are instrumented, where the entry text may go, and which offsets
are counted in what.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from ouroboros.languages import CorruptedSourceError, transformer_for_path
from ouroboros.languages.java_lang import (
    JavaEmitterError,
    JavaTransformer,
    _default_value,
    _javac,
    build_emitter,
    emitter_classpath,
)
from ouroboros.trace import load

has_jdk = shutil.which("javac") is not None and shutil.which("java") is not None
pytestmark = pytest.mark.skipif(not has_jdk, reason="no JDK available")

TIMEOUT = 180


@pytest.fixture
def tx() -> JavaTransformer:
    return JavaTransformer()


def test_registry_resolves_java():
    assert isinstance(transformer_for_path("A.java"), JavaTransformer)


def test_basic_wrap(tx):
    res = tx.wrap_source(
        "public class A {\n    int add(int a, int b) { return a + b; }\n}\n",
        filename="A.java")
    assert res.functions_wrapped == 1
    assert 'enter("A.add", new java.lang.Object[]{a, b})' in res.code
    assert "int __ouro_result = 0;" in res.code
    assert "(__ouro_result = a + b)" in res.code


def test_no_import_is_spliced(tx):
    """The helper is named in full, so the backend never edits the file header.

    That is the whole reason the qualified name is worth its length: a package
    declaration, an import block and a leading comment all stay untouched,
    because nothing is inserted above the first method.
    """
    src = ("// leading comment\npackage demo.app;\n\nimport java.util.List;\n\n"
           "public class A {\n    int f() { return 1; }\n}\n")
    res = tx.wrap_source(src, filename="A.java")
    head = res.code[: res.code.index("public class A")]
    assert head == src[: src.index("public class A")]
    assert "import ouroboros" not in res.code


def test_line_numbers_are_preserved(tx):
    """No inserted text carries a newline, so a stack trace still points at the
    line the reader is looking at. The equivalence corpus checks the effect;
    this checks the cause, which is cheaper to keep true."""
    src = ("public class A {\n"
           "    int f() {\n        return 1;\n    }\n"
           "    void g() {\n        return;\n    }\n}\n")
    res = tx.wrap_source(src, filename="A.java")
    assert res.code.count("\n") == src.count("\n")


def test_declarations_without_a_body_are_skipped(tx):
    src = ("public abstract class A {\n"
           "    abstract int missing();\n"
           "    native int alsoMissing();\n"
           "    int present() { return 1; }\n}\n")
    res = tx.wrap_source(src, filename="A.java")
    assert res.functions_wrapped == 1
    assert res.code.count("__ouro_ctx") > 0
    assert "missing() { ouroboros" not in res.code


def test_constructor_body_opens_after_the_explicit_super_call(tx):
    """`super(...)` has to stay the first statement of a constructor, so the
    entry text goes after it, not after the brace."""
    src = ("class B { B(int x) {} }\n"
           "public class A extends B {\n    A(int x) { super(x); int y = x; }\n}\n")
    res = tx.wrap_source(src, filename="A.java")
    assert "{ super(x); ouroboros.OuroborosRuntime.Ctx __ouro_ctx" in res.code


def test_constructor_takes_the_void_shape(tx):
    src = "public class A {\n    A() { int x = 1; }\n}\n"
    res = tx.wrap_source(src, filename="A.java")
    assert "exitVoid(__ouro_ctx)" in res.code
    assert "__ouro_result" not in res.code


def test_a_bare_return_is_left_alone(tx):
    """A void method's `return;` needs no edit: the outermost `finally` records
    the completion whichever way the body left."""
    src = "public class A {\n    void f(boolean b) { if (b) return; }\n}\n"
    res = tx.wrap_source(src, filename="A.java")
    assert "if (b) return;" in res.code
    assert "exitVoid(__ouro_ctx)" in res.code


def test_returns_inside_a_lambda_belong_to_the_lambda(tx):
    """A `return` in a lambda returns from the lambda. Assigning it to the
    enclosing method's temp would both mis-report the method's result and, when
    the types differ, stop compiling."""
    src = ("import java.util.function.*;\npublic class A {\n"
           "    Supplier<Integer> f() { return () -> { return 7; }; }\n}\n")
    res = tx.wrap_source(src, filename="A.java")
    # The lambda's own `return 7;` is untouched; only the method's `return` is
    # routed through the temp.
    assert "-> { return 7; }" in res.code
    assert res.code.count("(__ouro_result = ") == 1


def test_methods_of_an_anonymous_class_are_wrapped_under_their_own_name(tx):
    src = ("public class A {\n    Runnable f() {\n"
           "        return new Runnable() { public void run() { return; } };\n    }\n}\n")
    res = tx.wrap_source(src, filename="A.java")
    assert res.functions_wrapped == 2
    assert '"A.$anon.run"' in res.code


def test_nested_class_names_are_qualified(tx):
    src = "public class A {\n    static class B {\n        int f() { return 1; }\n    }\n}\n"
    res = tx.wrap_source(src, filename="A.java")
    assert '"A.B.f"' in res.code


def test_offsets_are_counted_in_code_points_not_utf16(tx):
    """javac reports UTF-16 indices and Python slices by code point. One
    character outside the basic plane above a method is enough to push every
    later edit off by one, which produces a file that no longer parses."""
    src = ('public class A {\n    // \U0001F600\n    int f() { return 1; }\n}\n')
    res = tx.wrap_source(src, filename="A.java")
    assert "{ ouroboros.OuroborosRuntime.Ctx __ouro_ctx" in res.code
    assert "(__ouro_result = 1)" in res.code


def test_idempotent(tx):
    once = tx.wrap_source("public class A {\n    int f() { return 1; }\n}\n",
                          filename="A.java").code
    again = tx.wrap_source(once, filename="A.java")
    assert again.functions_wrapped == 0
    assert again.code == once


def test_selective_mode_wraps_only_the_named_methods(tx):
    src = ("public class A {\n    int keep() { return 1; }\n"
           "    int drop() { return 2; }\n}\n")
    res = tx.wrap_source(src, filename="A.java", only={"keep"})
    assert res.functions_wrapped == 1
    assert "keep() { ouroboros" in res.code
    assert "drop() { return 2; }" in res.code


def test_corrupted_java_raises(tx):
    with pytest.raises(CorruptedSourceError) as caught:
        tx.wrap_source("public class A { int f( { }\n", filename="bad.java")
    assert caught.value.language == "java"


def test_the_minimal_probe_is_refused_here_by_name(tx):
    with pytest.raises(NotImplementedError):
        tx.wrap_source("public class A {}\n", filename="A.java", minimal=True)


def test_runtime_asset(tx):
    name, src = tx.runtime_asset()
    assert name == "OuroborosRuntime.java"
    assert "package ouroboros;" in src
    assert '"p":"in"' in src


def test_default_value_per_return_type():
    assert _default_value({"returnIsPrimitive": True, "returnType": "long"}) == "0L"
    assert _default_value({"returnIsPrimitive": True, "returnType": "boolean"}) == "false"
    assert _default_value({"returnIsPrimitive": False, "returnType": "String"}) == "null"


def test_a_missing_jdk_is_named_as_the_reason(tx, monkeypatch):
    """A toolchain that is not installed must not be reported as corrupt source —
    that is how a caller ends up rewriting a file that was fine."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("JAVA", raising=False)
    monkeypatch.delenv("OUROBOROS_JAVA_EMITTER", raising=False)
    with pytest.raises(JavaEmitterError) as caught:
        tx.wrap_source("public class A {}\n", filename="A.java")
    assert "java" in str(caught.value)


def test_a_failing_build_names_the_command(tmp_path, monkeypatch):
    monkeypatch.setattr("ouroboros.languages.java_lang._EMITTER_SRC",
                        tmp_path / "Broken.java")
    (tmp_path / "Broken.java").write_text("class Broken { oops }\n", encoding="utf-8")
    with pytest.raises(JavaEmitterError) as caught:
        build_emitter(tmp_path / "out")
    assert "range emitter" in str(caught.value)


def test_the_emitter_override_is_used_verbatim(monkeypatch):
    emitter_classpath.cache_clear()
    monkeypatch.setenv("OUROBOROS_JAVA_EMITTER", "/somewhere/classes")
    try:
        assert emitter_classpath() == "/somewhere/classes"
    finally:
        emitter_classpath.cache_clear()


def test_an_emitter_that_prints_nothing_is_a_toolchain_error(tx, monkeypatch):
    """Not a CorruptedSourceError: the source was never looked at."""
    class Result:
        returncode = 0
        stdout = "not json"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(JavaEmitterError):
        tx.wrap_source("public class A {}\n", filename="A.java")


def test_an_emitter_that_crashes_is_a_toolchain_error(tx, monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(JavaEmitterError) as caught:
        tx.wrap_source("public class A {}\n", filename="A.java")
    assert "boom" in str(caught.value)


def test_end_to_end_compile_and_run(tmp_path, tx):
    """The record the JDK actually writes, read back through the trace parser."""
    src = ("public class Prog {\n"
           "    static int add(int a, int b) { return a + b; }\n"
           '    static int boom() { throw new IllegalStateException("no"); }\n'
           "    public static void main(String[] args) {\n"
           "        add(2, 3);\n"
           "        try { boom(); } catch (IllegalStateException e) { }\n"
           "    }\n}\n")
    res = tx.wrap_source(src, filename="Prog.java")
    (tmp_path / "Prog.java").write_text(res.code, encoding="utf-8")
    name, helper = tx.runtime_asset()
    (tmp_path / name).write_text(helper, encoding="utf-8")

    subprocess.run(["javac", "-nowarn", "-d", ".", "Prog.java", name], cwd=tmp_path,
                   check=True, capture_output=True, timeout=TIMEOUT)
    sink = tmp_path / "debug.info"
    subprocess.run(["java", "-cp", ".", "Prog"], cwd=tmp_path, check=True,
                   capture_output=True, timeout=TIMEOUT,
                   env={**os.environ, "OUROBOROS_DEBUG_INFO": str(sink)})

    records = [json.loads(line) for line in
               sink.read_text(encoding="utf-8").splitlines() if line.strip()]
    entry = next(r for r in records if r["p"] == "in" and r["fn"] == "Prog.add")
    assert entry["a"] == "2, 3"
    assert entry["k"] == ""
    assert entry["ci"] == -1
    completion = next(r for r in records if r["p"] == "out" and r["id"] == entry["id"])
    assert completion["r"] == "5"
    raised = next(r for r in records if r["p"] == "out" and "x" in r)
    assert raised["x"] == "java.lang.IllegalStateException: no"

    trace = load(sink.read_text(encoding="utf-8"))
    assert trace.malformed == 0
    assert any(call.name == "Prog.add" for call in trace.calls)


def test_a_repr_that_throws_does_not_take_the_program_down(tmp_path, tx):
    """Rendering a value must never be able to change what the program does."""
    src = ("public class Prog {\n"
           "    static class Bad { public String toString() {"
           ' throw new IllegalStateException("no"); } }\n'
           "    static int use(Bad b) { return 1; }\n"
           "    public static void main(String[] args) {"
           " System.out.println(use(new Bad())); }\n}\n")
    res = tx.wrap_source(src, filename="Prog.java")
    (tmp_path / "Prog.java").write_text(res.code, encoding="utf-8")
    name, helper = tx.runtime_asset()
    (tmp_path / name).write_text(helper, encoding="utf-8")
    subprocess.run(["javac", "-nowarn", "-d", ".", "Prog.java", name], cwd=tmp_path,
                   check=True, capture_output=True, timeout=TIMEOUT)
    sink = tmp_path / "debug.info"
    proc = subprocess.run(["java", "-cp", ".", "Prog"], cwd=tmp_path, check=True,
                          capture_output=True, text=True, timeout=TIMEOUT,
                          env={**os.environ, "OUROBOROS_DEBUG_INFO": str(sink)})
    assert proc.stdout == "1\n"
    entry = next(json.loads(line) for line in
                 sink.read_text(encoding="utf-8").splitlines()
                 if line.strip() and '"p":"in"' in line and "Prog.use" in line)
    assert "toString threw" in entry["a"]


def test_javac_is_taken_from_the_environment_when_set(monkeypatch):
    monkeypatch.setenv("JAVAC", "javac")
    assert _javac() == "javac"


def test_a_missing_javac_is_named_as_the_reason(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("JAVAC", raising=False)
    with pytest.raises(JavaEmitterError) as caught:
        _javac()
    assert "javac" in str(caught.value)


def test_a_javac_that_cannot_be_started_is_named_as_the_reason(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("no exec")

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(JavaEmitterError) as caught:
        build_emitter(tmp_path / "out")
    assert "no exec" in str(caught.value)


def test_the_emitter_really_builds(tmp_path):
    """The success path of the build, which every other build test only fails."""
    build_emitter(tmp_path / "out")
    assert (tmp_path / "out" / "Emitter.class").is_file()


def test_the_build_is_cached_under_a_name_derived_from_the_source(tmp_path, monkeypatch):
    """First use builds; the second finds the same build instead of repeating it."""
    monkeypatch.setattr("ouroboros.languages.java_lang._cache_dir", lambda: tmp_path)
    monkeypatch.delenv("OUROBOROS_JAVA_EMITTER", raising=False)
    emitter_classpath.cache_clear()
    try:
        built = emitter_classpath()
        assert (pathlib.Path(built) / "Emitter.class").is_file()
        emitter_classpath.cache_clear()
        assert emitter_classpath() == built
    finally:
        emitter_classpath.cache_clear()


def test_an_emitter_that_cannot_be_started_is_a_toolchain_error(tx, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("no exec")

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(JavaEmitterError) as caught:
        tx.wrap_source("public class A {}\n", filename="A.java")
    assert "no exec" in str(caught.value)


def test_a_build_that_cannot_be_placed_is_named_as_the_reason(tmp_path, monkeypatch):
    """The rename is what makes a concurrent wrap safe; if it fails the caller
    must hear about the cache, not about their source."""
    monkeypatch.setattr("ouroboros.languages.java_lang._cache_dir", lambda: tmp_path)
    monkeypatch.delenv("OUROBOROS_JAVA_EMITTER", raising=False)

    def refuse(src, dst):
        raise OSError("read-only cache")

    monkeypatch.setattr(os, "replace", refuse)
    emitter_classpath.cache_clear()
    try:
        with pytest.raises(JavaEmitterError) as caught:
            emitter_classpath()
        assert "read-only cache" in str(caught.value)
    finally:
        emitter_classpath.cache_clear()
