"""C++ backend — RAII ScopeGuard instrumentation via libclang.

Follows example.md's C++ shape: a scope-guard object whose destructor is the
"finally". Versus C, C++ is simpler in one way (generic value stringification via
``operator<<`` / the templated ``_ouro::repr`` — no per-type printf specifiers)
and richer in others handled here: qualified names (namespaces/classes),
exception-aware exit (``std::uncaught_exceptions``), and excluding ``return``s
that belong to nested lambdas rather than the function.

Offsets from libclang are BYTE offsets, so this backend splices on ``bytes``.
Scope: userland C++. Special members (constructors/destructors/conversions),
lambdas and templates are skipped in this first cut.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import CorruptedSourceError, Edit, Transformer, WrapResult, apply_edits
from .c_lang import _clang, _compdb_warnings, _gate_diagnostics
from .treeflags import tree_flags_for

if TYPE_CHECKING:
    # libclang has no stubs -> Any (see the clang.* mypy override); naming the
    # cursor types keeps signatures readable. See c_lang for the same pattern.
    from clang.cindex import Cursor

_CPP_DIR = Path(__file__).parent / "_cpp"
_RUNTIME_HPP = _CPP_DIR / "ouroboros_runtime.hpp"
_INCLUDE = '#include "ouroboros_runtime.hpp"'


@lru_cache(maxsize=1)
def _cxx_args() -> list[str]:
    """C++ parse flags incl. the libstdc++ header search path (discovered from
    g++), so well-formed self-contained C++ parses with zero diagnostics."""
    args = ["-x", "c++", "-std=c++17", "-ferror-limit=0"]
    try:
        rd = subprocess.run(["clang", "-print-resource-dir"],
                            capture_output=True, text=True, timeout=10).stdout.strip()
        if rd:
            args += ["-isystem", f"{rd}/include"]
    except (OSError, subprocess.SubprocessError):
        pass
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


def _qualified_name(cindex: Any, fn: Cursor) -> str:
    parts = [fn.spelling]
    p = fn.semantic_parent
    while p is not None and _safe_kind(p) in (
        cindex.CursorKind.NAMESPACE, cindex.CursorKind.CLASS_DECL,
        cindex.CursorKind.STRUCT_DECL, cindex.CursorKind.CLASS_TEMPLATE,
        cindex.CursorKind.UNION_DECL,
    ):
        if p.spelling:
            parts.append(p.spelling)
        p = p.semantic_parent
    return "::".join(reversed(parts))


def _safe_kind(cursor: Cursor) -> Any:
    """``cursor.kind`` or ``None``. The bundled clang.cindex enum can lag the
    libclang .so, so a newer node (e.g. a C++20 cursor) raises ValueError from
    ``CursorKind.from_id``; we skip such nodes rather than abort the whole file."""
    try:
        return cursor.kind
    except ValueError:
        return None


def _returns(cindex: Any, fn: Cursor) -> list[Cursor]:
    """RETURN_STMT cursors belonging to fn, NOT to nested lambdas."""
    out: list[Cursor] = []

    def visit(node: Cursor) -> None:
        for ch in node.get_children():
            k = _safe_kind(ch)
            if k == cindex.CursorKind.LAMBDA_EXPR:
                continue  # a lambda's returns belong to the lambda
            if k == cindex.CursorKind.RETURN_STMT:
                out.append(ch)
            visit(ch)

    visit(fn)
    return out


def _is_constexpr(fn: Cursor, body: Cursor) -> bool:
    """True if the declaration carries ``constexpr`` / ``consteval``.

    Such a function must remain usable in a constant expression, and the entry
    block this backend splices in cannot be: it builds a ``std::ostringstream``
    and writes a file. Instrumenting one turns ``constexpr int v = sq(5);`` into
    a compile error, so these functions are left alone entirely.

    Read off the tokens ahead of the body rather than a cursor flag: libclang
    exposes no ``is_constexpr`` on the Python binding.
    """

    limit = body.extent.start.offset
    for tok in fn.get_tokens():
        if tok.extent.start.offset >= limit:
            break
        if tok.spelling in ("constexpr", "consteval"):
            return True
    return False


def _captures_safely(cindex: Any, fn: Cursor, value: Cursor) -> bool:
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

    if _safe_kind(value) == cindex.CursorKind.INIT_LIST_EXPR:
        return False
    canonical = fn.result_type.get_canonical()
    return bool(canonical.kind != cindex.TypeKind.RECORD)


_SKIP: Any = None


class CppTransformer(Transformer):
    language = "cpp"
    extensions = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")

    def runtime_asset(self) -> tuple[str, str]:
        return "ouroboros_runtime.hpp", _RUNTIME_HPP.read_text(encoding="utf-8")

    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        if minimal:
            raise NotImplementedError("minimal probe mode is C-only (kernel ring sink)")
        raw = source.encode("utf-8")
        # Not an early exit: only gates include (re-)insertion. -I to our runtime
        # dir (below) lets an already-instrumented buffer re-parse, so per-function
        # idempotency can add new functions without re-wrapping existing ones.
        already_included = _INCLUDE.encode() in raw

        cindex = _clang()
        global _SKIP
        if _SKIP is None:
            _SKIP = {cindex.CursorKind.CONSTRUCTOR, cindex.CursorKind.DESTRUCTOR,
                     cindex.CursorKind.CONVERSION_FUNCTION}
        fname = filename or "input.cpp"
        # A file under a tree with an .ouroboros.json gets that tree's exact
        # flags (compile_commands.json) + the toolchain's include dirs; anything
        # else uses the self-contained C++ defaults.
        tree = tree_flags_for(filename, self.language)
        args = (["-x", "c++", "-ferror-limit=0", *tree]) if tree is not None \
            else _cxx_args()
        # Always resolve our own runtime header so re-parsing instrumented code
        # works (incremental instrumentation in every context, no special-casing).
        args = [*args, "-I", str(_CPP_DIR)]
        idx = cindex.Index.create()
        try:
            tu = idx.parse(fname, args=args, unsaved_files=[(fname, raw)])
        except cindex.TranslationUnitLoadError as e:
            raise CorruptedSourceError("cpp", str(e), filename=filename) from e
        _gate_diagnostics(cindex, tu, "cpp", filename, strict=tree is None)

        edits: list[Edit[bytes]] = []
        wrapped = 0
        for fn in tu.cursor.walk_preorder():
            k = _safe_kind(fn)
            if k not in (cindex.CursorKind.FUNCTION_DECL,
                         cindex.CursorKind.CXX_METHOD):
                continue
            if k in _SKIP or not fn.is_definition():
                continue
            if not fn.location.file or fn.location.file.name != fname:
                continue
            if only is not None and fn.spelling not in only:
                continue  # selective mode (matches the unqualified method/fn name)
            body = next((c for c in fn.get_children()
                         if _safe_kind(c) == cindex.CursorKind.COMPOUND_STMT), None)
            if body is None:
                continue
            # Per-function idempotency (see c_lang): skip a body already carrying
            # our __ouro scope guard so an incremental call only adds new functions.
            if b"__ouro" in raw[body.extent.start.offset:body.extent.end.offset]:
                continue
            if _is_constexpr(fn, body):
                continue
            wrapped += 1
            edits.extend(self._instrument(cindex, fn, body))

        if wrapped and not already_included:
            edits.insert(0, Edit(0, 0, (_INCLUDE + "\n").encode("utf-8")))

        new_bytes = apply_edits(raw, edits)
        return WrapResult(code=new_bytes.decode("utf-8"),
                          language=self.language, functions_wrapped=wrapped,
                          warnings=_compdb_warnings(filename, self.language))

    def _instrument(self, cindex: Any, fn: Cursor, body: Cursor) -> list[Edit[bytes]]:
        edits: list[Edit[bytes]] = []
        qname = _qualified_name(cindex, fn)

        names = [p.spelling for p in fn.get_arguments() if p.spelling]
        open_off = body.extent.start.offset + 1
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
        edits.append(Edit(open_off, open_off, (entry + "\n").encode("utf-8")))
        close_off = body.extent.end.offset - 1  # the body's closing "}"
        edits.append(Edit(close_off, close_off,
                          b"\n\t} catch (...) { __ouro.note(); throw; }\n"))

        for ret in _returns(cindex, fn):
            kids = list(ret.get_children())
            if not kids:
                continue  # bare `return;`
            val = kids[0]
            if not _captures_safely(cindex, fn, val):
                continue  # would cost a copy elision or fail to compile
            a0, a1 = val.extent.start.offset, val.extent.end.offset
            edits.append(Edit(a0, a0, b"_ouro::capture(__ouro, ("))
            edits.append(Edit(a1, a1, b"))"))
        return edits
