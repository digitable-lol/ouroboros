#!/usr/bin/env python3
"""Benchmark harness: build the same project with and without Ouroboros.

Two arms, identical task / model / tooling except the one variable under test:

  * baseline  — Bash/Read/Write/Edit only.
  * ouroboros — the SAME tools PLUS the Ouroboros MCP server, and the tool's
                real docs (SKILL.md) appended to the system prompt.

Each run is a headless `claude -p ... --output-format json` invocation, metered
for tokens / cost / duration / turns. Success is judged FIRST (tokens are only
comparable across equal outcomes): the agent's report.py must reproduce the
sample output AND a held-out hidden log (the latter catches hardcoding).

Runs are interleaved (baseline, ouroboros, baseline, ...) to balance prompt-cache
warmth across arms. Everything is written under bench/runs/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).parent
PROJECT = BENCH.parent
TASK = BENCH / "task"
FIXTURES = BENCH / "fixtures"
RUNS = BENCH / "runs"
RUNTIME_PY = PROJECT / "ouroboros" / "runtime.py"
SKILL = (PROJECT / "SKILL.md").read_text(encoding="utf-8")

TASK_PROMPT = """\
You are working in the current directory. It contains:
- spec.md — the task specification; read it first
- sample.log — input data
- sample_expected.csv — the expected output for sample.log

