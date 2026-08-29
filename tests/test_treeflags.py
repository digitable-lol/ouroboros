"""Tests for `ouroboros.languages.treeflags`.

The core — everything that turns already-read values into an argument list — is
exercised with plain dicts and lists, so every branch of it is reachable without
a source tree, a compile database on disk, or a compiler.

The edge is exercised with real files under pytest's tmp dir (cheap and honest:
the point of those functions is that they read files) and with a stand-in for
the compiler probe, so the orchestration around the toolchain is checked without
depending on which gcc the machine carries.

Nothing here shares state with anything else: each test that needs a snapshot
makes its own. That is the property the nine module-level `lru_cache`s destroyed.
"""

from __future__ import annotations

import json
import os

import pytest

from ouroboros.languages import treeflags as tf
from ouroboros.languages.treeflags import TreeConfigError, TreeSnapshot


class FakeProbe:
    """A toolchain that answers from a script instead of from a compiler, and
    counts how many times it was asked — the caches used to hide exactly this."""

    def __init__(self, builtin=("-isystem", "/rt/include"),
                 dirs=("-isystem", "/tc/include"), predefs=("-D__GNUC__=13",),
                 clang_names=frozenset({"__STDC__"})):
        self._builtin, self._dirs = builtin, dirs
        self._predefs, self._clang_names = predefs, clang_names
        self.calls: list[tuple] = []

    def clang_builtin_include(self):
        self.calls.append(("builtin",))
        return self._builtin

    def include_search_dirs(self, cc_cmd, lang_x, sysroot=""):
        self.calls.append(("dirs", tuple(cc_cmd), lang_x, sysroot))
        return self._dirs

    def predef_macros(self, cc_cmd):
        self.calls.append(("predefs", tuple(cc_cmd)))
        return self._predefs

    def clang_macro_names(self, target):
        self.calls.append(("names", target))
        return self._clang_names


