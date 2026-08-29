"""Compile-flag + binary resolution shared by the lint and clangd tools.

Both clang-tidy and clangd consume clang compile flags and a
``compile_commands.json``. We reuse the C/C++ backends' EXACT flag-building so a
file linted/indexed here sees the same world the instrumentation parser saw —
otherwise the injected ``#include "ouroboros_runtime.h"`` (and the toolchain's
own headers) wouldn't resolve, and the tools would report phantom errors.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..languages import transformer_for_path
from ..languages.c_lang import _C_DIR, _clang_args
from ..languages.cpp_lang import _CPP_DIR, _cxx_args
from ..languages.treeflags import compdb_language_for, tree_flags_for


def language_for(path: str) -> str | None:
    """``"c"`` / ``"cpp"`` for a path, or ``None`` if it is neither. A compile
    database that recorded the file (e.g. a ``.h`` compiled as C++) wins over the
    bare extension; otherwise fall back to the transformer registry."""
    detected = compdb_language_for(path)
    if detected in ("c", "cpp"):
        return detected
    tx = transformer_for_path(path)
    lang = getattr(tx, "language", None)
    return lang if lang in ("c", "cpp") else None


def compile_flags_for(path: str, language: str) -> list[str]:
    """The clang compile flags for ``path`` — the tree's exact flags when an
    ``.ouroboros.json`` covers it, else the self-contained C/C++ defaults — plus
    the include dirs that make the Ouroboros runtime header resolve (the file's
    own directory for an in-place ``_drop_runtime_asset`` copy, and the backend's
    bundled ``_c``/``_cpp`` dir as a fallback)."""
    tree = tree_flags_for(path, language)
    if language == "cpp":
        flags = (["-x", "c++", "-ferror-limit=0", *tree]) if tree is not None else _cxx_args()
        runtime_dir = _CPP_DIR
    else:
        flags = (["-ferror-limit=0", *tree]) if tree is not None else _clang_args()
        runtime_dir = _C_DIR
    return [*flags, "-I", str(Path(path).expanduser().parent), "-I", str(runtime_dir)]


def find_tool(*names: str) -> str | None:
    """First of ``names`` (e.g. ``"clang-tidy"``, ``"clang-tidy-18"``) found on
    PATH. Mirrors how the C backend probes for a versioned ``clang`` binary."""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None
