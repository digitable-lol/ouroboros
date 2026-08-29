"""The C-family half of the locate-then-splice split: libclang, out of process.

The C and C++ backends used to call libclang through ``clang.cindex`` inside the
transformer, so reading a parameter's static type and building the text that
prints it happened in the same loop. This module moves the parser to the other
side of a process boundary — the same shape the JavaScript backend has with
``_js/emitter.js`` and the Elixir one with ``_elixir/emit.exs``:

* ``_clang/emitter.c`` is a small native program. It parses the buffer with
  libclang and prints a JSON description of every instrumentable function — body
  range, parameters (each with the printf conversion its type needs), the result
  type's capture plan, and where each ``return``'s expression sits. It generates
  no code.
* Everything below splices bytes at those offsets. It never asks a question
  about C or C++; it reads numbers out of a JSON document.

One contract serves both languages: the same program, the same JSON, one
argument (``c`` / ``cpp``) selecting the dialect. :class:`ClangTransformer`
holds the wrap loop both backends share, leaving each with only the text it
injects.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .base import CorruptedSourceError, Edit, Transformer, WrapResult, apply_edits
from .treeflags import compdb_covers, tree_flags_for

_log = logging.getLogger("ouroboros.clang")

_CLANG_DIR = Path(__file__).parent / "_clang"
_EMITTER_SRC = _CLANG_DIR / "emitter.c"
_EMITTER_HDR = _CLANG_DIR / "libclang_api.h"

#: Wall-clock ceiling for one parse. Generous on purpose: a kernel file with the
#: build's full ``-I``/``-D`` set is a real translation unit, not a snippet.
EMIT_TIMEOUT = 300


class ClangEmitterError(Exception):
    """The range emitter could not be built or could not be run.

    Deliberately NOT a :class:`CorruptedSourceError`: the source was never
    looked at. Reporting a toolchain problem as "your code is corrupt" is how a
    caller ends up rewriting a file that was fine.
    """


# --------------------------------------------------------------------------- #
# what the emitter says
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClangParam:
    """One declared parameter. ``spec`` is the printf conversion its static type
    needs, or ``None`` when the type cannot be printed at all. ``is_string`` marks
    a ``const char *``, which the caller must guard against NULL."""

    name: str
    spec: str | None
    is_string: bool


@dataclass(frozen=True)
class ClangResult:
    """The return type's capture plan. ``temp_type`` is the C spelling for the
    ``__ouro_result`` temp with top-level const/volatile stripped, or ``None``
    when no assignable spelling exists."""

    is_void: bool
    is_record: bool
    spec: str | None
    is_string: bool
    temp_type: str | None


@dataclass(frozen=True)
class ClangReturn:
    """One ``return`` belonging to the function (never one inside a nested
    lambda). ``arg_start`` is ``None`` for a bare ``return;``."""

    arg_start: int | None
    arg_end: int | None
    is_init_list: bool


@dataclass(frozen=True)
class ClangFunction:
    """One function definition in the file being wrapped. All offsets are BYTE
    offsets into the buffer that was handed to the emitter."""

    name: str
    qualified_name: str
    extent_start: int
    body_start: int
    body_end: int
    is_constexpr: bool
    params: tuple[ClangParam, ...]
    result: ClangResult
    returns: tuple[ClangReturn, ...]


@dataclass(frozen=True)
class ClangUnit:
    """The emitter's answer for one buffer.

    ``error_count`` is every error-or-worse diagnostic clang produced;
    ``errors`` carries only the first few messages, which is all any caller
    prints.
    """

    functions: tuple[ClangFunction, ...]
    error_count: int
    errors: tuple[str, ...]


# --------------------------------------------------------------------------- #
# building the emitter
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def libclang_library() -> str:
    """Path of the libclang shared object to link the emitter against.

    The ``libclang`` Python dependency ships one inside the wheel, so this
    normally resolves without touching the host. The system copies are the
    fallback for an installation that got libclang from the distribution
    instead.
    """

    for base in sys.path:
        native = Path(base) / "clang" / "native" / "libclang.so"
        if native.is_file():
            return str(native)
    for pattern in ("usr/lib/llvm-*/lib/libclang.so*",
                    "usr/lib/*/libclang-*.so.*",
                    "lib/*/libclang-*.so.*",
                    "usr/local/lib/libclang.so*",
                    "opt/homebrew/opt/llvm/lib/libclang.dylib"):
        for found in sorted(Path("/").glob(pattern), reverse=True):
            if found.is_file():
                return str(found)
    raise ClangEmitterError(
        "no libclang shared library found; the `libclang` Python dependency "
        "ships one, so this usually means the install is incomplete"
    )


def _compiler() -> str:
    """The C compiler used to build the emitter.

    Requiring one is not a new dependency: instrumented C and C++ has to be
    compiled to be worth anything, so a host that can use these backends has a
    compiler by definition.
    """

    for name in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if name and shutil.which(name):
            return name
    raise ClangEmitterError(
        "no C compiler found (tried $CC, cc, gcc, clang); the C/C++ range "
        "emitter is a C program and has to be built once per machine"
    )


@lru_cache(maxsize=1)
def clang_resource_dir_args() -> list[str]:
    """``-isystem`` for clang's builtin header directory (stddef.h and friends).

    Both self-contained flag sets need it and both used to find it themselves;
    the C one tried versioned binaries and the C++ one did not, so on a host
    where only ``clang-18`` is installed C parsed with builtin headers and C++
    parsed without. Empty when no clang can be found — the parse then relies on
    whatever the flags already carry.
    """

    for name in ("clang", "clang-20", "clang-19", "clang-18", "clang-17", "clang-16"):
        try:
            found = subprocess.run([name, "-print-resource-dir"], capture_output=True,
                                   text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if found:
            return ["-isystem", f"{found}/include"]
    return []


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ouroboros"


def build_emitter(destination: Path, *, system_header: str | None = None,
                  library: str | None = None) -> None:
    """Compile ``emitter.c`` to ``destination``.

    The ordinary build takes libclang's declarations from the vendored
    ``libclang_api.h`` and opens the shared object with ``dlopen`` at run time,
    so it needs neither the distribution's llvm *development* package nor a
    fixed libclang version: one built emitter works with any of them.

    ``system_header`` switches to the control build used by the test that holds
    the vendored declarations honest — the real ``<clang-c/Index.h>`` and an
    ordinary link against ``library``.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = [_compiler(), "-O2", "-o", str(destination), str(_EMITTER_SRC)]
    if system_header is not None:
        if library is None:
            raise ClangEmitterError("the control build needs a library to link")
        argv += ["-DOURO_SYSTEM_CLANG_C", "-I", system_header,
                 library, f"-Wl,-rpath,{Path(library).parent}"]
    else:
        argv += ["-I", str(_CLANG_DIR)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except OSError as e:
        raise ClangEmitterError(f"cannot run the C compiler: {e}") from e
    if proc.returncode != 0 or not destination.exists():
        raise ClangEmitterError(
            f"building the C/C++ range emitter failed:\n"
            f"  {' '.join(argv)}\n{proc.stderr.strip()}"
        )


@lru_cache(maxsize=1)
def emitter_path() -> str:
    """Path of the built range emitter, building it on first use.

    The build is cached per machine under a name derived from the emitter's own
    two source files, so editing either produces a different binary instead of
    reusing a stale one. The libclang it will use is NOT part of that name: the
    binary opens the library at run time, so one build serves every libclang on
    the machine. ``OUROBOROS_CLANG_EMITTER`` overrides everything — that is the
    hook for a packaged build that ships the binary rather than compiling here.
    """

    override = os.environ.get("OUROBOROS_CLANG_EMITTER")
    if override:
        return override

    key = hashlib.sha256(
        _EMITTER_SRC.read_bytes() + b"\0" + _EMITTER_HDR.read_bytes()
    ).hexdigest()[:16]
    built = _cache_dir() / f"clang-emit-{key}"
    if built.is_file() and os.access(built, os.X_OK):
        return str(built)

    # Build beside the final name and rename: two processes wrapping files at
    # the same time must not read a half-written binary.
    staging = built.with_name(f"{built.name}.{os.getpid()}.tmp")
    try:
        build_emitter(staging)
        os.replace(staging, built)
    finally:
        staging.unlink(missing_ok=True)
    return str(built)


# --------------------------------------------------------------------------- #
# running the emitter
# --------------------------------------------------------------------------- #


def _unit_from_json(data: dict[str, Any]) -> ClangUnit:
    functions = []
    for fn in data["functions"]:
        result = fn["result"]
        functions.append(ClangFunction(
            name=fn["name"],
            qualified_name=fn["qualifiedName"],
            extent_start=fn["extentStart"],
            body_start=fn["bodyStart"],
            body_end=fn["bodyEnd"],
            is_constexpr=fn["isConstexpr"],
            params=tuple(ClangParam(name=p["name"], spec=p["spec"],
                                    is_string=p["isString"])
                         for p in fn["params"]),
            result=ClangResult(is_void=result["isVoid"], is_record=result["isRecord"],
                               spec=result["spec"], is_string=result["isString"],
                               temp_type=result["tempType"]),
            returns=tuple(ClangReturn(arg_start=r["argStart"], arg_end=r["argEnd"],
                                      is_init_list=r["isInitList"])
                          for r in fn["returns"]),
        ))
    return ClangUnit(functions=tuple(functions),
                     error_count=int(data["errorCount"]),
                     errors=tuple(str(e) for e in data["errors"]))


def emit_ranges(source: bytes, *, language: str, filename: str,
                args: list[str], emitter: str | None = None) -> ClangUnit:
    """Run the range emitter over ``source`` and return what it found.

    ``language`` is ``"c"`` or ``"cpp"``; ``args`` are the clang flags the parse
    should use. Raises :class:`CorruptedSourceError` when libclang refuses to
    build a translation unit at all, and :class:`ClangEmitterError` when the
    helper itself is the problem.
    """

    argv = [emitter or emitter_path(), language, filename, *args]
    env = {**os.environ, "OUROBOROS_LIBCLANG": libclang_library()}
    try:
        proc = subprocess.run(argv, input=source, capture_output=True,
                              timeout=EMIT_TIMEOUT, env=env)
    except subprocess.TimeoutExpired as e:
        raise ClangEmitterError(
            f"the C/C++ range emitter did not finish within {EMIT_TIMEOUT}s "
            f"for {filename}"
        ) from e
    except OSError as e:
        raise ClangEmitterError(f"cannot run the C/C++ range emitter: {e}") from e
    if proc.returncode != 0:
        raise ClangEmitterError(
            f"the C/C++ range emitter exited with {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    try:
        data: dict[str, Any] = json.loads(proc.stdout)
    except ValueError as e:
        raise ClangEmitterError(
            f"the C/C++ range emitter printed something that is not JSON: {e}"
        ) from e
    if not data.get("ok"):
        raise CorruptedSourceError(language, str(data.get("error", "parse failed")),
                                   filename=filename)
    return _unit_from_json(data)


def gate_diagnostics(unit: ClangUnit, language: str, filename: str | None,
                     *, strict: bool) -> None:
    """The parse gate. For self-contained snippets (``strict``) any error means a
    malformed buffer → raise. For real tree files the parse uses clang against a
    tree that gcc built, so some diagnostics are irreducible (gcc-only atomics on
    ``_Atomic`` ptrs, ``_Float32``, other gcc extensions); the AST is still valid
    for instrumentation (each function we touch is gated on a well-formed body),
    so we record the residual instead of failing — measured, not hidden."""

    if not unit.error_count:
        return
    if strict:
        raise CorruptedSourceError(language, unit.errors[0], filename=filename)
    _log.info("tree-parse residual: %d clang/gcc diagnostic(s) in %s [%s]",
              unit.error_count, filename, "; ".join(unit.errors[:3]))


def compdb_warnings(filename: str | None, language: str) -> tuple[str, ...]:
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


# --------------------------------------------------------------------------- #
# the wrap loop both C-family backends share
# --------------------------------------------------------------------------- #


class ClangTransformer(Transformer):
    """Everything the C and C++ backends do identically.

    A subclass supplies four things: the flags a self-contained file parses
    with, the runtime helper it includes, the text it injects into one function,
    and where the include line goes. It never sees a cursor or a type — only the
    offsets and the small facts :class:`ClangFunction` carries.
    """

    #: directory holding this language's runtime helper (also handed to the
    #: parser as ``-I``, so an already-instrumented buffer re-parses)
    runtime_dir: Path
    #: file name of the runtime helper
    runtime_name: str
    #: the ``#include`` line injected into a wrapped file
    include_line: str
    #: file name assumed when the caller names none
    default_filename: str
    #: flags prepended to a *tree* file's own flags (the C++ backend has to say
    #: ``-x c++``; a compile database entry does not carry it)
    tree_arg_prefix: tuple[str, ...] = ()
    #: whether this backend implements the stackless depth-only probe
    supports_minimal: bool = False

    # ---- subclass hooks ------------------------------------------------- #

    def default_args(self) -> list[str]:
        """Parse flags for a self-contained file (no ``.ouroboros.json`` tree)."""
        raise NotImplementedError

    def instrument(self, fn: ClangFunction, *, minimal: bool) -> list[Edit[bytes]]:
        """The edits that wrap one function."""
        raise NotImplementedError

    def skip(self, fn: ClangFunction) -> bool:
        """True for a function this language must leave alone."""
        return False

    def include_anchor(self, raw: bytes, first: ClangFunction) -> int:
        """Byte offset the ``#include`` line is spliced at."""
        return 0

    # ---- shared ---------------------------------------------------------- #

    def runtime_asset(self) -> tuple[str, str]:
        return (self.runtime_name,
                (self.runtime_dir / self.runtime_name).read_text(encoding="utf-8"))

    def parse(self, raw: bytes, filename: str | None) -> ClangUnit:
        """Hand the buffer to the range emitter with the right flags, and apply
        the parse gate.

        A file under a tree with an ``.ouroboros.json`` gets that tree's exact
        flags + the tree compiler's predefs; everything else uses the
        self-contained defaults. ``-ferror-limit=0`` keeps the full diagnostic
        list so the corruption gate sees every error, not just the first. The
        parser is always pointed at our own runtime directory, so re-parsing an
        already-instrumented file (whose injected ``#include`` would otherwise be
        unresolved) succeeds instead of tripping the gate — which is what makes
        incremental instrumentation work with no filename special-casing.
        """

        fname = filename or self.default_filename
        tree = tree_flags_for(filename, self.language)
        args = ([*self.tree_arg_prefix, "-ferror-limit=0", *tree]) \
            if tree is not None else self.default_args()
        args = [*args, "-I", str(self.runtime_dir)]
        unit = emit_ranges(raw, language=self.language, filename=fname, args=args)
        gate_diagnostics(unit, self.language, filename, strict=tree is None)
        return unit

    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        if minimal and not self.supports_minimal:
            raise NotImplementedError("minimal probe mode is C-only (kernel ring sink)")
        raw = source.encode("utf-8")
        # `already_included` only decides whether to (re-)insert the include; it
        # is NOT an early exit. Per-function idempotency below leaves wrapped
        # functions alone while still adding new ones.
        already_included = self.include_line.encode() in raw

        unit = self.parse(raw, filename)

        edits: list[Edit[bytes]] = []
        wrapped = 0
        first: ClangFunction | None = None
        for fn in unit.functions:
            # Skip MACRO-GENERATED functions (BSD SPLAY_PROTOTYPE / RB_GENERATE /
            # the crash-test macros / ...): libclang reports the macro-EXPANDED
            # function with a body whose source offset points INSIDE the macro
            # invocation, so splicing probe text there splits the macro
            # ("S" + probe + "PLAY_PROTOTYPE(...)") and the file no longer
            # compiles. If the body's start byte is not a literal '{', it is not
            # real editable source — it cannot be text-instrumented.
            if raw[fn.body_start:fn.body_start + 1] != b"{":
                continue
            # FIRST real function — the include anchor. Tracked for EVERY real
            # function (before the `only`/idempotency filters below), so the
            # include lands ahead of the whole file's functions and stays correct
            # across incremental selective wraps.
            if first is None or fn.extent_start < first.extent_start:
                first = fn
            if only is not None and fn.name not in only:
                continue  # selective mode: leave non-listed functions untouched
            # Per-function idempotency: skip a function whose body already
            # carries our __ouro instrumentation, so a second call ADDS new
            # functions without re-wrapping.
            if b"__ouro" in raw[fn.body_start:fn.body_end]:
                continue
            if self.skip(fn):
                continue
            wrapped += 1
            edits.extend(self.instrument(fn, minimal=minimal))

        # Inject the include only when it is not already there AND we added at
        # least one new function.
        if wrapped and not already_included and first is not None:
            at = self.include_anchor(raw, first)
            edits.insert(0, Edit(at, at, (self.include_line + "\n").encode("utf-8")))

        new_bytes = apply_edits(raw, edits)
        return WrapResult(code=new_bytes.decode("utf-8"),
                          language=self.language, functions_wrapped=wrapped,
                          warnings=compdb_warnings(filename, self.language))
