"""Tests for the C backend (libclang + __attribute__((cleanup)) instrumentation)."""

from __future__ import annotations

import json
import shutil

import pytest

from ouroboros.languages import CorruptedSourceError, transformer_for_path
from ouroboros.languages.c_lang import CTransformer
from ouroboros.sandbox import Project, execute, write_file
from ouroboros.trace import load


def _normalize(text):
    """Parse JSONL and blank the volatile fields (t, id, d, ci, th) for schema pinning."""
    lines = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    for ev in lines:
        for k in ("t", "id", "d", "ci", "th"):
            if k in ev:
                ev[k] = "<X>"
    return lines

has_gcc = shutil.which("gcc") is not None


@pytest.fixture
def tx() -> CTransformer:
    return CTransformer()


def test_registry_resolves_c():
    assert isinstance(transformer_for_path("foo.c"), CTransformer)


def test_basic_wrap(tx):
    res = tx.wrap_source("int add(int a, int b) {\n    return a + b;\n}\n", filename="m.c")
    assert res.functions_wrapped == 1
    assert '#include "ouroboros_runtime.h"' in res.code
    assert '_ouro_enter(&__ouro, "add", "%d, %d", a, b)' in res.code
    assert "int __ouro_result;" in res.code


def test_idempotent(tx):
    once = tx.wrap_source("int f(int x) {\n    return x;\n}\n", filename="m.c").code
    again = tx.wrap_source(once, filename="m.c")
    assert again.functions_wrapped == 0
    assert again.code == once


def test_include_goes_after_leading_kernel_ring_define(tx):
    """An in-source `#define OUROBOROS_KERNEL_RING` selects the ring sink, but ONLY
    if it precedes the runtime include. The wrap must splice the include AFTER such
    a leading config define, never above it (the field bug: include landed at offset
    0 over a pre-existing define, silently disabling the ring sink)."""
    src = ("#define OUROBOROS_KERNEL_RING 1\n"
           "/* a copyright banner */\n"
           "int f(int x) {\n    return x;\n}\n")
    res = tx.wrap_source(src, filename="k.c")
    assert res.functions_wrapped == 1
    i_def = res.code.index("OUROBOROS_KERNEL_RING")
    i_inc = res.code.index('#include "ouroboros_runtime.h"')
    assert i_def < i_inc, "include must come AFTER the ring-sink define"
    assert res.code.count('#include "ouroboros_runtime.h"') == 1
    # the define stays the very first line (nothing spliced above it)
    assert res.code.startswith("#define OUROBOROS_KERNEL_RING 1\n")


def test_include_at_top_when_no_config_define(tx):
    """Without a leading OURO config define, behaviour is unchanged: include at top."""
    res = tx.wrap_source("int g(void){ return 0; }\n", filename="m.c")
    assert res.code.startswith('#include "ouroboros_runtime.h"\n')


def test_compdb_miss_warns_about_degraded_flags(tx, tmp_path):
    """A file under an .ouroboros.json tree whose compile DB DOESN'T list it parses
    with fallback flags (no build -D), so #ifdef-guarded functions are silently
    missed. The wrap must WARN about that (the md.c 15/18 regression in the field).
    """
    (tmp_path / ".ouroboros.json").write_text(
        json.dumps({"c": {"compdb": str(tmp_path / "compile_commands.json")}}),
        encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text("[]", encoding="utf-8")  # covers nothing
    src = ("int visible(void) { return 0; }\n"
           "#ifdef HAVE_X\n"                      # HAVE_X comes from a build -D we lack
           "int gated(void) { return 1; }\n"
           "#endif\n")
    f = tmp_path / "m.c"
    res = tx.wrap_source(src, filename=str(f))
    assert res.functions_wrapped == 1            # only `visible`; `gated` is #ifdef'd out
    assert res.warnings, "expected a compdb-miss warning"
    assert "compile_commands.json" in res.warnings[0]


def test_a_bare_return_in_a_value_returning_function_is_left_alone(tx, tmp_path):
    """`return;` inside a function that returns a value is a constraint
    violation the compiler refuses, so a self-contained parse never gets past
    the corruption gate and this never comes up there. A TREE file does: it is
    parsed with the tree's own flags against sources some other compiler built,
    where the gate records residual diagnostics instead of failing (see
    gate_diagnostics), and gcc lets exactly this through as a warning.

    There is no expression to capture, so the return-value rewrite has to skip
    it — the offsets it would splice at are not there — and let the cleanup
    handler write the completion record on the way out, which it does for every
    exit path anyway."""

    (tmp_path / ".ouroboros.json").write_text(
        json.dumps({"c": {"cflags": ["-std=gnu17"]}}), encoding="utf-8")
    src = "int f(int a) {\n    if (a) return;\n    return 1;\n}\n"
    f = tmp_path / "m.c"
    f.write_text(src, encoding="utf-8")

    res = tx.wrap_source(src, filename=str(f))

    assert res.functions_wrapped == 1
    assert "    if (a) return;\n" in res.code               # left exactly as it was
    assert "_ouro_set_result" in res.code                   # the valued return still captured
    assert res.code.count("_ouro_set_result") == 1          # and only that one


def test_compdb_no_config_no_warning(tx):
    """No .ouroboros.json in play -> self-contained parse is expected, not a miss."""
    res = tx.wrap_source("int f(void){return 0;}\n", filename="/nowhere/m.c")
    assert res.warnings == ()


def test_compdb_command_string_form_is_recognized(tmp_path):
    """clang's compile DB allows entries as a `command` STRING (not just an
    `arguments` list). Both must register, else a tree using `command` would have
    EVERY file silently fall back to degraded flags."""
    from ouroboros.languages.treeflags import compdb_covers
    f = tmp_path / "m.c"
    f.write_text("int x(void){ return 0; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps([{"directory": str(tmp_path),
                     "command": f"cc -DHAVE_X -c {f}",   # STRING form, not a list
                     "file": str(f)}]),
        encoding="utf-8")
    (tmp_path / ".ouroboros.json").write_text(
        json.dumps({"c": {"compdb": str(tmp_path / "compile_commands.json")}}),
        encoding="utf-8")
    assert compdb_covers(str(f), "c") is True   # recognised despite the command form


