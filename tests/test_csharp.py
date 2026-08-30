"""Tests for the C# backend (try/catch/finally body instrumentation).

The parser is Roslyn, taken from inside the installed .NET SDK, so these tests
need a .NET SDK and nothing else. What they hold down is every decision the
backend makes that a reader of the output could not check by eye: which members
it declines to touch and why, where the entry text may go, and which offsets are
counted in what.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from ouroboros.languages import CorruptedSourceError, transformer_for_path
from ouroboros.languages.csharp_lang import (
    CSharpEmitterError,
    CSharpTransformer,
    _dotnet,
    _version_key,
    build_emitter,
    emitter_assembly,
    installed_sdks,
    roslyn_bincore,
    target_framework,
)
from ouroboros.trace import load

has_dotnet = shutil.which("dotnet") is not None
pytestmark = pytest.mark.skipif(not has_dotnet, reason="no .NET SDK available")

TIMEOUT = 600
QUIET = {"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}


@pytest.fixture
def tx() -> CSharpTransformer:
    return CSharpTransformer()


def _framework() -> str:
    proc = subprocess.run(["dotnet", "--version"], capture_output=True, text=True,
                          timeout=TIMEOUT, check=True)
    return f"net{proc.stdout.strip().split('.')[0]}.0"


_PROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>{framework}</TargetFramework>
    <AssemblyName>app</AssemblyName>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
  </PropertyGroup>
</Project>
"""


def _build_and_run(root: pathlib.Path, sink: pathlib.Path) -> subprocess.CompletedProcess[str]:
    framework = _framework()
    (root / "app.csproj").write_text(_PROJ.format(framework=framework), encoding="utf-8")
    subprocess.run(["dotnet", "build", "-c", "Release", "--nologo"], cwd=root,
                   check=True, capture_output=True, timeout=TIMEOUT,
                   env={**os.environ, **QUIET})
    return subprocess.run([str(root / "bin" / "Release" / framework / "app")],
                          cwd=root, check=True, capture_output=True, text=True,
                          timeout=TIMEOUT,
                          env={**os.environ, **QUIET, "OUROBOROS_DEBUG_INFO": str(sink)})


def test_registry_resolves_csharp():
    assert isinstance(transformer_for_path("A.cs"), CSharpTransformer)


def test_basic_wrap(tx):
    res = tx.wrap_source(
        "class A {\n    int Add(int a, int b) { return a + b; }\n}\n", filename="A.cs")
    assert res.functions_wrapped == 1
    assert 'Enter("A.Add", new object[]{a, b})' in res.code
    assert "Ret<int>(__ouro_ctx, a + b)" in res.code


def test_no_using_is_spliced(tx):
    """The helper is named in full, so the backend never edits the file header."""
    src = ("// leading comment\nusing System;\n\nnamespace Demo;\n\n"
           "class A {\n    int F() { return 1; }\n}\n")
    res = tx.wrap_source(src, filename="A.cs")
    head = res.code[: res.code.index("class A")]
    assert head == src[: src.index("class A")]
    assert "using Ouroboros" not in res.code


def test_line_numbers_are_preserved(tx):
    """No inserted text carries a newline, so a stack trace still points at the
    line the reader is looking at. The equivalence corpus checks the effect;
    this checks the cause, which is cheaper to keep true."""
    src = ("class A {\n    int F() {\n        return 1;\n    }\n"
           "    void G() {\n        return;\n    }\n}\n")
    res = tx.wrap_source(src, filename="A.cs")
    assert res.code.count("\n") == src.count("\n")


def test_the_rethrow_keeps_the_original_throw_site(tx):
    """`throw;` and not `throw __ouro_e;`: rethrowing the variable would reset the
    exception's stack trace to the wrapper's line, so an observed program would
    print a different trace purely because it is observed."""
    res = tx.wrap_source("class A {\n    int F() { return 1; }\n}\n", filename="A.cs")
    assert "throw; }" in res.code
    assert "throw __ouro_e" not in res.code


