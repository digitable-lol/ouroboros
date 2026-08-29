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

import shutil
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
_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".beam"}


def finish(project: Project) -> list[str]:
    """Copy draft → output tree and return the list of copied relative paths.

    The copy keeps the logging instrumentation; see the module docstring.
    """

    clean = project.clean
    if clean.exists():
        shutil.rmtree(clean)
    clean.mkdir(parents=True)

    synced: list[str] = []
    for src in sorted(project.draft.rglob("*")):
        rel = src.relative_to(project.draft)
        if _excluded(rel):
            continue
        dest = clean / rel
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            synced.append(str(rel))
    return synced


def _excluded(rel: Path) -> bool:
    if any(part in _EXCLUDE_NAMES for part in rel.parts):
        return True
    return rel.suffix in _EXCLUDE_SUFFIXES
