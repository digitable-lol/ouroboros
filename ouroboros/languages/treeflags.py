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

In both cases the target compiler's include dirs are added (harvested from the
compiler itself, see ``toolchain.py``) so the parse matches the real toolchain,
and ``--target`` is added if absent (host libclang must be told the target).

Config schema (per language key, e.g. "c")::

    {
      "c": {
        "compdb":    "/home/u/netbsd/obj/compile_commands.json",
        "cflags":    ["-std=gnu11", "--sysroot=...", "-I...", "-D..."],
        "predef_cc": ["/.../riscv64--netbsd-gcc", "-std=gnu11"],
        "sysroot":   "/home/u/netbsd/dest",
        "target":    "riscv64-unknown-netbsd"
      }
    }

Two rules shape the layout of this file.

**Core and edge are separated, and the separation is the point.** Everything
above the "edge" banner is pure: values in, values out. It opens no file and
starts no program, so every branch of it is reachable from a dict written in a
test. Below the banner sit the few functions that must read a config, index a
compile database, or run a compiler.

**Nothing is cached invisibly.** What the edge reads is held in an explicit
`TreeSnapshot` that the caller can hold, pass and drop. The nine ``lru_cache``
decorators this replaces made an edited ``.ouroboros.json`` take no effect until
the process was restarted, and made two tests in the same process see each
other's answers, so the order they ran in changed the result.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import toolchain

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

_CXX_EXTS = (".cc", ".cpp", ".cxx", ".c++", ".hpp", ".hh", ".hxx")

#: target assumed when a config names none.
DEFAULT_TARGET = "riscv64-unknown-netbsd"

#: per-language config keys and the JSON type each must have.
_CONFIG_TYPES: tuple[tuple[str, type], ...] = (
    ("compdb", str), ("sysroot", str), ("target", str),
    ("cflags", list), ("predef_cc", list),
)


