"""Language detection and transformer lookup.

Detection is by file extension only (the prompt's "detect the language by file
extension"). Unknown extensions are not an error at this layer — the sandbox
decides whether to pass an unsupported file through untouched or reject it.
"""

from __future__ import annotations

import os

from .base import Transformer
from .c_lang import CTransformer
from .cpp_lang import CppTransformer
from .elixir_lang import ElixirTransformer
from .go_lang import GoTransformer
from .javascript import JavaScriptTransformer
from .python_lang import PythonTransformer

# Backends are registered eagerly. Languages whose toolchain is still being
# wired (C#) will append their transformers here as they land.
_TRANSFORMERS: list[Transformer] = [
    PythonTransformer(),
    JavaScriptTransformer(),
    CTransformer(),
    CppTransformer(),
    ElixirTransformer(),
    GoTransformer(),
]

_BY_EXT: dict[str, Transformer] = {}
for _t in _TRANSFORMERS:
    for _ext in _t.extensions:
        _BY_EXT[_ext] = _t


def transformer_for_extension(ext: str) -> Transformer | None:
    """Return the transformer owning ``ext`` (e.g. ``".py"``), or ``None``."""

    if not ext.startswith("."):
        ext = "." + ext
    return _BY_EXT.get(ext.lower())


def transformer_for_path(path: str) -> Transformer | None:
    """Return the transformer for ``path``. Normally by extension, but a C/C++
    source whose compile database recorded a *different* language than its
    extension implies (e.g. gdb compiles ``.c`` files as C++) is routed to the
    backend the build actually used, so it parses instead of failing."""

    t = transformer_for_extension(os.path.splitext(path)[1])
    # Only *upgrade* a C-extension source the build actually compiled as C++
    # (e.g. gdb's ``.c`` files). A definitive C++ extension is authoritative and
    # is never downgraded to C on the strength of a compile command.
    if t is not None and t.language == "c":
        from .treeflags import compdb_language_for
        if compdb_language_for(path) == "cpp":
            return transformer_for_language("cpp") or t
    return t


def transformer_for_language(language: str) -> Transformer | None:
    """Return the transformer registered under ``language`` (e.g. ``"python"``)."""

    for t in _TRANSFORMERS:
        if t.language == language.lower():
            return t
    return None


def supported_languages() -> list[str]:
    return [t.language for t in _TRANSFORMERS]


def supported_extensions() -> list[str]:
    return sorted(_BY_EXT)