def write_tree(root, config, entries=None):
    """A minimal tree: a source file, an .ouroboros.json, and optionally a
    compile database the config points at. Returns the source path."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    f = src / "m.c"
    f.write_text("int f(void){return 0;}\n", encoding="utf-8")
    if entries is not None:
        (root / "compile_commands.json").write_text(json.dumps(entries), encoding="utf-8")
    (root / ".ouroboros.json").write_text(json.dumps(config), encoding="utf-8")
    return str(f)


# =========================================================================== #
# core — entry_args
# =========================================================================== #

def test_entry_args_prefer_the_arguments_list():
    assert tf.entry_args({"arguments": ["cc", "-c", "a.c"], "command": "ignored"}) \
        == ["cc", "-c", "a.c"]


def test_entry_args_split_a_command_string():
    """Builds that emit only `command` are legal; not splitting them would make
    every entry in such a tree invisible and degrade the whole tree."""
    assert tf.entry_args({"command": "cc -DA='b c' -c a.c"}) \
        == ["cc", "-DA=b c", "-c", "a.c"]


def test_entry_args_none_when_the_entry_carries_neither():
    assert tf.entry_args({"file": "a.c"}) is None
    assert tf.entry_args({"arguments": "not a list", "command": 7}) is None


# =========================================================================== #
# core — ast_args
# =========================================================================== #

def test_ast_args_drop_the_compiler_and_the_source():
    assert tf.ast_args(["cc", "-DA", "m.c"]) == ["-DA"]


def test_ast_args_drop_an_output_flag_with_its_argument():
    """`-o m.o` left in makes libclang write an object file instead of parsing."""
    assert tf.ast_args(["cc", "-o", "m.o", "-DA"]) == ["-DA"]


def test_ast_args_drop_dependency_generation():
    """A stray -MD makes libclang print a makefile rule to stdout."""
    assert tf.ast_args(["cc", "-MD", "-MF", "m.d", "-MT", "m.o", "-DA"]) == ["-DA"]


def test_ast_args_drop_warnings_optimisation_and_debug():
    assert tf.ast_args(["cc", "-Wall", "-Werror", "-O2", "-g3", "-pipe", "-c", "-DA"]) \
        == ["-DA"]


def test_ast_args_drop_codegen_f_flags_but_keep_the_parsing_ones():
    """gcc-only -f flags are a hard error for clang, so the allow-list is not an
    optimisation — dropping the wrong one fails the parse outright."""
    got = tf.ast_args(["cc", "-fno-ipa-icf", "-fbuilding-libgcc", "-ffreestanding",
                       "-fno-builtin-printf", "-fexec-charset=UTF-8"])
    assert got == ["-ffreestanding", "-fno-builtin-printf", "-fexec-charset=UTF-8"]


def test_ast_args_resolve_a_separate_include_path_against_the_build_directory():
    """`-I .` in the obj dir is where the build's generated headers are; left
    relative it resolves against wherever the instrument happens to run."""
    assert tf.ast_args(["cc", "-I", "../src", "-isystem", "/abs"], "/build/obj") \
        == ["-I", "/build/src", "-isystem", "/abs"]


def test_ast_args_resolve_a_joined_include_path_against_the_build_directory():
    assert tf.ast_args(["cc", "-I.", "--sysroot=rel", "-I/abs"], "/build/obj") \
        == ["-I/build/obj", "--sysroot=/build/obj/rel", "-I/abs"]


def test_ast_args_keep_a_trailing_path_flag_that_has_no_argument():
    """A truncated command line must not swallow the flag silently or crash."""
    assert tf.ast_args(["cc", "-DA", "-I"]) == ["-DA", "-I"]


def test_ast_args_keep_plain_arguments_untouched():
    assert tf.ast_args(["cc", "-std=gnu11", "-DHAVE_X=1", "-nostdinc"]) \
        == ["-std=gnu11", "-DHAVE_X=1", "-nostdinc"]


def test_ast_args_drop_every_source_extension_it_knows():
    args = ["cc", "a.c", "b.cc", "c.cpp", "d.cxx", "e.c++", "f.S", "g.s", "-DA"]
    assert tf.ast_args(args) == ["-DA"]


# =========================================================================== #
# core — detect_lang
# =========================================================================== #

def test_detect_lang_a_definitive_cxx_extension_wins():
    """gcc compiles its own .cc files with the *gcc* driver and no -x c++, so
    the extension is the only evidence there is."""
    assert tf.detect_lang(["gcc", "-c", "x.cc"], ".cc") == "cpp"
    assert tf.detect_lang(["gcc"], ".HPP") == "cpp"


def test_detect_lang_from_the_driver_name():
    assert tf.detect_lang(["g++", "-c", "x.c"]) == "cpp"
    assert tf.detect_lang(["/opt/tc/bin/clang++"]) == "cpp"
    assert tf.detect_lang(["/opt/sun/bin/CC"]) == "cpp"


def test_detect_lang_from_a_separate_x_flag():
    assert tf.detect_lang(["cc", "-x", "c++", "x.c"]) == "cpp"
    assert tf.detect_lang(["cc", "-x", "c", "x.c"]) == "c"


def test_detect_lang_from_a_joined_x_flag():
    assert tf.detect_lang(["cc", "-xc++"]) == "cpp"
    assert tf.detect_lang(["cc", "-xc"]) == "c"


def test_detect_lang_from_the_std_flag():
    """gdb compiles .c files as C++ with nothing but -std=gnu++17 to say so."""
    assert tf.detect_lang(["cc", "-std=gnu++17", "x.c"]) == "cpp"


def test_detect_lang_defaults_to_c():
    assert tf.detect_lang([]) == "c"
    assert tf.detect_lang(["cc", "-std=gnu11", "x.c"]) == "c"


# =========================================================================== #
# core — index_compdb
# =========================================================================== #

def test_index_compdb_records_flags_and_language_per_absolute_path():
    db = tf.index_compdb([{"directory": "/b", "file": "s/m.c",
                           "arguments": ["cc", "-DA", "-c", "s/m.c"]}])
    assert db.args == {"/b/s/m.c": ("-DA",)}
    assert db.languages == {"/b/s/m.c": "c"}


def test_index_compdb_rejects_a_document_that_is_not_an_array():
    """A compile database that is not an array is not a compile database. Going
    on with an empty one hands every file in the tree the host's flags, and the
    functions behind the build's -D defines vanish from the output."""
    with pytest.raises(TreeConfigError) as e:
        tf.index_compdb({"not": "a list"}, "/t/compile_commands.json")
    assert "/t/compile_commands.json" in str(e.value)
    assert "dict" in str(e.value)


def test_index_compdb_rejects_an_entry_that_is_not_an_object():
    with pytest.raises(TreeConfigError) as e:
        tf.index_compdb([{"file": "a.c", "arguments": ["cc"]}, "junk"], "/t/db.json")
    assert "entry 1" in str(e.value)


def test_index_compdb_skips_entries_it_cannot_use():
    """Unusable *entries*, unlike an unusable file, are normal — a database need
    not cover every file, and `compdb_covers` reports the ones it misses."""
    db = tf.index_compdb([
        {"file": "", "arguments": ["cc"]},                 # no file
        {"file": 7, "arguments": ["cc"]},                  # file is not a path
        {"file": "a.c"},                                   # no arguments, no command
        {"file": "b.c", "directory": 5, "arguments": ["cc"]},  # directory is not a path
        {"file": "/ok.c", "arguments": ["cc", "-DA"]},
    ], "/t/db.json")
    assert list(db.args) == ["/ok.c"]


