"""Tests for `ouroboros.languages.toolchain`.

The parsers are checked against real compiler output pasted as strings: no
compiler has to be installed for the interesting branches, and the answers do
not drift with whatever gcc the machine happens to carry.

The probes need a compiler. Each one is checked twice: once against a binary
that does not exist (the "could not ask" branch, which needs no toolchain), and
once against the real gcc/clang when the machine has them.
"""

from __future__ import annotations

import shutil

import pytest

from ouroboros.languages import toolchain as tc

HAVE_GCC = shutil.which("gcc") is not None
HAVE_CLANG = shutil.which("clang") is not None

# Trimmed from a real `gcc -E -v -x c -` run.
VERBOSE = """\
Using built-in specs.
COLLECT_GCC=gcc
ignoring nonexistent directory "/usr/local/include/x86_64-linux-gnu"
#include "..." search starts here:
#include <...> search starts here:
 /usr/lib/gcc/x86_64-linux-gnu/15/include
 /usr/local/include

 /usr/include
End of search list.
# 0 "<stdin>"
"""


# --------------------------------------------------------------------------- #
# parsers
# --------------------------------------------------------------------------- #

def test_search_dirs_are_taken_only_from_between_the_markers():
    """The compiler's chatter surrounds the list. Picking up the 'ignoring
    nonexistent directory' line would add a directory gcc explicitly refused."""
    assert tc.parse_search_dirs(VERBOSE) == (
        "/usr/lib/gcc/x86_64-linux-gnu/15/include",
        "/usr/local/include",
        "/usr/include",
    )


def test_search_dirs_empty_when_the_output_has_no_list():
    """A compiler that failed before printing its search list must yield
    nothing, not a stray line from its error message."""
    assert tc.parse_search_dirs("cc1: error: bad -x argument\n") == ()


def test_search_dirs_stop_at_the_end_marker():
    """Everything after 'End of search list.' is preprocessed output, not paths."""
    text = ("#include <...> search starts here:\n /a\nEnd of search list.\n"
            "# 1 \"/not/a/search/dir\"\n")
    assert tc.parse_search_dirs(text) == ("/a",)


def test_toolchain_dirs_drop_paths_that_do_not_exist():
    """gcc lists directories it will look in, existing or not; libclang would
    warn on every missing one."""
    got = tc.keep_toolchain_dirs(["/gone", "/here"], [], {"/here": "/here"})
    assert got == ("-isystem", "/here")


def test_toolchain_dirs_drop_host_paths_when_roots_are_given():
    """The whole point of the roots filter: a cross gcc that fell back to the
    host /usr/include must not have it kept, or the host glibc headers get into
    the parse and the tree's own types are wrong."""
    dirs = ["/tc/lib/gcc/include", "/usr/include"]
    resolved = {"/tc/lib/gcc/include": "/tc/lib/gcc/include", "/usr/include": "/usr/include"}
    got = tc.keep_toolchain_dirs(dirs, ["/tc"], resolved)
    assert got == ("-isystem", "/tc/lib/gcc/include")


def test_toolchain_dirs_keep_everything_when_no_roots():
    """No roots means the host toolchain IS the intended one (the C++ backend's
    self-contained parse), so nothing may be filtered out."""
    dirs = ["/usr/include", "/opt/x"]
    resolved = {d: d for d in dirs}
    assert tc.keep_toolchain_dirs(dirs, [], resolved) == (
        "-isystem", "/usr/include", "-isystem", "/opt/x")


def test_toolchain_dirs_are_normalised():
    """gcc prints '/usr/lib/gcc/../../include'; libclang is happier with the
    collapsed form, and duplicate spellings of one dir confuse the caller."""
    got = tc.keep_toolchain_dirs(["/usr/lib/../include"], [],
                                 {"/usr/lib/../include": "/usr/include"})
    assert got == ("-isystem", "/usr/include")


def test_toolchain_dirs_filter_on_the_real_path_not_the_spelling():
    """A symlink inside the sysroot pointing out of it is a host path wearing a
    sysroot name; the realpath is what decides."""
    dirs = ["/sysroot/usr/include"]
    resolved = {"/sysroot/usr/include": "/host/usr/include"}
    assert tc.keep_toolchain_dirs(dirs, ["/sysroot"], resolved) == ()


