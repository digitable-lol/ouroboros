"""``finish`` — sync the draft (``черновик``) to the clean tree (``чистовик``).

The clean tree is rebuilt as a faithful mirror of the draft's *source*, minus
git internals and runtime artifacts (``.git``, ``debug.info``). The bundled
``ouroboros_runtime.py`` IS copied, since the instrumented code imports it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .project import DEBUG_INFO_NAME, Project

#: Directory/file names never carried into the clean tree.
_EXCLUDE = {".git", DEBUG_INFO_NAME}


def finish(project: Project) -> list[str]:
    """Mirror draft → clean and return the list of synced relative paths."""

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
    return any(part in _EXCLUDE for part in rel.parts)