def test_index_compdb_builds_both_maps_in_one_pass():
    """Flags and language come from the same walk; the file used to be parsed
    and walked twice, once for each map."""
    db = tf.index_compdb([{"directory": "/b", "file": "x.c",
                           "arguments": ["g++", "-DA", "x.c"]}])
    assert db.args == {"/b/x.c": ("-DA",)}
    assert db.languages == {"/b/x.c": "cpp"}


# =========================================================================== #
# core — language_config
# =========================================================================== #

def test_language_config_returns_none_when_the_file_says_nothing_for_it():
    """A C++-only config has nothing to offer a .c file, and that is not an
    error — the backend falls back to its self-contained defaults."""
    assert tf.language_config({"cpp": {"compdb": "/x"}}, "c") is None


def test_language_config_rejects_a_block_that_is_not_an_object():
    with pytest.raises(TreeConfigError) as e:
        tf.language_config({"c": ["-DA"]}, "c", "/t/.ouroboros.json")
    assert '"c" must be an object' in str(e.value)
    assert "/t/.ouroboros.json" in str(e.value)


@pytest.mark.parametrize(("key", "bad", "want"), [
    ("compdb", ["/a"], "str"),
    ("sysroot", 7, "str"),
    ("target", {"a": 1}, "str"),
    ("cflags", "-DA", "list"),
    ("predef_cc", "gcc", "list"),
])
def test_language_config_rejects_a_key_of_the_wrong_type(key, bad, want):
    """`"cflags": "-DA"` used to be accepted and then iterated character by
    character, producing the flags -, D, A. Loud beats creative."""
    with pytest.raises(TreeConfigError) as e:
        tf.language_config({"c": {key: bad}}, "c", "/t/.ouroboros.json")
    assert f'"c.{key}" must be a JSON {want}' in str(e.value)


def test_language_config_accepts_a_well_formed_block():
    block = {"compdb": "/db.json", "cflags": ["-DA"], "predef_cc": ["gcc"],
             "sysroot": "/dest", "target": "riscv64-unknown-netbsd"}
    assert tf.language_config({"c": block}, "c") == block


# =========================================================================== #
# core — base_flags / sysroot_of / extra_predefs / finish_flags
# =========================================================================== #

def test_base_flags_prefer_the_compile_database_entry():
    assert tf.base_flags({"cflags": ["-DFALLBACK"]}, ("-DEXACT",)) == ["-DEXACT"]


def test_base_flags_fall_back_to_static_cflags():
    assert tf.base_flags({"cflags": ["-DFALLBACK"]}, None) == ["-DFALLBACK"]


def test_base_flags_accept_an_empty_compile_database_entry():
    """An entry with nothing left after filtering is still an entry: the file IS
    covered, so the static cflags must not quietly replace it."""
    assert tf.base_flags({"cflags": ["-DFALLBACK"]}, ()) == []


def test_base_flags_none_when_the_config_shapes_nothing():
    """No cflags and no compiler to ask means the config has nothing to say, and
    the caller must use its self-contained defaults rather than an empty list."""
    assert tf.base_flags({}, None) is None
    assert tf.base_flags({"cflags": []}, None) is None


def test_base_flags_survive_on_predef_cc_alone():
    """A config that only names the toolchain still wants its include dirs."""
    assert tf.base_flags({"predef_cc": ["gcc"]}, None) == []


def test_base_flags_fill_in_the_default_sysroot():
    """NetBSD's rump hypercall shims are compiled with no --sysroot; without one
    <sys/cdefs.h> is not found and the file will not parse at all."""
    assert tf.base_flags({"cflags": ["-DA"], "sysroot": "/dest"}, None) \
        == ["-DA", "--sysroot=/dest"]


def test_base_flags_do_not_override_the_entrys_own_sysroot():
    assert tf.base_flags({"sysroot": "/dest"}, ("--sysroot=/build",)) \
        == ["--sysroot=/build"]


def test_base_flags_ignore_an_empty_default_sysroot():
    assert tf.base_flags({"cflags": ["-DA"], "sysroot": ""}, None) == ["-DA"]


def test_sysroot_of_takes_the_last_one_as_a_command_line_would():
    assert tf.sysroot_of(["--sysroot=/a", "-DX", "--sysroot=/b"]) == "/b"
    assert tf.sysroot_of(["-DX"]) == ""


def test_extra_predefs_add_only_what_clang_lacks():
    """Feeding clang gcc's own __GNUC__ sends headers down their gcc-builtin
    branch, which clang then rejects. Only the gap gets filled."""
    got = tf.extra_predefs(("-D__GNUC__=13", "-D__WCHAR_MIN__=(-2147483647-1)"),
                           frozenset({"__GNUC__"}))
    assert got == ("-D__WCHAR_MIN__=(-2147483647-1)",)