class TreeConfigError(Exception):
    """A tree's settings file, or the compile database it names, cannot be used.

    Raised rather than quietly falling back to an empty config. That fallback
    was how trees lost code: with no settings the file is parsed with the *host*
    compiler's flags instead of the build's, every ``#ifdef`` gated on a build
    ``-D`` takes the other branch, and the functions inside those branches are
    never seen — so they are missing from the instrumented output, with nothing
    printed to say so. A broken config has to stop the run, not shrink it.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


# --------------------------------------------------------------------------- #
# core — pure. No file is opened and no program is run below this line.
# --------------------------------------------------------------------------- #

def entry_args(entry: dict[str, Any]) -> list[str] | None:
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


def _abspath(path: str, directory: str) -> str:
    """Resolve ``path`` against the compile entry's ``directory`` (where the
    build actually ran), so relative ``-I.``/``-I../x`` point at the obj dir
    holding generated headers (nodes.h, opt_*.h, menu_defs.h)."""
    if not path or os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(directory, path))


def ast_args(arguments: list[str], directory: str = "") -> list[str]:
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


def detect_lang(arguments: list[str], ext: str = "") -> str:
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
        if "++" in comp or comp.endswith("CC"):
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


@dataclass(frozen=True)
class CompDb:
    """One compile database, indexed by absolute source path.

    Both maps are built in a single pass over the entries: the flag lookup and
    the language lookup used to parse and walk the same JSON file twice.
    """

    args: dict[str, tuple[str, ...]] = field(default_factory=dict)
    languages: dict[str, str] = field(default_factory=dict)


def index_compdb(data: Any, path: str = "<compdb>") -> CompDb:
    """Index a parsed compile_commands.json.

    The document as a whole must be a JSON array; anything else means this is
    not a compile database at all, and going on without one would hand every
    file in the tree the host compiler's flags — so it raises. *Entries* are
    treated more gently: one with no ``file``, or with neither ``arguments`` nor
    ``command``, is skipped, because a database that does not cover every file
    is a normal state and `compdb_covers` already reports the files it misses.
    """
    if not isinstance(data, list):
        raise TreeConfigError(
            path, f"expected a JSON array of compile commands, got {type(data).__name__}")
    db = CompDb()
    for pos, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise TreeConfigError(
                path, f"entry {pos} is a {type(entry).__name__}, not an object")
        f = entry.get("file")
        args = entry_args(entry)
        directory = entry.get("directory", "")
        if not isinstance(f, str) or not f or args is None \
                or not isinstance(directory, str):
            continue
        absf = os.path.normpath(os.path.join(directory, f))
        db.args[absf] = tuple(ast_args(args, directory))
        db.languages[absf] = detect_lang(args, os.path.splitext(f)[1])
    return db


def language_config(config: dict[str, Any], language: str,
                    path: str = CONFIG_NAME) -> dict[str, Any] | None:
    """The per-language block of a settings file, checked over.

    ``None`` means the file simply says nothing about this language — a config
    with only a ``"cpp"`` key has nothing to offer a ``.c`` file, and that is
    not an error. An exception means the file *does* say something and what it
    says is unusable, which must not be read as "no config".
    """
    if language not in config:
        return None
    block = config[language]
    if not isinstance(block, dict):
        raise TreeConfigError(
            path, f'"{language}" must be an object, got {type(block).__name__}')
    for key, want in _CONFIG_TYPES:
        if key in block and not isinstance(block[key], want):
            raise TreeConfigError(
                path,
                f'"{language}.{key}" must be a JSON {want.__name__}, '
                f"got {type(block[key]).__name__}")
    return block


def base_flags(lang_cfg: dict[str, Any],
               compdb_args: tuple[str, ...] | None) -> list[str] | None:
    """The tree's own arguments for one file, before the toolchain's are added.

    The compile database entry wins when there is one; otherwise the config's
    static ``cflags``. ``None`` when the config holds nothing that could shape a
    parse at all, which is the caller's signal to use its self-contained
    defaults instead.

    A few compile entries carry no ``--sysroot`` (NetBSD's rump ``*_user.c``
    hypercall shims, for one): without one, ``<sys/cdefs.h>`` and friends are
    not found, so the config's default sysroot is filled in.
    """
    if compdb_args is not None:
        flags = list(compdb_args)
    else:
        flags = list(lang_cfg.get("cflags", []))
        if not flags and not lang_cfg.get("predef_cc"):
            return None
    if not any(a.startswith("--sysroot") for a in flags):
        default_sysroot = lang_cfg.get("sysroot")
        if default_sysroot:
            flags.append(f"--sysroot={default_sysroot}")
    return flags


def sysroot_of(flags: list[str]) -> str:
    """The sysroot an argument list settles on — the last ``--sysroot=`` wins,
    as it does on a real command line. ``""`` when there is none."""
    sysroot = ""
    for a in flags:
        if a.startswith("--sysroot="):
            sysroot = a[len("--sysroot="):]
    return sysroot


def extra_predefs(gcc_defs: Sequence[str], clang_names: frozenset[str]
                  ) -> tuple[str, ...]:
    """The gcc predefined macros clang does NOT define for the target — the
    compatibility macros NetBSD headers need (``__WCHAR_MIN__``, ``__WINT_MIN__``,
    ``__SIG_ATOMIC_TYPE__``...).

    Only the *difference* is added. Macros clang already defines keep clang's
    own self-consistent values, so a header is not dragged onto its gcc-builtin
    branch — the cause of the ``_Atomic``/``__restrict`` breakage when the full
    gcc dump was injected. An empty ``clang_names`` means clang could not be
    asked, which is "unknown", not "clang defines nothing": add nothing.
    """
    if not clang_names:
        return ()
    return tuple(d for d in gcc_defs
                 if d[2:].split("=", 1)[0] not in clang_names)


def finish_flags(flags: list[str], *, target: str,
                 builtin_include: Sequence[str] = (),
                 include_dirs: Sequence[str] = (),
                 predefs: Sequence[str] = ()) -> list[str]:
    """Assemble the final libclang argument list.

    Order matters. clang's own builtin headers come first, so libstdc++'s
    ``#include_next <stddef.h>`` chains onto clang's header and not gcc's; then
    the toolchain's implicit include dirs; then the gcc compatibility macros;
    then ``--target``, unless the tree already named one.
    """
    out = [*flags, *builtin_include, *include_dirs, *predefs]
    if not any(a.startswith(("--target", "-target")) for a in out):
        out.append(f"--target={target}")
    return out


# --------------------------------------------------------------------------- #
# edge — reads files, runs compilers. Everything it learns lands in a snapshot.
# --------------------------------------------------------------------------- #

#: what a file looked like when we read it. Cheap enough to take on every
#: lookup, and strong enough for the case that matters: an operator fixing a
#: config while the run is going. ``st_ctime_ns`` moves on any write, including
#: one that leaves the size alone and one whose mtime was set back by hand.
#: Two writes inside the same nanosecond that leave the size and inode
#: unchanged would still slip past — the same bound every mtime-based tool has.
Stamp = tuple[int, int, int, int]  #: (mtime_ns, ctime_ns, size, inode)


class Probe(Protocol):
    """The compiler questions a snapshot asks. `toolchain` satisfies it; a test
    passes a stand-in so the orchestration can be checked without a toolchain."""

    def clang_builtin_include(self) -> tuple[str, ...]: ...

    def include_search_dirs(self, cc_cmd: Sequence[str], lang_x: str,
                            sysroot: str = "") -> tuple[str, ...]: ...

    def predef_macros(self, cc_cmd: Sequence[str]) -> tuple[str, ...]: ...

    def clang_macro_names(self, target: str) -> frozenset[str]: ...


def _stamp(path: str) -> Stamp | None:
    """How a file looks right now, or ``None`` if it cannot be stat'ed (which is
    itself a change worth noticing: the config was deleted)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


