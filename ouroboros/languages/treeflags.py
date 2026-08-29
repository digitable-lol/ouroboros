"""Per-source-tree compiler flags, so the C/C++ backends can instrument files
from a *real* source tree (not just self-contained snippets).

Two discovery mechanisms, both keyed on the file's path via an ``.ouroboros.json``
found by walking up from the file (clangd/clang-tidy style):

1. **Compilation database** (preferred, whole-tree): point ``compdb`` at a
   ``compile_commands.json``. Every file the build compiled has an exact flag
   set there, so *any* file in the tree is one ``wrap_file`` away — no per-area
   config. We take that entry's ``-I``/``-D``/``-std``/``--sysroot`` (dropping
   ``-c``/``-o``/``-W``/``-O``/deps/the source itself, which don't affect the AST).

2. **Static ``cflags``** (fallback for files not in the db / small trees): a
   literal argument list.

In both cases the target compiler's predefined macros are appended (harvested via
``predef_cc -dM -E``) so the parse matches the real toolchain, and ``--target`` is
added if absent (host libclang must be told the target). All lookups are cached.

Config schema (per language key, e.g. "c")::

    {
      "c": {
        "compdb":    "/home/u/netbsd/obj/compile_commands.json",
        "cflags":    ["-std=gnu11", "--sysroot=...", "-I...", "-D..."],
        "predef_cc": ["/.../riscv64--netbsd-gcc", "-std=gnu11"]
      }
    }
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any


def _entry_args(entry: dict[str, Any]) -> list[str] | None:
    """Compile args for a compile_commands.json entry. The clang JSON-compilation-
    database spec allows EITHER ``arguments`` (a list, preferred) OR ``command`` (a
    single shell string). Accept both — a build that emits only ``command`` would
    otherwise make EVERY entry invisible, silently degrading the whole tree to
    fallback flags."""
    args = entry.get("arguments")
    if isinstance(args, list):
        return args
    command = entry.get("command")
    if isinstance(command, str):
        return shlex.split(command)
    return None

CONFIG_NAME = ".ouroboros.json"

_SRC_EXTS = (".c", ".cc", ".cpp", ".cxx", ".c++", ".S", ".s")
#: flags that take a following argument we must drop together
_DROP_PAIR = {"-o", "-MF", "-MT", "-MQ", "-MJ"}
#: standalone flags to drop (don't affect the AST / would fight libclang).
#: any other ``-M*`` (dep generation: -M, -MM, -MD, -MMD, -MP, -MG) is dropped
#: too — leaving it in makes libclang dump a makefile rule to stdout.
_DROP_FLAG = {"-c", "-pipe", "-Werror"}
#: path-valued flags in separate-argument form (``-I path``); the path that
#: follows is resolved against the compile entry's ``directory``.
_PATH_PAIR = {"-I", "-iquote", "-isystem", "-idirafter", "-include",
              "-imacros", "-iprefix", "-isysroot", "--sysroot"}
#: path-valued flags in joined form (``-I.`` / ``-isystem/abs``); everything
#: after the prefix is the path.
_PATH_JOINED = ("-I", "-iquote", "-isystem", "-idirafter", "-include",
                "-imacros", "-iprefix", "-isysroot", "--sysroot=")
#: ``-f`` flags worth keeping — they change *what parses* (language dialect,
#: freestanding/builtins, C++ exceptions/rtti, char/enum signedness). Every other
#: ``-f`` is codegen/optimization with no effect on the AST, and many are
#: gcc-only (``-fno-ipa-icf``, ``-fbuilding-libgcc``...) which clang rejects as a
#: hard error — so we keep this allow-list and drop the rest.
_KEEP_F = {
    "-ffreestanding", "-fhosted", "-fno-builtin", "-fbuiltin",
    "-fgnu89-inline", "-fno-gnu89-inline", "-fms-extensions", "-fno-asm",
    "-fasm", "-fopenmp", "-fopenmp-simd", "-fno-openmp", "-fblocks",
    "-fexceptions", "-fno-exceptions", "-frtti", "-fno-rtti",
    "-fshort-enums", "-fno-short-enums", "-fshort-wchar",
    "-fsigned-char", "-funsigned-char", "-fchar8_t", "-fno-char8_t",
    "-fno-operator-names", "-fno-threadsafe-statics", "-fwrapv",
    "-fno-strict-aliasing", "-fcoroutines", "-fconcepts", "-fmodules-ts",
}
#: ``-f`` prefixes whose any-suffix form affects parsing (``-fno-builtin-printf``,
#: ``-fvisibility-inlines-hidden`` is codegen so NOT here).
_KEEP_F_PREFIX = ("-fno-builtin-", "-fexec-charset=", "-finput-charset=",
                  "-fextended-identifiers")


def _abspath(path: str, directory: str) -> str:
    """Resolve ``path`` against the compile entry's ``directory`` (where the
    build actually ran), so relative ``-I.``/``-I../x`` point at the obj dir
    holding generated headers (nodes.h, opt_*.h, menu_defs.h)."""
    if not path or os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(directory, path))


@lru_cache(maxsize=512)
def _find_config(start: str) -> str | None:
    p = Path(start).resolve()
    for d in (p, *p.parents):
        candidate = d / CONFIG_NAME
        if candidate.is_file():
            return str(candidate)
    return None


@lru_cache(maxsize=64)
def _load(config_path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _clang_builtin_include() -> tuple[str, ...]:
    """``-isystem <resource>/include`` for clang's *builtin* headers (stdatomic.h,
    stddef.h, stdarg.h, intrinsics...). The pip ``libclang`` wheel ships only the
    shared lib, not these headers, so a tree parse that pulls in e.g. <stdatomic.h>
    fails unless we point libclang at a real resource dir. Probe the clang on PATH
    (unversioned first, then common Debian/Ubuntu versioned names)."""
    for cc in ("clang", "clang-20", "clang-19", "clang-18", "clang-17", "clang-16"):
        try:
            rd = subprocess.run([cc, "-print-resource-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if rd and os.path.isdir(os.path.join(rd, "include")):
            return ("-isystem", os.path.join(rd, "include"))
    return ()


@lru_cache(maxsize=32)
def _harvest_include_dirs(cc_cmd: tuple[str, ...], lang_x: str,
                          sysroot: str = "") -> tuple[str, ...]:
    """The cross compiler's *implicit* system include search path as ``-isystem``
    flags. gcc adds these silently (libstdc++ ``<cstdio>``, fixed-includes,
    target headers); libclang knows nothing of them, so a tree parse that pulls
    in a C++ stdlib header fails. We read the search list gcc prints with
    ``-E -v`` (between the two marker lines) for the given ``-x`` language.

    ``sysroot`` MUST be the build's sysroot: a cross gcc with no ``--sysroot``
    falls back to the *host* ``/usr/include`` (glibc), which then poisons the
    parse. We pass it through and keep only dirs under the sysroot or the
    compiler's own install prefix, so no host header path can leak in."""
    cmd = list(cc_cmd)
    if sysroot:
        cmd.append(f"--sysroot={sysroot}")
    try:
        proc = subprocess.run(
            [*cmd, "-E", "-v", "-x", lang_x, "/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    # compiler install prefix: .../tools/bin/<cc>  ->  .../tools
    prefix = os.path.dirname(os.path.dirname(os.path.abspath(cc_cmd[0])))
    roots = tuple(os.path.realpath(r) for r in (sysroot, prefix) if r)
    out: list[str] = []
    collecting = False
    for line in proc.stderr.splitlines():
        if "#include <...> search starts here:" in line:
            collecting = True
            continue
        if "End of search list." in line:
            break
        if collecting:
            d = line.strip()
            if not d or not os.path.isdir(d):
                continue
            if roots and not os.path.realpath(d).startswith(roots):
                continue  # drop host paths — only sysroot / toolchain dirs
            out.append("-isystem")
            out.append(os.path.normpath(d))
    return tuple(out)


@lru_cache(maxsize=32)
def _harvest_predefs(cc_cmd: tuple[str, ...]) -> tuple[str, ...]:
    """Object-like predefined macros of ``cc_cmd``, as ``-D`` flags. Cached."""
    try:
        proc = subprocess.run(
            [*cc_cmd, "-dM", "-E", "-x", "c", "/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if proc.returncode != 0:
        return ()
    defs: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or parts[0] != "#define" or "(" in parts[1]:
            continue
        name = parts[1]
        value = parts[2] if len(parts) == 3 else ""
        defs.append(f"-D{name}={value}" if value != "" else f"-D{name}")
    return tuple(defs)


@lru_cache(maxsize=8)
def _clang_macro_names(target: str) -> frozenset[str]:
    """Names of macros clang predefines for ``target`` (so we don't re-add them
    and reintroduce gcc/clang conflicts). Empty set if no clang on PATH."""
    for cc in ("clang", "clang-20", "clang-19", "clang-18", "clang-17", "clang-16"):
        try:
            proc = subprocess.run(
                [cc, f"--target={target}", "-dM", "-E", "-x", "c", "/dev/null"],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        names = set()
        for line in proc.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 2 and parts[0] == "#define":
                names.add(parts[1].split("(", 1)[0])
        return frozenset(names)
    return frozenset()


@lru_cache(maxsize=32)
def _missing_predefs(cc_cmd: tuple[str, ...], target: str) -> tuple[str, ...]:
    """gcc predefined macros that clang does NOT define for ``target`` — the
    compatibility macros NetBSD headers need (``__WCHAR_MIN__``, ``__WINT_MIN__``,
    ``__SIG_ATOMIC_TYPE__``...). We add only the *difference*: macros clang
    already defines keep clang's own self-consistent values, so we don't drag a
    header onto its gcc-builtin branch (the cause of the _Atomic/__restrict
    breakage when the full gcc dump was injected)."""
    gcc = _harvest_predefs(cc_cmd)
    clang_names = _clang_macro_names(target)
    if not clang_names:
        return ()
    out = []
    for d in gcc:
        name = d[2:].split("=", 1)[0]
        if name not in clang_names:
            out.append(d)
    return tuple(out)


def _ast_args(arguments: list[str], directory: str = "") -> list[str]:
    """Keep only the arguments that affect parsing (includes/defines/std/sysroot/
    target/freestanding...); drop the compiler, output, deps, warnings, codegen.

    Relative include paths are resolved against ``directory`` (the dir the build
    ran in) so that ``-I.``/``-I../x`` find generated headers in the obj tree."""
    out: list[str] = []
    skip = False
    for i, a in enumerate(arguments):
        if skip:
            skip = False
            continue
        if i == 0:                       # the compiler itself
            continue
        if a in _DROP_PAIR:
            skip = True
            continue
        if a in _DROP_FLAG or a.startswith(("-W", "-O", "-g", "-M")):
            continue
        if a.startswith("-f") and a not in _KEEP_F \
                and not a.startswith(_KEEP_F_PREFIX):
            continue                     # codegen/opt or gcc-only -f: no AST effect
        if a.endswith(_SRC_EXTS):        # the source file being compiled
            continue
        # separate-argument path flag: "-I", "<path>"  ->  "-I", "<abs>"
        if a in _PATH_PAIR and i + 1 < len(arguments):
            out.append(a)
            out.append(_abspath(arguments[i + 1], directory))
            skip = True
            continue
        # joined path flag: "-I.", "-isystem/abs", "--sysroot=..."
        for pfx in _PATH_JOINED:
            if a.startswith(pfx) and len(a) > len(pfx):
                out.append(pfx + _abspath(a[len(pfx):], directory))
                break
        else:
            out.append(a)
    return out


@lru_cache(maxsize=8)
def _load_compdb(compdb_path: str) -> dict[str, Any]:
    """Index a compile_commands.json as {abs_source_path: tuple(ast_args)}."""
    try:
        data = json.loads(Path(compdb_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    index: dict[str, tuple[str, ...]] = {}
    for entry in data:
        f = entry.get("file")
        args = _entry_args(entry)
        if not f or args is None:
            continue
        directory = entry.get("directory", "")
        absf = os.path.normpath(os.path.join(directory, f))
        index[absf] = tuple(_ast_args(args, directory))
    return index


_CXX_EXTS = (".cc", ".cpp", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".hpp")


def _detect_lang(arguments: list[str], ext: str = "") -> str:
    """Language a compile entry actually used — ``"cpp"`` or ``"c"``. A definitive
    C++ extension wins outright. Otherwise some trees compile ``.c`` files with the
    C++ driver (gdb, parts of gcc); the extension lies, the compile command tells
    the truth — decide by the driver name and the std. (gcc compiles its own
    ``.cc`` with the *gcc* driver and no ``-x c++``, relying on the extension, so
    the extension check is load-bearing, not just an optimisation.)"""
    if ext.lower() in _CXX_EXTS:
        return "cpp"
    if arguments:
        comp = os.path.basename(arguments[0])
        if "++" in comp or "g++" in comp or comp.endswith("CC"):
            return "cpp"
    expect_x = False
    for a in arguments:
        if expect_x:
            return "cpp" if a.strip().startswith("c++") else "c"
        if a == "-x":
            expect_x = True
        elif a.startswith("-x") and len(a) > 2:
            return "cpp" if a[2:].strip().startswith("c++") else "c"
        elif a.startswith("-std=") and "++" in a:
            return "cpp"
    return "c"


@lru_cache(maxsize=8)
def _compdb_lang_index(compdb_path: str) -> dict[str, str]:
    """Index a compile_commands.json as {abs_source_path: "c"|"cpp"}."""
    try:
        data = json.loads(Path(compdb_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    index: dict[str, str] = {}
    for entry in data:
        f = entry.get("file")
        args = _entry_args(entry)
        if not f or args is None:
            continue
        absf = os.path.normpath(os.path.join(entry.get("directory", ""), f))
        index[absf] = _detect_lang(args, os.path.splitext(f)[1])
    return index


def compdb_covers(filename: str | None, language: str) -> bool | None:
    """Whether the tree's compile database has an exact entry for ``filename``.

    ``True``  — covered: the parse gets the build's exact flags (incl. ``-D`` defines).
    ``False`` — a config with a ``compdb`` applies but has NO entry for this file, so
                the parse falls back to degraded flags and code inside inactive
                ``#ifdef`` branches (gated on build ``-D`` defines) is silently missed.
    ``None``  — no applicable config / no ``compdb`` (nothing to warn about here).
    """
    if not filename:
        return None
    config_path = _find_config(filename)
    if config_path is None:
        return None
    lang_cfg = _load(config_path).get(language)
    if not isinstance(lang_cfg, dict) or not isinstance(lang_cfg.get("compdb"), str):
        return None
    key = os.path.normpath(os.path.abspath(filename))
    return key in _load_compdb(lang_cfg["compdb"])


def compdb_language_for(filename: str | None) -> str | None:
    """The language a tree's compile database recorded for ``filename`` (``"c"``
    or ``"cpp"``), or ``None`` if no compdb covers it. Lets the dispatcher route
    a ``.c`` that was actually compiled as C++ to the C++ backend."""
    if not filename:
        return None
    config_path = _find_config(filename)
    if config_path is None:
        return None
    cfg = _load(config_path)
    key = os.path.normpath(os.path.abspath(filename))
    for lang in ("c", "cpp"):
        lc = cfg.get(lang)
        if isinstance(lc, dict) and isinstance(lc.get("compdb"), str):
            detected = _compdb_lang_index(lc["compdb"]).get(key)
            if detected:
                return detected
    return None


def tree_flags_for(filename: str | None, language: str) -> list[str] | None:
    """libclang args for ``filename`` from the discovered config, or ``None`` when
    no config applies. Prefers the compile_commands.json entry; falls back to
    static ``cflags``; always appends the toolchain predefs + a ``--target``."""
    if not filename:
        return None
    config_path = _find_config(filename)
    if config_path is None:
        return None
    lang_cfg = _load(config_path).get(language)
    if not isinstance(lang_cfg, dict):
        return None

    flags: list[str] | None = None

    compdb = lang_cfg.get("compdb")
    if isinstance(compdb, str):
        entry = _load_compdb(compdb).get(os.path.normpath(os.path.abspath(filename)))
        if entry is not None:
            flags = list(entry)

    if flags is None:
        flags = list(lang_cfg.get("cflags", []))
        if not flags and not lang_cfg.get("predef_cc"):
            return None

    # A few compile entries carry no --sysroot (e.g. NetBSD's rump *_user.c
    # hypercall shims): without one, <sys/cdefs.h> & friends aren't found. Fall
    # back to the config's default sysroot so those still resolve.
    if not any(a.startswith("--sysroot") for a in flags):
        default_sysroot = lang_cfg.get("sysroot")
        if isinstance(default_sysroot, str) and default_sysroot:
            flags.append(f"--sysroot={default_sysroot}")

    # clang's own builtin headers (stdatomic.h, stddef.h, intrinsics) FIRST, so
    # libstdc++'s `#include_next <stddef.h>` chains onto clang's, not gcc's — the
    # pip libclang wheel doesn't ship these, so add a real resource dir's include.
    flags += list(_clang_builtin_include())

    predef_cc = lang_cfg.get("predef_cc")
    if isinstance(predef_cc, list) and predef_cc:
        # the cross compiler's implicit system include dirs (libstdc++, target
        # headers, fixed-includes) — after clang's builtins so builtins win.
        # Use the build's sysroot so gcc doesn't fall back to host /usr/include.
        #
        # NOTE: we deliberately do NOT harvest gcc's *predefined macros* here.
        # Feeding gcc's __GNUC__/__ATOMIC_*/__restrict macros into a clang parse
        # makes headers take their gcc-builtin branch, which clang then rejects
        # (e.g. openssl refcount.h's __atomic_fetch_* on _Atomic ptrs, cdefs.h's
        # __restrict in C++). clang defines its own self-consistent target macros
        # from --target, which match its own builtins. predef_cc is used only to
        # locate the toolchain's include dirs.
        sysroot = ""
        for a in flags:
            if a.startswith("--sysroot="):
                sysroot = a[len("--sysroot="):]
        lang_x = "c++" if language == "cpp" else "c"
        flags += list(_harvest_include_dirs(tuple(predef_cc), lang_x, sysroot))

    target = lang_cfg.get("target", "riscv64-unknown-netbsd")

    # Add ONLY the gcc compatibility macros clang lacks (e.g. __WCHAR_MIN__,
    # __SIG_ATOMIC_TYPE__) that NetBSD headers require — never the ones clang
    # already defines, which would re-create the gcc-branch conflicts above.
    if isinstance(predef_cc, list) and predef_cc:
        flags += list(_missing_predefs(tuple(predef_cc), target))

    if not any(a.startswith(("--target", "-target")) for a in flags):
        flags.append(f"--target={target}")

    return flags
