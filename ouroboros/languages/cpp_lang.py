"""C++ backend — RAII ScopeGuard instrumentation.

Follows example.md's C++ shape: a scope-guard object whose destructor is the
"finally". Versus C, C++ is simpler in one way (generic value stringification via
``operator<<`` / the templated ``_ouro::repr`` — no per-type printf specifiers)
and richer in others handled here: qualified names (namespaces/classes),
exception-aware exit (``std::uncaught_exceptions``), and excluding ``return``s
that belong to nested lambdas rather than the function.

Offsets are BYTE offsets, so this backend splices on ``bytes``. Scope: userland
C++. Special members (constructors/destructors/conversions), lambdas and
templates are skipped in this first cut.

The parse happens in another process — see ``clangbridge``, which the C backend
shares. What is left here is the text this language injects and the two
questions only it asks: is this function ``constexpr``, and may this returned
expression be routed through ``_ouro::capture``.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from .base import Edit
from .clangbridge import (
    ClangFunction,
    ClangReturn,
    ClangTransformer,
    clang_resource_dir_args,
)

_CPP_DIR = Path(__file__).parent / "_cpp"
_INCLUDE = '#include "ouroboros_runtime.hpp"'


@lru_cache(maxsize=1)
def _cxx_args() -> list[str]:
    """C++ parse flags incl. the libstdc++ header search path (discovered from
    g++), so well-formed self-contained C++ parses with zero diagnostics."""
    args = ["-x", "c++", "-std=c++17", "-ferror-limit=0", *clang_resource_dir_args()]
    try:
        out = subprocess.run(["g++", "-E", "-x", "c++", "-v", "-"], input="",
                            capture_output=True, text=True, timeout=10).stderr
        collecting = False
        for line in out.splitlines():
            if "#include <...> search starts here:" in line:
                collecting = True
                continue
            if "End of search list." in line:
                break
            if collecting:
                d = line.strip()
                if d and Path(d).is_dir():
                    args += ["-isystem", d]
    except (OSError, subprocess.SubprocessError):
        pass
    return args


def _captures_safely(fn: ClangFunction, ret: ClangReturn) -> bool:
    """May the returned expression be routed through ``_ouro::capture``?

    ``capture`` takes the expression by forwarding reference and hands it back,
    which is transparent for scalars and references but NOT for a class type
    returned by value. There, the extra function call:

    * destroys guaranteed copy elision — a program that counted one constructor
      before now runs a move constructor it never ran, so instrumentation is
      visible in the program's own output;
    * fails outright for a type with copy and move both ``= delete``, which is
      legal to return by value in C++17 and now stops compiling;
    * cannot be written at all around a braced initialiser (``return {1,2};``),
      because ``(...)`` around a braced-init-list is not an expression.

    So class/struct/union return values are recorded as "(no value)". The entry
    record, the arguments, the duration and the exception flag are all still
    logged; only the returned object's repr is given up — the price of not
    changing the program being observed.
    """

    return not ret.is_init_list and not fn.result.is_record


class CppTransformer(ClangTransformer):
    language = "cpp"
    extensions = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")

    runtime_dir = _CPP_DIR
    runtime_name = "ouroboros_runtime.hpp"
    include_line = _INCLUDE
    default_filename = "input.cpp"
    #: a compile database entry for a C++ file does not carry ``-x c++``, and
    #: libclang decides the dialect from the file name otherwise.
    tree_arg_prefix = ("-x", "c++")

    def default_args(self) -> list[str]:
        return _cxx_args()

    def skip(self, fn: ClangFunction) -> bool:
        """``constexpr`` / ``consteval`` functions are left alone entirely.

        Such a function must remain usable in a constant expression, and the
        entry block this backend splices in cannot be: it builds a
        ``std::ostringstream`` and writes a file. Instrumenting one turns
        ``constexpr int v = sq(5);`` into a compile error.
        """
        return fn.is_constexpr

    def instrument(self, fn: ClangFunction, *, minimal: bool = False) -> list[Edit[bytes]]:
        qname = fn.qualified_name
        names = [p.name for p in fn.params if p.name]
        open_off = fn.body_start + 1
        if names:
            # Values only, comma-separated. SPEC.md splits the two fields: `a`
            # carries positional values, `k` carries name=value pairs. Emitting
            # "a=2, b=3" into `a` put names in the field that must not hold them,
            # and made the C++ trace uncomparable with the Python and JS ones.
            pieces = ' << ", " << '.join(f"_ouro::repr({n})" for n in names)
            entry = (
                f"\n\tstd::ostringstream __ouro_args; __ouro_args << {pieces};"
                f'\n\t_ouro::Scope __ouro("{qname}", __ouro_args.str());'
            )
        else:
            entry = f'\n\t_ouro::Scope __ouro("{qname}", "");'
        # The scope guard alone cannot name the exception that left the function:
        # during unwinding std::current_exception() is null. A catch clause that
        # records and immediately rethrows can, and leaves control flow exactly
        # as it was — the guard is declared OUTSIDE the try so its destructor
        # still runs after the rethrow and writes the completion record.
        entry += "\n\ttry {"
        edits: list[Edit[bytes]] = [
            Edit(open_off, open_off, (entry + "\n").encode("utf-8")),
        ]
        close_off = fn.body_end - 1  # the body's closing "}"
        edits.append(Edit(close_off, close_off,
                          b"\n\t} catch (...) { __ouro.note(); throw; }\n"))

        for ret in fn.returns:
            if ret.arg_start is None or ret.arg_end is None:
                continue  # bare `return;`
            if not _captures_safely(fn, ret):
                continue  # would cost a copy elision or fail to compile
            edits.append(Edit(ret.arg_start, ret.arg_start, b"_ouro::capture(__ouro, ("))
            edits.append(Edit(ret.arg_end, ret.arg_end, b"))"))
        return edits