@pytest.mark.parametrize(("name", "src", "reason"), [
    ("iterator",
     "using System.Collections.Generic;\nclass A {\n"
     "    IEnumerable<int> F() { yield return 1; }\n}\n", "iterator"),
    ("ref_return", "class A {\n    int[] xs = new int[2];\n"
                   "    ref int F(int i) { return ref xs[i]; }\n}\n", "ref-return"),
    ("pointer_return", "class A {\n    unsafe int* F(int* p) { return p; }\n}\n",
     "pointer"),
    ("bcl_ref_struct", "using System;\nclass A {\n"
                       "    int F(Span<int> s) { return s.Length; }\n}\n", "ref-struct"),
])
def test_members_that_cannot_be_wrapped_are_named_not_silently_dropped(
        tx, name, src, reason):
    """Each of these produces code that does not compile if it is wrapped, so the
    backend leaves it alone — and says so, rather than letting the caller believe
    the member was covered."""
    res = tx.wrap_source(src, filename="A.cs")
    assert res.functions_wrapped == 0
    assert res.warnings, name
    assert reason in res.warnings[0], res.warnings


def test_a_ref_struct_declared_in_the_same_file_is_recognised(tx):
    """Roslyn is asked for syntax only, so a name is never resolved to its
    declaration. A `ref struct` written in the file being wrapped is still visible
    — as a declaration — and members using it are left alone."""
    src = ("ref struct View { public int Len; }\n"
           "class A {\n    int F(View v) { return v.Len; }\n"
           "    View G() { return new View(); }\n"
           "    int Plain() { return 1; }\n}\n")
    res = tx.wrap_source(src, filename="A.cs")
    assert res.functions_wrapped == 1
    assert len(res.warnings) == 2
    assert all("ref-struct" in w for w in res.warnings)


def test_out_parameters_are_kept_but_left_out_of_the_entry_snapshot(tx):
    """An `out` parameter is not definitely assigned when the method is entered,
    so reading one there is CS0269 — the member is still wrapped, the argument is
    simply absent."""
    src = "class A {\n    bool F(int a, out int b) { b = a; return true; }\n}\n"
    res = tx.wrap_source(src, filename="A.cs")
    assert res.functions_wrapped == 1
    assert "new object[]{a}" in res.code


def test_ref_and_in_parameters_are_logged(tx):
    src = "class A {\n    int F(ref int a, in int b) { return a + b; }\n}\n"
    res = tx.wrap_source(src, filename="A.cs")
    assert "new object[]{a, b}" in res.code


def test_an_expression_body_is_expanded_not_skipped(tx):
    """`=> e;` has no block to splice into. Expanding it beats skipping: neither
    edit touches the expression's own text, and a one-line member is exactly the
    kind a caller wants timed."""
    src = "class A {\n    int F(int a) => a + 1;\n}\n"
    res = tx.wrap_source(src, filename="A.cs")
    assert res.functions_wrapped == 1
    assert "a + 1" in res.code
    assert "=>" not in res.code


def test_an_expression_bodied_property_is_left_alone_with_a_reason(tx):
    """Expanding `int P => x;` means writing `{ get { … } }` — reprinting the
    member's own shape rather than splicing into its body, which is the one thing
    this tool does not do."""
    src = "class A {\n    int x = 1;\n    int P => x;\n}\n"
    res = tx.wrap_source(src, filename="A.cs")
    assert res.functions_wrapped == 0
    assert res.warnings and "expression" in res.warnings[0]


def test_returns_inside_a_lambda_or_local_function_are_not_the_members(tx):
    src = ("using System;\nclass A {\n    int F() {\n"
           "        Func<int,int> f = x => { return x + 1; };\n"
           "        int local() { return 2; }\n"
           "        return f(local());\n    }\n}\n")
    res = tx.wrap_source(src, filename="A.cs")
    assert res.functions_wrapped == 1
    assert "{ return x + 1; }" in res.code
    assert "int local() { return 2; }" in res.code
    assert res.code.count("Ret<int>(__ouro_ctx, ") == 1


def test_properties_indexers_and_operators_get_their_own_names(tx):
    src = ("class A {\n    int v;\n"
           "    public int Value { get { return v; } set { v = value; } }\n"
           "    public int this[int i] { get { return i; } }\n"
           "    public static A operator +(A a, A b) { return a; }\n}\n")
    res = tx.wrap_source(src, filename="A.cs")
    assert '"A.Value.get"' in res.code
    assert '"A.Value.set"' in res.code
    assert '"A.this[].get"' in res.code
    assert '"A.operator+"' in res.code


def test_nested_type_names_are_qualified(tx):
    src = "class A {\n    class B {\n        int F() { return 1; }\n    }\n}\n"
    res = tx.wrap_source(src, filename="A.cs")
    assert '"A.B.F"' in res.code