def test_extra_predefs_add_nothing_when_clang_could_not_be_asked():
    """An empty name set means 'unknown', not 'clang defines nothing'. Reading
    it the other way injects gcc's whole macro dump — the exact breakage the
    difference was introduced to avoid."""
    assert tf.extra_predefs(("-D__GNUC__=13",), frozenset()) == ()


def test_finish_flags_order_builtins_before_the_toolchains_headers():
    """libstdc++'s `#include_next <stddef.h>` has to land on clang's header, so
    clang's resource dir must precede the toolchain's include dirs."""
    got = tf.finish_flags(["-DA"], target="t", builtin_include=("-isystem", "/rt"),
                          include_dirs=("-isystem", "/tc"), predefs=("-D__X__",))
    assert got == ["-DA", "-isystem", "/rt", "-isystem", "/tc", "-D__X__", "--target=t"]


def test_finish_flags_add_a_target_because_host_libclang_has_no_idea():
    assert tf.finish_flags([], target="riscv64-unknown-netbsd") \
        == ["--target=riscv64-unknown-netbsd"]


@pytest.mark.parametrize("existing", ["--target=x", "-target"])
def test_finish_flags_leave_a_target_the_tree_already_chose(existing):
    assert tf.finish_flags([existing], target="other") == [existing]


# =========================================================================== #
# edge — reading files
# =========================================================================== #

def test_read_json_reports_a_file_it_cannot_read(tmp_path):
    with pytest.raises(TreeConfigError) as e:
        tf.read_json(str(tmp_path / "absent.json"))
    assert "cannot be read" in str(e.value)


def test_read_json_reports_a_file_that_is_not_json(tmp_path):
    """The failure this replaces: a half-written .ouroboros.json turned into an
    empty dict, the tree's settings were dropped, and the run carried on."""
    p = tmp_path / "broken.json"
    p.write_text('{"c": {"compdb": ', encoding="utf-8")
    with pytest.raises(TreeConfigError) as e:
        tf.read_json(str(p))
    assert "not valid JSON" in str(e.value)
    assert str(p) in str(e.value)


def test_find_config_walks_up_from_the_file(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    cfg = tmp_path / "a" / tf.CONFIG_NAME
    cfg.write_text("{}", encoding="utf-8")
    assert tf.find_config(str(deep / "m.c")) == str(cfg)


def test_find_config_is_none_when_no_tree_claims_the_file(tmp_path):
    assert tf.find_config(str(tmp_path / "orphan" / "m.c")) is None


def test_snapshot_rejects_a_config_that_is_not_an_object(tmp_path):
    p = tmp_path / tf.CONFIG_NAME
    p.write_text('["c"]', encoding="utf-8")
    with pytest.raises(TreeConfigError) as e:
        TreeSnapshot().config(str(p))
    assert "must be a JSON object" in str(e.value)


# =========================================================================== #
# edge — the snapshot itself
# =========================================================================== #

def test_snapshot_reads_each_file_once(tmp_path):
    """The reason the caches existed. Removing them must not reintroduce a read
    (or a compiler run) per file."""
    entries = []
    for i in range(3):
        f = tmp_path / "src" / f"m{i}.c"
        entries.append({"directory": str(tmp_path), "file": str(f),
                        "arguments": ["cc", f"-DN={i}", str(f)]})
    src = write_tree(tmp_path,
                     {"c": {"compdb": str(tmp_path / "compile_commands.json"),
                            "predef_cc": ["gcc"]}},
                     entries)
    probe = FakeProbe()
    snap = TreeSnapshot(probe)
    reads = []
    real = tf.read_json
    tf.read_json = lambda p: (reads.append(p), real(p))[1]
    try:
        for i in range(3):
            assert tf.tree_flags_for(str(tmp_path / "src" / f"m{i}.c"), "c",
                                     snapshot=snap) is not None
        tf.tree_flags_for(src, "c", snapshot=snap)
    finally:
        tf.read_json = real
    assert sorted(set(reads)) == sorted(reads), "a file was read twice"
    assert len(reads) == 2, reads          # the config and the compile database
    assert len(probe.calls) == 4, probe.calls  # builtin, dirs, predefs, names


def test_snapshot_picks_up_an_edited_config(tmp_path):
    """The reported bug: fixing .ouroboros.json while the process ran changed
    nothing, because nine lru_caches still held the old answer."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DOLD"], "target": "t"}})
    snap = TreeSnapshot(FakeProbe())
    assert "-DOLD" in (tf.tree_flags_for(src, "c", snapshot=snap) or [])

    (tmp_path / tf.CONFIG_NAME).write_text(
        json.dumps({"c": {"cflags": ["-DNEW"], "target": "t"}}), encoding="utf-8")
    os.utime(tmp_path / tf.CONFIG_NAME, ns=(0, 0))     # a clearly different stamp

    flags = tf.tree_flags_for(src, "c", snapshot=snap) or []
    assert "-DNEW" in flags and "-DOLD" not in flags


def test_snapshot_picks_up_an_edited_compile_database(tmp_path):
    """Same for the compile database: adding the missing file to it has to take
    effect, or an operator following the 'add it to the compile DB' advice sees
    no change until they restart."""
    src = write_tree(tmp_path, {"c": {"compdb": str(tmp_path / "compile_commands.json")}},
                     entries=[])
    snap = TreeSnapshot(FakeProbe())
    assert tf.compdb_covers(src, "c", snapshot=snap) is False

    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path), "file": src,
                     "arguments": ["cc", "-DADDED", src]}]), encoding="utf-8")
    os.utime(tmp_path / "compile_commands.json", ns=(0, 0))

    assert tf.compdb_covers(src, "c", snapshot=snap) is True
    assert "-DADDED" in (tf.tree_flags_for(src, "c", snapshot=snap) or [])


def test_snapshot_does_not_reread_an_untouched_config(tmp_path):
    """Rechecking is a stat, not a re-read: a large compile database must not be
    reparsed once per file."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"], "target": "t"}})
    snap = TreeSnapshot(FakeProbe())
    tf.tree_flags_for(src, "c", snapshot=snap)
    reads = []
    real = tf.read_json
    tf.read_json = lambda p: (reads.append(p), real(p))[1]
    try:
        for _ in range(5):
            tf.tree_flags_for(src, "c", snapshot=snap)
    finally:
        tf.read_json = real
    assert reads == []


