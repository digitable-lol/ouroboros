"""The draft (``черновик``) workspace and its local git repo.

Per the README contract, ``create`` is given a *base* path and provisions a
``черновик/`` subdirectory as a git repository; ``finish`` later syncs it to a
sibling ``чистовик/``. There is deliberately **no** filesystem-watching daemon
— the MCP CRUD operations are themselves the write path.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Subdirectory names from the spec. The draft holds work-in-progress (git
#: tracked); the clean tree is the synced final output.
DRAFT_DIRNAME = "черновик"
CLEAN_DIRNAME = "чистовик"

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

    # ---- construction -----------------------------------------------------
    @classmethod
    def _paths(cls, base: str | Path) -> tuple[Path, Path, Path]:
        base_p = Path(base).expanduser().resolve()
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

        # Ship the runtime helper so instrumented code is self-contained, and
        # keep runtime artifacts out of version control.
        proj._install_runtime()
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
    def _install_runtime(self) -> None:
        from .. import runtime as runtime_mod

        src = Path(runtime_mod.__file__).read_text(encoding="utf-8")
        (self.draft / RUNTIME_FILENAME).write_text(src, encoding="utf-8")

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


def _path_env() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")
