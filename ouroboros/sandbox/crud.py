"""CRUD operations on a draft project, with wrap-on-save and a commit per op.

Every write runs the matching language transformer *before* the bytes hit disk.
If the transformer cannot parse the buffer it raises
:class:`CorruptedSourceError`, which we let propagate without writing or
committing — that rejection is the prototype's "the filesystem refuses to
persist low-quality code" gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..languages import WrapResult, transformer_for_path
from .project import Project


@dataclass(frozen=True)
class WriteOutcome:
    rel_path: str
    language: str | None
    functions_wrapped: int
    wrapped: bool
    #: Whether the file is in the draft's git history after this write. Read back
    #: from git, never assumed: a path matched by ``.gitignore`` is skipped by
    #: ``git add -A``, so the commit still succeeds while carrying nothing.
    committed: bool


def write_file(project: Project, rel_path: str, content: str) -> WriteOutcome:
    """Wrap (if the extension is supported) and persist ``content``, then commit.

    Raises :class:`ouroboros.languages.CorruptedSourceError` if the content is
    unparseable — nothing is written in that case.
    """

    target = project.resolve_in_draft(rel_path)
    tx = transformer_for_path(rel_path)

    if tx is not None:
        result: WrapResult = tx.wrap_source(content, filename=rel_path)
        final_text = result.code
        language: str | None = result.language
        functions = result.functions_wrapped
        wrapped = True
    else:
        final_text = content
        language = None
        functions = 0
        wrapped = False

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(final_text, encoding="utf-8")

    # Ensure the language's runtime helper is present so the wrapped code can
    # run (e.g. ouroboros_runtime.js for a first .js write). Idempotent.
    #
    # NEXT TO THE FILE, not at the draft root. The wrapped source refers to the
    # helper by a path relative to ITSELF — C/C++ `#include "ouroboros_runtime.h"`
    # and the JS `import ... from "./ouroboros_runtime.js"` both resolve against
    # the including file's directory. With the helper parked at the root, a write
    # to `src/main.c` produced a file that could not compile
    # (`ouroboros_runtime.h: No such file or directory`) and a `.mjs` that could
    # not load — while write_file answered ok: true. Measured across the five
    # backends: C, C++ and JavaScript broke on a nested path, Python did not.
    # This also matches what wrap_file already does (`_drop_runtime_asset` writes
    # beside its target), so the two ways into the same tree now agree.
    if tx is not None:
        asset = tx.runtime_asset()
        if asset is not None:
            asset_name, asset_src = asset
            asset_path = target.parent / asset_name
            if not asset_path.exists():
                asset_path.write_text(asset_src, encoding="utf-8")

    # Stage everything so a freshly-created runtime helper is committed too.
    project._git("add", "-A")
    summary = f"ouroboros: write {rel_path}"
    if wrapped:
        summary += f" (+{functions} wrapped)"
    # --allow-empty keeps the "one commit per operation" invariant even when an
    # agent re-writes identical content (otherwise git exits non-zero).
    project._git("commit", "-q", "--allow-empty", "-m", summary)

    # Ask git what it ended up holding rather than asserting the happy path:
    # `add -A` skips ignored paths (debug.info), so the commit above can be empty
    # and the file untracked. `committed=True` used to be written flat here and
    # reported a version that did not exist.
    committed = project.is_tracked(target.relative_to(project.draft))

    return WriteOutcome(
        rel_path=rel_path,
        language=language,
        functions_wrapped=functions,
        wrapped=wrapped,
        committed=committed,
    )


def read_file(project: Project, rel_path: str) -> str:
    target = project.resolve_in_draft(rel_path)
    return target.read_text(encoding="utf-8")


def delete_file(project: Project, rel_path: str) -> None:
    target = project.resolve_in_draft(rel_path)
    target.unlink()
    project._git("add", "-A", "--", str(target))
    project._git("commit", "-q", "-m", f"ouroboros: delete {rel_path}")


def list_files(project: Project) -> list[str]:
    """Tracked files in the draft (relative paths), excluding git internals."""

    out = project._git("ls-files")
    return [line for line in out.splitlines() if line]
