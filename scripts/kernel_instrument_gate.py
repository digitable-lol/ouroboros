#!/usr/bin/env python3
"""Release gate: prove that Ouroboros-instrumented KERNEL code actually COMPILES
with the real cross toolchain — not just that it parses or that the userland shim
formats correctly.

This bakes in the hard lesson (2026-06-15): the `OURO_KERNEL_TEST` userland shim
in tests/test_c.py only checks record FORMATTING. It cannot catch a `_KERNEL`-branch
regression like a missing `#include <sys/time.h>` (struct timespec) or an atomic
primitive that doesn't exist — those compile-fail ONLY against the real kernel
headers + cross compiler. So before trusting the kernel sink, run this on the
target (gpu), which:
  1. instruments a real kernel .c IN PLACE via the ouroboros CLI (drops the header),
  2. compiles it with that file's exact compile_commands.json command, but with the
     real cross gcc as argv[0] and a throwaway -o,
  3. ALWAYS restores the file + removes the dropped header + temp object.
Exit 0 = the instrumented kernel translation unit built clean (rc=0).

Usage (on gpu):
  python3 kernel_instrument_gate.py \
      --ros /home/u/netbsd/ROS \
      --compdb /home/u/netbsd/obj/compile_commands.json \
      --cc /home/u/netbsd/tools/bin/riscv64--netbsd-gcc \
      --ouroboros /home/u/ouroboros/.venv/bin/ouroboros \
      sys/uvm/pmap/pmap_segtab.c pmap_segtab_activate pmap_segtab_deactivate
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ros", required=True, help="ROS source tree root")
    ap.add_argument("--compdb", required=True, help="compile_commands.json path")
    ap.add_argument("--cc", required=True, help="real cross compiler (argv[0] override)")
    ap.add_argument("--ouroboros", required=True, help="ouroboros CLI path")
    ap.add_argument("relpath", help="kernel .c relative to --ros")
    ap.add_argument("functions", nargs="+", help="functions to instrument")
    a = ap.parse_args()

    src = Path(a.ros) / a.relpath
    if not src.is_file():
        print(f"FAIL: no such file {src}")
        return 2
    abspath = src.absolute()
    compdb = json.loads(Path(a.compdb).read_text(encoding="utf-8"))
    entry = next((e for e in compdb if Path(e["file"]).absolute() == abspath), None)
    if entry is None:
        print(f"FAIL: {a.relpath} has no compile_commands.json entry")
        return 2

    backup = src.with_name(src.name + ".gate.bak")
    shutil.copy2(src, backup)
    header = src.parent / "ouroboros_runtime.h"
    header_pre_existing = header.exists()
    objfd, obj_name = tempfile.mkstemp(suffix=".o", prefix="gate-")
    obj = Path(obj_name)
    os.close(objfd)
    obj.unlink()
    try:
        r = subprocess.run([a.ouroboros, "wrap-functions", str(src), *a.functions],
                           capture_output=True, text=True, check=False)
        print("instrument:", r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            print("FAIL: instrumentation step failed")
            return 1

        args = list(entry.get("arguments") or entry["command"].split())
        args[0] = a.cc
        for i, tok in enumerate(args):
            if tok == "-o" and i + 1 < len(args):
                args[i + 1] = str(obj)
        c = subprocess.run(args, cwd=entry["directory"],
                           capture_output=True, text=True, check=False)
        if c.returncode == 0 and obj.exists():
            print(f"PASS: instrumented {a.relpath} cross-compiled clean (rc=0)")
            return 0
        print(f"FAIL: cross-compile rc={c.returncode}\n{c.stderr[-2000:]}")
        return 1
    finally:
        shutil.move(backup, src)            # restore original exactly
        if not header_pre_existing:
            header.unlink(missing_ok=True)
        obj.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
