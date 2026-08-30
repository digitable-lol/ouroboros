"""Go backend — named results plus a deferred closure.

Go has no decorator and no ``try/finally``, but it has the construct both are
standing in for: ``defer``. A deferred closure registered as the first statement
of a body runs on **every** exit path — every ``return``, a ``panic`` unwinding
through the frame, a fall-through off the end — which is what the SPEC's
"completion line on return or raise" needs.

Two consequences shape everything below:

* **Return statements are never touched.** The result values are read from the
  signature instead: the transformer gives every result a name (``__ouro_r0``…)
  and the deferred closure reads those names after the ``return`` has assigned
  them. This is what lets ``return f()`` — forwarding another call's several
  results — work with no special case at all, where the C and JavaScript
  backends have to rewrite each return site.
* **There is no import line.** The runtime helper is a file in the SAME package
  as the file being wrapped, so instrumented code calls it by plain
  package-scope names. Nothing is ever spliced above the file's header, which is
  how a ``//go:build`` constraint and a package doc comment survive untouched.
  The price is that the helper has to carry the wrapped file's package name —
  see :meth:`GoTransformer.runtime_asset_for`.

The panic path costs one thing, and it is visible: the closure has to
``recover()`` to learn the panic's type and message, then ``panic()`` again to
leave control flow alone. A panic that nothing catches therefore prints
``panic: bad [recovered, repanicked]`` where the plain program printed
``panic: bad``. Exit status, stdout and every recovered panic are unchanged; the
uncaught-panic stack trace on stderr is not, and it would differ anyway because
instrumentation shifts the line numbers it prints.

All offsets from the emitter are BYTE offsets (``go/token`` counts bytes), so
this backend splices ``bytes`` to stay correct on source with non-ASCII
identifiers or strings. The parse itself happens in another process — see
``_go/emitter.go``. Nothing below knows what a Go AST node is; it reads numbers
out of a JSON document.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .base import CorruptedSourceError, Edit, Transformer, WrapResult, apply_edits

_GO_DIR = Path(__file__).parent / "_go"
_EMITTER_SRC = _GO_DIR / "emitter.go"
_RUNTIME = _GO_DIR / "ouroboros_runtime.go"

#: File name the runtime helper is dropped under, beside the wrapped file.
RUNTIME_NAME = "ouroboros_runtime.go"

#: Proves a buffer is the runtime helper itself, so wrapping the tool's own sink
#: is a no-op. Without it the sink would log its own writes, and every
#: instrumented Go program would recurse until it died.
_RUNTIME_MARKER = "func _ouroEnter("

#: Marks an instrumented body (idempotency) and prefixes every name injected.
_MARKER = "__ouro"

#: What the wrapper passes for a parameter whose value cannot be named — an
#: unnamed parameter (``func f(int)``) or the blank identifier.
_OMITTED = "_ouroOmitted"

#: Wall-clock ceiling for one parse. go/parser is fast; the slack is for a cold
#: build cache on the first call.
EMIT_TIMEOUT = 120


class GoEmitterError(Exception):
    """The range emitter could not be built or could not be run.

    Deliberately NOT a :class:`CorruptedSourceError`: the source was never
    looked at. Reporting a toolchain problem as "your code is corrupt" is how a
    caller ends up rewriting a file that was fine.
    """


# --------------------------------------------------------------------------- #
# what the emitter says
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GoParam:
    """One declared parameter VALUE — the field ``a, b int`` yields two.

    ``usable`` is False when nothing in the program can name the value: an
    unnamed parameter or the blank identifier. Its record shows ``<...>``.
    """

    name: str
    usable: bool


@dataclass(frozen=True)
class GoName:
    """One identifier in a result list, with the bytes it occupies."""

    text: str
    start: int
    end: int


@dataclass(frozen=True)
class GoResultField:
    """One field of a result list: ``(a, b int)`` is one field with two names,
    ``(int, error)`` is two fields with none."""

    type_start: int
    type_end: int
    names: tuple[GoName, ...]


@dataclass(frozen=True)
class GoResults:
    """The whole result clause. ``parenthesized`` is False for the single bare
    type of ``func f() int``, which must gain parentheses before a name can be
    written into it."""

    start: int
    end: int
    parenthesized: bool
    fields: tuple[GoResultField, ...]


@dataclass(frozen=True)
class GoFunction:
    """One function definition with a body. Offsets are BYTE offsets."""

    name: str
    qualified_name: str
    body_start: int
    body_end: int
    params: tuple[GoParam, ...]
    results: GoResults | None


@dataclass(frozen=True)
class GoUnit:
    """The emitter's answer for one buffer. ``error_count`` is every syntax
    error the parser produced; ``errors`` carries only the first few messages,
    which is all any caller prints."""

    package: str
    functions: tuple[GoFunction, ...]
    error_count: int
    errors: tuple[str, ...]


# --------------------------------------------------------------------------- #
# building and running the emitter
# --------------------------------------------------------------------------- #


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ouroboros"


def _go_binary() -> str:
    """The ``go`` command used to build the emitter.

    Requiring one is not a new dependency: instrumented Go has to be compiled to
    be worth anything, so a host that can use this backend has a Go toolchain by
    definition.
    """

    for name in (os.environ.get("GO"), "go"):
        if name and shutil.which(name):
            return name
    raise GoEmitterError(
        "no go command found (tried $GO, go); the Go range emitter is a Go "
        "program and has to be built once per machine"
    )


def build_emitter(destination: Path) -> None:
    """Compile ``emitter.go`` to ``destination``.

    Built as a single file rather than a package, so no ``go.mod`` is needed
    anywhere: the emitter imports nothing but the standard library.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = [_go_binary(), "build", "-o", str(destination), str(_EMITTER_SRC)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300,
                              cwd=str(_GO_DIR))
    except OSError as e:
        raise GoEmitterError(f"cannot run the go command: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise GoEmitterError("building the Go range emitter timed out") from e
    if proc.returncode != 0 or not destination.exists():
        raise GoEmitterError(
            f"building the Go range emitter failed:\n"
            f"  {' '.join(argv)}\n{proc.stderr.strip()}"
        )


@lru_cache(maxsize=1)
def emitter_path() -> str:
    """Path of the built range emitter, building it on first use.

    The build is cached per machine under a name derived from the emitter's own
    source, so editing it produces a different binary instead of reusing a stale
    one. ``OUROBOROS_GO_EMITTER`` overrides everything — that is the hook for a
    packaged build that ships the binary rather than compiling here.
    """

    override = os.environ.get("OUROBOROS_GO_EMITTER")
    if override:
        return override

    key = hashlib.sha256(_EMITTER_SRC.read_bytes()).hexdigest()[:16]
    built = _cache_dir() / f"go-emit-{key}"
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


def _unit_from_json(data: dict[str, Any]) -> GoUnit:
    functions = []
    for fn in data["functions"]:
        results = None
        if fn["results"] is not None:
            r = fn["results"]
            results = GoResults(
                start=r["start"], end=r["end"], parenthesized=r["parenthesized"],
                fields=tuple(
                    GoResultField(
                        type_start=f["typeStart"], type_end=f["typeEnd"],
                        names=tuple(GoName(text=n["text"], start=n["start"], end=n["end"])
                                    for n in f["names"]),
                    )
                    for f in r["fields"]
                ),
            )
        functions.append(GoFunction(
            name=fn["name"],
            qualified_name=fn["qualifiedName"],
            body_start=fn["bodyStart"],
            body_end=fn["bodyEnd"],
            params=tuple(GoParam(name=p["name"], usable=p["usable"]) for p in fn["params"]),
            results=results,
        ))
    return GoUnit(package=data.get("package", ""), functions=tuple(functions),
                  error_count=int(data["errorCount"]),
                  errors=tuple(str(e) for e in data["errors"]))


def emit_ranges(source: bytes, *, filename: str, emitter: str | None = None) -> GoUnit:
    """Run the range emitter over ``source`` and return what it found.

    Raises :class:`GoEmitterError` when the helper itself is the problem, and
    :class:`CorruptedSourceError` when it read the source and the source is bad.
    """

    argv = [emitter or emitter_path(), filename]
    try:
        proc = subprocess.run(argv, input=source, capture_output=True,
                              timeout=EMIT_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise GoEmitterError(
            f"the Go range emitter did not finish within {EMIT_TIMEOUT}s for {filename}"
        ) from e
    except OSError as e:
        raise GoEmitterError(f"cannot run the Go range emitter: {e}") from e
    if proc.returncode != 0:
        raise GoEmitterError(
            f"the Go range emitter exited with {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    try:
        data: dict[str, Any] = json.loads(proc.stdout)
    except ValueError as e:
        raise GoEmitterError(
            f"the Go range emitter printed something that is not JSON: {e}"
        ) from e
    if not data.get("ok"):
        raise GoEmitterError(str(data.get("error", "the emitter could not read the source")))
    unit = _unit_from_json(data)
    if unit.error_count:
        raise CorruptedSourceError("go", unit.errors[0], filename=filename)
    return unit


# --------------------------------------------------------------------------- #
# reading the package clause
# --------------------------------------------------------------------------- #

_PACKAGE_CLAUSE = re.compile(r"package\s+(\w+)")
_PACKAGE_LINE = re.compile(r"^package\s+\w+$", re.MULTILINE)


def package_name(source: str) -> str | None:
    """The package a Go file declares, or ``None`` if it declares none.

    Nothing but comments and whitespace may precede the package clause — that is
    a rule of the language, not a guess — so skipping those and reading the next
    word is exact for any file that parses. Doing it here rather than asking the
    emitter keeps :meth:`GoTransformer.runtime_asset_for` free of a subprocess.
    """

    i, n = 0, len(source)
    while i < n:
        if source[i] in " \t\r\n":
            i += 1
        elif source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end < 0 else end + 1
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                return None  # unterminated comment: the file does not parse
            i = end + 2
        else:
            break
    m = _PACKAGE_CLAUSE.match(source, i)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# the backend
# --------------------------------------------------------------------------- #


class GoTransformer(Transformer):
    language = "go"
    extensions = (".go",)

    def runtime_asset(self) -> tuple[str, str]:
        """The helper as shipped, joining ``package main``.

        Callers holding the wrapped source should use :meth:`runtime_asset_for`
        instead: a helper whose package clause disagrees with the file beside it
        does not compile.
        """
        return RUNTIME_NAME, _RUNTIME.read_text(encoding="utf-8")

    def runtime_asset_for(self, source: str) -> tuple[str, str]:
        """The helper carrying ``source``'s own package name.

        Go resolves sibling files by directory, not by import: the helper lands
        next to the wrapped file and therefore joins its package, so it has to
        declare the same package name. That is also what buys the wrapped file
        its untouched header — there is no import line to splice in.
        """
        name, text = self.runtime_asset()
        package = package_name(source) or "main"
        return name, _PACKAGE_LINE.sub(f"package {package}", text, count=1)

    # ---- transform ------------------------------------------------------ #

    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        if minimal:
            raise NotImplementedError("minimal probe mode is C-only (kernel ring sink)")
        if _RUNTIME_MARKER in source:
            # The sink itself. Wrapping it would make every logged call log its
            # own logging, until the program died of it.
            return WrapResult(code=source, language=self.language, functions_wrapped=0)

        raw = source.encode("utf-8")
        unit = emit_ranges(raw, filename=filename or "input.go")

        edits: list[Edit[bytes]] = []
        wrapped = 0
        for fn in unit.functions:
            if only is not None and fn.name not in only:
                continue  # selective mode: leave non-listed functions untouched
            # Per-function idempotency: a body that already carries our probe is
            # left alone, so a second call ADDS new functions without
            # re-wrapping the ones already done.
            if _MARKER.encode() in raw[fn.body_start:fn.body_end]:
                continue
            wrapped += 1
            edits.extend(self._instrument(fn))

        new_bytes = apply_edits(raw, edits)
        return WrapResult(code=new_bytes.decode("utf-8"), language=self.language,
                          functions_wrapped=wrapped)

    # ---- one function --------------------------------------------------- #

    def _instrument(self, fn: GoFunction) -> list[Edit[bytes]]:
        result_names, edits = self._name_results(fn.results)

        args = "".join(
            ", " + (p.name if p.usable else _OMITTED) for p in fn.params
        )
        results = "".join(", " + name for name in result_names)
        name_literal = json.dumps(fn.qualified_name)
        entry = (
            f"\n\t{_MARKER}_ctx := _ouroEnter({name_literal}{args})\n"
            f"\tdefer func() {{\n"
            # recover() has to be called DIRECTLY by the deferred function, so
            # this cannot move into the helper. It runs last among the
            # function's own defers (ours is registered first), so a panic the
            # program itself recovers is already gone by the time we look — and
            # such a call is recorded as the normal return it is.
            f"\t\tif {_MARKER}_p := recover(); {_MARKER}_p != nil {{\n"
            f"\t\t\t_ouroPanicked({_MARKER}_ctx, {_MARKER}_p)\n"
            f"\t\t\tpanic({_MARKER}_p)\n"
            f"\t\t}}\n"
            f"\t\t_ouroReturned({_MARKER}_ctx{results})\n"
            f"\t}}()\n"
        )
        edits.append(Edit(fn.body_start + 1, fn.body_start + 1, entry.encode("utf-8")))
        return edits

    def _name_results(self, results: GoResults | None) -> tuple[list[str], list[Edit[bytes]]]:
        """Expressions naming each result value, and the edits that create them.

        A signature whose results are all named already is left exactly as the
        author wrote it — the body may well refer to those names, and renaming
        them would break it. Anything else (bare types, a blank ``_``) gets a
        generated name spliced in, which changes the signature's TEXT and not
        its type, so callers, interfaces and function values are unaffected.
        """

        if results is None:
            return [], []

        usable = all(
            f.names and all(n.text != "_" for n in f.names) for f in results.fields
        )
        if usable:
            return [n.text for f in results.fields for n in f.names], []

        edits: list[Edit[bytes]] = []
        if not results.parenthesized:
            # `func f() int` -> `func f() (__ouro_r0 int)`. The opening bracket
            # is listed BEFORE the name insertion at the same offset, and
            # apply_edits keeps insertions sharing an offset in list order.
            edits.append(Edit(results.start, results.start, b"("))

        names: list[str] = []
        for field in results.fields:
            if not field.names:
                generated = f"{_MARKER}_r{len(names)}"
                names.append(generated)
                edits.append(Edit(field.type_start, field.type_start,
                                  f"{generated} ".encode()))
                continue
            for declared in field.names:
                if declared.text == "_":
                    # A blank result is write-only; renaming it can break
                    # nothing, because nothing could read it before.
                    generated = f"{_MARKER}_r{len(names)}"
                    names.append(generated)
                    edits.append(Edit(declared.start, declared.end, generated.encode()))
                else:
                    names.append(declared.text)

        if not results.parenthesized:
            edits.append(Edit(results.end, results.end, b")"))
        return names, edits
