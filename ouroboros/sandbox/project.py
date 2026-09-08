"""The draft workspace and its local git repo.

Per the README contract, ``create`` is given a *base* path and provisions a
``draft/`` subdirectory as a git repository; ``finish`` later syncs it to a
sibling ``clean/``. There is deliberately **no** filesystem-watching daemon
— the MCP CRUD operations are themselves the write path.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Subdirectory names from the spec. The draft holds work-in-progress (git
#: tracked); the clean tree is the synced final output.
DRAFT_DIRNAME = "draft"
CLEAN_DIRNAME = "clean"

#: What those two directories were called before this change: ``черновик`` and
#: ``чистовик``, the Russian for draft and clean.
#:
#: They are still recognised, because a project made by an earlier build is on
#: somebody's disk right now with its change history inside it. Had the constants
#: merely been rewritten, ``create`` would have made an empty ``draft/`` beside
#: that work and reported success, while the earlier draft stayed there — intact,
#: unreferenced and unmentioned. Losing sight of the work is the same failure as
#: losing the work. So a base that has the old draft and not the new one is used
#: exactly as it is; and where both are present, nothing is guessed silently —
#: the answer names the other directory and says what to do with it. See
#: :func:`legacy_notice`.
LEGACY_DRAFT_DIRNAME = "черновик"
LEGACY_CLEAN_DIRNAME = "чистовик"

#: Runtime artifacts that live in the draft but are neither committed nor synced.
DEBUG_INFO_NAME = "debug.info"
RUNTIME_FILENAME = "ouroboros_runtime.py"

_GIT_ENV = {
    # Deterministic, headless-safe identity so commits never prompt or fail.
    "GIT_AUTHOR_NAME": "ouroboros",
    "GIT_AUTHOR_EMAIL": "ouroboros@localhost",
    "GIT_COMMITTER_NAME": "ouroboros",
    "GIT_COMMITTER_EMAIL": "ouroboros@localhost",
}


class SandboxError(Exception):
    """Raised for sandbox-level failures (bad paths, git errors)."""


@dataclass(frozen=True)
class Project:
    base: Path
    draft: Path
    clean: Path

    @property
    def uses_legacy_layout(self) -> bool:
        """True when this project's directories carry the pre-rename names."""

        return self.draft.name == LEGACY_DRAFT_DIRNAME

    # ---- construction -----------------------------------------------------
    @classmethod
    def _paths(cls, base: str | Path) -> tuple[Path, Path, Path]:
        """base, draft and clean — under the old names when those are what is there.

        The old layout is adopted only when it is the *only* one present. A base
        holding both directories is a half-finished move, and choosing one of
        them without a word is exactly the silence this function exists to
        prevent: the current names win, and :func:`legacy_notice` says the other
        directory is there.
        """

        base_p = Path(base).expanduser().resolve()
        legacy = base_p / LEGACY_DRAFT_DIRNAME
        if legacy.is_dir() and not (base_p / DRAFT_DIRNAME).exists():
            return base_p, legacy, base_p / LEGACY_CLEAN_DIRNAME
        return base_p, base_p / DRAFT_DIRNAME, base_p / CLEAN_DIRNAME

    @classmethod
    def create(cls, base: str | Path, *, exist_ok: bool = False) -> Project:
        base_p, draft, clean = cls._paths(base)
        if draft.exists():
            if not exist_ok:
                raise SandboxError(f"draft already exists: {draft}")
            return cls.open(base_p)

        draft.mkdir(parents=True)
        proj = cls(base=base_p, draft=draft, clean=clean)
        proj._git("init", "-q")
        proj._git("config", "user.name", "ouroboros")
        proj._git("config", "user.email", "ouroboros@localhost")

        # No runtime helper is dropped here. `create` does not yet know the
        # project's language — it is given a path and nothing else — so the only
        # helper it could install unconditionally is Python's, and that is what
        # it used to do: every C project got a stray ouroboros_runtime.py, in the
        # draft and then in the output tree. `write_file` installs the RIGHT
        # helper for the language on the first write (see sandbox/crud.py), and
        # `wrap_file` installs one next to the file it wraps, so nothing needs a
        # helper before it exists. Measured: with this removed, first-write plus
        # execute works for all six backends, and the C output tree loses only
        # the stray Python file.
        (draft / ".gitignore").write_text(f"{DEBUG_INFO_NAME}\n", encoding="utf-8")
        proj._git("add", "-A")
        proj._git("commit", "-q", "-m", "ouroboros: init draft")
        return proj

    @classmethod
    def open(cls, base: str | Path) -> Project:
        base_p, draft, clean = cls._paths(base)
        if not (draft / ".git").exists():
            raise SandboxError(f"no draft git repo at {draft}")
        return cls(base=base_p, draft=draft, clean=clean)

    # ---- helpers ----------------------------------------------------------
    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.draft,
            env={**_GIT_ENV, "PATH": _path_env()},
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SandboxError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    def debug_info_path(self) -> Path:
        return self.draft / DEBUG_INFO_NAME

    def resolve_in_draft(self, rel_path: str | Path) -> Path:
        """Resolve ``rel_path`` inside the draft, rejecting escapes (``..``)."""

        target = (self.draft / rel_path).resolve()
        if target != self.draft and self.draft not in target.parents:
            raise SandboxError(f"path escapes the draft sandbox: {rel_path}")
        return target

    def is_tracked(self, rel_path: str | Path) -> bool:
        """True if ``rel_path`` is in the draft's git index.

        Lets a write REPORT what git actually did instead of assuming it: ``git
        add -A`` silently skips paths matched by ``.gitignore`` (``debug.info``),
        so the commit that follows can succeed while carrying nothing. The
        ``:(literal)`` prefix stops a filename containing ``*`` or ``?`` from
        being read as a match pattern.
        """

        out = self._git("ls-files", "-z", "--", f":(literal){rel_path}")
        return bool(out.strip("\x00"))

    def git_log(self) -> list[str]:
        out = self._git("log", "--format=%s")
        return [line for line in out.splitlines() if line]


