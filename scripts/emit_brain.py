"""Print the flang brain into Python, or check that the committed print is current.

The brain — every rule that decides something about a trace — is written in
flang (``ouroboros/brain/trace_brain.flang``). The compiler prints it into
Python, and the printed files are committed under ``ouroboros/brain/_flang/``
so that installing the tool needs pip and nothing else: a user who has never
heard of flang still gets a working ``ouroboros trace``.

Committed output rots silently — that is the whole cost of committing it — so
the print is a machine-checked artefact, not a file anyone edits::

    uv run python scripts/emit_brain.py           # print again
    uv run python scripts/emit_brain.py --check   # is the committed print current?

``--check`` asks two questions, and they fail apart:

**Does the print belong to this source?** The digest of ``trace_brain.flang``
is committed beside the print, in ``_flang/printed-from.txt``, and compared
against the source as it stands now. This needs no compiler, so it is the half
that always runs.

**Is the print what this compiler makes of that source?** The script prints into
a temporary directory and compares byte for byte. This needs the pinned
compiler, and says so when it is not there.

Two questions because comparing prints alone answers only the second, and a
whole class of source change never reaches a print: a ``note``, or a
postcondition the proof kernel closes and strips before printing. Such an edit
left the print identical and the gate green — the source had moved and nothing
said so. The digest is what notices.

Return codes, the way the rest of this tree's gates use them: 0 both questions
answered and both current, 1 stale, 2 the digest is current and the print was
not compared. Two is not "fine": it means there is no flang on this machine, or
a different one from the compiler that made the committed print. Comparing
prints from two compilers would report a difference that says nothing about this
repository, so the script says what it did not check instead of guessing.

``--self-test`` feeds the digest check a source it did not print from and
requires a refusal: a guard that has never gone red is indistinguishable from
one that cannot.

One line of the printed module is rewritten: the compiler emits
``import flang_runtime as rt``, which resolves only when the printed directory
is itself on ``sys.path``. Inside a package it has to be a package-relative
import, and that single substitution is what makes the print importable as
``ouroboros.brain._flang.trace_brain``. It is applied here, in one place, and
checked by the same comparison as the rest of the file.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "ouroboros" / "brain" / "trace_brain.flang"
TARGET = ROOT / "ouroboros" / "brain" / "_flang"

#: What the compiler writes and we keep. It also writes a ``Makefile`` for a
#: standalone print; inside a package it has nothing to build, so it is dropped.
#: ``trace_brain.pyi`` is not written by the compiler — it is derived from the
#: print below, so that the type checker reads a stub nobody maintains by hand.
PRINTED = ("trace_brain.py", "flang_runtime.py", "trace_brain.pyi")

#: Which source, and which compiler, the committed print was made from. Written
#: here and read by ``--check``; the one part of the check that needs no
#: compiler, and therefore the only part that runs everywhere.
STAMP = TARGET / "printed-from.txt"

#: The compiler that made the committed print. Pinned, and the print is a
#: byte-for-byte artefact of it: another version prints the same rules
#: differently — different helper names, a different runtime — and a comparison
#: across versions would be noise. Raising this number means printing again and
#: committing what comes out. Note that npm carries 0.7.0 and 0.7.3 only, so a
#: machine with the published compiler cannot check this print at all; it says
#: so rather than failing.
EXPECTED_VERSION = "0.7.14"

#: The one rewrite, and its reason, in the module docstring above.
IMPORT_FROM = "import flang_runtime as rt"
IMPORT_TO = "from ouroboros.brain._flang import flang_runtime as rt"

#: Head of the derived stub. The printed module carries no annotations at all —
#: flang types are checked by the compiler, before anything is printed — so
#: ``mypy --strict`` would report every function in it. A stub beside the module
#: is what mypy reads instead, and deriving it from the print is what keeps it
#: from drifting: a hand-written one would go stale the first time a function is
#: renamed in the flang source, and go stale silently.
STUB_HEAD = '''"""Types for the printed brain. Derived by scripts/emit_brain.py — do not edit.

