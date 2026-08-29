"""C backend — type-directed instrumentation via libclang + `__attribute__((cleanup))`.

C has no runtime value representation and no RAII/exceptions, so this backend is
qualitatively different from the Python/JS ones:

* **Type-directed formatting.** libclang gives each parameter's and the return
  value's static type; we pick a printf specifier per type at instrumentation
  time (`int`→``%d``, ``long``→``%ld``, pointer→``%p``, ``const char*``→guarded
  ``%s``…). Non-scalar/unprintable types degrade to ``<...>`` / ``(no value)``.
* **Scope exit via `__attribute__((cleanup))`.** A GCC/Clang extension (NetBSD
  builds with both, and uses the idiom). A cleanup-attributed context emits the
  ``out`` JSONL line on EVERY exit path — return, ``goto out``, fall-through —
  which is the C equivalent of try/finally and satisfies the SPEC.

All offsets from libclang are BYTE offsets, so this backend splices on ``bytes``
(never ``str``) to stay correct on non-ASCII source. Scope: userland C (the
runtime uses stdio); kernel/freestanding needs a different sink.
"""

from __future__ import annotations

import glob
import logging
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import CorruptedSourceError, Edit, Transformer, WrapResult, apply_edits
from .treeflags import compdb_covers, tree_flags_for


def _compdb_warnings(filename: str | None, language: str) -> tuple[str, ...]:
    """Advisory when a tree file isn't in the compile DB, so the parse used
    degraded flags (no build ``-D``) and ``#ifdef``-guarded functions may have
    been silently skipped — shared by the C and C++ backends."""
    if compdb_covers(filename, language) is False:
        return (
            f"{filename} is under an .ouroboros.json tree but not in its "
            "compile_commands.json; parsed with fallback flags, so functions in "
            "inactive #ifdef branches (build -D defines) may be missed — add it to "
            "the compile DB for complete instrumentation.",
        )
    return ()

if TYPE_CHECKING:
    # libclang ships no type stubs, so these resolve to Any (see the clang.*
    # mypy override). Naming them keeps the signatures readable — `body: Cursor`
    # reads better than `body: Any` — without claiming type safety we don't have.
    from clang.cindex import Cursor, Type

_log = logging.getLogger("ouroboros.c")


def _gate_diagnostics(cindex: Any, tu: Any, lang: str, filename: str | None,
                      *, strict: bool) -> None:
    """The parse gate. For self-contained snippets (``strict``) any error means a
    malformed buffer → raise. For real tree files the parse uses clang against a
    tree that gcc built, so some diagnostics are irreducible (gcc-only atomics on
    ``_Atomic`` ptrs, ``_Float32``, other gcc extensions); the AST is still valid
    for instrumentation (each function we touch is gated on a well-formed body),
    so we record the residual instead of failing — measured, not hidden."""
    errors = [d for d in tu.diagnostics
              if d.severity >= cindex.Diagnostic.Error]
    if not errors:
        return
    if strict:
        raise CorruptedSourceError(lang, errors[0].spelling, filename=filename)
    sample = "; ".join(d.spelling for d in errors[:3])
    _log.info("tree-parse residual: %d clang/gcc diagnostic(s) in %s [%s]",
              len(errors), filename, sample)

_C_DIR = Path(__file__).parent / "_c"
_RUNTIME_H = _C_DIR / "ouroboros_runtime.h"

_INCLUDE = '#include "ouroboros_runtime.h"'



def _body_already_wrapped(raw: bytes, body: Cursor) -> bool:
    """True if a function body already carries our ``__ouro`` instrumentation.

    Used for per-function idempotency: an incremental call must skip functions it
    wrapped before while still being free to wrap new ones. We look for the
    ``__ouro`` token (the cleanup-attributed local our entry injection introduces)
    inside the body's own byte range, not file-wide."""
    return b"__ouro" in raw[body.extent.start.offset:body.extent.end.offset]

