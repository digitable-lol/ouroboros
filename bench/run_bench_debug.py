#!/usr/bin/env python3
"""Experiment B′: opaque-state debugging with forced tool use.

Both arms get the SAME buggy pipeline.py and must fix it so the output matches.
The failure is a wrong VALUE with no traceback, opaque from the final output.

  * baseline  — debug with Bash/Read/Write/Edit (read code, add prints, run).
  * ouroboros — the buggy pipeline.py is pre-instrumented with ouroboros logging
    and the system prompt directs the agent to run it and read debug.info (each
    function's inputs/outputs) to localize the wrong value.

Success = the fixed pipeline reproduces BOTH the sample and a held-out hidden
expected score (the hidden check defeats hardcoding). Tokens/turns compared only
across equal outcomes; debug.info read burden recorded separately.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from run_bench import debug_info_chars, extract_metrics, run_report  # reuse helpers

BENCH = Path(__file__).parent
PROJECT = BENCH.parent
TASK = BENCH / "task_debug"
FIXTURES = BENCH / "fixtures_debug"
RUNS = BENCH / "runs_debug"
RUNTIME_PY = PROJECT / "ouroboros" / "runtime.py"
BUGGY = TASK / "pipeline_buggy.py"
OURO = f"uv run --directory {PROJECT} ouroboros"

TASK_PROMPT = """\
You are in the current directory. It contains:
- spec.md — describes the intended pipeline and the bug to fix; read it first
- pipeline.py — the buggy program
- sample.txt — input
- sample_expected.txt — the correct output for sample.txt

`python3 pipeline.py sample.txt` currently prints the wrong score. Find and fix
the single bug in pipeline.py so its output matches sample_expected.txt exactly.
Do not hardcode the answer — the fix is validated against a hidden input too.
"""

OUROBOROS_SYSTEM = (
    "pipeline.py in your working directory is ALREADY INSTRUMENTED with the "
    "`ouroboros` logging tool: every function logs its name, arguments and return "
    "value to a file named debug.info when the program runs (logging goes to "
    "debug.info, not stdout). `ouroboros_runtime.py` is present (the instrumented "
    "code imports it).\n\n"
    "To localize the bug, prefer this over adding print statements:\n"
    "  1. Run `python3 pipeline.py sample.txt` (it appends to debug.info).\n"
    "  2. Read debug.info and inspect each function's inputs and outputs to find "
    "which stage produces a value that disagrees with the spec — that stage holds "
    "the bug.\n"
    "  3. Fix it, re-run, and confirm the output matches sample_expected.txt.\n\n"
    "Each debug.info record looks like:\n"
    "  ШАБЛОН_НАЧАЛО:\n"
    "      <time>: <uuid>: <function name>\n"
    "      args: (...) kwargs: {...}\n"
    "      result: <return value>\n"
    "  ШАБЛОН_КОНЕЦ\n"
    f"\nIf you edit a function and want fresh logs, re-running is enough; "
    f"`{OURO} wrap-file pipeline.py` re-instruments idempotently if needed.\n"
)

# Control arm: the SAME stage-by-stage debugging strategy as the ouroboros arm,
# but WITHOUT the tool — to separate "the runtime trace helped" from "the
# divide-and-conquer hint helped". If `strategy` matches `ouroboros`, the gain
# was the hint, not the tool.
STRATEGY_SYSTEM = (
    "To localize the bug efficiently, work stage by stage: for each function in "
    "the pipeline, check whether its output agrees with the spec for the given "
    "input (add temporary print statements or reason it through). The first stage "
    "whose output disagrees with the spec holds the bug. Fix that stage.\n"
)

BASE_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]


def build_cmd(arm: str, model: str, workdir: Path) -> list[str]:
    cmd = ["claude", "-p", TASK_PROMPT, "--output-format", "json",
           "--model", model, "--add-dir", str(workdir)]
    if arm == "ouroboros":
        cmd += ["--append-system-prompt", OUROBOROS_SYSTEM]
    elif arm == "strategy":
        cmd += ["--append-system-prompt", STRATEGY_SYSTEM]
    cmd += ["--allowedTools", *BASE_TOOLS]
    return cmd


def setup_workdir(workdir: Path, arm: str) -> None:
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    shutil.copy2(TASK / "spec.md", workdir / "spec.md")
    shutil.copy2(TASK / "sample.txt", workdir / "sample.txt")
    shutil.copy2(TASK / "sample_expected.txt", workdir / "sample_expected.txt")
    shutil.copy2(BUGGY, workdir / "pipeline.py")
    if arm == "ouroboros":
        shutil.copy2(RUNTIME_PY, workdir / "ouroboros_runtime.py")
        # Pre-instrument so runtime logs are available the first time it runs.
        subprocess.run(
            ["uv", "run", "--directory", str(PROJECT), "ouroboros", "wrap-file",
             str(workdir / "pipeline.py")],
            capture_output=True, text=True, timeout=120, check=True,
        )


def find_pipeline(workdir: Path) -> Path | None:
    direct = workdir / "pipeline.py"
    return direct if direct.is_file() else None


def judge(workdir: Path) -> dict:
    pipeline = find_pipeline(workdir)
    res = {"pipeline_found": pipeline is not None, "sample_ok": False, "hidden_ok": False}
    if pipeline is None:
        return res
    sample_expected = (TASK / "sample_expected.txt").read_text(encoding="utf-8")
    hidden_expected = (FIXTURES / "hidden_expected.txt").read_text(encoding="utf-8")
    try:
        _, out_s = run_report(pipeline, TASK / "sample.txt")
        res["sample_ok"] = out_s == sample_expected
        _, out_h = run_report(pipeline, FIXTURES / "hidden.txt")
        res["hidden_ok"] = out_h == hidden_expected
    except Exception as e:  # noqa: BLE001
        res["judge_error"] = str(e)
    return res


def detect_tool_use(workdir: Path) -> bool:
    debug = workdir / "debug.info"
    return debug.is_file() and "ШАБЛОН_НАЧАЛО" in debug.read_text(encoding="utf-8", errors="replace")


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

    row = {"arm": arm, "idx": idx, "model": model, "wall_s": round(wall_s, 1)}
    try:
        row.update(extract_metrics(json.loads(proc.stdout)))
    except json.JSONDecodeError:
        row["parse_error"] = True

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
    ap.add_argument("--out", default="results.json")
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    rows = []
    for idx in range(1, args.n + 1):
        for arm in args.arms:
            print(f"=== run {arm} #{idx} (model={args.model}) ===", flush=True)
            row = one_run(arm, idx, args.model)
            rows.append(row)
            print(json.dumps({k: row.get(k) for k in
                              ("success", "sample_ok", "hidden_ok", "used_ouroboros",
                               "num_turns", "output_tokens", "debug_info_chars", "duration_ms")},
                             ensure_ascii=False), flush=True)
            (RUNS / args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nWrote", RUNS / args.out)


if __name__ == "__main__":
    main()