def test_const_char_pointer_prints_as_an_address_not_a_string(tx):
    """`%s` on a `const char *` is an out-of-bounds read waiting to happen.

    The type does not promise a NUL-terminated string: `len(&c)` on a single
    char is ordinary C, and `%s` would read past `c` to the first zero
    anywhere in memory. That is instrumentation introducing undefined behaviour
    into a program that had none — proven with AddressSanitizer before this
    changed. Readability lost to safety, deliberately.
    """

    res = tx.wrap_source(
        'int len(const char *s) {\n    return 0;\n}\n', filename="m.c")

    assert '"%p"' in res.code
    assert "%s" not in res.code
    assert '(s ? s : "(null)")' not in res.code


def test_const_return_strips_qualifier(tx):
    res = tx.wrap_source("const int f(int v) {\n    return v;\n}\n", filename="m.c")
    # the temp must be assignable: `int __ouro_result`, not `const int`
    assert "int __ouro_result;" in res.code
    assert "const int __ouro_result;" not in res.code


def test_selective_only_wraps_named_functions(tx):
    """Selective mode (the `only=` kwarg behind the wrap_functions MCP tool):
    instrument ONLY the listed function, leave every other definition untouched.
    This is the mode for hot/kernel files where blanket wrapping floods the sink."""
    src = ("int a(int x) {\n    return x;\n}\n"
           "int b(int y) {\n    return y;\n}\n"
           "int c(int z) {\n    return z;\n}\n")
    res = tx.wrap_source(src, filename="m.c", only={"b"})
    assert res.functions_wrapped == 1
    assert '_ouro_enter(&__ouro, "b"' in res.code
    # a and c are left alone -- no instrumentation leaked into them.
    assert '"a"' not in res.code
    assert '"c"' not in res.code
    assert '#include "ouroboros_runtime.h"' in res.code


def test_selective_only_unknown_name_wraps_nothing(tx):
    src = "int a(int x) {\n    return x;\n}\n"
    res = tx.wrap_source(src, filename="m.c", only={"nonexistent"})
    assert res.functions_wrapped == 0
    # nothing matched -> file is returned untouched (no header injected either).
    assert '#include "ouroboros_runtime.h"' not in res.code


def test_void_function_has_no_result_temp(tx):
    res = tx.wrap_source("void p(int n) {\n    (void)n;\n}\n", filename="m.c")
    assert res.functions_wrapped == 1
    assert "__ouro_result" not in res.code
    assert '_ouro_enter(&__ouro, "p", "%d", n)' in res.code


def test_struct_return_is_not_captured(tx):
    src = ("struct pt { int x; };\n"
           "struct pt mk(int x) {\n    struct pt p; p.x = x; return p;\n}\n")
    res = tx.wrap_source(src, filename="m.c")
    assert res.functions_wrapped == 1
    # unprintable return -> no capture, but args still logged
    assert "__ouro_result" not in res.code
    assert '_ouro_enter(&__ouro, "mk", "%d", x)' in res.code


def test_double_uses_float_specifier(tx):
    res = tx.wrap_source("double sc(double x) {\n    return x * 2;\n}\n", filename="m.c")
    assert '"%f"' in res.code
    assert '_ouro_set_result(&__ouro, "%f"' in res.code


def test_corrupted_c_raises(tx):
    with pytest.raises(CorruptedSourceError):
        tx.wrap_source("int broken( {\n", filename="bad.c")


def test_runtime_asset_is_header(tx):
    name, src = tx.runtime_asset()
    assert name == "ouroboros_runtime.h"
    assert '\\"p\\":\\"in\\"' in src and "__attribute__((cleanup" in src


