"""Java backend — ``try/catch/finally`` body instrumentation.

Java has no decorator for methods, so this backend follows the same
``try/finally`` + captured-return shape the JavaScript backend uses, and routes
the logging through the ``OuroborosRuntime.java`` helper (see SPEC.md).

All parsing is delegated to an external range emitter (``_java/Emitter.java``);
this module only splices text at the offsets it returns — the same
"core just orchestrates" split the JavaScript, Elixir and C/C++ backends have.

**The parser costs nothing to obtain.** It is ``javax.tools`` +
``com.sun.source``, the compiler that already ships inside every JDK: a machine
that can compile the instrumented Java can parse it, with no download and no
third-party library. The one price is that the emitter is a Java program and
has to be compiled once per machine, which is the same arrangement the C/C++
range emitter already has.

**No import is spliced.** The instrumented code names the helper in full
(``ouroboros.OuroborosRuntime``), so this backend never has to decide where a
header may go. The JavaScript backend needed three separate rules for that one
line — below the ``#!``, below the directive prologue, and ``import`` vs
``require`` — and each of them was a bug before it was a rule. Java lets the
question not be asked, at the price of a longer call site.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from .base import (
    CorruptedSourceError,
    Edit,
    Transformer,
    WrapResult,
    apply_edits,
)

_JAVA_DIR = Path(__file__).parent / "_java"
_EMITTER_SRC = _JAVA_DIR / "Emitter.java"
_RUNTIME_SRC = _JAVA_DIR / "OuroborosRuntime.java"

#: How the instrumented code names the helper. Also the idempotency marker: a
#: file that already calls it is already wrapped.
_RT = "ouroboros.OuroborosRuntime"
_MARKER = f"{_RT}.enter("

#: Wall-clock ceiling for one parse. The JVM start dominates it.
EMIT_TIMEOUT = 120


class JavaEmitterError(Exception):
    """The range emitter could not be built or could not be run.

    Deliberately NOT a :class:`CorruptedSourceError`: the source was never
    looked at. Reporting a missing JDK as "your code is corrupt" is how a caller
    ends up rewriting a file that was fine.
    """


def _javac() -> str:
    """The ``javac`` used to build the emitter.

    Requiring one is not a new dependency: instrumented Java has to be compiled
    to be worth anything, so a host that can use this backend has a JDK by
    definition.
    """

    for name in (os.environ.get("JAVAC"), "javac"):
        if name and shutil.which(name):
            return name
    raise JavaEmitterError(
        "no javac found (tried $JAVAC, javac); the Java range emitter is a Java "
        "program and has to be compiled once per machine"
    )


def _java() -> str:
    for name in (os.environ.get("JAVA"), "java"):
        if name and shutil.which(name):
            return name
    raise JavaEmitterError("no java found (tried $JAVA, java)")


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ouroboros"


def build_emitter(destination: Path) -> None:
    """Compile ``Emitter.java`` into the directory ``destination``."""

    destination.mkdir(parents=True, exist_ok=True)
    argv = [_javac(), "-nowarn", "-d", str(destination), str(_EMITTER_SRC)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=EMIT_TIMEOUT)
    except OSError as e:
        raise JavaEmitterError(f"cannot run javac: {e}") from e
    if proc.returncode != 0 or not (destination / "Emitter.class").is_file():
        raise JavaEmitterError(
            f"building the Java range emitter failed:\n  {' '.join(argv)}\n{proc.stderr.strip()}"
        )


@lru_cache(maxsize=1)
def emitter_classpath() -> str:
    """Directory holding the built emitter, building it on first use.

    The build is cached per machine under a name derived from the emitter's own
    source, so editing it produces a different build instead of reusing a stale
    one. ``OUROBOROS_JAVA_EMITTER`` overrides everything — the hook for a
    packaged build that ships the classes rather than compiling here.
    """

    override = os.environ.get("OUROBOROS_JAVA_EMITTER")
    if override:
        return override

    key = hashlib.sha256(_EMITTER_SRC.read_bytes()).hexdigest()[:16]
    built = _cache_dir() / f"java-emit-{key}"
    if (built / "Emitter.class").is_file():
        return str(built)

    # Build beside the final name and rename: two processes wrapping files at the
    # same time must not read a half-written class file.
    staging = built.with_name(f"{built.name}.{os.getpid()}.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        build_emitter(staging)
        os.replace(staging, built)
    except OSError as e:
        raise JavaEmitterError(f"cannot place the built emitter: {e}") from e
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return str(built)


#: What a temp of each primitive type is initialised with. A reference type takes
#: ``null``. The temp must have a value: the outermost ``finally`` reads it on the
#: path where the body threw before assigning anything, and javac rejects reading
#: a local that is not definitely assigned.
_PRIMITIVE_DEFAULT = {"boolean": "false", "byte": "0", "short": "0", "int": "0",
                      "long": "0L", "char": "0", "float": "0f", "double": "0d"}


def _default_value(fn: dict[str, Any]) -> str:
    if fn["returnIsPrimitive"]:
        return _PRIMITIVE_DEFAULT[fn["returnType"]]
    return "null"


class JavaTransformer(Transformer):
    language = "java"
    extensions = (".java",)

    def runtime_asset(self) -> tuple[str, str]:
        return "OuroborosRuntime.java", _RUNTIME_SRC.read_text(encoding="utf-8")

    # ---- emitter -------------------------------------------------------- #
    def _emit_ranges(self, source: str, filename: str | None) -> dict[str, Any]:
        argv = [_java(), "-cp", emitter_classpath(), "Emitter"]
        try:
            proc = subprocess.run(
                argv, input=source, capture_output=True, text=True, timeout=EMIT_TIMEOUT,
            )
        except OSError as e:
            raise JavaEmitterError(f"cannot run the Java range emitter: {e}") from e
        if proc.returncode != 0:
            raise JavaEmitterError(
                f"the Java range emitter crashed: {proc.stderr.strip()[:500]}"
            )
        try:
            data: dict[str, Any] = json.loads(proc.stdout)
        except ValueError as e:
            raise JavaEmitterError(
                f"the Java range emitter printed no JSON: {proc.stdout[:200]!r}"
            ) from e
        if not data.get("ok"):
            raise CorruptedSourceError("java", data.get("error", "parse failed"),
                                       filename=filename)
        return data

    # ---- transform ------------------------------------------------------ #
    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        if minimal:
            raise NotImplementedError("minimal probe mode is C-only (kernel ring sink)")

        data = self._emit_ranges(source, filename)

        if _MARKER in source:  # already instrumented
            return WrapResult(code=source, language=self.language, functions_wrapped=0)

        edits: list[Edit[str]] = []
        wrapped = 0
        for fn in data["functions"]:
            if only is not None and fn["name"] not in only:
                continue  # selective mode
            wrapped += 1
            name_lit = json.dumps(fn["qualifiedName"])
            args = ", ".join(fn["params"])
            is_void = bool(fn["isVoid"])
            temp = "" if is_void else (
                f" {fn['returnType']} __ouro_result = {_default_value(fn)};"
            )
            entry = (
                f" {_RT}.Ctx __ouro_ctx = {_RT}.enter({name_lit},"
                f" new java.lang.Object[]{{{args}}});{temp} try {{"
            )
            # `catch (Throwable e) { ...; throw e; }` with an effectively final
            # catch parameter is a *precise* rethrow (JLS 11.2.2): javac works out
            # that only what the body can actually throw escapes, so a method that
            # declares no `throws` still compiles.
            done = (f"{_RT}.exitVoid(__ouro_ctx);" if is_void
                    else f"{_RT}.exit(__ouro_ctx, __ouro_result);")
            exit_ = (
                f" }} catch (java.lang.Throwable __ouro_e) {{"
                f" {_RT}.exitThrow(__ouro_ctx, __ouro_e); throw __ouro_e;"
                f" }} finally {{ {done} }}"
            )
            edits.append(Edit(fn["bodyStart"], fn["bodyStart"], entry))
            edits.append(Edit(fn["bodyEnd"], fn["bodyEnd"], exit_))

            for ret in fn["returns"]:
                if ret["argStart"] is None:
                    # A bare `return;` leaves a void method or a constructor, and
                    # the outermost `finally` already records that completion. It
                    # needs no edit at all.
                    continue
                # Assign into a temp declared with the method's OWN return type,
                # then return the assignment — the `return (__result = expr)` shape
                # the JavaScript and C++ backends use. A generic helper
                # (`ret(ctx, expr)`) reads simpler and was tried first, but it
                # infers its type parameter from the argument instead of from the
                # method: `char f() { return 65; }` became `ret(ctx, 65)`, inferred
                # `Integer`, and stopped compiling — "inferred type does not conform
                # to upper bound(s) ... inferred: Integer, upper bound(s):
                # Character". The temp puts the value back in an assignment context
                # with the declared type, where narrowing a constant is legal again,
                # and leaves poly expressions (lambdas, `switch`, conditionals)
                # their original target type.
                edits.append(Edit(ret["keywordEnd"], ret["argStart"],
                                  " (__ouro_result = "))
                edits.append(Edit(ret["argEnd"], ret["argEnd"], ")"))

        new_code = apply_edits(source, edits)
        return WrapResult(code=new_code, language=self.language, functions_wrapped=wrapped)