def test_offsets_are_counted_in_code_points_not_utf16(tx):
    """Roslyn reports UTF-16 indices and Python slices by code point. One
    character outside the basic plane above a member is enough to push every later
    edit off by one, which produces a file that no longer parses."""
    src = 'class A {\n    // \U0001F600\n    int F() { return 1; }\n}\n'
    res = tx.wrap_source(src, filename="A.cs")
    assert "{ Ouroboros.OuroborosRuntime.Ctx __ouro_ctx" in res.code
    assert "Ret<int>(__ouro_ctx, 1)" in res.code


def test_idempotent(tx):
    once = tx.wrap_source("class A {\n    int F() { return 1; }\n}\n",
                          filename="A.cs").code
    again = tx.wrap_source(once, filename="A.cs")
    assert again.functions_wrapped == 0
    assert again.code == once


def test_selective_mode_wraps_only_the_named_members(tx):
    src = "class A {\n    int Keep() { return 1; }\n    int Drop() { return 2; }\n}\n"
    res = tx.wrap_source(src, filename="A.cs", only={"Keep"})
    assert res.functions_wrapped == 1
    assert "int Drop() { return 2; }" in res.code


def test_corrupted_csharp_raises(tx):
    with pytest.raises(CorruptedSourceError) as caught:
        tx.wrap_source("class A { int F( { }\n", filename="bad.cs")
    assert caught.value.language == "csharp"


def test_the_minimal_probe_is_refused_here_by_name(tx):
    with pytest.raises(NotImplementedError):
        tx.wrap_source("class A {}\n", filename="A.cs", minimal=True)


def test_runtime_asset(tx):
    name, src = tx.runtime_asset()
    assert name == "OuroborosRuntime.cs"
    assert "namespace Ouroboros" in src
    assert '"p":"in"' in src


def test_sdk_versions_sort_numerically_not_as_text():
    """`10.0.100` is newer than `9.0.100`; sorted as text it is not."""
    assert _version_key("9.0.100") < _version_key("10.0.100")
    assert _version_key("9.0.100-preview.3") == (9, 0, 100)


def test_installed_sdks_are_reported_newest_last():
    sdks = installed_sdks()
    assert sdks, "dotnet reported no SDK"
    assert all(pathlib.Path(root).is_dir() for _v, root in sdks)
    assert [v for v, _ in sdks] == sorted((v for v, _ in sdks), key=_version_key)


def test_roslyn_is_found_inside_an_installed_sdk():
    found = pathlib.Path(roslyn_bincore())
    assert (found / "Microsoft.CodeAnalysis.CSharp.dll").is_file()


def test_the_roslyn_override_is_used_verbatim(monkeypatch):
    roslyn_bincore.cache_clear()
    monkeypatch.setenv("OUROBOROS_ROSLYN_BINCORE", "/somewhere/bincore")
    try:
        assert roslyn_bincore() == "/somewhere/bincore"
    finally:
        roslyn_bincore.cache_clear()


def test_no_roslyn_under_any_sdk_is_named_as_the_reason(monkeypatch, tmp_path):
    roslyn_bincore.cache_clear()
    monkeypatch.delenv("OUROBOROS_ROSLYN_BINCORE", raising=False)
    monkeypatch.setattr("ouroboros.languages.csharp_lang.installed_sdks",
                        lambda: [("9.9.999", str(tmp_path))])
    try:
        with pytest.raises(CSharpEmitterError) as caught:
            roslyn_bincore()
        assert "Roslyn" in str(caught.value)
    finally:
        roslyn_bincore.cache_clear()


def test_no_sdk_at_all_is_named_as_the_reason(monkeypatch):
    roslyn_bincore.cache_clear()
    monkeypatch.delenv("OUROBOROS_ROSLYN_BINCORE", raising=False)
    monkeypatch.setattr("ouroboros.languages.csharp_lang.installed_sdks", list)
    try:
        with pytest.raises(CSharpEmitterError) as caught:
            roslyn_bincore()
        assert "no SDK at all" in str(caught.value)
    finally:
        roslyn_bincore.cache_clear()


def test_dotnet_is_taken_from_the_environment_when_set(monkeypatch):
    monkeypatch.setenv("DOTNET", "dotnet")
    assert _dotnet() == "dotnet"