def test_snapshot_reports_a_config_deleted_underneath_it(tmp_path):
    """Silence here would mean flags built from a file that is no longer there."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"], "target": "t"}})
    snap = TreeSnapshot(FakeProbe())
    tf.tree_flags_for(src, "c", snapshot=snap)
    (tmp_path / tf.CONFIG_NAME).unlink()
    with pytest.raises(TreeConfigError):
        tf.tree_flags_for(src, "c", snapshot=snap)


def test_two_snapshots_do_not_see_each_others_answers(tmp_path):
    """Test order used to change results, because every lookup went through
    module-level caches no test could reset."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"], "target": "t"}})
    first = TreeSnapshot(FakeProbe(builtin=("-isystem", "/first")))
    second = TreeSnapshot(FakeProbe(builtin=("-isystem", "/second")))
    assert "/first" in (tf.tree_flags_for(src, "c", snapshot=first) or [])
    assert "/second" in (tf.tree_flags_for(src, "c", snapshot=second) or [])


def test_the_module_level_snapshot_can_be_replaced_and_put_back(tmp_path):
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"], "target": "t"}})
    mine = TreeSnapshot(FakeProbe(builtin=("-isystem", "/mine")))
    previous = tf.set_snapshot(mine)
    try:
        assert tf.current_snapshot() is mine
        assert "/mine" in (tf.tree_flags_for(src, "c") or [])
    finally:
        tf.set_snapshot(previous)
    assert tf.current_snapshot() is not mine


def test_the_module_level_snapshot_is_created_on_first_use():
    assert tf.set_snapshot(None) is not None or True
    assert isinstance(tf.current_snapshot(), TreeSnapshot)


def test_snapshot_finds_the_config_once_per_directory(tmp_path):
    """A tree walk per file is the wasteful half of what the caches hid."""
    write_tree(tmp_path, {"c": {"cflags": ["-DA"], "target": "t"}})
    snap = TreeSnapshot(FakeProbe())
    walks = []
    real = tf.find_config
    tf.find_config = lambda s: (walks.append(s), real(s))[1]
    try:
        for i in range(4):
            snap.config_path_for(str(tmp_path / "src" / f"m{i}.c"))
    finally:
        tf.find_config = real
    assert len(walks) == 1


def test_snapshot_handles_a_bare_relative_filename(tmp_path, monkeypatch):
    """`wrap_source(filename="m.c")` has no directory at all; the search must
    start from the working directory rather than crash on an empty path."""
    write_tree(tmp_path, {"c": {"cflags": ["-DHERE"], "target": "t"}})
    monkeypatch.chdir(tmp_path / "src")
    snap = TreeSnapshot(FakeProbe())
    assert "-DHERE" in (tf.tree_flags_for("m.c", "c", snapshot=snap) or [])


