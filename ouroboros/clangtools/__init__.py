"""C/C++ static-analysis tooling that wraps LLVM's official binaries.

Two capabilities, both keyed on the SAME ``compile_commands.json`` that
``treeflags.py`` already discovers (via ``.ouroboros.json`` → ``compdb``):

* :mod:`lint` — run **clang-tidy** over a file and report real bugs the
  parse-only corruption gate can't see (use-after-free, ``if (a = b)``, dead
  stores, perf traps), with the instrumentation's own injected identifiers
  filtered out so we never report phantom problems.
* :mod:`clangd` — drive **clangd** (the LLVM C/C++ language server) over LSP to
  answer ``workspace/symbol`` — smart cross-file symbol search to pick
  ``wrap_functions`` targets on a big tree.

Both reuse the C/C++ backends' flag-building so header resolution matches the
instrumentation path exactly (the injected ``#include "ouroboros_runtime.h"``
resolves; no spurious "header not found").
"""

from __future__ import annotations

from .clangd import (
    call_hierarchy,
    describe_symbol,
    document_symbols,
    references,
    symbol_search,
)
from .lint import lint_file

__all__ = ["call_hierarchy", "describe_symbol", "document_symbols", "lint_file",
           "references", "symbol_search"]