def legacy_notice(project: Project) -> str | None:
    """What to say about the pre-rename directory names, or ``None`` if nothing.

    Two situations, and each gets a sentence rather than a shrug:

    * the project *is* the old layout — it was adopted, and the person is told
      so plus how to move it over;
    * the project is the current layout and an old draft is lying beside it —
      the tool is not touching that draft, so it names it and says what to do.

    Everything else returns ``None``: there is nothing to warn about, and a
    warning printed when nothing is wrong is the fastest way to teach people to
    stop reading warnings.
    """

    if project.uses_legacy_layout:
        return (
            f"This project still uses the previous directory names — "
            f"{LEGACY_DRAFT_DIRNAME}/ and {LEGACY_CLEAN_DIRNAME}/ — so it is "
            f"being used exactly as it is, change history and all. Nothing was "
            f"created beside it. To move to the current names, rename the "
            f"directories yourself: `mv {LEGACY_DRAFT_DIRNAME} {DRAFT_DIRNAME}` "
            f"(and `mv {LEGACY_CLEAN_DIRNAME} {CLEAN_DIRNAME}` if that one "
            f"exists); the git history lives inside the directory and travels "
            f"with it."
        )
    if (project.base / LEGACY_DRAFT_DIRNAME).is_dir():
        return (
            f"There is also a {LEGACY_DRAFT_DIRNAME}/ directory beside this "
            f"project — a draft from a build that used the previous names, with "
            f"its own change history. This project reads and writes "
            f"{project.draft.name}/, and {LEGACY_DRAFT_DIRNAME}/ is left "
            f"untouched: copy across whatever you still need, then delete it."
        )
    return None


def _path_env() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")