def test_snapshot_asks_the_compiler_once_per_distinct_question(tmp_path):
    """Two languages in one tree share the toolchain but not the -x, so the
    include-dir probe must run twice and the macro probe once."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"], "predef_cc": ["xcc"], "target": "t"},
                                "cpp": {"cflags": ["-DA"], "predef_cc": ["xcc"], "target": "t"}})
    probe = FakeProbe()
    snap = TreeSnapshot(probe)
    for _ in range(2):
        tf.tree_flags_for(src, "c", snapshot=snap)
        tf.tree_flags_for(src, "cpp", snapshot=snap)
    dirs = [c for c in probe.calls if c[0] == "dirs"]
    assert [c[2] for c in dirs] == ["c", "c++"]
    assert len([c for c in probe.calls if c[0] == "predefs"]) == 1
    assert len([c for c in probe.calls if c[0] == "builtin"]) == 1


# =========================================================================== #
# edge — the three questions callers ask
# =========================================================================== #

def test_no_filename_means_no_tree():
    """`wrap_source` may be handed a buffer with no path at all."""
    snap = TreeSnapshot(FakeProbe())
    assert tf.tree_flags_for(None, "c", snapshot=snap) is None
    assert tf.compdb_covers(None, "c", snapshot=snap) is None
    assert tf.compdb_language_for(None, snapshot=snap) is None
    assert tf.tree_flags_for("", "c", snapshot=snap) is None


def test_a_file_under_no_tree_gets_no_tree_flags(tmp_path):
    snap = TreeSnapshot(FakeProbe())
    orphan = str(tmp_path / "orphan" / "m.c")
    assert tf.tree_flags_for(orphan, "c", snapshot=snap) is None
    assert tf.compdb_covers(orphan, "c", snapshot=snap) is None
    assert tf.compdb_language_for(orphan, snapshot=snap) is None


def test_compdb_covers_says_nothing_without_a_compile_database(tmp_path):
    """None, not False: a cflags-only tree has no database to be missing from,
    so there is no 'add it to the compile DB' advice to give."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"], "target": "t"}})
    assert tf.compdb_covers(src, "c", snapshot=TreeSnapshot(FakeProbe())) is None


def test_compdb_covers_reports_a_file_the_build_never_compiled(tmp_path):
    """False is what makes the backend warn that #ifdef-guarded functions may
    have been missed."""
    src = write_tree(tmp_path, {"c": {"compdb": str(tmp_path / "compile_commands.json")}},
                     entries=[{"directory": str(tmp_path), "file": "other.c",
                               "arguments": ["cc", "other.c"]}])
    assert tf.compdb_covers(src, "c", snapshot=TreeSnapshot(FakeProbe())) is False


def test_compdb_covers_normalises_the_path_it_looks_up(tmp_path):
    """The database records absolute paths; callers pass whatever they were
    given, `./src/../src/m.c` included."""
    src = write_tree(tmp_path, {"c": {"compdb": str(tmp_path / "compile_commands.json")}},
                     entries=[{"directory": str(tmp_path), "file": str(tmp_path / "src" / "m.c"),
                               "arguments": ["cc", "-DA"]}])
    noisy = str(tmp_path / "src" / ".." / "src" / "m.c")
    assert tf.compdb_covers(noisy, "c", snapshot=TreeSnapshot(FakeProbe())) is True
    assert tf.compdb_covers(src, "c", snapshot=TreeSnapshot(FakeProbe())) is True


def test_compdb_language_routes_a_c_file_the_build_compiled_as_cxx(tmp_path):
    """gdb's .c files are compiled with g++; sending them to the C backend fails
    the parse outright."""
    src = write_tree(tmp_path, {"c": {"compdb": str(tmp_path / "compile_commands.json")}},
                     entries=[{"directory": str(tmp_path), "file": str(tmp_path / "src" / "m.c"),
                               "arguments": ["g++", "-c", "src/m.c"]}])
    assert tf.compdb_language_for(src, snapshot=TreeSnapshot(FakeProbe())) == "cpp"


def test_compdb_language_looks_in_the_cpp_block_too(tmp_path):
    """A tree may record its C++ files in a separate database under the "cpp"
    key; a file listed only there still has a recorded language."""
    src = write_tree(tmp_path,
                     {"c": {"cflags": ["-DA"]},
                      "cpp": {"compdb": str(tmp_path / "compile_commands.json")}},
                     entries=[{"directory": str(tmp_path), "file": str(tmp_path / "src" / "m.c"),
                               "arguments": ["clang++", "-c", "src/m.c"]}])
    assert tf.compdb_language_for(src, snapshot=TreeSnapshot(FakeProbe())) == "cpp"


