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
    if tx is not None:
        asset = tx.runtime_asset()
        if asset is not None:
            asset_name, asset_src = asset
            asset_path = project.draft / asset_name
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

    return WriteOutcome(
        rel_path=rel_path,
        language=language,
        functions_wrapped=functions,
        wrapped=wrapped,
        committed=True,
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
