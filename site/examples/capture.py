"""Run every example the site shows, and save the real output next to it.

Nothing on the site is typed by hand. Each block of program output on the
landing page is a file under ``captured/``, and ``site/build.py`` refuses to
build when one of them is missing. Re-take them with::

    PATH=.venv/bin:$PATH python3 site/examples/capture.py

What is taken:

* the four steps of the shop example — wrap, the wrapped file, the run, the
  trace — plus the run that crashes;
* the same call ``add(2, 3)`` on all eight languages, straight out of the
  project's own cross-language test helpers, so the eight blocks on the site
  are one run and not eight anecdotes;
* how much the trace weighs, in lines and in bytes per call.

The cross-language half imports the project's own test helpers, so `pytest`
has to be installed (`uv pip install pytest`); without it that half refuses
rather than inventing eight blocks.

The speed table is a separate, slower run — five minutes, because it builds and
runs the same program twice on eight languages, seven times each::

    PATH=.venv/bin:$PATH python3 site/examples/capture.py --measure

That calls the project's own `scripts/measure/run.sh` and only renames its
fields; no number on the site is measured by this file itself.

Captures are scrubbed of the throwaway directory the run happened in — a
stack trace printed from `/tmp/tmpugcdpwje` teaches the reader nothing — but
NOT of times, durations and call ids. Those are what a real trace looks like,
and re-taking the captures moves them. That is the point of them.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "captured"
ROOT = HERE.parent.parent

#: The order that is charged for delivery it should not have been charged for.
ORDER = ["tea", "mug", "kettle"]
#: The order with a typo in it, which raises.
TYPO = ["tea", "mugg"]


def ouroboros(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    exe = os.environ.get("OUROBOROS", shutil.which("ouroboros"))
    if exe is None:
        raise SystemExit("no `ouroboros` on PATH — see docs/install.md")
    return subprocess.run([exe, *args], cwd=cwd, capture_output=True,
                          text=True, check=True, timeout=600)


#: Replaced in every capture: the throwaway directory the run happened in.
WHERE = "/home/you/shop"


def write(name: str, text: str, work: Path | None = None) -> None:
    if work is not None:
        text = text.replace(str(work), WHERE)
    if not text.endswith("\n"):
        text += "\n"
    (OUT / name).write_text(text, encoding="utf-8")
    print(f"  {name}: {len(text.splitlines())} lines")


def shop_example() -> dict[str, object]:
    """The four steps of the landing page, plus the crash."""

    print("== the shop example")
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        shutil.copy(HERE / "shop.py", work / "shop.py")

        wrapped = ouroboros("wrap-file", "shop.py", cwd=work)
        write("shop-wrap.json", wrapped.stdout.strip(), work)
        write("shop-wrapped.py", (work / "shop.py").read_text(encoding="utf-8"), work)

        run = subprocess.run([sys.executable, "shop.py", *ORDER], cwd=work,
                             capture_output=True, text=True, check=True, timeout=600)
        write("shop-run.txt", run.stdout.strip(), work)

        trace = (work / "debug.info").read_text(encoding="utf-8")
        write("shop-debug-info.jsonl", trace.strip(), work)
        lines = [ln for ln in trace.splitlines() if ln.strip()]
        calls = sum(1 for ln in lines if json.loads(ln)["p"] == "in")

        read = ouroboros("trace", "debug.info", cwd=work)
        write("shop-trace.json", read.stdout.strip(), work)

        stats = ouroboros("trace-stats", "debug.info", cwd=work)
        write("shop-trace-stats.json", stats.stdout.strip(), work)

        # The same program, an order with a typo in it: the call raises.
        (work / "debug.info").unlink()
        crash = subprocess.run([sys.executable, "shop.py", *TYPO], cwd=work,
                               capture_output=True, text=True, timeout=600, check=False)
        write("shop-crash-stderr.txt", crash.stderr.strip(), work)
        raised = ouroboros("trace", "debug.info", "--outcome", "raised", cwd=work)
        write("shop-crash-trace.json", raised.stdout.strip(), work)

        return {
            "wrapped_functions": json.loads(wrapped.stdout)["functions_wrapped"],
            "trace_lines": len(lines),
            "trace_calls": calls,
            "trace_bytes": len(trace.encode("utf-8")),
            "bytes_per_call": round(len(trace.encode("utf-8")) / calls, 1),
            "crash_returncode": crash.returncode,
        }


def cross_language() -> dict[str, object]:
    """`add(2, 3)` on each of the eight backends, from the project's own helpers."""

    print("== add(2, 3) on eight languages")
    sys.path.insert(0, str(ROOT / "tests"))
    from test_schema_parity import (
        _ADD,
        _LANGS,
        _TOOL,
        _sources,
        _trace_lines,
    )

    taken: dict[str, object] = {}
    blocks: list[str] = []
    for lang in _LANGS:
        tool = _TOOL[lang]
        if tool is not None and shutil.which(tool) is None:
            taken[lang] = {"skipped": f"no {tool} on this machine"}
            print(f"  {lang}: skipped, no {tool}")
            continue
        with tempfile.TemporaryDirectory() as td:
            src, fname, tail = _sources(lang)
            lines = _trace_lines(lang, Path(td), src, fname, tail)
        want = _ADD[lang]
        pair = [ln for ln in lines if json.loads(ln).get("fn") == want]
        if len(pair) != 2:
            raise SystemExit(f"{lang}: expected 2 lines for {want}, got {len(pair)}")
        write(f"add-{lang}.jsonl", "\n".join(pair))
        taken[lang] = {"fn": want}
        blocks.append(lang)
        print(f"  {lang}: ok")
    taken["taken"] = blocks
    return taken