def test_compdb_language_is_none_for_a_file_no_database_records(tmp_path):
    src = write_tree(tmp_path, {"c": {"compdb": str(tmp_path / "compile_commands.json")}},
                     entries=[])
    assert tf.compdb_language_for(src, snapshot=TreeSnapshot(FakeProbe())) is None


def test_tree_flags_use_the_exact_compile_entry(tmp_path):
    """The whole point: the build's own -D defines, so the #ifdef branches the
    build took are the ones that parse."""
    src = write_tree(tmp_path,
                     {"c": {"compdb": str(tmp_path / "compile_commands.json"),
                            "target": "riscv64-unknown-netbsd"}},
                     entries=[{"directory": str(tmp_path / "obj"),
                               "file": str(tmp_path / "src" / "m.c"),
                               "arguments": ["cc", "-O2", "-DHAVE_CONFIG_H", "-I.",
                                             "-c", "-o", "m.o", str(tmp_path / "src" / "m.c")]}])
    flags = tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(FakeProbe()))
    assert flags == ["-DHAVE_CONFIG_H", f"-I{tmp_path / 'obj'}",
                     "-isystem", "/rt/include", "--target=riscv64-unknown-netbsd"]


def test_tree_flags_fall_back_to_cflags_for_a_file_outside_the_database(tmp_path):
    src = write_tree(tmp_path,
                     {"c": {"compdb": str(tmp_path / "compile_commands.json"),
                            "cflags": ["-DFALLBACK"], "target": "t"}},
                     entries=[])
    flags = tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(FakeProbe()))
    assert flags == ["-DFALLBACK", "-isystem", "/rt/include", "--target=t"]


def test_tree_flags_are_none_when_the_config_has_nothing_for_the_language(tmp_path):
    src = write_tree(tmp_path, {"cpp": {"cflags": ["-DA"]}})
    assert tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(FakeProbe())) is None


def test_tree_flags_are_none_when_the_block_shapes_nothing(tmp_path):
    """An empty block is a config that applies but asks for nothing; the backend
    must use its self-contained defaults, not an empty argument list."""
    src = write_tree(tmp_path, {"c": {}})
    assert tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(FakeProbe())) is None


def test_tree_flags_add_the_toolchains_headers_and_macros(tmp_path):
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA", "--sysroot=/dest"],
                                      "predef_cc": ["/tc/bin/xcc", "-std=gnu11"],
                                      "target": "riscv64-unknown-netbsd"}})
    probe = FakeProbe(predefs=("-D__GNUC__=13", "-D__WCHAR_MIN__=x"),
                      clang_names=frozenset({"__GNUC__"}))
    flags = tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(probe))
    assert flags == ["-DA", "--sysroot=/dest", "-isystem", "/rt/include",
                     "-isystem", "/tc/include", "-D__WCHAR_MIN__=x",
                     "--target=riscv64-unknown-netbsd"]
    assert ("dirs", ("/tc/bin/xcc", "-std=gnu11"), "c", "/dest") in probe.calls


def test_tree_flags_ask_the_toolchain_with_the_builds_sysroot(tmp_path):
    """A cross gcc asked without the build's sysroot answers with the host's
    /usr/include, and the host glibc headers then poison the parse."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"], "sysroot": "/dest",
                                      "predef_cc": ["xcc"], "target": "t"}})
    probe = FakeProbe()
    tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(probe))
    assert ("dirs", ("xcc",), "c", "/dest") in probe.calls


def test_tree_flags_ask_the_cxx_toolchain_for_a_cxx_file(tmp_path):
    """`-x c++` changes gcc's search list: without it libstdc++'s own headers
    are missing and every <vector> fails."""
    src = write_tree(tmp_path, {"cpp": {"cflags": ["-DA"], "predef_cc": ["xcc"], "target": "t"}})
    probe = FakeProbe()
    tf.tree_flags_for(src, "cpp", snapshot=TreeSnapshot(probe))
    assert ("dirs", ("xcc",), "c++", "") in probe.calls


def test_tree_flags_skip_the_toolchain_probe_without_a_predef_cc(tmp_path):
    """No compiler named means no compiler run — the config asked for none."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"], "target": "t"}})
    probe = FakeProbe()
    tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(probe))
    assert [c[0] for c in probe.calls] == ["builtin"]


def test_tree_flags_use_the_default_target_when_the_config_names_none(tmp_path):
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"]}})
    flags = tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(FakeProbe())) or []
    assert flags[-1] == f"--target={tf.DEFAULT_TARGET}"


# =========================================================================== #
# edge — a broken config is heard, not swallowed
# =========================================================================== #

