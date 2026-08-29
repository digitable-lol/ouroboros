"""Tests for language detection and transformer lookup.

The registry is the one place that answers "which backend owns this file", and
it answers it twice: from the extension, and — for a C-extension source — from
what the build actually compiled it as.
"""

from __future__ import annotations

import json

import pytest

from ouroboros.languages import (
    supported_extensions,
    supported_languages,
    transformer_for_extension,
    transformer_for_language,
    transformer_for_path,
)


@pytest.mark.parametrize("given", [".py", "py", ".PY", "PY"])
def test_an_extension_is_taken_with_or_without_its_dot_and_in_any_case(given):
    """Callers hand this both halves of `os.path.splitext` and bare words from a
    config file; a leading dot and letter case are not the caller's problem."""

    assert transformer_for_extension(given).language == "python"


def test_an_unknown_extension_is_not_an_error():
    """Unknown is a normal state here: the sandbox, not the registry, decides
    whether to pass such a file through untouched or refuse it."""

    assert transformer_for_extension(".txt") is None
    assert transformer_for_path("/x/notes.txt") is None


def test_every_supported_extension_belongs_to_a_supported_language():
    exts = supported_extensions()

    assert exts == sorted(set(exts))              # sorted, no duplicates
    assert all(e.startswith(".") for e in exts)
    for ext in exts:
        assert transformer_for_extension(ext).language in supported_languages()


def test_every_supported_language_can_be_looked_up_by_name():
    for language in supported_languages():
        assert transformer_for_language(language).language == language
        assert transformer_for_language(language.upper()) is not None
    assert transformer_for_language("cobol") is None


def test_a_c_file_the_build_compiled_as_cpp_goes_to_the_cpp_backend(tmp_path):
    """gdb compiles its ``.c`` files with the C++ driver. The extension lies and
    the compile command tells the truth, so the file is routed by the command —
    otherwise it is parsed as C and fails on the first C++ construct.
    """

    src = tmp_path / "a.c"
    src.write_text("struct S { int f() { return 1; } };\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {"directory": str(tmp_path), "file": "a.c",
         "arguments": ["clang++", "-c", "a.c"]}]), encoding="utf-8")
    (tmp_path / ".ouroboros.json").write_text(json.dumps(
        {"c": {"compdb": str(tmp_path / "compile_commands.json")}}), encoding="utf-8")

    assert transformer_for_path(str(src)).language == "cpp"


def test_a_c_file_the_build_compiled_as_c_stays_with_the_c_backend(tmp_path):
    """The same tree, the same routing question, the ordinary answer."""

    src = tmp_path / "a.c"
    src.write_text("int f(int x) { return x + 1; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {"directory": str(tmp_path), "file": "a.c",
         "arguments": ["clang", "-c", "a.c"]}]), encoding="utf-8")
    (tmp_path / ".ouroboros.json").write_text(json.dumps(
        {"c": {"compdb": str(tmp_path / "compile_commands.json")}}), encoding="utf-8")

    assert transformer_for_path(str(src)).language == "c"


def test_a_cpp_extension_is_never_downgraded_to_c(tmp_path):
    """A definitive C++ extension is authoritative. A compile database that
    records it as C — a stale entry, a generated command — does not get to
    hand a C++ file to the C parser."""

    src = tmp_path / "a.cpp"
    src.write_text("struct S { int f() { return 1; } };\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {"directory": str(tmp_path), "file": "a.cpp",
         "arguments": ["clang", "-x", "c", "-c", "a.cpp"]}]), encoding="utf-8")
    (tmp_path / ".ouroboros.json").write_text(json.dumps(
        {"c": {"compdb": str(tmp_path / "compile_commands.json")}}), encoding="utf-8")

    assert transformer_for_path(str(src)).language == "cpp"