def test_predef_macros_become_d_flags():
    text = ("#define __GNUC__ 15\n"
            "#define __linux__ 1\n")
    assert tc.parse_predef_macros(text) == ("-D__GNUC__=15", "-D__linux__=1")


def test_predef_macros_keep_a_valueless_define_valueless():
    """`-D__x` and `-D__x=` are not the same to the preprocessor: the second
    defines it as empty, which changes `#if defined(x) && x` branches."""
    assert tc.parse_predef_macros("#define __x\n") == ("-D__x",)


def test_predef_macros_skip_function_like_macros():
    """`-D'max(a,b)=...'` cannot be passed as a plain -D, and no header branch
    we follow tests one."""
    text = "#define max(a,b) ((a)>(b)?(a):(b))\n#define __KEEP__ 1\n"
    assert tc.parse_predef_macros(text) == ("-D__KEEP__=1",)


def test_predef_macros_ignore_non_define_lines():
    """gcc puts a banner and blank lines in the same stream."""
    assert tc.parse_predef_macros("\n# 1 \"<stdin>\"\n#undef X\n") == ()


def test_predef_macros_keep_values_containing_spaces():
    """`#define __INT64_TYPE__ long int` must survive whole — the split takes
    at most three fields exactly so the value keeps its spaces."""
    got = tc.parse_predef_macros("#define __INT64_TYPE__ long int\n")
    assert got == ("-D__INT64_TYPE__=long int",)


def test_macro_names_record_function_like_macros_under_their_bare_name():
    """The names are used to answer 'does clang already define this?'. A gcc
    function-like macro clang also defines must count as known."""
    names = tc.parse_macro_names("#define max(a,b) x\n#define __GNUC__ 15\nnot a define\n")
    assert names == frozenset({"max", "__GNUC__"})


def test_install_prefix_from_an_absolute_compiler_path():
    assert tc.install_prefix("/opt/tools/bin/riscv64--netbsd-gcc") == "/opt/tools"


@pytest.mark.skipif(not HAVE_GCC, reason="needs gcc on PATH")
def test_install_prefix_resolves_a_bare_name_through_path():
    """A bare 'gcc' must be looked up, not turned into a prefix built from the
    current directory — that prefix rejects every real directory."""
    assert tc.install_prefix("gcc") == tc.install_prefix(shutil.which("gcc") or "")


def test_install_prefix_is_empty_for_an_unfindable_bare_name():
    assert tc.install_prefix("no-such-compiler-xyz") == ""


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #

MISSING = "ouroboros-no-such-compiler-xyz"


def test_clang_resource_dir_is_none_when_no_clang_can_be_run():
    assert tc.clang_resource_dir([MISSING]) is None


def test_clang_builtin_include_is_empty_when_no_clang_can_be_run():
    """Empty, not a broken flag pair: the caller appends this straight into a
    libclang argument list."""
    assert tc.clang_builtin_include([MISSING]) == ()


def test_include_search_dirs_is_empty_without_a_compiler():
    assert tc.include_search_dirs([], "c") == ()
    assert tc.include_search_dirs([MISSING], "c") == ()


def test_predef_macros_probe_is_empty_without_a_compiler():
    assert tc.predef_macros([]) == ()
    assert tc.predef_macros([MISSING]) == ()


def test_predef_macros_probe_is_empty_when_the_compiler_refuses():
    """A non-zero exit means the dump is partial or absent; half a macro set is
    worse than none, because the caller adds the difference against clang."""
    assert tc.predef_macros(["sh", "-c", "exit 3", "--"]) == ()


def test_clang_macro_names_is_empty_when_no_clang_can_be_run():
    assert tc.clang_macro_names("riscv64-unknown-netbsd", [MISSING]) == frozenset()


def test_clang_macro_names_skips_a_clang_that_rejects_the_target():
    """The list of clang names is tried in order; one that exits non-zero must
    not end the search."""
    assert tc.clang_macro_names("x", ["sh"]) == frozenset()


@pytest.mark.skipif(not HAVE_CLANG, reason="needs clang on PATH")
def test_clang_resource_dir_points_at_a_directory_holding_stddef():
    """The reason we probe at all: the pip libclang wheel has no headers, so the
    resource dir must be a real one with the builtin headers in it."""
    import os
    rd = tc.clang_resource_dir()
    assert rd is not None
    assert os.path.isfile(os.path.join(rd, "include", "stddef.h"))
    assert tc.clang_builtin_include() == ("-isystem", os.path.join(rd, "include"))


