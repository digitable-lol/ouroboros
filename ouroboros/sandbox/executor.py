"""``execute`` — run an arbitrary command in the draft, funnel runtime info to
``debug.info``.

The child process inherits ``OUROBOROS_DEBUG_INFO`` pointing at the draft's
``debug.info`` so the injected ``_ouro_log`` decorator appends its structured
``in``/``out`` JSONL records there. After the process exits we also append one
JSONL ``exec`` meta record capturing the command's stdout/stderr and exit code,
so ``debug.info`` is the single place to read "what ran and why" — and stays
uniformly line-delimited JSON (the trace parser skips ``exec`` records as
non-call events rather than flagging them).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from .project import Project


@dataclass(frozen=True)
class ExecResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def execute(
    project: Project,
    command: list[str],
    *,
    timeout: float | None = None,
) -> ExecResult:
    debug_info = project.debug_info_path()
    env = {
        **os.environ,
        "OUROBOROS_DEBUG_INFO": str(debug_info),
        # Make the bundled runtime importable regardless of how the command is
        # invoked from within the draft.
        "PYTHONPATH": os.pathsep.join(
            [str(project.draft), os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep),
    }

    proc = subprocess.run(
        command,
        cwd=project.draft,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    _append_exec_section(project, command, proc)
    return ExecResult(
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _append_exec_section(
    project: Project, command: list[str], proc: subprocess.CompletedProcess[str]
) -> None:
    record = {
        "p": "exec",
        "cmd": command,
        "rc": proc.returncode,
        "out": proc.stdout,
        "err": proc.stderr,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(project.debug_info_path(), "a", encoding="utf-8") as fh:
        fh.write(line)