Implement the task described in spec.md: create report.py so that running
`python3 report.py sample.log` prints byte-for-byte what is in sample_expected.csv.
Run it and iterate until the output matches exactly. The final report.py must be
runnable as `python3 report.py <logfile>` for an arbitrary log file of the same
format. Do the real transformation — do not hardcode the expected output.
"""

# The `ouroboros wrap-file` CLI instruments a Python file in place; the runtime
# helper (ouroboros_runtime.py) is pre-seeded in the workdir (in real use the
# tool's create step provides it). Arm B uses the CLI directly so the comparison
# measures the instrument→observe workflow, not MCP deferred-tool plumbing.
OURO = f"uv run --directory {PROJECT} ouroboros"
OUROBOROS_SYSTEM = (
    "You have an `ouroboros` command-line tool that instruments Python code with "
    "function-level logging. The file `ouroboros_runtime.py` is already present in "
    "your working directory (the instrumented code imports it).\n\n"
    "Intended workflow — prefer it while you develop and debug:\n"
    f"  1. Write your report.py normally.\n"
    f"  2. Instrument it in place:  {OURO} wrap-file report.py\n"
    "     (This wraps every function so each call logs its name, arguments and "
    "return value. It is idempotent — safe to re-run after edits. It prints "
    "nothing to stdout; logs go to a file named debug.info.)\n"
    "  3. Run your program normally (e.g. `python3 report.py sample.log`). Logging "
    "is written to ./debug.info, NOT stdout, so your CSV output stays clean.\n"
    "  4. Read debug.info to see each function's inputs and outputs at runtime — "
    "use it to localize where a wrong value comes from while you iterate.\n\n"
    "Each debug.info record looks like:\n"
    "  ШАБЛОН_НАЧАЛО:\n"
    "      <time>: <uuid>: <function name>\n"
    "      args: (...) kwargs: {...}\n"
    "      result: <return value>\n"
    "  ШАБЛОН_КОНЕЦ\n\n"
    "Usage guide (SKILL.md):\n\n" + SKILL
)

BASE_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]


def build_cmd(arm: str, model: str, workdir: Path) -> list[str]:
    cmd = [
        "claude", "-p", TASK_PROMPT,
        "--output-format", "json",
        "--model", model,
        "--add-dir", str(workdir),
    ]
    if arm == "ouroboros":
        cmd += ["--append-system-prompt", OUROBOROS_SYSTEM]
    cmd += ["--allowedTools", *BASE_TOOLS]
    return cmd


def setup_workdir(workdir: Path, arm: str) -> None:
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    for name in ("spec.md", "sample.log", "sample_expected.csv"):
        shutil.copy2(TASK / name, workdir / name)
    if arm == "ouroboros":
        # Pre-seed the runtime helper the instrumented code imports.
        shutil.copy2(RUNTIME_PY, workdir / "ouroboros_runtime.py")


def find_report(workdir: Path) -> Path | None:
    direct = workdir / "report.py"
    if direct.is_file():
        return direct
    candidates = [p for p in workdir.rglob("report.py") if p.is_file()]
    if not candidates:
        return None

    def rank(p: Path) -> tuple:
        parts = p.parts
        return (0 if "чистовик" in parts else 1 if "черновик" in parts else 2, len(parts))

    return sorted(candidates, key=rank)[0]


def run_report(report: Path, logfile: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, report.name, str(logfile.resolve())],
        cwd=str(report.parent),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout


def judge(workdir: Path) -> dict:
    report = find_report(workdir)
    result = {
        "report_found": report is not None,
        "report_path": str(report.relative_to(workdir)) if report else None,
        "sample_ok": False,
        "hidden_ok": False,
    }
    if report is None:
        return result
    sample_expected = (TASK / "sample_expected.csv").read_text(encoding="utf-8")
    hidden_expected = (FIXTURES / "hidden_expected.csv").read_text(encoding="utf-8")
    try:
        _, out_sample = run_report(report, TASK / "sample.log")
        result["sample_ok"] = out_sample == sample_expected
        _, out_hidden = run_report(report, FIXTURES / "hidden.log")
        result["hidden_ok"] = out_hidden == hidden_expected
    except Exception as e:  # noqa: BLE001
        result["judge_error"] = str(e)
    return result


def detect_tool_use(workdir: Path) -> bool:
    """True iff the agent actually engaged the instrumentation: a debug.info
    with real records, or an instrumented report.py."""
    debug = workdir / "debug.info"
    if debug.is_file() and "ШАБЛОН_НАЧАЛО" in debug.read_text(encoding="utf-8", errors="replace"):
        return True
    report = find_report(workdir)
    if report and "_ouro_log" in report.read_text(encoding="utf-8", errors="replace"):
        return True
    return False


def debug_info_chars(workdir: Path) -> int:
    """Size of generated debug.info (proxy for the read burden the tool adds)."""
    debug = workdir / "debug.info"
    return len(debug.read_text(encoding="utf-8", errors="replace")) if debug.is_file() else 0


def extract_metrics(result_json: dict) -> dict:
    usage = result_json.get("usage", {}) or {}
    return {
        "subtype": result_json.get("subtype"),
        "is_error": result_json.get("is_error"),
        "num_turns": result_json.get("num_turns"),
        "duration_ms": result_json.get("duration_ms"),
        "total_cost_usd": result_json.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
    }


def one_run(arm: str, idx: int, model: str) -> dict:
    run_dir = RUNS / f"{arm}_{idx}"
    workdir = run_dir / "work"
    setup_workdir(workdir, arm)
    cmd = build_cmd(arm, model, workdir)
    (run_dir / "cmd.txt").write_text("\n".join(cmd), encoding="utf-8")

    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, timeout=1800)
    wall_s = time.monotonic() - t0

    (run_dir / "stdout.json").write_text(proc.stdout, encoding="utf-8")
    if proc.stderr:
        (run_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")

    row = {"arm": arm, "idx": idx, "model": model, "wall_s": round(wall_s, 1),
           "returncode": proc.returncode}
    try:
        result_json = json.loads(proc.stdout)
        row.update(extract_metrics(result_json))
    except json.JSONDecodeError:
        row["parse_error"] = True

    # Capture instrumentation engagement + read burden BEFORE the judge re-runs
    # report.py (which would append more records to debug.info).
    row["used_ouroboros"] = detect_tool_use(workdir)
    row["debug_info_chars"] = debug_info_chars(workdir)
    row.update(judge(workdir))
    row["success"] = bool(row.get("sample_ok") and row.get("hidden_ok"))
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--arms", nargs="+", default=["baseline", "ouroboros"])
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    rows = []
    # Interleave arms to balance prompt-cache warmth.
    for idx in range(1, args.n + 1):
        for arm in args.arms:
            print(f"=== run {arm} #{idx} (model={args.model}) ===", flush=True)
            row = one_run(arm, idx, args.model)
            rows.append(row)
            print(json.dumps({k: row.get(k) for k in
                              ("success", "sample_ok", "hidden_ok", "used_ouroboros",
                               "num_turns", "output_tokens", "duration_ms")}, ensure_ascii=False),
                  flush=True)
            (RUNS / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nWrote", RUNS / "results.json")


if __name__ == "__main__":
    main()