#: `scripts/measure/run.sh` names its rows in Russian, because that is the
#: language of the page it feeds. The site is English, so the names are mapped
#: here — and nothing else about the numbers is touched.
_ROWS = {
    "python": "Python", "js": "JavaScript", "c": "C", "cpp": "C++",
    "elixir": "Elixir", "go": "Go", "java": "Java", "csharp": "C#",
    "c-краткий": "C, short form (--minimal)", "go-без-th": "Go, without the goroutine id",
}


def measurements(source: Path | None) -> None:
    """Speed and volume on eight languages, from the project's own measure run."""

    print("== speed and volume on eight languages")
    with tempfile.TemporaryDirectory() as td:
        if source is None:
            work = Path(td) / "measure"
            subprocess.run([str(ROOT / "scripts" / "measure" / "run.sh"), str(work)],
                           check=True, timeout=3600)
            source = work / "итог.jsonl"
        raw = [json.loads(ln) for ln in
               Path(source).read_text(encoding="utf-8").splitlines() if ln.strip()]

    rows: dict[str, dict[str, Any]] = {}
    for rec in raw:
        name = str(rec["имя"])
        base, _, half = name.rpartition("-")
        # "go-без-th" and "c-краткий" are rows of their own, not halves of a pair.
        key, half = (name, "с") if name in _ROWS else (base, half)
        row = rows.setdefault(_ROWS[key], {"repeats": rec["повторов"]})
        side = "instrumented" if half == "с" else "plain"
        row[f"{side}_median_s"] = rec["медиана_с"]
        row[f"{side}_min_s"] = rec["минимум_с"]
        row[f"{side}_max_s"] = rec["максимум_с"]
        if "байт_записей" in rec:
            row["trace_bytes"] = rec["байт_записей"]
            row["trace_lines"] = rec["строк"]
            row["calls"] = rec["строк_входа"]

    for label, row in rows.items():
        plain = row.get("plain_median_s")
        # The two extra rows share the plain half of their language.
        if plain is None:
            plain = rows["C" if label.startswith("C,") else "Go"]["plain_median_s"]
            row["plain_median_s"] = plain
        added = float(row["instrumented_median_s"]) - float(plain)
        row["added_s"] = round(added, 6)
        row["added_us_per_call"] = round(added / int(row["calls"]) * 1e6, 1)
        row["bytes_per_call"] = round(int(row["trace_bytes"]) / int(row["calls"]), 1)
        print(f"  {label}: +{row['added_us_per_call']} us/call, "
              f"{row['bytes_per_call']} bytes/call")

    (OUT / "measurements.json").write_text(
        json.dumps({"machine": machine(), "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def machine() -> dict[str, str]:
    """What the numbers were taken on. Read, not typed."""

    def first(cmd: list[str]) -> str:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError):
            return "not on this machine"
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) \
            else "not on this machine"

    cpu = "unknown"
    for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("model name"):
            cpu = line.split(":", 1)[1].strip()
            break
    return {
        "cpu": cpu,
        "cores": first(["nproc"]),
        "kernel": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "node": first(["node", "-v"]),
        "gcc": first(["gcc", "-dumpfullversion"]),
        "go": first(["go", "version"]),
        "javac": first(["javac", "-version"]),
        "dotnet": first(["dotnet", "--version"]),
        "elixir": first(["elixir", "-e", "IO.puts System.version()"]),
    }


def main(argv: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if "--measure" in argv or "--measure-from" in argv:
        source = None
        if "--measure-from" in argv:
            source = Path(argv[argv.index("--measure-from") + 1])
        measurements(source)
        return 0

    facts: dict[str, object] = {"shop": shop_example(), "languages": cross_language()}
    facts["machine"] = machine()
    (OUT / "facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwritten to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