@pytest.mark.skipif(not has_gcc, reason="gcc not available")
def test_c_schema_matches_spec(tmp_path):
    """A compiled+run instrumented C program emits the SPEC JSONL schema."""
    name, header = CTransformer().runtime_asset()
    (tmp_path / name).write_text(header, encoding="utf-8")
    src = CTransformer().wrap_source(
        "int add(int a, int b) {\n    return a + b;\n}\n", filename="prog.c").code
    (tmp_path / "prog.c").write_text(src, encoding="utf-8")
    (tmp_path / "main.c").write_text(
        "int add(int,int);\nint main(void){ add(2,3); return 0; }\n", encoding="utf-8")

    import subprocess
    subprocess.run(["gcc", "-std=gnu11", "main.c", "prog.c", "-o", "app"],
                   cwd=tmp_path, check=True, capture_output=True)
    debug = tmp_path / "debug.info"
    subprocess.run(["./app"], cwd=tmp_path, check=True,
                   env={"OUROBOROS_DEBUG_INFO": str(debug), "PATH": "/usr/bin:/bin"})
    assert _normalize(debug.read_text(encoding="utf-8")) == [
        {"p": "in", "t": "<X>", "id": "<X>", "ci": "<X>", "th": "<X>",
         "fn": "add", "a": "2, 3", "k": ""},
        {"p": "out", "id": "<X>", "fn": "add", "r": "5", "d": "<X>"},
    ]


@pytest.mark.skipif(not has_gcc, reason="gcc not available")
def test_c_escapes_special_chars(tmp_path):
    """Exercise the `"`/`\\`/control-char paths of the hand-rolled _ouro_jesc — the
    integer-arg tests only ever hit its pass-through branch. A broken escaper would
    emit a malformed JSON line (data silently lost), not a green-test failure."""
    name, header = CTransformer().runtime_asset()
    (tmp_path / name).write_text(header, encoding="utf-8")
    # Driven through the sink directly with a STRING LITERAL, not through an
    # instrumented `const char *` parameter. Instrumentation no longer formats
    # such a parameter with %s — it cannot know the pointer is a string — so the
    # escaper is no longer reachable that way. A literal is a real string, so
    # this exercises the escaper without the out-of-bounds read.
    (tmp_path / "main.c").write_text(
        '#include "ouroboros_runtime.h"\n'
        'static void show(void) {\n'
        '\tstruct _ouro_call __ouro __attribute__((cleanup(_ouro_emit)));\n'
        '\t_ouro_enter(&__ouro, "show", "%s", "a\\"b\\\\c\\nd");\n'
        '}\n'
        'int main(void){ show(); return 0; }\n', encoding="utf-8")

    import subprocess
    subprocess.run(["gcc", "-std=gnu11", "-I.", "main.c", "-o", "app"],
                   cwd=tmp_path, check=True, capture_output=True)
    debug = tmp_path / "debug.info"
    subprocess.run(["./app"], cwd=tmp_path, check=True,
                   env={"OUROBOROS_DEBUG_INFO": str(debug), "PATH": "/usr/bin:/bin"})
    loaded = load(debug.read_text(encoding="utf-8"))
    assert loaded.malformed == 0           # the quote/backslash/newline didn't break the line
    assert 'a"b\\c' in loaded.calls[0].args  # content survived escape -> json.loads round-trip
    assert "\n" in loaded.calls[0].args      # the embedded newline came back intact