@pytest.mark.skipif(not HAVE_GCC, reason="needs gcc on PATH")
def test_include_search_dirs_finds_the_hosts_own_headers():
    flags = tc.include_search_dirs(["gcc"], "c", restrict=False)
    assert flags[0::2] == ("-isystem",) * (len(flags) // 2)
    assert "/usr/include" in flags[1::2]


@pytest.mark.skipif(not HAVE_GCC, reason="needs gcc on PATH")
def test_predef_macros_probe_reports_the_compilers_own_identity():
    defs = tc.predef_macros(["gcc"])
    assert any(d.startswith("-D__GNUC__=") for d in defs)


@pytest.mark.skipif(not HAVE_CLANG, reason="needs clang on PATH")
def test_clang_macro_names_follow_the_requested_target():
    """Asking for a different target must give a different macro set, or the
    'macros clang already defines' difference would be computed against the
    wrong machine."""
    host = tc.clang_macro_names("x86_64-unknown-linux-gnu")
    riscv = tc.clang_macro_names("riscv64-unknown-netbsd")
    assert "__x86_64__" in host
    assert "__x86_64__" not in riscv
    assert "__riscv" in riscv


def test_clang_resource_dir_keeps_looking_past_a_clang_with_no_headers():
    """A binary can answer -print-resource-dir with a path that has no include
    directory (a stripped install). That is not an answer — the next name in the
    list must still be tried."""
    assert tc.clang_resource_dir(["echo", MISSING]) is None


def _fake_cc(tmp_path, search_dirs):
    """A stand-in compiler that prints a fixed `-E -v` search list, installed
    under <tmp>/tools/bin so it has a real install prefix."""
    import os
    bin_dir = tmp_path / "tools" / "bin"
    bin_dir.mkdir(parents=True)
    listing = "\n".join(f" {d}" for d in search_dirs)
    script = bin_dir / "fakecc"
    script.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        'echo "#include <...> search starts here:" >&2\n'
        f'cat >&2 <<\'EOF\'\n{listing}\nEOF\n'
        'echo "End of search list." >&2\n',
        encoding="utf-8")
    os.chmod(script, 0o755)
    return str(script)


def test_include_search_dirs_keep_only_the_toolchains_own_directories(tmp_path):
    """The host-leak guard, end to end: a compiler that lists both its own
    include dir and the host's /usr/include must hand back only its own."""
    own = tmp_path / "tools" / "include"
    own.mkdir(parents=True)
    cc = _fake_cc(tmp_path, [str(own), "/usr/include"])
    flags = tc.include_search_dirs([cc], "c")
    assert flags == ("-isystem", str(own))


def test_include_search_dirs_also_accept_directories_under_the_sysroot(tmp_path):
    """The build's sysroot is the other place a tree's headers legitimately
    live, and it is only consulted when it is passed."""
    own = tmp_path / "tools" / "include"
    own.mkdir(parents=True)
    sysroot = tmp_path / "dest"
    inside = sysroot / "usr" / "include"
    inside.mkdir(parents=True)
    cc = _fake_cc(tmp_path, [str(own), str(inside), "/usr/include"])
    without = tc.include_search_dirs([cc], "c")
    with_sysroot = tc.include_search_dirs([cc], "c", str(sysroot))
    assert str(inside) not in without
    assert with_sysroot == ("-isystem", str(own), "-isystem", str(inside))


def test_include_search_dirs_pass_the_sysroot_to_the_compiler(tmp_path):
    """Not just a filter: a cross gcc asked with no --sysroot answers with the
    HOST paths, so the flag has to reach the command line."""
    import os
    bin_dir = tmp_path / "tools" / "bin"
    bin_dir.mkdir(parents=True)
    seen = tmp_path / "argv"
    script = bin_dir / "fakecc"
    script.write_text(f'#!/bin/sh\ncat >/dev/null\nprintf "%s\\n" "$@" > {seen}\n',
                      encoding="utf-8")
    os.chmod(script, 0o755)
    tc.include_search_dirs([str(script)], "c", "/some/root")
    assert "--sysroot=/some/root" in seen.read_text(encoding="utf-8").splitlines()
