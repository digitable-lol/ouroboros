"""C# backend — ``try/catch/finally`` body instrumentation.

C# has no decorator for methods, so this backend follows the same
``try/finally`` + captured-return shape the Java and JavaScript backends use, and
routes the logging through the ``OuroborosRuntime.cs`` helper (see SPEC.md).

All parsing is delegated to an external range emitter (``_csharp/Emitter.cs``);
this module only splices text at the offsets it returns — the same
"core just orchestrates" split every other backend has.

**The parser costs nothing to obtain.** It is Roslyn, and Roslyn already lives
inside the installed .NET SDK (``<sdk>/<version>/Roslyn/bincore``): a machine
that can compile the instrumented C# can parse it, with no download and no NuGet
package. The install directory is never written down — :func:`roslyn_bincore`
reads it out of the SDK list ``dotnet`` prints, so a different SDK version or a
different install prefix needs no edit. The one price is that the emitter is a
C# program and has to be built once per machine, the same arrangement the Java
and C/C++ range emitters have.

**No ``using`` is spliced.** The instrumented code names the helper in full
(``Ouroboros.OuroborosRuntime``), so this backend never has to decide where a
header may go — the question the JavaScript backend needed three separate rules
to answer, each of which was a bug before it was a rule.

**The type argument of ``Ret<T>`` is spelled out, never inferred.** C# cannot
infer a type parameter from ``null``, from a lambda, from a method group or from
a collection expression (CS0411), and ``return null;`` is far too common to give
up on. The emitter reports the declared return type — unwrapped from
``Task<T>``/``ValueTask<T>`` for an ``async`` method, since that is what its
``return`` actually carries — and it is written into the call site verbatim, in
the same file and the same namespace, so it resolves exactly as it did before.

**What C# does not let this wrap** (the emitter marks each with a reason and this
module skips it — the rule is that not wrapping is always better than breaking):

* **iterators** (``yield return`` / ``yield break``) — CS1626 forbids ``yield``
  inside a ``try`` that has a ``catch``, so the wrap cannot be applied at all;
* **``ref`` returns** (``ref int M()``) — ``return ref x;`` cannot be routed
  through a helper call;
* **pointers** — a pointer type is neither boxable into the ``object[]``
  snapshot nor usable as a generic type argument;
* **``ref struct`` parameters and returns** — same two reasons. The platform's
  (``Span``, ``ReadOnlySpan``, …) are known by name, and every ``ref struct``
  declared in the file being wrapped is picked up from its declaration. What
  stays invisible is a ``ref struct`` declared in a DIFFERENT file of the same
  project: this backend reads syntax and never resolves a name to a declaration,
  so a member using one is wrapped and then fails to compile (CS0029 / CS9244).
  That is the known hole in this backend, and the only one where wrapping
  produces code that does not build.

``out`` parameters are kept but left out of the entry snapshot: they are not
definitely assigned when the method is entered, so reading one there is CS0269.
``ref`` and ``in`` parameters *are* definitely assigned on entry and are logged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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

_CSHARP_DIR = Path(__file__).parent / "_csharp"
_EMITTER_SRC = _CSHARP_DIR / "Emitter.cs"
_EMITTER_PROJ = _CSHARP_DIR / "emitter.csproj"
_RUNTIME_SRC = _CSHARP_DIR / "OuroborosRuntime.cs"

#: Name of the built emitter assembly, as ``emitter.csproj`` declares it.
_ASSEMBLY = "ouro_cs_emitter.dll"

#: How the instrumented code names the helper. Also the idempotency marker: a
#: file that already calls it is already wrapped.
_RT = "Ouroboros.OuroborosRuntime"
_MARKER = f"{_RT}.Enter("

#: Wall-clock ceiling for one parse. The .NET start-up dominates it.
EMIT_TIMEOUT = 120

#: Wall-clock ceiling for the one-off build of the emitter.
BUILD_TIMEOUT = 600

#: Kept out of the build/parse environment: the first-run banner and the
#: telemetry notice are printed on stdout and would land in the JSON.
_QUIET_ENV = {
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
}


class CSharpEmitterError(Exception):
    """The range emitter could not be built or could not be run.

    Deliberately NOT a :class:`CorruptedSourceError`: the source was never looked
    at. Reporting a missing .NET SDK as "your code is corrupt" is how a caller
    ends up rewriting a file that was fine.
    """


def _dotnet() -> str:
    """The ``dotnet`` used to build and to run the emitter.

    Requiring one is not a new dependency: instrumented C# has to be compiled to
    be worth anything, so a host that can use this backend has a .NET SDK by
    definition.
    """

    for name in (os.environ.get("DOTNET"), "dotnet"):
        if name and shutil.which(name):
            return name
    raise CSharpEmitterError(
        "no dotnet found (tried $DOTNET, dotnet); the C# range emitter is a C# "
        "program and has to be built once per machine"
    )


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_QUIET_ENV)
    return env


def _version_key(version: str) -> tuple[int, ...]:
    """Sort key for an SDK version such as ``10.0.110`` or ``9.0.100-preview.3``."""

    return tuple(int(part) for part in re.findall(r"\d+", version.split("-", 1)[0]))


def installed_sdks() -> list[tuple[str, str]]:
    """``(version, root)`` for every SDK ``dotnet`` reports, newest last."""

    argv = [_dotnet(), "--list-sdks"]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=EMIT_TIMEOUT, env=_env(),
        )
    except OSError as e:
        raise CSharpEmitterError(f"cannot run `dotnet --list-sdks`: {e}") from e
    if proc.returncode != 0:
        raise CSharpEmitterError(
            f"`dotnet --list-sdks` failed: {proc.stderr.strip()[:500]}"
        )
    found: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        match = re.match(r"^(\S+)\s+\[(.+)\]\s*$", line.strip())
        if match:
            found.append((match.group(1), match.group(2)))
    found.sort(key=lambda vr: _version_key(vr[0]))
    return found


@lru_cache(maxsize=1)
def roslyn_bincore() -> str:
    """Directory holding the SDK's own Roslyn assemblies.

    Never hard-coded: it is derived from what ``dotnet`` says is installed, so
    the same tree works on a machine with another SDK version or another install
    prefix. ``OUROBOROS_ROSLYN_BINCORE`` overrides everything, for a layout this
    search does not know about.
    """

    override = os.environ.get("OUROBOROS_ROSLYN_BINCORE")
    if override:
        return override

    sdks = installed_sdks()
    tried: list[str] = []
    for version, root in reversed(sdks):
        candidate = Path(root) / version / "Roslyn" / "bincore"
        tried.append(str(candidate))
        if (candidate / "Microsoft.CodeAnalysis.CSharp.dll").is_file():
            return str(candidate)
    raise CSharpEmitterError(
        "no Roslyn found inside any installed .NET SDK; looked for "
        "Roslyn/bincore/Microsoft.CodeAnalysis.CSharp.dll under "
        + (", ".join(tried) if tried else "(dotnet reported no SDK at all)")
    )


@lru_cache(maxsize=1)
def target_framework() -> str:
    """``netN.0`` for the newest installed SDK.

    Not written into the project file: a fixed ``net10.0`` there fails outright
    on a host whose newest SDK is 9 ("The current .NET SDK does not support
    targeting .NET 10.0"), and the emitter is a build-time helper that should
    follow whatever .NET the machine actually has.
    """

    override = os.environ.get("OUROBOROS_CSHARP_TARGET_FRAMEWORK")
    if override:
        return override
    sdks = installed_sdks()
    if not sdks:
        raise CSharpEmitterError("`dotnet --list-sdks` reported no SDK to target")
    return f"net{_version_key(sdks[-1][0])[0]}.0"


def build_emitter(destination: Path) -> None:
    """Build ``Emitter.cs`` into ``destination``; the assembly lands in ``out/``."""

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_EMITTER_SRC, destination / _EMITTER_SRC.name)
    shutil.copy2(_EMITTER_PROJ, destination / _EMITTER_PROJ.name)
    argv = [
        _dotnet(), "build", str(destination / _EMITTER_PROJ.name),
        "-c", "Release", "--nologo",
        "-o", str(destination / "out"),
        f"-p:RoslynBinCore={roslyn_bincore()}",
        f"-p:OuroTargetFramework={target_framework()}",
    ]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=BUILD_TIMEOUT, env=_env(),
        )
    except OSError as e:
        raise CSharpEmitterError(f"cannot run dotnet build: {e}") from e
    if proc.returncode != 0 or not (destination / "out" / _ASSEMBLY).is_file():
        raise CSharpEmitterError(
            "building the C# range emitter failed:\n  "
            + " ".join(argv) + "\n" + (proc.stdout + proc.stderr).strip()[:2000]
        )
    # The build tree is several megabytes of intermediates that nothing reads
    # again; only `out/` is kept in the cache.
    for leftover in ("obj", "bin"):
        shutil.rmtree(destination / leftover, ignore_errors=True)


@lru_cache(maxsize=1)
def emitter_assembly() -> str:
    """Path of the built emitter assembly, building it on first use.

    The build is cached per machine under a name derived from the emitter's own
    sources, so editing either of them produces a different build instead of
    reusing a stale one. ``OUROBOROS_CSHARP_EMITTER`` overrides everything — the
    hook for a packaged build that ships the assembly rather than building here.
    """

    override = os.environ.get("OUROBOROS_CSHARP_EMITTER")
    if override:
        return override

    digest = hashlib.sha256()
    digest.update(_EMITTER_SRC.read_bytes())
    digest.update(_EMITTER_PROJ.read_bytes())
    # The framework is part of the identity: the same sources built against a
    # different .NET produce a different assembly, and reusing one for the other
    # is how a cache serves a build that cannot run.
    digest.update(target_framework().encode())
    key = digest.hexdigest()[:16]
    built = _cache_dir() / f"csharp-emit-{key}"
    if (built / "out" / _ASSEMBLY).is_file():
        return str(built / "out" / _ASSEMBLY)

    # Build beside the final name and rename: two processes wrapping files at the
    # same time must not read a half-written assembly.
    staging = built.with_name(f"{built.name}.{os.getpid()}.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        build_emitter(staging)
        os.replace(staging, built)
    except OSError as e:
        raise CSharpEmitterError(f"cannot place the built emitter: {e}") from e
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return str(built / "out" / _ASSEMBLY)


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ouroboros"


class CSharpTransformer(Transformer):
    language = "csharp"
    extensions = (".cs",)

    def runtime_asset(self) -> tuple[str, str]:
        return "OuroborosRuntime.cs", _RUNTIME_SRC.read_text(encoding="utf-8")

    # ---- emitter -------------------------------------------------------- #
    def _emit_ranges(self, source: str, filename: str | None) -> dict[str, Any]:
        argv = [_dotnet(), emitter_assembly()]
        try:
            proc = subprocess.run(
                argv, input=source.encode("utf-8"), capture_output=True,
                timeout=EMIT_TIMEOUT, env=_env(),
            )
        except OSError as e:
            raise CSharpEmitterError(f"cannot run the C# range emitter: {e}") from e
        if proc.returncode != 0:
            complaint = proc.stderr.decode("utf-8", errors="replace").strip()
            raise CSharpEmitterError(f"the C# range emitter crashed: {complaint[:500]}")
        text = proc.stdout.decode("utf-8", errors="replace")
        try:
            data: dict[str, Any] = json.loads(text)
        except ValueError as e:
            raise CSharpEmitterError(
                f"the C# range emitter printed no JSON: {text[:200]!r}"
            ) from e
        if not data.get("ok"):
            raise CorruptedSourceError("csharp", data.get("error", "parse failed"),
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
        warnings: list[str] = []
        wrapped = 0
        for fn in data["functions"]:
            if only is not None and fn["name"] not in only:
                continue  # selective mode
            if fn["skip"]:
                warnings.append(f"{fn['qualifiedName']}: left alone ({fn['skip']})")
                continue
            wrapped += 1
            name_lit = json.dumps(fn["qualifiedName"])
            args = ", ".join(fn["params"])
            entry = (
                f" {_RT}.Ctx __ouro_ctx = {_RT}.Enter({name_lit},"
                f" new object[]{{{args}}}); try {{"
            )
            # A bare `throw;` rather than `throw __ouro_e;`: rethrowing the caught
            # variable resets the exception's stack trace to this line, and the
            # observed program would print a different trace purely because it is
            # being observed.
            exit_ = (
                f" }} catch (System.Exception __ouro_e) {{"
                f" {_RT}.ExitThrow(__ouro_ctx, __ouro_e); throw;"
                f" }} finally {{ {_RT}.ExitPending(__ouro_ctx); }}"
            )
            capture = (f" {_RT}.Ret<{fn['retType']}>(__ouro_ctx, " if fn["retType"]
                       else f" {_RT}.Ret(__ouro_ctx, ")

            if fn["bodyStart"] >= 0:
                edits.append(Edit(fn["bodyStart"], fn["bodyStart"], entry))
                edits.append(Edit(fn["bodyEnd"], fn["bodyEnd"], exit_))
            else:
                # An expression body has no block to splice into, so `=> e;` is
                # expanded into `{ … }`. Expanding beats skipping: the expression
                # itself is never reprinted (both edits stop at its edges), so the
                # member keeps its exact text, and a one-line member is precisely
                # the kind a caller wants timed.
                if fn["exprIsThrow"]:
                    head, tail = "{" + entry + " ", ";" + exit_ + " }"
                elif fn["isVoid"]:
                    head = "{" + entry + " "
                    tail = f"; {_RT}.RetVoid(__ouro_ctx);" + exit_ + " }"
                else:
                    head = "{" + entry + " return" + capture
                    tail = ");" + exit_ + " }"
                edits.append(Edit(fn["arrowStart"], fn["exprStart"], head))
                edits.append(Edit(fn["exprEnd"], fn["tailEnd"], tail))

            for ret in fn["returns"]:
                if ret["argStart"] is not None:
                    # Replace the gap between `return` and its argument. No
                    # parentheses are added around the argument: it is already
                    # delimited by the helper call's own `)`.
                    edits.append(Edit(ret["keywordEnd"], ret["argStart"], capture))
                    edits.append(Edit(ret["argEnd"], ret["argEnd"], ")"))
                else:
                    # A bare `return;` cannot carry a call — `return f();` is
                    # illegal when f is void — so the note is made in a statement
                    # before it, and the pair is braced because the `return` may be
                    # the unbraced body of an `if`.
                    edits.append(Edit(ret["start"], ret["start"],
                                      f"{{ {_RT}.RetVoid(__ouro_ctx); "))
                    edits.append(Edit(ret["stmtEnd"], ret["stmtEnd"], " }"))

        new_code = apply_edits(source, edits)
        return WrapResult(code=new_code, language=self.language,
                          functions_wrapped=wrapped, warnings=tuple(warnings))