# printf specifier per canonical scalar TypeKind name.
_SCALAR_SPEC = {
    "INT": "%d", "ENUM": "%d", "SHORT": "%d", "SCHAR": "%d", "CHAR_S": "%d",
    "BOOL": "%d", "WCHAR": "%d",
    "UINT": "%u", "USHORT": "%u", "UCHAR": "%u", "CHAR_U": "%u",
    "LONG": "%ld", "ULONG": "%lu",
    "LONGLONG": "%lld", "ULONGLONG": "%llu",
    "FLOAT": "%f", "DOUBLE": "%f", "LONGDOUBLE": "%Lf",
}

_cindex: Any = None


@lru_cache(maxsize=1)
def _clang_args() -> list[str]:
    """Parse flags for **self-contained** code: clang builtin headers + the host
    /usr/include, so a well-formed snippet parses with zero diagnostics. Files
    that belong to a real source tree instead get that tree's flags + the tree
    compiler's predefs via an `.ouroboros.json` config — see `treeflags.py`."""
    args = ["-std=gnu11", "-ferror-limit=0"]
    # Resolve the clang builtin resource dir (provides stddef.h etc.). Try the
    # unversioned name first, then common versioned binaries — on some hosts only
    # e.g. clang-18 is on PATH (Debian/Ubuntu llvm packages).
    for _cc in ("clang", "clang-20", "clang-19", "clang-18", "clang-17", "clang-16"):
        try:
            rd = subprocess.run([_cc, "-print-resource-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if rd:
            args += ["-isystem", f"{rd}/include"]
            break
    args += ["-isystem", "/usr/include"]
    return args


def _clang() -> Any:
    """Import and configure clang.cindex lazily; return the module."""
    global _cindex
    if _cindex is not None:
        return _cindex
    from clang import cindex

    try:
        cindex.Index.create()
    except cindex.LibclangError:
        for so in sorted(glob.glob("/lib/*/libclang-*.so.*"), reverse=True):
            try:
                cindex.Config.set_library_file(so)
                cindex.Index.create()
                break
            except Exception:  # noqa: BLE001
                continue
    _cindex = cindex
    return cindex


@dataclass
class _Spec:
    fmt: str | None          # printf specifier, or None if unprintable
    is_string: bool = False  # const char* -> guarded %s


def _spec_for(t: Type) -> _Spec:
    cindex = _cindex
    c = t.get_canonical()
    if c.kind == cindex.TypeKind.POINTER:
        pointee = c.get_pointee()
        if pointee.get_canonical().kind in (
            cindex.TypeKind.CHAR_S, cindex.TypeKind.CHAR_U,
            cindex.TypeKind.SCHAR, cindex.TypeKind.UCHAR,
        ) and pointee.is_const_qualified():
            return _Spec("%s", is_string=True)
        return _Spec("%p")
    return _Spec(_SCALAR_SPEC.get(c.kind.name))


def _temp_type(t: Type) -> str | None:
    """C type string for the `__ouro_result` temp, with top-level const/volatile
    stripped (you cannot assign to a const). Returns None if it can't be made
    assignable safely."""
    spelling: str = t.spelling
    if t.is_const_qualified() or t.is_volatile_qualified():
        for q in ("const ", "volatile "):
            if spelling.startswith(q):
                spelling = spelling[len(q):]
        if t.is_const_qualified() and "const" in spelling.split("*")[0]:
            return None  # couldn't strip a leading top-level const
    return spelling


class CTransformer(Transformer):
    language = "c"
    extensions = (".c", ".h")

    def runtime_asset(self) -> tuple[str, str]:
        return "ouroboros_runtime.h", _RUNTIME_H.read_text(encoding="utf-8")

    def _parse(self, source: bytes, filename: str | None) -> tuple[Any, Any, str]:
        cindex = _clang()
        fname = filename or "input.c"
        # A file under a tree with an .ouroboros.json gets that tree's exact
        # flags + the tree compiler's predefs; everything else uses the
        # self-contained defaults. -ferror-limit=0 keeps the full diagnostic
        # list so the corruption gate sees every error, not just the first.
        tree = tree_flags_for(filename, self.language)
        args = (["-ferror-limit=0", *tree]) if tree is not None else _clang_args()
        # Always let the parser find our OWN runtime header, so re-parsing an
        # already-instrumented file (whose injected #include "ouroboros_runtime.h"
        # would otherwise be unresolved) succeeds instead of tripping the gate.
        # This is what makes incremental instrumentation work in every context --
        # no filename special-casing needed.
        args = [*args, "-I", str(_C_DIR)]
        idx = cindex.Index.create()
        try:
            tu = idx.parse(fname, args=args,
                           unsaved_files=[(fname, source)])
        except cindex.TranslationUnitLoadError as e:
            raise CorruptedSourceError("c", str(e), filename=filename) from e
        _gate_diagnostics(cindex, tu, "c", filename, strict=tree is None)
        return cindex, tu, fname

    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        raw = source.encode("utf-8")
        # `already_included` only decides whether to (re-)insert the include; it is
        # NOT an early exit. The parser is given -I to our runtime dir (see _parse),
        # so an already-instrumented buffer re-parses cleanly and per-function
        # idempotency below leaves wrapped functions alone while adding new ones.
        already_included = _INCLUDE.encode() in raw

        cindex, tu, fname = self._parse(raw, filename)

        edits: list[Edit[bytes]] = []
        wrapped = 0
        first_off: int | None = None      # start offset of the first instrumented function
        for fn in tu.cursor.walk_preorder():
            if fn.kind != cindex.CursorKind.FUNCTION_DECL or not fn.is_definition():
                continue
            if not fn.location.file or fn.location.file.name != fname:
                continue  # skip decls pulled in from headers
            body = next((c for c in fn.get_children()
                         if c.kind == cindex.CursorKind.COMPOUND_STMT), None)
            if body is None:
                continue
            # Skip MACRO-GENERATED functions (BSD SPLAY_PROTOTYPE / RB_GENERATE / the
            # crash-test macros / ...): libclang reports the macro-EXPANDED function with a
            # body whose source offset points INSIDE the macro invocation, so splicing probe
            # text there splits the macro ("S" + probe + "PLAY_PROTOTYPE(...)") and the file
            # no longer compiles. If the body's start byte is not a literal '{', it is not
            # real editable source — it cannot be text-instrumented, so leave it alone.
            bstart = body.extent.start.offset
            if raw[bstart:bstart + 1] != b"{":
                continue
            # FIRST real function offset — the include anchor. Tracked for EVERY real
            # function (before the `only`/idempotency filters below), so the include lands
            # ahead of the whole file's functions and stays correct across incremental
            # selective wraps (a later-wrapped function is never left above the include).
            if first_off is None or fn.extent.start.offset < first_off:
                first_off = fn.extent.start.offset
            if only is not None and fn.spelling not in only:
                continue  # selective mode: leave non-listed functions untouched
            # Per-function idempotency: skip a function whose body already carries our
            # __ouro instrumentation, so a second call ADDS new functions without re-wrapping.
            if _body_already_wrapped(raw, body):
                continue
            wrapped += 1
            edits.extend(self._instrument(cindex, fn, body, minimal=minimal))

        # Inject the include only when it is not already there AND we added at least one
        # new function. Splice it at the START of the line of the FIRST instrumented
        # function — robustly AFTER every #include/#define/#if block (libclang LOCATED the
        # function, so this survives multi-line macros, `#if 0` dead code and RCSID
        # boilerplate that defeat text scanning) and BEFORE any probe use.
        if wrapped and not already_included and first_off is not None:
            at = raw.rfind(b"\n", 0, first_off) + 1
            edits.insert(0, Edit(at, at, (_INCLUDE + "\n").encode("utf-8")))

        new_bytes = apply_edits(raw, edits)
        return WrapResult(code=new_bytes.decode("utf-8"),
                          language=self.language, functions_wrapped=wrapped,
                          warnings=_compdb_warnings(filename, self.language))

    def _instrument(self, cindex: Any, fn: Cursor, body: Cursor,
                    *, minimal: bool = False) -> list[Edit[bytes]]:
        edits: list[Edit[bytes]] = []
        name = fn.spelling

        # ---- minimal probe: stackless, depth-only (hot/recursive/locked kernel) ----
        # No per-frame struct, no args, no return rewriting — just a 1-byte cleanup
        # marker initialised to _ouro_min_enter's return (1 = recorded, 0 = skipped by the
        # re-entrancy guard), so _ouro_min_exit balances the depth. The `__ouro` token keeps
        # idempotency ([[_body_already_wrapped]]); the cleanup attribute marks it used.
        if minimal:
            open_off = body.extent.start.offset + 1
            entry = (
                "\n\tchar __ouro __attribute__((cleanup(_ouro_min_exit)))"
                f' = _ouro_min_enter("{name}");\n'
            ).encode()
            return [Edit(open_off, open_off, entry)]

        # ---- argument format (type-directed) ----
        fmt_parts, arg_exprs = [], []
        # Values only, comma-separated. SPEC.md splits the two fields: `a` carries
        # positional values, `k` carries name=value pairs (and C, having no named
        # arguments, emits `k` empty). Writing "a=2, b=3" into `a` put names in
        # the field that must not hold them, and made the C trace uncomparable
        # with the Python and JS ones for the same call.
        for p in fn.get_arguments():
            pname = p.spelling or "_"
            spec = _spec_for(p.type)
            if spec.fmt is None:
                fmt_parts.append("<...>")
            elif spec.is_string:
                fmt_parts.append("%s")
                arg_exprs.append(f'({pname} ? {pname} : "(null)")')
            else:
                fmt_parts.append(spec.fmt)
                arg_exprs.append(pname)
        fmt = ", ".join(fmt_parts)
        tail = ("" if not arg_exprs else ", " + ", ".join(arg_exprs))

        # ---- return-value capture plan ----
        ret_spec = _spec_for(fn.result_type)
        temp_t = None if fn.result_type.get_canonical().kind == cindex.TypeKind.VOID \
            else _temp_type(fn.result_type)
        capture = ret_spec.fmt is not None and temp_t is not None

        # ---- entry injection (just after the body's `{`) ----
        open_off = body.extent.start.offset + 1
        lines = ["", "\tstruct _ouro_call __ouro __attribute__((cleanup(_ouro_emit)));"]
        if capture:
            lines.append(f"\t{temp_t} __ouro_result;")
        lines.append(f'\t_ouro_enter(&__ouro, "{name}", "{fmt}"{tail});')
        entry = ("\n".join(lines) + "\n").encode("utf-8")
        edits.append(Edit(open_off, open_off, entry))

        # ---- return-value capture at each return-with-value ----
        if capture:
            res_arg = ('(__ouro_result ? __ouro_result : "(null)")'
                       if ret_spec.is_string else "__ouro_result")
            for d in fn.walk_preorder():
                if d.kind != cindex.CursorKind.RETURN_STMT:
                    continue
                kids = list(d.get_children())
                if not kids:
                    continue  # bare `return;` — cleanup handles it
                val = kids[0]
                a0, a1 = val.extent.start.offset, val.extent.end.offset
                pre = b" (__ouro_result = ("
                post = (f"), _ouro_set_result(&__ouro, \"{ret_spec.fmt}\", "
                        f"{res_arg}), __ouro_result)").encode()
                edits.append(Edit(a0, a0, pre))
                edits.append(Edit(a1, a1, post))
        return edits