@pytest.mark.skipif(not has_gcc, reason="gcc not available")
def test_kernel_branch_formats_via_shim(tmp_path):
    """The #ifdef _KERNEL sink emits the same JSONL schema (uptime-relative
    timestamp dialect). This shim compiles the kernel branch against userland
    stubs and checks FORMATTING ONLY — not stack/reentrancy/freestanding safety,
    which require an on-target NetBSD kernel build."""
    _, header = CTransformer().runtime_asset()
    (tmp_path / "ouroboros_runtime.h").write_text(header, encoding="utf-8")
    (tmp_path / "kshim.h").write_text(
        "#pragma once\n#include <stdio.h>\n#include <stdlib.h>\n#include <stdint.h>\n"
        "#include <stdarg.h>\n#include <time.h>\n"
        "static inline void getnanouptime(struct timespec *ts){ timespec_get(ts, TIME_UTC); }\n"
        # The kernel sink derives its uuid from a lockless atomic counter (NOT
        # cprng_strong32, which can block/assert under the pmap spinlocks the
        # kernel branch is used to instrument) -- stub the AMO for the shim.
        "static inline unsigned int atomic_inc_uint_nv(volatile unsigned int *p){ return ++*p; }\n"
        # the re-entrancy guard is now PER-CPU under kpreempt_disable -- stub the
        # CPU/preemption primitives so the shim exercises that code path too.
        "#define MAXCPUS 4\n"
        "#define curcpu() ((const void *)0)\n"
        "static inline int cpu_index(const void *ci){ (void)ci; return 2; }\n"
        "static inline void kpreempt_disable(void){}\n"
        "static inline void kpreempt_enable(void){}\n"
        # thread identity: the kernel sink reads curlwp->l_proc->p_pid / l_lid.
        "struct proc { int p_pid; };\n"
        "struct lwp { struct proc *l_proc; int l_lid; };\n"
        "static struct proc _ouro_test_proc = { 4242 };\n"
        "static struct lwp _ouro_test_lwp = { &_ouro_test_proc, 7 };\n"
        "#define curlwp (&_ouro_test_lwp)\n",
        encoding="utf-8")
    (tmp_path / "t.c").write_text(
        '#include "kshim.h"\n#include "ouroboros_runtime.h"\n'
        "int kfn(int a) {\n"
        "    struct _ouro_call __ouro __attribute__((cleanup(_ouro_emit)));\n"
        "    int __ouro_result;\n"
        '    _ouro_enter(&__ouro, "kfn", "%d", a);\n'
        '    return (__ouro_result = (a * 2), '
        '_ouro_set_result(&__ouro, "%d", __ouro_result), __ouro_result);\n'
        "}\n"
        "int main(void){ kfn(21); return 0; }\n", encoding="utf-8")

    import subprocess
    subprocess.run(["gcc", "-std=gnu11", "-D_KERNEL", "-DOURO_KERNEL_TEST", "t.c", "-o", "t"],
                   cwd=tmp_path, check=True, capture_output=True)
    out = subprocess.run(["./t"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    # the captured identity is real, not just schema-shaped: cpu_index()->2, pid.lid 4242.7
    assert '"ci":2,"th":"4242.7"' in out, out
    lines = _normalize(out)
    # the kernel `t` dialect is uptime-relative, but it is blanked by _normalize
    assert lines == [
        {"p": "in", "t": "<X>", "id": "<X>", "ci": "<X>", "th": "<X>",
         "fn": "kfn", "a": "21", "k": ""},
        {"p": "out", "id": "<X>", "fn": "kfn", "r": "42", "d": "<X>"},
    ]


def test_kernel_ring_buffer_via_shim(tmp_path):
    """Ring-buffer mode (-DOUROBOROS_KERNEL_RING): the sink emits NOTHING during the
    run (records go to a lockless ring), and _ouro_dump() prints them all at once —
    the no-flood path for hot kernel paths. Same JSONL schema, just deferred."""
    _, header = CTransformer().runtime_asset()
    (tmp_path / "ouroboros_runtime.h").write_text(header, encoding="utf-8")
    (tmp_path / "kshim.h").write_text(
        "#pragma once\n#include <stdio.h>\n#include <stdlib.h>\n#include <stdint.h>\n"
        "#include <stdarg.h>\n#include <time.h>\n"
        "static inline void getnanouptime(struct timespec *ts){ timespec_get(ts, TIME_UTC); }\n"
        "static inline unsigned int atomic_inc_uint_nv(volatile unsigned int *p){ return ++*p; }\n"
        "#define MAXCPUS 4\n"
        # _ouro_enter captures cpu/thread identity in ring mode too.
        "#define curcpu() ((const void *)0)\n"
        "static inline int cpu_index(const void *ci){ (void)ci; return 1; }\n"
        "struct proc { int p_pid; };\n"
        "struct lwp { struct proc *l_proc; int l_lid; };\n"
        "static struct proc _ouro_test_proc = { 4242 };\n"
        "static struct lwp _ouro_test_lwp = { &_ouro_test_proc, 7 };\n"
        "#define curlwp (&_ouro_test_lwp)\n",
        encoding="utf-8")
    # 3 calls -> 6 records buffered; main dumps the ring and prints a sentinel so we
    # can prove NOTHING was emitted before the dump.
    (tmp_path / "t.c").write_text(
        '#include "kshim.h"\n#include "ouroboros_runtime.h"\n'
        "int kfn(int a) {\n"
        "    struct _ouro_call __ouro __attribute__((cleanup(_ouro_emit)));\n"
        "    int __ouro_result;\n"
        '    _ouro_enter(&__ouro, "kfn", "%d", a);\n'
        '    return (__ouro_result = (a * 2), '
        '_ouro_set_result(&__ouro, "%d", __ouro_result), __ouro_result);\n'
        "}\n"
        "int main(void){ kfn(1); kfn(2); kfn(3);\n"
        '    printf("BEFORE_DUMP\\n"); _ouro_dump(); return 0; }\n', encoding="utf-8")

    import subprocess
    subprocess.run(["gcc", "-std=gnu11", "-D_KERNEL", "-DOURO_KERNEL_TEST",
                    "-DOUROBOROS_KERNEL_RING", "-DOUROBOROS_RING_OWNER", "t.c", "-o", "t"],
                   cwd=tmp_path, check=True, capture_output=True)
    out = subprocess.run(["./t"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    # nothing emitted before the explicit dump (no per-call flood)
    pre, _, post = out.partition("BEFORE_DUMP\n")
    assert "ouroboros" not in pre and '"p":"in"' not in pre, pre
    assert "ouroboros ring dump: 6 records" in post
    # the dump brackets the records with non-JSON "=== ... ===" lines; keep records
    jsonl = "\n".join(ln for ln in post.splitlines() if ln.strip().startswith("{"))
    lines = _normalize(jsonl)
    # the 6 records (3 calls x in/out), in order, parse as the same JSONL schema
    assert lines == [
        {"p": "in", "t": "<X>", "id": "<X>", "ci": "<X>", "th": "<X>",
         "fn": "kfn", "a": "1", "k": ""},
        {"p": "out", "id": "<X>", "fn": "kfn", "r": "2", "d": "<X>"},
        {"p": "in", "t": "<X>", "id": "<X>", "ci": "<X>", "th": "<X>",
         "fn": "kfn", "a": "2", "k": ""},
        {"p": "out", "id": "<X>", "fn": "kfn", "r": "4", "d": "<X>"},
        {"p": "in", "t": "<X>", "id": "<X>", "ci": "<X>", "th": "<X>",
         "fn": "kfn", "a": "3", "k": ""},
        {"p": "out", "id": "<X>", "fn": "kfn", "r": "6", "d": "<X>"},
    ]


def test_inplace_wrap_drops_runtime_header(tmp_path):
    """wrap_file / wrap_functions edit a file IN PLACE and inject
    `#include "ouroboros_runtime.h"`; they MUST also drop the header next to it,
    else the result does not compile (the gap that broke the riscv kernel build)."""
    from ouroboros.mcp.server import tool_wrap_file, tool_wrap_functions

    src = tmp_path / "m.c"
    src.write_text("int add(int a, int b) {\n    return a + b;\n}\n", encoding="utf-8")
    res = tool_wrap_file(str(src))
    assert res["ok"] and res["functions_wrapped"] == 1
    dropped = tmp_path / "ouroboros_runtime.h"
    assert dropped.exists(), "wrap_file must drop ouroboros_runtime.h next to the file"
    assert res["runtime_header"] == str(dropped)

    # selective tool too, in its own dir
    src2 = tmp_path / "sub" / "n.c"
    src2.parent.mkdir()
    src2.write_text("int f(int x) {\n    return x;\n}\n"
                    "int g(int y) {\n    return y;\n}\n", encoding="utf-8")
    res2 = tool_wrap_functions(str(src2), ["g"])
    assert res2["ok"] and res2["functions_wrapped"] == 1
    assert (src2.parent / "ouroboros_runtime.h").exists()


def test_wrap_file_binary_input_is_clean_error(tmp_path):
    """A non-UTF-8 / binary file must yield a structured error, not an uncaught
    UnicodeDecodeError escaping the tool (would disrupt the MCP server)."""
    from ouroboros.mcp.server import tool_wrap_file

    p = tmp_path / "bin.c"
    p.write_bytes(b"\xff\xfe\x00\x01 int x(void){}")
    r = tool_wrap_file(str(p))
    assert r["ok"] is False and "UTF-8" in r["error"]


def test_wrap_file_unwritable_target_is_clean_error(tmp_path):
    """A non-writable target must yield a structured error, not an uncaught
    OSError from the atomic write escaping the tool (the write-side twin of the
    binary-input case)."""
    import os

    from ouroboros.mcp.server import tool_wrap_file

    p = tmp_path / "ro.c"
    p.write_text("int f(void) {\n    return 0;\n}\n", encoding="utf-8")
    os.chmod(p, 0o444)
    os.chmod(tmp_path, 0o555)   # read-only dir -> temp create / replace fails
    try:
        r = tool_wrap_file(str(p))
        assert r["ok"] is False and "cannot write" in r["error"]
    finally:
        os.chmod(tmp_path, 0o755)


def test_incremental_selective_wrap_adds_functions(tmp_path):
    """Per-function idempotency: a second wrap_functions call ADDS new functions to
    an already-instrumented file (and never re-wraps or duplicates the include).
    This is the incremental selective workflow the old file-wide include check
    silently blocked."""
    from ouroboros.mcp.server import tool_wrap_functions

    src = tmp_path / "m.c"
    src.write_text("int a(int x) {\n    return x;\n}\n"
                   "int b(int y) {\n    return y;\n}\n"
                   "int c(int z) {\n    return z;\n}\n", encoding="utf-8")

    assert tool_wrap_functions(str(src), ["b"])["functions_wrapped"] == 1
    # header was dropped next to the file, so the re-parse resolves the include
    assert tool_wrap_functions(str(src), ["a"])["functions_wrapped"] == 1  # adds a
    code = src.read_text(encoding="utf-8")
    assert code.count('#include "ouroboros_runtime.h"') == 1   # not duplicated
    assert '_ouro_enter(&__ouro, "a"' in code and '_ouro_enter(&__ouro, "b"' in code
    assert '"c"' not in code                                   # c never requested
    # asking for an already-wrapped function is a no-op
    assert tool_wrap_functions(str(src), ["b"])["functions_wrapped"] == 0


@pytest.mark.skipif(not has_gcc, reason="gcc not available")
def test_inplace_wrapped_file_compiles(tmp_path):
    """End-to-end: an in-place wrapped userland C file compiles + runs because the
    header was dropped beside it (no manual copy, no sandbox)."""
    import subprocess

    from ouroboros.mcp.server import tool_wrap_file

    src = tmp_path / "p.c"
    src.write_text(
        "#include <stdio.h>\n"
        "int square(int n) {\n    return n * n;\n}\n"
        "int main(void) {\n    printf(\"%d\\n\", square(6));\n    return 0; }\n",
        encoding="utf-8")
    assert tool_wrap_file(str(src))["functions_wrapped"] == 2
    subprocess.run(["gcc", "-std=gnu11", "p.c", "-o", "p"],
                   cwd=tmp_path, check=True, capture_output=True)
    out = subprocess.run(["./p"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert out.strip() == "36"


@pytest.mark.skipif(not has_gcc, reason="gcc not available")
def test_end_to_end_via_sandbox(tmp_path):
    """Write C through the sandbox (wraps + drops the header), compile and run it."""
    proj = Project.create(tmp_path / "site")
    src = (
        "#include <stdio.h>\n"
        "int square(int n) {\n    return n * n;\n}\n"
        "int main(void) {\n    printf(\"%d\\n\", square(6));\n    return 0;\n}\n"
    )
    out = write_file(proj, "m.c", src)
    assert out.wrapped and out.language == "c"
    assert (proj.draft / "ouroboros_runtime.h").is_file()

    comp = execute(proj, ["gcc", "-std=gnu11", "m.c", "-o", "m"])
    assert comp.returncode == 0, comp.stderr
    run = execute(proj, ["./m"])
    assert run.returncode == 0
    assert "36" in run.stdout

    loaded = load(proj.debug_info_path().read_text(encoding="utf-8"))
    assert loaded.malformed == 0
    square = [c for c in loaded.calls if c.name == "square"]
    assert len(square) == 1
    assert square[0].outcome_kind == "result" and square[0].outcome == "36"


def test_minimal_probe_codegen(tx):
    """minimal=True emits the stackless depth-only probe: a 1-byte cleanup marker +
    _ouro_min_enter, and NONE of the per-frame struct / args / return-capture
    machinery that overflows the kernel stack in deep recursion."""
    src = "int fact(int n) {\n    if (n <= 0) return 1;\n    return n * fact(n - 1);\n}\n"
    res = tx.wrap_source(src, filename="m.c", only={"fact"}, minimal=True)
    code = res.code
    assert res.functions_wrapped == 1
    assert ('char __ouro __attribute__((cleanup(_ouro_min_exit))) = '
            '_ouro_min_enter("fact");') in code
    # the heavy full-mode machinery must be ABSENT (that is the whole point)
    assert "struct _ouro_call" not in code
    assert "_ouro_enter(&__ouro" not in code
    assert "__ouro_result" not in code
    assert "_ouro_set_result" not in code  # returns are NOT rewritten in minimal mode


def test_minimal_probe_rejected_for_non_c():
    """minimal mode is C-only; other backends refuse it loudly (NotImplementedError)
    rather than silently emit a probe that does not exist for them."""
    pytx = transformer_for_path("m.py")
    assert pytx is not None
    with pytest.raises(NotImplementedError):
        pytx.wrap_source("def f():\n    return 1\n", filename="m.py", minimal=True)


@pytest.mark.skipif(not has_gcc, reason="gcc not available")
def test_minimal_probe_kernel_ring_recursion(tmp_path):
    """The minimal probe in the kernel ring path: a RECURSIVE function emits one
    depth-stamped IN-record per call — no per-frame struct, no out-records, no id.
    The depth sequence encodes the call tree WITHOUT id-pairing (the phantom-free
    encoding that survives record loss). This is the fix for the segtab-recursion
    kernel-stack overflow that the full struct caused on the real target."""
    _, header = CTransformer().runtime_asset()
    (tmp_path / "ouroboros_runtime.h").write_text(header, encoding="utf-8")
    (tmp_path / "kshim.h").write_text(
        "#pragma once\n#include <stdio.h>\n#include <stdlib.h>\n#include <stdint.h>\n"
        "#include <stdarg.h>\n#include <time.h>\n"
        "static inline void getnanouptime(struct timespec *ts){ timespec_get(ts, TIME_UTC); }\n"
        "static inline unsigned int atomic_inc_uint_nv(volatile unsigned int *p){ return ++*p; }\n"
        "#define MAXCPUS 4\n"
        "#define curcpu() ((const void *)0)\n"
        "static inline int cpu_index(const void *ci){ (void)ci; return 1; }\n"
        "struct proc { int p_pid; };\n"
        "struct lwp { struct proc *l_proc; int l_lid; };\n"
        "static struct proc _ouro_test_proc = { 4242 };\n"
        "static struct lwp _ouro_test_lwp = { &_ouro_test_proc, 7 };\n"
        "#define curlwp (&_ouro_test_lwp)\n",
        encoding="utf-8")
    prog = CTransformer().wrap_source(
        "int fact(int n) {\n    if (n <= 0) return 1;\n    return n * fact(n - 1);\n}\n",
        filename="prog.c", only={"fact"}, minimal=True).code
    (tmp_path / "t.c").write_text(
        '#include "kshim.h"\n#include "ouroboros_runtime.h"\n'
        + prog
        + 'int main(void){ fact(3); printf("BEFORE_DUMP\\n"); _ouro_dump(); return 0; }\n',
        encoding="utf-8")

    import subprocess
    subprocess.run(["gcc", "-std=gnu11", "-D_KERNEL", "-DOURO_KERNEL_TEST",
                    "-DOUROBOROS_KERNEL_RING", "-DOUROBOROS_RING_OWNER", "t.c", "-o", "t"],
                   cwd=tmp_path, check=True, capture_output=True)
    out = subprocess.run(["./t"], cwd=tmp_path, check=True,
                         capture_output=True, text=True).stdout
    pre, _, post = out.partition("BEFORE_DUMP\n")
    assert '"p":"in"' not in pre, pre            # ring defers — nothing before the dump
    assert "ouroboros ring dump: 4 records" in post   # fact(3),fact(2),fact(1),fact(0)
    recs = [json.loads(ln) for ln in post.splitlines() if ln.strip().startswith("{")]
    assert [r["dep"] for r in recs] == [0, 1, 2, 3]   # depth encodes the recursion nesting
    assert all(r["p"] == "in" and r["fn"] == "fact" for r in recs)
    assert all(r["ci"] == 0 for r in recs)            # ci hardcoded 0 (curcpu-free probe)
    assert all("id" not in r for r in recs)           # stackless: no per-call id at all


@pytest.mark.skipif(not has_gcc, reason="gcc not available")
def test_minimal_probe_edge_dedup(tmp_path):
    """EDGE_DEDUP mode: the shadow stack gives each call its caller, and the seen-bitmap
    emits every unique caller->callee edge exactly ONCE — so a whole-kernel boot's call
    GRAPH fits a small, boot-safe ring. Recursive fact(3) makes only two distinct edges:
    (root)->fact (the first, top-level call) and fact->fact (the recursion); the repeated
    fact->fact calls are deduped away."""
    _, header = CTransformer().runtime_asset()
    (tmp_path / "ouroboros_runtime.h").write_text(header, encoding="utf-8")
    (tmp_path / "kshim.h").write_text(
        "#pragma once\n#include <stdio.h>\n#include <stdint.h>\n#include <stdarg.h>\n"
        "static inline unsigned int atomic_inc_uint_nv(volatile unsigned int *p){ return ++*p; }\n",
        encoding="utf-8")
    prog = CTransformer().wrap_source(
        "int fact(int n) {\n    if (n <= 0) return 1;\n    return n * fact(n - 1);\n}\n",
        filename="prog.c", only={"fact"}, minimal=True).code
    (tmp_path / "t.c").write_text(
        '#include "kshim.h"\n#include "ouroboros_runtime.h"\n'
        + prog
        + 'int main(void){ fact(3); printf("BEFORE_DUMP\\n"); _ouro_dump(); return 0; }\n',
        encoding="utf-8")

    import subprocess
    subprocess.run(["gcc", "-std=gnu11", "-D_KERNEL", "-DOURO_KERNEL_TEST",
                    "-DOUROBOROS_KERNEL_RING", "-DOUROBOROS_RING_OWNER",
                    "-DOUROBOROS_MINIMAL_ONLY", "-DOUROBOROS_EDGE_DEDUP", "t.c", "-o", "t"],
                   cwd=tmp_path, check=True, capture_output=True)
    out = subprocess.run(["./t"], cwd=tmp_path, check=True,
                         capture_output=True, text=True).stdout
    _, _, post = out.partition("BEFORE_DUMP\n")
    assert "ouroboros ring dump: 2 records" in post   # only the two UNIQUE edges
    recs = [json.loads(ln) for ln in post.splitlines() if ln.strip().startswith("{")]
    assert all(r["p"] == "e" for r in recs)
    assert {(r["ca"], r["fn"]) for r in recs} == {("(root)", "fact"), ("fact", "fact")}


@pytest.mark.skipif(not has_gcc, reason="gcc not available")
def test_shared_ring_across_two_tus(tmp_path):
    """The SHARED cross-TU ring (-DOUROBOROS_RING_OWNER on ONE file): two ring-mode TUs
    link without a duplicate-symbol clash, write into ONE ring, and a single _ouro_dump
    (owned by the owner TU) prints BOTH — with depth nesting that crosses the file
    boundary (fa in file A calls fb in file B -> fb at dep 1, via the SHARED depth
    counter). This is what lets the whole kernel be instrumented into one ring and proved
    in one boot, instead of one file per boot."""
    _, header = CTransformer().runtime_asset()
    (tmp_path / "ouroboros_runtime.h").write_text(header, encoding="utf-8")
    (tmp_path / "kshim.h").write_text(
        "#pragma once\n#include <stdio.h>\n#include <stdlib.h>\n#include <stdint.h>\n"
        "#include <stdarg.h>\n#include <time.h>\n"
        "static inline void getnanouptime(struct timespec *ts){ timespec_get(ts, TIME_UTC); }\n"
        "static inline unsigned int atomic_inc_uint_nv(volatile unsigned int *p){ return ++*p; }\n"
        "#define MAXCPUS 4\n#define curcpu() ((const void *)0)\n"
        "static inline int cpu_index(const void *ci){ (void)ci; return 0; }\n"
        "struct proc { int p_pid; };\nstruct lwp { struct proc *l_proc; int l_lid; };\n"
        "static struct proc _ouro_test_proc = { 1 };\n"
        "static struct lwp _ouro_test_lwp = { &_ouro_test_proc, 1 };\n"
        "#define curlwp (&_ouro_test_lwp)\n", encoding="utf-8")
    a = CTransformer().wrap_source(
        "extern int fb(int);\nint fa(int n) {\n    return fb(n);\n}\n",
        filename="a.c", minimal=True).code
    b = CTransformer().wrap_source(
        "int fb(int n) {\n    return n * 2;\n}\n", filename="b.c", minimal=True).code
    (tmp_path / "a.c").write_text('#include "kshim.h"\n' + a, encoding="utf-8")
    (tmp_path / "b.c").write_text('#include "kshim.h"\n' + b, encoding="utf-8")
    (tmp_path / "main.c").write_text(
        # <stdio.h> for printf: gcc >= 14 promotes an implicit function
        # declaration to a hard error, which broke the link step below.
        "#include <stdio.h>\nint fa(int);\nvoid _ouro_dump(void);\n"
        'int main(void){ fa(7); printf("BEFORE_DUMP\\n"); _ouro_dump(); return 0; }\n',
        encoding="utf-8")

    import subprocess
    base = ["gcc", "-std=gnu11", "-D_KERNEL", "-DOURO_KERNEL_TEST", "-DOUROBOROS_KERNEL_RING"]
    # a.c is the OWNER (defines the ring + depth + _ouro_dump); b.c is a plain ring-mode TU
    subprocess.run([*base, "-DOUROBOROS_RING_OWNER", "-c", "a.c", "-o", "a.o"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run([*base, "-c", "b.c", "-o", "b.o"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["gcc", "-std=gnu11", "main.c", "a.o", "b.o", "-o", "t"],
                   cwd=tmp_path, check=True, capture_output=True)  # links: no duplicate symbol
    out = subprocess.run(["./t"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    _, _, post = out.partition("BEFORE_DUMP\n")
    recs = [json.loads(ln) for ln in post.splitlines() if ln.strip().startswith("{")]
    fns = [(r["fn"], r["dep"]) for r in recs]
    assert ("fa", 0) in fns, fns      # owner TU's function at depth 0
    assert ("fb", 1) in fns, fns      # OTHER TU's fn, nested dep 1 -> shared ring + depth


@pytest.mark.skipif(not has_gcc, reason="gcc not available")
def test_minimal_only_drops_full_probe_deps(tmp_path):
    """OUROBOROS_MINIMAL_ONLY gates out the FULL probe and its heavy includes
    (sys/proc.h, sys/lwp.h, getnanouptime) so a minimal-wrapped file compiles even
    where those would clash — the kern_mutex.c __MUTEX_PRIVATE include-ordering break
    that aborted the whole-kern sweep. Proven with a MINIMAL kshim that provides NONE
    of curlwp / struct lwp / getnanouptime: it must still build + run."""
    _, header = CTransformer().runtime_asset()
    (tmp_path / "ouroboros_runtime.h").write_text(header, encoding="utf-8")
    (tmp_path / "kshim.h").write_text(  # NO curlwp/lwp/proc/getnanouptime — minimal needs none
        "#pragma once\n#include <stdio.h>\n#include <stdlib.h>\n#include <stdint.h>\n"
        "static inline unsigned int atomic_inc_uint_nv(volatile unsigned int *p){ return ++*p; }\n"
        "#define MAXCPUS 4\n#define curcpu() ((const void *)0)\n"
        "static inline int cpu_index(const void *ci){ (void)ci; return 0; }\n", encoding="utf-8")
    prog = CTransformer().wrap_source(
        "int leaf(int n) {\n    return n + 1;\n}\n", filename="prog.c", minimal=True).code
    (tmp_path / "t.c").write_text(
        '#include "kshim.h"\n#include "ouroboros_runtime.h"\n' + prog
        + 'int main(void){ leaf(1); printf("BEFORE_DUMP\\n"); _ouro_dump(); return 0; }\n',
        encoding="utf-8")

    import subprocess
    subprocess.run(["gcc", "-std=gnu11", "-D_KERNEL", "-DOURO_KERNEL_TEST",
                    "-DOUROBOROS_KERNEL_RING", "-DOUROBOROS_RING_OWNER",
                    "-DOUROBOROS_MINIMAL_ONLY", "t.c", "-o", "t"],
                   cwd=tmp_path, check=True, capture_output=True)  # builds w/o curlwp/getnanouptime
    out = subprocess.run(["./t"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    _, _, post = out.partition("BEFORE_DUMP\n")
    recs = [json.loads(ln) for ln in post.splitlines() if ln.strip().startswith("{")]
    assert [r["fn"] for r in recs] == ["leaf"]      # the minimal probe ran, no full-probe deps