def test_a_broken_config_stops_the_lookup_instead_of_degrading_it(tmp_path):
    """Measured harm: the config was dropped, the file was parsed with the host
    compiler's flags, and every function behind a build -D disappeared from the
    instrumented output without a word."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DA"]}})
    (tmp_path / tf.CONFIG_NAME).write_text("{ oops", encoding="utf-8")
    snap = TreeSnapshot(FakeProbe())
    for call in (lambda: tf.tree_flags_for(src, "c", snapshot=snap),
                 lambda: tf.compdb_covers(src, "c", snapshot=snap),
                 lambda: tf.compdb_language_for(src, snapshot=snap)):
        with pytest.raises(TreeConfigError) as e:
            call()
        assert str(tmp_path / tf.CONFIG_NAME) in str(e.value)


def test_a_broken_compile_database_stops_the_lookup(tmp_path):
    src = write_tree(tmp_path, {"c": {"compdb": str(tmp_path / "compile_commands.json")}})
    (tmp_path / "compile_commands.json").write_text("[{,", encoding="utf-8")
    with pytest.raises(TreeConfigError) as e:
        tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(FakeProbe()))
    assert "not valid JSON" in str(e.value)


def test_a_missing_compile_database_stops_the_lookup(tmp_path):
    """A compdb path that points nowhere is a configuration mistake, and it used
    to look exactly like a tree whose database covers nothing."""
    src = write_tree(tmp_path, {"c": {"compdb": str(tmp_path / "gone.json"),
                                      "cflags": ["-DA"]}})
    with pytest.raises(TreeConfigError) as e:
        tf.compdb_covers(src, "c", snapshot=TreeSnapshot(FakeProbe()))
    assert "cannot be read" in str(e.value)


def test_a_broken_config_names_the_file_and_the_reason(tmp_path):
    """An operator has to be able to act on it."""
    p = tmp_path / tf.CONFIG_NAME
    p.write_text(json.dumps({"c": {"cflags": "-DA"}}), encoding="utf-8")
    src = write_tree(tmp_path, {"c": {"cflags": "-DA"}})
    with pytest.raises(TreeConfigError) as e:
        tf.tree_flags_for(src, "c", snapshot=TreeSnapshot(FakeProbe()))
    assert e.value.path == str(p)
    assert "cflags" in e.value.reason and "list" in e.value.reason


def test_snapshot_notices_an_edit_that_keeps_the_file_the_same_size(tmp_path):
    """`"-DOLD"` -> `"-DNEW"` does not move the size, and an operator who
    restores the timestamp does not move mtime either. The change still has to
    be seen, or the fix appears to do nothing."""
    src = write_tree(tmp_path, {"c": {"cflags": ["-DOLD"], "target": "t"}})
    cfg = tmp_path / tf.CONFIG_NAME
    before = cfg.stat()
    snap = TreeSnapshot(FakeProbe())
    assert "-DOLD" in (tf.tree_flags_for(src, "c", snapshot=snap) or [])

    cfg.write_text(json.dumps({"c": {"cflags": ["-DNEW"], "target": "t"}}),
                   encoding="utf-8")
    os.utime(cfg, ns=(before.st_atime_ns, before.st_mtime_ns))  # put mtime back
    assert cfg.stat().st_size == before.st_size, "the test needs an equal-size edit"
    assert cfg.stat().st_mtime_ns == before.st_mtime_ns, "the test needs an equal mtime"

    assert "-DNEW" in (tf.tree_flags_for(src, "c", snapshot=snap) or [])


def test_a_broken_config_stops_the_instrumentation_of_the_file(tmp_path):
    """The harm, end to end. `gated` sits behind a -D the compile database was
    to supply; with the config dropped it is never seen, and the file comes back
    instrumented, warning-free, and missing a function. The run has to stop
    instead."""
    src = tmp_path / "src" / "m.c"
    src.parent.mkdir()
    src.write_text("#ifdef HAVE_FEATURE\nint gated(void){return 1;}\n#endif\n"
                   "int visible(void){return 0;}\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(json.dumps(
        [{"directory": str(tmp_path), "file": str(src),
          "arguments": ["cc", "-DHAVE_FEATURE", "-c", str(src)]}]), encoding="utf-8")
    (tmp_path / tf.CONFIG_NAME).write_text(
        f'{{"c": {{"compdb": "{tmp_path}/compile_commands.json"', encoding="utf-8")

    from ouroboros.languages.registry import transformer_for_language
    tx = transformer_for_language("c")
    previous = tf.set_snapshot(TreeSnapshot())
    try:
        with pytest.raises(TreeConfigError) as e:
            tx.wrap_source(src.read_text(encoding="utf-8"), filename=str(src))
    finally:
        tf.set_snapshot(previous)
    assert str(tmp_path / tf.CONFIG_NAME) in str(e.value)
