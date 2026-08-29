"""``lint_file`` — run clang-tidy over a C/C++ file and report real defects.

This is the static-analysis layer ABOVE the corruption gate: the gate only
catches code clang can't parse; clang-tidy catches code that parses fine but is
wrong — use-after-free, ``if (a = b)``, dead stores, performance traps. Same
``compile_commands.json`` discovery as the instrumenter (see :mod:`.flags`).

It also filters the ONE class of self-inflicted noise the instrumentation
creates: our injected ``__ouro`` / ``_ouro_*`` identifiers begin with an
underscore, which clang-tidy flags as ``bugprone-reserved-identifier``. We drop
exactly those (by check + our naming convention) so the report is the user's
code, never our wrapper — and we count what we dropped so the filtering is
visible, not hidden.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .flags import compile_flags_for, find_tool, language_for

# Default check groups: the high-signal analyses that find real bugs without the
# stylistic churn of the full `bugprone-*,readability-*` set. Callers can pass
# their own `checks` string (clang-tidy syntax) to widen or narrow this.
_DEFAULT_CHECKS = "bugprone-*,clang-analyzer-*,performance-*,clang-diagnostic-*"

# clang-tidy binary names, newest first — find_tool takes the first on PATH, so a
# hand-installed bare `clang-tidy` wins over a distro `clang-tidy-NN`. Named so
# the test suite can probe with THIS list instead of a copy that drifts from it.
_CLANG_TIDY_NAMES = ("clang-tidy", "clang-tidy-22", "clang-tidy-21", "clang-tidy-20",
                     "clang-tidy-19", "clang-tidy-18", "clang-tidy-17")

# clang-tidy text diagnostic: `path:line:col: severity: message [check.name]`.
_DIAG_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<severity>warning|error):\s+(?P<message>.*?)"
    r"(?:\s+\[(?P<check>[\w.-]+(?:,[\w.-]+)*)\])?$"
)

# A diagnostic our OWN instrumentation provably injects: a reserved-identifier
# complaint about an Ouroboros-named symbol (`__ouro`, `__ouro_result`,
# `_ouro_enter`, ...). Matching on BOTH the check and our naming convention keeps
# a user's genuine reserved-id elsewhere reportable.
_OURO_IDENT_RE = re.compile(r"'_+ouro\w*'")


def _is_instrumentation_noise(check: str, message: str) -> bool:
    return "reserved-identifier" in check and bool(_OURO_IDENT_RE.search(message))


def _same_file(reported: str, target: Path) -> bool:
    """Whether a diagnostic's path names ``target``. Compared after ``resolve`` so
    a relative path, a ``./`` prefix or a symlinked directory still matches; an
    unresolvable path counts as elsewhere rather than raising."""
    try:
        return Path(reported).resolve() == target
    except OSError:  # pragma: no cover - needs a filesystem that fails on resolve
        return False


def parse_diagnostics(output: str, target: Path) -> tuple[list[dict[str, Any]], int, int]:
    """clang-tidy's text output -> ``(diagnostics, filtered_noise, out_of_file)``.

    Kept apart from running the tool because this is where all the judgement
    lives, and none of it needs a subprocess: which lines are diagnostics at all,
    which belong to the linted file, which are our own instrumentation talking,
    and which are duplicates of a row already seen.

    Only diagnostics IN the linted file are reported. clang-tidy's own checks
    already honour that (an empty HeaderFilterRegex means main file only), but the
    static analyzer (``clang-analyzer-*``) follows calls into headers and flags
    code there — including OUR runtime header (``_ouro_enter``'s snprintf and so
    on). Those are not the user's file's problems, and which checks fire there
    shifts between clang-tidy versions, so we scope by location: the cut that
    survives a version bump, instead of chasing each check name.
    """
    diagnostics: list[dict[str, Any]] = []
    filtered = 0       # our own injected `__ouro` reserved-identifier noise
    out_of_file = 0    # diagnostics located in an #included header, not this file
    seen: set[tuple[str, int, int, str]] = set()
    for line in output.splitlines():
        m = _DIAG_RE.match(line.strip())
        if not m:
            continue
        if not _same_file(m.group("file"), target):
            out_of_file += 1
            continue
        check = m.group("check") or ""
        message = m.group("message")
        if _is_instrumentation_noise(check, message):
            filtered += 1
            continue
        key = (m.group("file"), int(m.group("line")), int(m.group("col")), check)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append({
            "file": m.group("file"),
            "line": int(m.group("line")),
            "col": int(m.group("col")),
            "severity": m.group("severity"),
            "check": check,
            "message": message,
        })
    return diagnostics, filtered, out_of_file


def summarise(diagnostics: list[dict[str, Any]]) -> dict[str, int]:
    """Diagnostic rows -> a count per severity."""
    return {"error": sum(d["severity"] == "error" for d in diagnostics),
            "warning": sum(d["severity"] == "warning" for d in diagnostics)}


def lint_file(path: str, checks: str | None = None,
              timeout: float | None = 120.0) -> dict[str, Any]:
    """Run clang-tidy over the C/C++ file at ``path`` and return its diagnostics.

    Returns ``{ok, path, language, tool, checks, diagnostics, counts,
    filtered_instrumentation_noise}``. ``diagnostics`` is a list of
    ``{file, line, col, severity, check, message}``; ``counts`` aggregates by
    severity. ``filtered_instrumentation_noise`` is how many of OUR injected
    ``__ouro`` reserved-identifier diagnostics were dropped (transparency).
    """
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": False, "error": f"cannot read {path}: no such file"}
    language = language_for(str(p))
    if language is None:
        return {"ok": False,
                "error": f"lint supports only C/C++ files; {path} is neither"}
    tool = find_tool(*_CLANG_TIDY_NAMES)
    if tool is None:
        return {"ok": False, "error": "clang-tidy not found on PATH"}

    check_arg = checks or _DEFAULT_CHECKS
    cmd = [tool, str(p), f"--checks={check_arg}", "--quiet",
           "--", *compile_flags_for(str(p), language)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"clang-tidy timed out after {timeout}s"}
    except OSError as e:
        return {"ok": False, "error": f"clang-tidy failed to run: {e}"}

    # clang-tidy writes diagnostics to stderr; stdout carries them too on some
    # builds. Scan both — parse_diagnostics dedups identical rows.
    diagnostics, filtered, out_of_file = parse_diagnostics(
        proc.stderr + "\n" + proc.stdout, p.resolve())
    return {
        "ok": True,
        "path": str(p),
        "language": language,
        "tool": tool,
        "checks": check_arg,
        "diagnostics": diagnostics,
        "counts": summarise(diagnostics),
        "filtered_instrumentation_noise": filtered,
        "filtered_out_of_file": out_of_file,
    }