Every flang function takes the evaluation context first and flang values after
it, and answers with a flang value. There is nothing finer to say here: inside
the printed module a value is one tagged shape, and which shape it holds is
what the flang declarations say, checked by the compiler before the print.
"""

from ouroboros.brain._flang.flang_runtime import Ctx as Ctx
from ouroboros.brain._flang.flang_runtime import Value as Value

'''


def flang_binary() -> str | None:
    """The compiler, or None when it is not installed."""

    return shutil.which("flang")


def flang_version(binary: str) -> str:
    """What ``flang --version`` says, as a bare version number."""

    proc = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False)
    return proc.stdout.strip().removeprefix("flang").strip()


def print_brain(out_dir: Path) -> None:
    """Run the compiler and leave the printed, rewritten files in ``out_dir``."""

    flang = flang_binary()
    if flang is None:
        raise SystemExit("flang is not installed: npm i -g @digitable-lol/flang")
    proc = subprocess.run(
        [flang, "emit", str(SOURCE), "--target", "python", "--out", str(out_dir),
         "--no-cli"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"flang emit failed with code {proc.returncode}")
    module = out_dir / "trace_brain.py"
    text = module.read_text(encoding="utf-8")
    if IMPORT_FROM not in text:
        raise SystemExit(
            f"the printed module no longer contains {IMPORT_FROM!r}: the Python "
            "backend changed how it imports its runtime, and the rewrite in "
            "scripts/emit_brain.py has to follow it"
        )
    module.write_text(text.replace(IMPORT_FROM, IMPORT_TO, 1), encoding="utf-8")
    (out_dir / "trace_brain.pyi").write_text(stub_for(module), encoding="utf-8")
    for leftover in out_dir.iterdir():
        if leftover.name not in PRINTED:
            leftover.unlink()


def stub_for(module: Path) -> str:
    """The type stub for a printed module, read off its own definitions."""

    lines = [STUB_HEAD]
    for name, params in re.findall(r"^def (\w+)\(([^)]*)\):", module.read_text(
            encoding="utf-8"), re.MULTILINE):
        if name.startswith("_"):
            continue
        if name == "new_context":
            lines.append("def new_context() -> Ctx: ...")
            continue
        if name == "entry":
            lines.append("def entry() -> object: ...")
            continue
        if name == "call":
            lines.append("def call(ctx: Ctx, name: str, args: list[Value]) -> Value: ...")
            continue
        typed = ", ".join(
            f"{one.strip()}: Ctx" if index == 0 and one.strip() == "ctx"
            else f"{one.strip()}: Value"
            for index, one in enumerate(params.split(",")) if one.strip())
        lines.append(f"def {name}({typed}) -> Value: ...")
    return "\n".join(lines) + "\n"


def install(out_dir: Path) -> None:
    for name in PRINTED:
        (TARGET / name).write_bytes((out_dir / name).read_bytes())
    STAMP.write_text(stamp_for(SOURCE), encoding="utf-8")


def stamp_for(source: Path) -> str:
    """What ``printed-from.txt`` says about a source: its digest and the compiler.

    The digest is of the bytes on disk, not of anything the compiler derived
    from them — that is the whole point. A change the print never sees still
    changes these bytes.
    """

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return (f"source {source.name}\n"
            f"sha256 {digest}\n"
            f"compiler flang {EXPECTED_VERSION}\n")


def stale_stamp(source: Path = SOURCE) -> list[str]:
    """What the committed stamp says that the source no longer says.

    Two things can disagree and they are different findings: the source has
    moved since it was printed, or the print was made by another compiler than
    the one pinned here. Reporting both as "the source changed" would send the
    reader to the wrong file.
    """

    if not STAMP.exists():
        return [f"{STAMP.name}: not committed at all — nothing says which source "
                "this print was made from"]
    committed = STAMP.read_text(encoding="utf-8")
    if committed == stamp_for(source):
        return []
    said = dict(line.split(" ", 1) for line in committed.splitlines() if " " in line)
    if said.get("sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
        return [f"{source.name}: changed since it was printed — the print is a "
                f"print of some earlier text ({STAMP.name} says so)"]
    made_by = said.get("compiler", "a compiler this file does not name")
    return [f"{STAMP.name}: this print was made by {made_by}, and the compiler "
            f"pinned here is flang {EXPECTED_VERSION}"]


def differences(out_dir: Path) -> list[str]:
    bad = []
    for name in PRINTED:
        committed = TARGET / name
        fresh = (out_dir / name).read_bytes()
        if not committed.exists():
            bad.append(f"{name}: not committed at all")
        elif committed.read_bytes() != fresh:
            bad.append(f"{name}: committed print differs from a fresh one")
    return bad


def self_test() -> int:
    """Hand the digest check a source it did not print from; require a refusal.

    No compiler, no temporary print: the question is only whether the guard can
    say no. It is asked here because the answer it gives on this tree is always
    yes, and a check that has never refused says nothing about the day it should.
    """

    findings = []
    with tempfile.TemporaryDirectory(prefix="brain-stamp-") as tmp:
        moved = Path(tmp) / SOURCE.name
        moved.write_bytes(SOURCE.read_bytes()
                          + b'\nnote "a term that never reaches the print"\n')
        if not stale_stamp(moved):
            findings.append("a source with one more note was called current")
    if stale_stamp():
        findings.append("the tree's own source was called stale")
    for line in findings:
        print(f"   {line}")
    if findings:
        print("   the source-digest check does not refuse what it must refuse")
        return 1
    print("   the source-digest check refuses a source it did not print from")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="do not write; fail if the committed print is stale")
    parser.add_argument("--self-test", action="store_true",
                        help="require the source-digest check to refuse a source "
                             "it did not print from")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    if args.check:
        # The digest first, and on its own: it answers whether the print belongs
        # to the source in front of us, which is the question a missing compiler
        # must not be allowed to leave unasked.
        stale = stale_stamp()
        if stale:
            for line in stale:
                print(f"   {line}")
            print("   run: uv run python scripts/emit_brain.py, then commit the result")
            return 1
        binary = flang_binary()
        if binary is None:
            print("   the print belongs to the source; flang is not installed, so "
                  "it was not printed again and compared")
            return 2
        found = flang_version(binary)
        if found != EXPECTED_VERSION:
            print(f"   the print belongs to the source; this is flang {found} and "
                  f"the print was made by {EXPECTED_VERSION} — not compared")
            return 2

    with tempfile.TemporaryDirectory(prefix="brain-print-") as tmp:
        out_dir = Path(tmp)
        print_brain(out_dir)
        if not args.check:
            install(out_dir)
            total = sum((TARGET / n).stat().st_size for n in PRINTED)
            print(f"printed {len(PRINTED)} files, {total} bytes, into {TARGET}")
            print(f"stamped {STAMP.relative_to(ROOT)} with the digest of {SOURCE.name}")
            return 0
        bad = differences(out_dir)

    if bad:
        for line in bad:
            print(f"   {line}")
        print("   run: uv run python scripts/emit_brain.py, then commit the result")
        return 1
    print(f"   the committed print of {SOURCE.name} is current, and belongs to it")
    return 0


if __name__ == "__main__":  # pragma: no cover — the entry point, not a rule
    raise SystemExit(main())
