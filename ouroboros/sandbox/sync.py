"""``finish`` — copy the draft (``черновик``) into the output tree (``чистовик``).

**What this does NOT do: it does not remove the instrumentation.** The copy in
``чистовик`` is instrumented exactly like the draft, and that is deliberate.
There is no un-instrumented text anywhere to restore:
:func:`ouroboros.sandbox.crud.write_file` wraps the buffer *before* the bytes
reach disk, so neither the draft nor its git history has ever held the author's
original. Taking the wrapping back off would mean a second, inverse
transformation per language that no backend has and whose result could not be
checked against anything — a wrong inverse quietly damages the user's code,
which is worse than leaving working logging in place. So the operation is
honestly a copy, and the tool that exposes it says so (see ``tool_finish``).

What the copy leaves behind is what a tool regenerates rather than what a person
wrote: git internals, the runtime trace, and compiler/interpreter output
(``__pycache__``, ``*.pyc``, ``*.beam``, tool caches). Everything else — source,
plus the runtime helpers the instrumented code imports — is mirrored as-is.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .project import DEBUG_INFO_NAME, Project

#: Directory/file names never carried into the output tree: git internals, the
#: runtime trace, and caches a tool rebuilds on demand.
_EXCLUDE_NAMES = {
    ".git",
    DEBUG_INFO_NAME,
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

#: Compiled output of the languages the sandbox runs: Python bytecode and BEAM
#: modules are produced by ``execute``, not authored, so they are not source.
#: The rest are the other build products measured coming out of the sandbox's
#: five backends: object files and libraries, Java classes, and the text
#: intermediates ``gcc -save-temps`` leaves behind (``.i``, ``.ii``, ``.s`` —
#: text, so no content check would ever catch them).
_EXCLUDE_SUFFIXES = {
    ".pyc", ".pyo", ".beam",
    ".o", ".obj", ".a", ".lib", ".so", ".dll", ".dylib", ".node",
    ".class", ".jar",
    ".pdb", ".gch", ".pch", ".bc", ".wasm",
    ".i", ".ii", ".s",
}

#: A crash dump: half a megabyte of the failed process's memory, named by pid.
#: Not source by any reading, and a data-protection problem on its own.
_CORE_DUMP = re.compile(r"\Acore\.\d+\Z")

#: Extensions that are source or plain text, and are therefore NEVER judged by
#: content. This is the guard that keeps the content check from eating the
#: author's own work: a .c file containing a real NUL byte, a note saved as
#: UTF-16, a text file in some older encoding. Measured on this repository's own
#: 246 source files: zero of them are dropped.
_TEXTUAL_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".ex", ".exs",
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".csv", ".tsv", ".xml", ".html", ".css", ".sh", ".sql", ".rst", ".map",
}

#: Leading bytes of the compiled formats the sandbox was measured producing:
#: ELF (gcc/clang here), ``ar`` archives, Mach-O and COFF (this box's clang
#: cross-compiles to both without extra setup), zip containers (``.jar``),
#: WebAssembly, BEAM, and Java class files.
_BUILD_MAGIC: tuple[bytes, ...] = (
    b"\x7fELF", b"!<arch>", b"PK\x03\x04", b"\x00asm", b"FOR1", b"\xca\xfe\xba\xbe",
    b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
    b"\x4c\x01", b"\x64\x86",
)

#: How much of a file is read to judge it. Every compiled file measured here has
#: a NUL inside its first 16 bytes (ELF's is at offset 7), so this is generous.
_SNIFF_BYTES = 8192


@dataclass(frozen=True)
class FinishResult:
    """What the copy carried over, and what it left behind and why.

    ``skipped`` exists because dropping a file silently is the same failure this
    module was just fixed for elsewhere: reporting a success that is not the
    whole truth. The rule below cannot tell a compiler's output from a picture
    the program was asked to produce — both are made by a program, both are
    reproducible by running it again — so the honest move is to drop the ones
    that look built and SAY which, letting the author see a name they wanted.
    """

    synced: list[str]
    skipped: list[tuple[str, str]]


def finish(project: Project) -> FinishResult:
    """Copy draft → output tree; report what was copied and what was left.

    The copy keeps the logging instrumentation; see the module docstring.
    """

    clean = project.clean
    if clean.exists():
        shutil.rmtree(clean)
    clean.mkdir(parents=True)

    synced: list[str] = []
    skipped: list[tuple[str, str]] = []
    for src in sorted(project.draft.rglob("*")):
        rel = src.relative_to(project.draft)
        verdict = _exclusion_reason(rel, src)
        if verdict is not None:
            reason, worth_saying = verdict
            if src.is_file() and worth_saying:
                skipped.append((str(rel), reason))
            continue
        dest = clean / rel
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            synced.append(str(rel))
    return FinishResult(synced=synced, skipped=skipped)


def _looks_built(path: Path) -> bool:
    """True if the file's first bytes say a tool produced it.

    Two signals, both cheap: a known compiled-format signature, or a NUL byte —
    which no text file in this repository's 246 sources has anywhere, and which
    every compiled file measured here has within its first 16 bytes.

    Only ever consulted for a file whose extension is not textual, so a source
    file that happens to contain a NUL is never judged by this.
    """

    try:
        with path.open("rb") as fh:
            head = fh.read(_SNIFF_BYTES)
    except OSError:
        # Unreadable is not "built": leave the decision to the name-based rules
        # and let the copy itself fail loudly if it is going to.
        return False
    if head.startswith(_BUILD_MAGIC):
        return True
    return b"\x00" in head


def _exclusion_reason(rel: Path, src: Path) -> tuple[str, bool] | None:
    """Why this path stays in the draft, or None to carry it over.

    The second element says whether the author needs to hear about it. It is
    False for the routine drops — ``.git``, the trace, tool caches — because
    nobody wants those in the output tree and listing them would bury the one
    line that matters: a single ``.git`` costs about thirty entries, which is
    how a useful report turns into a wall nobody reads.

    It is True wherever the rule made a JUDGEMENT that could be wrong — a file
    that merely looks built. That is the case where the author may disagree, so
    that is the case they get told about.
    """

    for part in rel.parts:
        if part in _EXCLUDE_NAMES:
            return (f"{part}: git internals, the trace, or a cache a tool rebuilds",
                    False)
        if _CORE_DUMP.match(part):
            return (f"{part}: a crash dump of the process's memory", True)
    if rel.suffix in _EXCLUDE_SUFFIXES:
        return (f"{rel.suffix}: build output, remade by rebuilding", True)
    if src.is_file() and rel.suffix not in _TEXTUAL_SUFFIXES and _looks_built(src):
        return ("looks built (compiled-format signature, or a NUL byte in the "
                "first 8 KiB) and has no source extension", True)
    return None