def read_json(path: str) -> Any:
    """Parse a JSON file, or say why not.

    Both failures — unreadable and unparseable — used to return an empty dict.
    See `TreeConfigError` for what that cost.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise TreeConfigError(path, f"cannot be read ({e.strerror or e})") from e
    try:
        return json.loads(text)
    except ValueError as e:
        raise TreeConfigError(path, f"is not valid JSON ({e})") from e


def find_config(start: str) -> str | None:
    """The nearest ``.ouroboros.json`` at or above ``start``, clangd-style."""
    p = Path(start).resolve()
    for d in (p, *p.parents):
        candidate = d / CONFIG_NAME
        if candidate.is_file():
            return str(candidate)
    return None


class TreeSnapshot:
    """Everything one run has read from disk and asked of the compilers.

    Explicit on purpose. The nine ``lru_cache`` decorators this replaces were
    invisible: no caller could refresh them after fixing a config, and two tests
    in one process shared them, so the order the tests ran in changed their
    answers. A snapshot is made, passed and dropped by whoever owns the run, and
    a test makes its own.

    Every config and compile database is remembered together with its
    `Stamp`; `recheck` forgets the ones whose file changed underneath,
    so editing an ``.ouroboros.json`` mid-run takes effect on the next lookup.
    Which config applies to a directory is settled once per snapshot: a config
    file *appearing* or *disappearing* mid-run needs a fresh snapshot, not a
    recheck.

    Compiler answers are keyed by the exact command asked, and are not
    rechecked — a toolchain binary does not change under a running instrument.
    """

    def __init__(self, probe: Probe | None = None) -> None:
        self._probe: Probe = probe if probe is not None else toolchain
        self._config_of: dict[str, str | None] = {}
        self._configs: dict[str, tuple[Stamp | None, dict[str, Any]]] = {}
        self._compdbs: dict[str, tuple[Stamp | None, CompDb]] = {}
        self._builtin: tuple[str, ...] | None = None
        self._include_dirs: dict[tuple[tuple[str, ...], str, str], tuple[str, ...]] = {}
        self._predefs: dict[tuple[tuple[str, ...], str], tuple[str, ...]] = {}

    # -- files ------------------------------------------------------------- #

    def recheck(self) -> None:
        """Forget every config and compile database whose file changed on disk."""
        for path in [p for p, (s, _) in self._configs.items() if _stamp(p) != s]:
            del self._configs[path]
        for path in [p for p, (s, _) in self._compdbs.items() if _stamp(p) != s]:
            del self._compdbs[path]

    def config_path_for(self, filename: str) -> str | None:
        """The config that governs ``filename``, searched once per directory."""
        key = os.path.dirname(filename) or "."
        if key not in self._config_of:
            self._config_of[key] = find_config(key)
        return self._config_of[key]

    def config(self, path: str) -> dict[str, Any]:
        """The parsed settings file at ``path``. Raises if it is unusable."""
        hit = self._configs.get(path)
        if hit is None:
            stamp = _stamp(path)
            data = read_json(path)
            if not isinstance(data, dict):
                raise TreeConfigError(
                    path, f"must be a JSON object, got {type(data).__name__}")
            hit = (stamp, data)
            self._configs[path] = hit
        return hit[1]

    def compdb(self, path: str) -> CompDb:
        """The indexed compile database at ``path``. Raises if it is unusable."""
        hit = self._compdbs.get(path)
        if hit is None:
            stamp = _stamp(path)
            hit = (stamp, index_compdb(read_json(path), path))
            self._compdbs[path] = hit
        return hit[1]

    # -- compilers --------------------------------------------------------- #

    def builtin_include(self) -> tuple[str, ...]:
        if self._builtin is None:
            self._builtin = self._probe.clang_builtin_include()
        return self._builtin

    def include_dirs(self, cc_cmd: Sequence[str], lang_x: str,
                     sysroot: str) -> tuple[str, ...]:
        key = (tuple(cc_cmd), lang_x, sysroot)
        if key not in self._include_dirs:
            self._include_dirs[key] = self._probe.include_search_dirs(
                cc_cmd, lang_x, sysroot)
        return self._include_dirs[key]

    def missing_predefs(self, cc_cmd: Sequence[str], target: str) -> tuple[str, ...]:
        key = (tuple(cc_cmd), target)
        if key not in self._predefs:
            self._predefs[key] = extra_predefs(
                self._probe.predef_macros(cc_cmd),
                self._probe.clang_macro_names(target))
        return self._predefs[key]

    # -- the three questions callers ask ------------------------------------ #

    def language_block(self, filename: str, language: str) -> dict[str, Any] | None:
        """The validated per-language config governing ``filename``, or ``None``
        when no config applies or it says nothing about this language."""
        config_path = self.config_path_for(filename)
        if config_path is None:
            return None
        return language_config(self.config(config_path), language, config_path)


_snapshot: TreeSnapshot | None = None


def current_snapshot() -> TreeSnapshot:
    """The snapshot the module-level helpers use, made on first need."""
    global _snapshot
    if _snapshot is None:
        _snapshot = TreeSnapshot()
    return _snapshot


def set_snapshot(snapshot: TreeSnapshot | None) -> TreeSnapshot | None:
    """Install ``snapshot`` (``None`` to drop the current one) and return the
    one it replaced, so a caller can put it back."""
    global _snapshot
    previous = _snapshot
    _snapshot = snapshot
    return previous


def _snap(snapshot: TreeSnapshot | None) -> TreeSnapshot:
    """The snapshot to use for one lookup, brought up to date with the disk."""
    snap = current_snapshot() if snapshot is None else snapshot
    snap.recheck()
    return snap


def _key(filename: str) -> str:
    return os.path.normpath(os.path.abspath(filename))


def compdb_covers(filename: str | None, language: str, *,
                  snapshot: TreeSnapshot | None = None) -> bool | None:
    """Whether the tree's compile database has an exact entry for ``filename``.

    ``True``  — covered: the parse gets the build's exact flags (incl. ``-D`` defines).
    ``False`` — a config with a ``compdb`` applies but has NO entry for this file, so
                the parse falls back to degraded flags and code inside inactive
                ``#ifdef`` branches (gated on build ``-D`` defines) is silently missed.
    ``None``  — no applicable config / no ``compdb`` (nothing to warn about here).
    """
    if not filename:
        return None
    snap = _snap(snapshot)
    lang_cfg = snap.language_block(filename, language)
    if lang_cfg is None or "compdb" not in lang_cfg:
        return None
    return _key(filename) in snap.compdb(lang_cfg["compdb"]).args


def compdb_language_for(filename: str | None, *,
                        snapshot: TreeSnapshot | None = None) -> str | None:
    """The language a tree's compile database recorded for ``filename`` (``"c"``
    or ``"cpp"``), or ``None`` if no compdb covers it. Lets the dispatcher route
    a ``.c`` that was actually compiled as C++ to the C++ backend."""
    if not filename:
        return None
    snap = _snap(snapshot)
    key = _key(filename)
    for language in ("c", "cpp"):
        lang_cfg = snap.language_block(filename, language)
        if lang_cfg is None or "compdb" not in lang_cfg:
            continue
        detected = snap.compdb(lang_cfg["compdb"]).languages.get(key)
        if detected:
            return detected
    return None


def tree_flags_for(filename: str | None, language: str, *,
                   snapshot: TreeSnapshot | None = None) -> list[str] | None:
    """libclang args for ``filename`` from the discovered config, or ``None`` when
    no config applies. Prefers the compile_commands.json entry; falls back to
    static ``cflags``; always appends the toolchain's include dirs + a ``--target``."""
    if not filename:
        return None
    snap = _snap(snapshot)
    lang_cfg = snap.language_block(filename, language)
    if lang_cfg is None:
        return None

    compdb_args = None
    if "compdb" in lang_cfg:
        compdb_args = snap.compdb(lang_cfg["compdb"]).args.get(_key(filename))

    flags = base_flags(lang_cfg, compdb_args)
    if flags is None:
        return None

    target = lang_cfg.get("target") or DEFAULT_TARGET
    predef_cc = lang_cfg.get("predef_cc") or []
    include_dirs: tuple[str, ...] = ()
    predefs: tuple[str, ...] = ()
    if predef_cc:
        # The cross compiler's implicit system include dirs (libstdc++, target
        # headers, fixed-includes). Its sysroot must be the build's, or gcc
        # falls back to the host /usr/include and poisons the parse.
        #
        # NOTE: we deliberately do NOT take gcc's predefined macros wholesale.
        # Feeding gcc's __GNUC__/__ATOMIC_*/__restrict into a clang parse makes
        # headers take their gcc-builtin branch, which clang then rejects
        # (openssl's refcount.h __atomic_fetch_* on _Atomic ptrs, cdefs.h's
        # __restrict in C++). Only the macros clang lacks are added.
        lang_x = "c++" if language == "cpp" else "c"
        include_dirs = snap.include_dirs(predef_cc, lang_x, sysroot_of(flags))
        predefs = snap.missing_predefs(predef_cc, target)

    return finish_flags(flags, target=target,
                        builtin_include=snap.builtin_include(),
                        include_dirs=include_dirs, predefs=predefs)
