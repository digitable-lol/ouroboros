"""Asking a compiler about itself, and reading what it answers.

Three things about a toolchain cannot be worked out from a config file — they
have to be asked of the compiler:

1. clang's **resource directory**, which holds its builtin headers (``stddef.h``,
   ``stdatomic.h``, the intrinsics). The pip ``libclang`` wheel ships the shared
   library but not those headers, so any parse that reaches one fails unless we
   point libclang at a real resource dir.
2. a toolchain's **implicit include search path** — the directories gcc/g++ add
   silently (libstdc++, fixed-includes, target headers). libclang knows nothing
   of them.
3. a toolchain's **predefined macros**.

This module is split in two on purpose:

* the **parsers** take the compiler's output as a string and return values. They
  open no file and start no process, so every branch in them is reachable from a
  string literal in a test.
* the **probes** run the compiler. They are the only part that needs a real
  toolchain on the machine.

The C backend, the C++ backend and the tree-flag builder all need probes 1 and
2. This module is where each of them takes it from — before, the clang-version
loop was written out three times and the ``-E -v`` parser twice.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence

#: clang binaries to try, in order: the unversioned name first, then the
#: versioned names Debian/Ubuntu's llvm packages install. Some hosts have only
#: ``clang-18`` on PATH and no plain ``clang``.
CLANG_NAMES: tuple[str, ...] = (
    "clang", "clang-21", "clang-20", "clang-19", "clang-18", "clang-17", "clang-16",
)

_SEARCH_BEGIN = "#include <...> search starts here:"
_SEARCH_END = "End of search list."


# --------------------------------------------------------------------------- #
# parsers — text in, values out
# --------------------------------------------------------------------------- #

def parse_search_dirs(stderr_text: str) -> tuple[str, ...]:
    """The directory list gcc/clang print for ``-E -v``, in search order.

    The list sits between two marker lines; everything outside them is the
    compiler's own chatter. Blank lines are dropped, and each entry is stripped
    of the leading space the compiler indents it with. Nothing here checks that
    a directory exists — that is a question for the disk, asked separately by
    `keep_toolchain_dirs`.
    """
    out: list[str] = []
    collecting = False
    for line in stderr_text.splitlines():
        if _SEARCH_BEGIN in line:
            collecting = True
            continue
        if _SEARCH_END in line:
            break
        if collecting:
            d = line.strip()
            if d:
                out.append(d)
    return tuple(out)


def keep_toolchain_dirs(dirs: Sequence[str], roots: Sequence[str],
                        resolved: Mapping[str, str]) -> tuple[str, ...]:
    """``-isystem`` flags for the search dirs that belong to the toolchain.

    ``resolved`` maps each directory that actually exists to its real path;
    a directory absent from it does not exist and is dropped. ``roots`` are the
    real paths the toolchain owns (its sysroot and its install prefix): when it
    is non-empty, a directory outside all of them is a **host** directory, and
    keeping it would let the host's glibc headers into a cross parse. An empty
    ``roots`` means no restriction — the caller is deliberately parsing against
    the host toolchain.
    """
    out: list[str] = []
    for d in dirs:
        real = resolved.get(d)
        if real is None:
            continue
        if roots and not real.startswith(tuple(roots)):
            continue
        out.append("-isystem")
        out.append(os.path.normpath(d))
    return tuple(out)


def parse_predef_macros(stdout_text: str) -> tuple[str, ...]:
    """``-D`` flags for the object-like macros in a ``-dM -E`` dump.

    Function-like macros (``#define max(a,b) ...``) are skipped: they cannot be
    expressed as a ``-D`` on a command line without quoting games, and no header
    branch we care about tests one.
    """
    defs: list[str] = []
    for line in stdout_text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or parts[0] != "#define" or "(" in parts[1]:
            continue
        name = parts[1]
        value = parts[2] if len(parts) == 3 else ""
        defs.append(f"-D{name}={value}" if value != "" else f"-D{name}")
    return tuple(defs)


def parse_macro_names(stdout_text: str) -> frozenset[str]:
    """Just the names from a ``-dM -E`` dump, function-like macros included
    (under their bare name), for asking "does this compiler already define X?".
    """
    names: set[str] = set()
    for line in stdout_text.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] == "#define":
            names.add(parts[1].split("(", 1)[0])
    return frozenset(names)


def install_prefix(compiler: str) -> str:
    """The directory a toolchain is installed under, from the path of one of its
    binaries: ``.../tools/bin/riscv64--netbsd-gcc`` -> ``.../tools``.

    A bare name (``g++``) is looked up on PATH first; if it is not there, the
    answer is ``""`` — better no prefix at all than a prefix derived from the
    current working directory, which would reject every real directory.
    """
    path = compiler
    if os.sep not in compiler:
        found = shutil.which(compiler)
        if found is None:
            return ""
        path = found
    return os.path.dirname(os.path.dirname(os.path.abspath(path)))


# --------------------------------------------------------------------------- #
# probes — the only code here that starts a program
# --------------------------------------------------------------------------- #

def _run(cmd: Sequence[str], timeout: int, stdin_text: str | None = None
         ) -> subprocess.CompletedProcess[str] | None:
    """Run a compiler, or ``None`` if it could not be run at all."""
    try:
        return subprocess.run(list(cmd), input=stdin_text, capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def clang_resource_dir(names: Iterable[str] = CLANG_NAMES) -> str | None:
    """The resource dir of the first clang on PATH that has one with an
    ``include`` subdirectory, or ``None`` when there is no usable clang."""
    for cc in names:
        proc = _run([cc, "-print-resource-dir"], timeout=10)
        if proc is None:
            continue
        rd = proc.stdout.strip()
        if rd and os.path.isdir(os.path.join(rd, "include")):
            return rd
    return None


def clang_builtin_include(names: Iterable[str] = CLANG_NAMES) -> tuple[str, ...]:
    """``("-isystem", "<resource dir>/include")``, or ``()`` if no clang has one.

    These must come *first* in the argument list, so that libstdc++'s
    ``#include_next <stddef.h>`` chains onto clang's header rather than gcc's.
    """
    rd = clang_resource_dir(names)
    return () if rd is None else ("-isystem", os.path.join(rd, "include"))


def include_search_dirs(cc_cmd: Sequence[str], lang_x: str, sysroot: str = "",
                        *, restrict: bool = True) -> tuple[str, ...]:
    """The toolchain's implicit include search path, as ``-isystem`` flags.

    ``sysroot`` should be the build's sysroot: a cross gcc asked without one
    falls back to the *host* ``/usr/include``, whose glibc headers then poison
    the parse. With ``restrict`` set (the default) only directories under the
    sysroot or under the compiler's own install prefix survive, so no host path
    can leak in. Clear ``restrict`` when the host toolchain is the intended one.
    """
    if not cc_cmd:
        return ()
    cmd = [*cc_cmd]
    if sysroot:
        cmd.append(f"--sysroot={sysroot}")
    proc = _run([*cmd, "-E", "-v", "-x", lang_x, "-"], timeout=30, stdin_text="")
    if proc is None:
        return ()
    dirs = parse_search_dirs(proc.stderr)
    resolved = {d: os.path.realpath(d) for d in dirs if os.path.isdir(d)}
    roots: tuple[str, ...] = ()
    if restrict:
        prefix = install_prefix(cc_cmd[0])
        roots = tuple(os.path.realpath(r) for r in (sysroot, prefix) if r)
    return keep_toolchain_dirs(dirs, roots, resolved)


def predef_macros(cc_cmd: Sequence[str]) -> tuple[str, ...]:
    """``-D`` flags for everything ``cc_cmd`` predefines, or ``()`` if the
    compiler could not be run or refused the probe."""
    if not cc_cmd:
        return ()
    proc = _run([*cc_cmd, "-dM", "-E", "-x", "c", "-"], timeout=30, stdin_text="")
    if proc is None or proc.returncode != 0:
        return ()
    return parse_predef_macros(proc.stdout)


def clang_macro_names(target: str, names: Iterable[str] = CLANG_NAMES) -> frozenset[str]:
    """The macro names clang predefines for ``target``. Empty when no clang on
    PATH can answer — which the caller must read as "unknown", not as "clang
    defines nothing"."""
    for cc in names:
        proc = _run([cc, f"--target={target}", "-dM", "-E", "-x", "c", "-"],
                    timeout=15, stdin_text="")
        if proc is None or proc.returncode != 0:
            continue
        return parse_macro_names(proc.stdout)
    return frozenset()