def test_a_missing_dotnet_is_named_as_the_reason(monkeypatch):
    """A toolchain that is not installed must not be reported as corrupt source —
    that is how a caller ends up rewriting a file that was fine."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("DOTNET", raising=False)
    with pytest.raises(CSharpEmitterError) as caught:
        _dotnet()
    assert "dotnet" in str(caught.value)


def test_a_dotnet_that_cannot_be_started_is_named_as_the_reason(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("no exec")

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(CSharpEmitterError) as caught:
        installed_sdks()
    assert "no exec" in str(caught.value)


def test_a_failing_list_sdks_is_named_as_the_reason(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "broken install"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(CSharpEmitterError) as caught:
        installed_sdks()
    assert "broken install" in str(caught.value)


def test_a_failing_build_names_the_command(tmp_path, monkeypatch):
    monkeypatch.setattr("ouroboros.languages.csharp_lang._EMITTER_SRC",
                        tmp_path / "Broken.cs")
    (tmp_path / "Broken.cs").write_text("class Broken { oops }\n", encoding="utf-8")
    with pytest.raises(CSharpEmitterError) as caught:
        build_emitter(tmp_path / "out")
    assert "range emitter" in str(caught.value)


def test_a_build_that_cannot_be_started_is_named_as_the_reason(tmp_path, monkeypatch):
    real = subprocess.run

    def refuse(argv, *args, **kwargs):
        if "build" in argv:
            raise OSError("no exec")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(CSharpEmitterError) as caught:
        build_emitter(tmp_path / "out")
    assert "no exec" in str(caught.value)


def test_the_emitter_override_is_used_verbatim(monkeypatch):
    emitter_assembly.cache_clear()
    monkeypatch.setenv("OUROBOROS_CSHARP_EMITTER", "/somewhere/emitter.dll")
    try:
        assert emitter_assembly() == "/somewhere/emitter.dll"
    finally:
        emitter_assembly.cache_clear()


def test_a_build_that_cannot_be_placed_is_named_as_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr("ouroboros.languages.csharp_lang._cache_dir", lambda: tmp_path)
    monkeypatch.delenv("OUROBOROS_CSHARP_EMITTER", raising=False)

    def refuse(src, dst):
        raise OSError("read-only cache")

    monkeypatch.setattr(os, "replace", refuse)
    emitter_assembly.cache_clear()
    try:
        with pytest.raises(CSharpEmitterError) as caught:
            emitter_assembly()
        assert "read-only cache" in str(caught.value)
    finally:
        emitter_assembly.cache_clear()


def test_the_build_is_cached_under_a_name_derived_from_the_sources(tmp_path, monkeypatch):
    monkeypatch.setattr("ouroboros.languages.csharp_lang._cache_dir", lambda: tmp_path)
    monkeypatch.delenv("OUROBOROS_CSHARP_EMITTER", raising=False)
    emitter_assembly.cache_clear()
    try:
        built = emitter_assembly()
        assert pathlib.Path(built).is_file()
        emitter_assembly.cache_clear()
        assert emitter_assembly() == built
    finally:
        emitter_assembly.cache_clear()


def test_an_emitter_that_prints_no_json_is_a_toolchain_error(tx, monkeypatch):
    """Not a CorruptedSourceError: the source was never looked at."""
    class Result:
        returncode = 0
        stdout = b"not json"
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(CSharpEmitterError):
        tx.wrap_source("class A {}\n", filename="A.cs")


def test_an_emitter_that_crashes_is_a_toolchain_error(tx, monkeypatch):
    class Result:
        returncode = 1
        stdout = b""
        stderr = b"boom"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(CSharpEmitterError) as caught:
        tx.wrap_source("class A {}\n", filename="A.cs")
    assert "boom" in str(caught.value)


def test_an_emitter_that_cannot_be_started_is_a_toolchain_error(tx, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("no exec")

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(CSharpEmitterError) as caught:
        tx.wrap_source("class A {}\n", filename="A.cs")
    assert "no exec" in str(caught.value)


def test_end_to_end_build_and_run(tmp_path, tx):
    """The record .NET actually writes, read back through the trace parser."""
    src = ("using System;\n"
           "class Program {\n"
           "    static int Add(int a, int b) { return a + b; }\n"
           '    static int Boom() { throw new InvalidOperationException("no"); }\n'
           "    static void Main() {\n"
           "        Add(2, 3);\n"
           "        try { Boom(); } catch (InvalidOperationException) { }\n"
           "    }\n}\n")
    res = tx.wrap_source(src, filename="Program.cs")
    (tmp_path / "Program.cs").write_text(res.code, encoding="utf-8")
    name, helper = tx.runtime_asset()
    (tmp_path / name).write_text(helper, encoding="utf-8")

    sink = tmp_path / "debug.info"
    _build_and_run(tmp_path, sink)

    records = [json.loads(line) for line in
               sink.read_text(encoding="utf-8").splitlines() if line.strip()]
    entry = next(r for r in records if r["p"] == "in" and r["fn"] == "Program.Add")
    assert entry["a"] == "2, 3"
    assert entry["k"] == ""
    assert entry["ci"] == -1
    completion = next(r for r in records if r["p"] == "out" and r["id"] == entry["id"])
    assert completion["r"] == "5"
    raised = next(r for r in records if r["p"] == "out" and "x" in r)
    assert raised["x"] == "System.InvalidOperationException: no"

    trace = load(sink.read_text(encoding="utf-8"))
    assert trace.malformed == 0
    assert any(call.name == "Program.Add" for call in trace.calls)


def test_a_repr_that_throws_does_not_take_the_program_down(tmp_path, tx):
    """Rendering a value must never be able to change what the program does."""
    src = ("using System;\n"
           "class Program {\n"
           "    class Bad { public override string ToString() {"
           ' throw new InvalidOperationException("no"); } }\n'
           "    static int Use(Bad b) { return 1; }\n"
           "    static void Main() { Console.WriteLine(Use(new Bad())); }\n}\n")
    res = tx.wrap_source(src, filename="Program.cs")
    (tmp_path / "Program.cs").write_text(res.code, encoding="utf-8")
    name, helper = tx.runtime_asset()
    (tmp_path / name).write_text(helper, encoding="utf-8")
    sink = tmp_path / "debug.info"
    proc = _build_and_run(tmp_path, sink)
    assert proc.stdout == "1\n"
    entry = next(json.loads(line) for line in
                 sink.read_text(encoding="utf-8").splitlines()
                 if line.strip() and '"p":"in"' in line and "Program.Use" in line)
    assert "ToString threw" in entry["a"]


def test_an_expression_bodied_void_member_is_expanded(tx):
    src = ("using System;\nclass A {\n"
           "    void F(string s) => Console.WriteLine(s);\n}\n")
    res = tx.wrap_source(src, filename="A.cs")
    assert res.functions_wrapped == 1
    assert "Console.WriteLine(s)" in res.code
    assert "RetVoid(__ouro_ctx)" in res.code
    assert "=>" not in res.code


def test_an_expression_bodied_throw_is_expanded(tx):
    src = ("using System;\nclass A {\n"
           '    int F() => throw new NotSupportedException("no");\n}\n')
    res = tx.wrap_source(src, filename="A.cs")
    assert res.functions_wrapped == 1
    assert 'throw new NotSupportedException("no")' in res.code
    assert "Ret<" not in res.code
    assert "=>" not in res.code


def test_lines_dotnet_prints_that_are_not_sdk_entries_are_ignored(monkeypatch):
    """`dotnet --list-sdks` is asked a question and may answer with other text
    too; only the `version [root]` lines are SDKs."""
    class Result:
        returncode = 0
        stdout = ("Welcome to .NET!\n"
                  "9.0.100 [/usr/lib/dotnet/sdk]\n"
                  "\n"
                  "10.0.110 [/usr/lib/dotnet/sdk]\n")
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    assert installed_sdks() == [("9.0.100", "/usr/lib/dotnet/sdk"),
                                ("10.0.110", "/usr/lib/dotnet/sdk")]


def test_the_target_framework_follows_the_installed_sdk():
    """A fixed `net10.0` in the project file fails outright on a host whose newest
    SDK is 9, and the emitter is a build helper that should follow the machine."""
    target_framework.cache_clear()
    try:
        assert target_framework() == f"net{installed_sdks()[-1][0].split('.')[0]}.0"
    finally:
        target_framework.cache_clear()


def test_the_target_framework_override_is_used_verbatim(monkeypatch):
    target_framework.cache_clear()
    monkeypatch.setenv("OUROBOROS_CSHARP_TARGET_FRAMEWORK", "net8.0")
    try:
        assert target_framework() == "net8.0"
    finally:
        target_framework.cache_clear()


def test_no_sdk_to_target_is_named_as_the_reason(monkeypatch):
    target_framework.cache_clear()
    monkeypatch.delenv("OUROBOROS_CSHARP_TARGET_FRAMEWORK", raising=False)
    monkeypatch.setattr("ouroboros.languages.csharp_lang.installed_sdks", list)
    try:
        with pytest.raises(CSharpEmitterError) as caught:
            target_framework()
        assert "no SDK" in str(caught.value)
    finally:
        target_framework.cache_clear()
