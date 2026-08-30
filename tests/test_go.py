"""Tests for the Go backend (go/parser range emitter + named results + defer)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from ouroboros.languages import CorruptedSourceError, transformer_for_path
from ouroboros.languages.go_lang import (
    GoEmitterError,
    GoTransformer,
    build_emitter,
    emit_ranges,
    emitter_path,
    package_name,
)
from ouroboros.trace import load

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go not available")

TIMEOUT = 300


@pytest.fixture
def tx() -> GoTransformer:
    return GoTransformer()


# --------------------------------------------------------------------------- #
# registry and assets
# --------------------------------------------------------------------------- #


def test_registry_resolves_by_extension():
    assert isinstance(transformer_for_path("a.go"), GoTransformer)


def test_runtime_asset_is_go_and_self_contained(tx):
    name, src = tx.runtime_asset()
    assert name == "ouroboros_runtime.go"
    assert src.lstrip().startswith("//")
    assert "\npackage main\n" in src
    assert "func _ouroEnter(" in src
    # Standard library only: a helper pulling in a third-party module would need
    # a go.mod entry and a network fetch in every tree it is dropped into.
    stdlib = {"crypto/rand", "fmt", "os", "runtime", "strconv", "strings", "time",
              "unicode/utf8"}
    imports = {line.strip().strip('"')
               for line in src.split("import (", 1)[1].split("\n)", 1)[0].splitlines()
               if line.strip()}
    assert imports <= stdlib, f"non-stdlib import: {imports - stdlib}"


def test_runtime_asset_for_takes_the_package_of_the_file_it_joins(tx):
    _, src = tx.runtime_asset_for("package deepthought\n\nfunc f() {}\n")
    assert "\npackage deepthought\n" in src
    assert "\npackage main\n" not in src


def test_runtime_asset_for_falls_back_to_main_without_a_package_clause(tx):
    _, src = tx.runtime_asset_for("// no package clause at all\n")
    assert "\npackage main\n" in src


@pytest.mark.parametrize(("source", "expected"), [
    ("package main\n", "main"),
    ("\n\n   package spaced\n", "spaced"),
    ("// a line comment\npackage after_line\n", "after_line"),
    ("//go:build linux\n\npackage tagged\n", "tagged"),
    ("/* block\n   comment */ package after_block\n", "after_block"),
    ("/* one */ /* two */\npackage twice\n", "twice"),
    ("", None),
    ("/* never closed\n", None),
    ("import \"fmt\"\n", None),
])
def test_package_name_reads_the_clause(source, expected):
    """Only comments and whitespace may precede a Go package clause, so skipping
    those and reading the next word is exact rather than a guess."""
    assert package_name(source) == expected


# --------------------------------------------------------------------------- #
# the emitter contract
# --------------------------------------------------------------------------- #


def test_emitter_reports_bodies_params_and_results():
    src = b"package p\n\nfunc add(a, b int) int { return a + b }\n"
    unit = emit_ranges(src, filename="p.go")
    assert unit.package == "p"
    assert unit.error_count == 0
    (fn,) = unit.functions
    assert fn.name == "add" and fn.qualified_name == "add"
    assert src[fn.body_start:fn.body_start + 1] == b"{"
    assert src[fn.body_end:fn.body_end + 1] == b"}"
    assert [p.name for p in fn.params] == ["a", "b"]
    assert all(p.usable for p in fn.params)
    assert fn.results is not None and not fn.results.parenthesized
    assert src[fn.results.start:fn.results.end] == b"int"


def test_emitter_names_methods_the_way_the_runtime_does():
    src = (b"package p\n"
           b"type C struct{}\n"
           b"func (c C) Value() int { return 1 }\n"
           b"func (c *C) Set(v int) {}\n"
           b"type Box[T any] struct{}\n"
           b"func (b Box[T]) Get() int { return 0 }\n")
    names = {fn.name: fn.qualified_name for fn in emit_ranges(src, filename="p.go").functions}
    assert names == {"Value": "C.Value", "Set": "(*C).Set", "Get": "Box.Get"}


def test_emitter_skips_a_declaration_with_no_body():
    """`func f()` with no body is implemented in assembly elsewhere: there is no
    brace to splice into and nothing to instrument."""
    src = b"package p\n\nfunc external(a int) int\n\nfunc here() int { return 1 }\n"
    assert [fn.name for fn in emit_ranges(src, filename="p.go").functions] == ["here"]


def test_emitter_marks_unreadable_parameters():
    src = b"package p\n\nfunc f(_ int, y string) {}\n\nfunc g(int, string) {}\n"
    fns = {fn.name: fn for fn in emit_ranges(src, filename="p.go").functions}
    assert [(p.name, p.usable) for p in fns["f"].params] == [("_", False), ("y", True)]
    assert [(p.name, p.usable) for p in fns["g"].params] == [("", False), ("", False)]


def test_emitter_reports_every_syntax_error_not_only_the_first():
    src = b"package p\n\nfunc f( { }\n\nfunc g( { }\n"
    with pytest.raises(CorruptedSourceError) as e:
        emit_ranges(src, filename="broken.go")
    assert e.value.language == "go"
    assert "broken.go" in str(e.value)


def test_emitter_offsets_are_bytes_not_characters():
    """A non-ASCII comment above the function shifts byte and character offsets
    apart; splicing on the wrong one lands mid-identifier."""
    src = "package p\n\n// комментарий\nfunc f() int { return 1 }\n".encode()
    (fn,) = emit_ranges(src, filename="p.go").functions
    assert src[fn.body_start:fn.body_start + 1] == b"{"


def test_emitter_path_is_cached_and_overridable(tmp_path, monkeypatch):
    monkeypatch.setenv("OUROBOROS_GO_EMITTER", "/nowhere/emitter")
    emitter_path.cache_clear()
    try:
        assert emitter_path() == "/nowhere/emitter"
    finally:
        emitter_path.cache_clear()


def test_emit_ranges_reports_a_broken_emitter_as_a_toolchain_problem(tmp_path):
    """A helper that cannot run is NOT a corrupted source. Saying otherwise is
    how a caller ends up rewriting a file that was fine."""
    fake = tmp_path / "not-an-emitter"
    fake.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
    fake.chmod(0o755)
    with pytest.raises(GoEmitterError, match="exited with 9"):
        emit_ranges(b"package p\n", filename="p.go", emitter=str(fake))


def test_emit_ranges_rejects_non_json_from_the_emitter(tmp_path):
    fake = tmp_path / "chatty"
    fake.write_text("#!/bin/sh\necho not json\n", encoding="utf-8")
    fake.chmod(0o755)
    with pytest.raises(GoEmitterError, match="not JSON"):
        emit_ranges(b"package p\n", filename="p.go", emitter=str(fake))


def test_emit_ranges_reports_an_emitter_that_could_not_read_the_source(tmp_path):
    fake = tmp_path / "refuses"
    fake.write_text('#!/bin/sh\nprintf \'{"ok":false,"error":"stdin died"}\'\n',
                    encoding="utf-8")
    fake.chmod(0o755)
    with pytest.raises(GoEmitterError, match="stdin died"):
        emit_ranges(b"package p\n", filename="p.go", emitter=str(fake))


def test_emit_ranges_reports_a_missing_emitter_binary(tmp_path):
    with pytest.raises(GoEmitterError, match="cannot run"):
        emit_ranges(b"package p\n", filename="p.go", emitter=str(tmp_path / "absent"))


def test_build_emitter_reports_a_failed_build(tmp_path, monkeypatch):
    import ouroboros.languages.go_lang as go_lang
    monkeypatch.setattr(go_lang, "_EMITTER_SRC", tmp_path / "broken.go")
    (tmp_path / "broken.go").write_text("package main\nfunc main() { undefined() }\n",
                                        encoding="utf-8")
    with pytest.raises(GoEmitterError, match="failed"):
        build_emitter(tmp_path / "out")


def test_build_emitter_needs_a_go_command(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("GO", raising=False)
    with pytest.raises(GoEmitterError, match="no go command found"):
        build_emitter(tmp_path / "out")


# --------------------------------------------------------------------------- #
# the wrap itself
# --------------------------------------------------------------------------- #


def test_simple_function_is_wrapped_and_names_its_result(tx):
    res = tx.wrap_source("package main\n\nfunc add(a, b int) int { return a + b }\n",
                         filename="m.go")
    assert res.functions_wrapped == 1
    assert "func add(a, b int) (__ouro_r0 int) {" in res.code
    assert '__ouro_ctx := _ouroEnter("add", a, b)' in res.code
    assert "_ouroReturned(__ouro_ctx, __ouro_r0)" in res.code
    assert "_ouroPanicked(__ouro_ctx, __ouro_p)" in res.code


def test_no_import_line_is_added(tx):
    """The helper is a sibling file in the same package, so nothing is spliced
    above the file's header — which is what keeps a //go:build constraint and a
    package doc comment where the language requires them."""
    src = "//go:build linux\n\n// Package m does things.\npackage m\n\nfunc f() {}\n"
    res = tx.wrap_source(src, filename="m.go")
    assert res.functions_wrapped == 1
    assert res.code.startswith("//go:build linux\n\n// Package m does things.\npackage m\n")
    assert "import" not in res.code.split("func f")[0].replace("//go:build", "")


def test_existing_named_results_are_reused_not_renamed(tx):
    """The body may refer to those names — renaming them would break it."""
    src = "package m\n\nfunc f() (x int, err error) {\n\tx = 1\n\treturn\n}\n"
    res = tx.wrap_source(src, filename="m.go")
    assert "func f() (x int, err error) {" in res.code
    assert "_ouroReturned(__ouro_ctx, x, err)" in res.code
    assert "__ouro_r0" not in res.code


def test_blank_result_name_is_replaced_and_the_readable_one_kept(tx):
    src = "package m\n\nfunc f() (_ int, err error) { return 0, nil }\n"
    res = tx.wrap_source(src, filename="m.go")
    assert "func f() (__ouro_r0 int, err error) {" in res.code
    assert "_ouroReturned(__ouro_ctx, __ouro_r0, err)" in res.code


def test_unparenthesized_single_result_gains_brackets(tx):
    res = tx.wrap_source("package m\n\nfunc f() int { return 1 }\n", filename="m.go")
    assert "func f() (__ouro_r0 int) {" in res.code


def test_several_unnamed_results_are_all_named(tx):
    res = tx.wrap_source("package m\n\nfunc f() (int, error) { return 1, nil }\n",
                         filename="m.go")
    assert "func f() (__ouro_r0 int, __ouro_r1 error) {" in res.code
    assert "_ouroReturned(__ouro_ctx, __ouro_r0, __ouro_r1)" in res.code


def test_grouped_result_names_are_all_read(tx):
    res = tx.wrap_source("package m\n\nfunc f() (a, b int) { return 1, 2 }\n",
                         filename="m.go")
    assert "_ouroReturned(__ouro_ctx, a, b)" in res.code


def test_function_without_results_records_no_value(tx):
    res = tx.wrap_source("package m\n\nfunc f() {}\n", filename="m.go")
    assert "_ouroReturned(__ouro_ctx)\n" in res.code
    assert "__ouro_r0" not in res.code


def test_unreadable_parameters_become_a_placeholder(tx):
    res = tx.wrap_source("package m\n\nfunc f(_ int, y string) {}\n", filename="m.go")
    assert '_ouroEnter("f", _ouroOmitted, y)' in res.code


def test_function_literals_are_skipped(tx):
    """A `func(x int) int { ... }` in a variable or an argument is left alone —
    the Go analogue of skipping Python lambdas and concise-body arrows. Calls
    through one therefore never reach the trace, which is a documented gap
    rather than an accident."""
    src = ("package m\n\nfunc apply(f func(int) int, v int) int { return f(v) }\n\n"
           "func use() int {\n\tdouble := func(x int) int { return x * 2 }\n"
           "\treturn apply(double, 21)\n}\n")
    res = tx.wrap_source(src, filename="m.go")
    assert res.functions_wrapped == 2, "apply and use, not the literal"
    assert res.code.count("_ouroEnter(") == 2
    assert "double := func(x int) int { return x * 2 }" in res.code


def test_method_records_its_qualified_name(tx):
    src = ("package m\n\ntype C struct{ n int }\n\n"
           "func (c *C) Bump(by int) int { c.n += by; return c.n }\n")
    res = tx.wrap_source(src, filename="m.go")
    assert '_ouroEnter("(*C).Bump", by)' in res.code


def test_wrapping_is_idempotent(tx):
    src = "package m\n\nfunc f() int { return 1 }\n"
    once = tx.wrap_source(src, filename="m.go")
    twice = tx.wrap_source(once.code, filename="m.go")
    assert twice.functions_wrapped == 0
    assert twice.code == once.code


def test_second_pass_adds_only_the_new_function(tx):
    src = "package m\n\nfunc f() int { return 1 }\n"
    once = tx.wrap_source(src, filename="m.go")
    grown = once.code + "\nfunc g() int { return 2 }\n"
    again = tx.wrap_source(grown, filename="m.go")
    assert again.functions_wrapped == 1
    assert again.code.count("_ouroEnter(") == 2


def test_selective_mode_leaves_other_functions_alone(tx):
    src = "package m\n\nfunc f() int { return 1 }\n\nfunc g() int { return 2 }\n"
    res = tx.wrap_source(src, filename="m.go", only={"g"})
    assert res.functions_wrapped == 1
    assert '_ouroEnter("g")' in res.code
    assert '_ouroEnter("f")' not in res.code
    assert "func f() int { return 1 }" in res.code


def test_the_sink_itself_is_never_wrapped(tx):
    """Instrumenting the helper would make every logged call log its own
    logging, until the program died of it."""
    _, helper = tx.runtime_asset()
    res = tx.wrap_source(helper, filename="ouroboros_runtime.go")
    assert res.functions_wrapped == 0
    assert res.code == helper


def test_minimal_mode_is_refused(tx):
    with pytest.raises(NotImplementedError):
        tx.wrap_source("package m\n", filename="m.go", minimal=True)


def test_corrupted_source_is_rejected(tx):
    with pytest.raises(CorruptedSourceError) as e:
        tx.wrap_source("package m\n\nfunc f( {\n", filename="m.go")
    assert e.value.language == "go"


def test_wrapped_output_is_still_valid_go(tx, tmp_path):
    """The strongest check the transformer alone can make: hand its output back
    to the real parser."""
    src = ("package m\n\ntype C struct{ n int }\n\n"
           "func (c *C) Bump(by int) (int, error) { return c.n + by, nil }\n"
           "func f() (x int, err error) { return }\n")
    res = tx.wrap_source(src, filename="m.go")
    unit = emit_ranges(res.code.encode("utf-8"), filename="m.go")
    assert unit.error_count == 0
    assert len(unit.functions) == 2


# --------------------------------------------------------------------------- #
# end to end: build it and read the trace
# --------------------------------------------------------------------------- #


def _build_and_run(tx, root, source, *, argv_env=None, filename="prog.go"):
    """Wrap, drop the helper, compile and run — returning the parsed trace."""
    res = tx.wrap_source(source, filename=filename)
    (root / filename).write_text(res.code, encoding="utf-8")
    name, helper = tx.runtime_asset_for(res.code)
    (root / name).write_text(helper, encoding="utf-8")
    build = subprocess.run(["go", "build", "-o", "prog.bin", filename, name],
                           cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
    assert build.returncode == 0, build.stderr
    debug = root / "debug.info"
    env = {**os.environ, "OUROBOROS_DEBUG_INFO": str(debug), **(argv_env or {})}
    run = subprocess.run(["./prog.bin"], cwd=root, capture_output=True, text=True,
                         env=env, timeout=TIMEOUT)
    return run, load(debug.read_text(encoding="utf-8"))


def test_end_to_end_records_the_call(tx, tmp_path):
    src = ('package main\n\nimport "fmt"\n\n'
           "func add(a, b int) int { return a + b }\n\n"
           "func main() { fmt.Println(add(2, 3)) }\n")
    run, trace = _build_and_run(tx, tmp_path, src)
    assert run.returncode == 0 and run.stdout == "5\n"
    add = next(r for r in trace.calls if r.name == "add")
    assert add.args == "2, 3"
    assert add.kwargs == ""
    assert add.outcome == "5"
    assert add.outcome_kind == "result"
    assert add.cpu is None, "-1 is the contract's unknown, parsed as None"
    process, goroutine = add.thread.split(".")
    assert process.isdigit() and goroutine.isdigit(), (
        f"`th` is <process>.<goroutine>, got {add.thread!r}")


def test_end_to_end_records_a_panic_with_type_and_message(tx, tmp_path):
    src = ('package main\n\nimport "fmt"\n\n'
           'func boom() { panic("bad") }\n\n'
           "func main() {\n"
           "\tdefer func() { fmt.Println(\"caught\", recover()) }()\n"
           "\tboom()\n}\n")
    run, trace = _build_and_run(tx, tmp_path, src)
    assert run.returncode == 0 and run.stdout == "caught bad\n"
    boom = next(r for r in trace.calls if r.name == "boom")
    assert boom.outcome_kind == "raised"
    assert boom.outcome == "string: bad"


def test_a_panic_the_program_recovers_itself_is_a_normal_return(tx, tmp_path):
    """Our deferred closure is registered first, so it runs LAST. By then the
    function's own recover has already swallowed the panic — and from the
    caller's side the call did return normally, which is what gets recorded."""
    src = ('package main\n\nimport "fmt"\n\n'
           "func safe() (out string) {\n"
           "\tdefer func() {\n"
           '\t\tif r := recover(); r != nil { out = "caught" }\n'
           "\t}()\n"
           '\tpanic("inner")\n}\n\n'
           "func main() { fmt.Println(safe()) }\n")
    run, trace = _build_and_run(tx, tmp_path, src)
    assert run.stdout == "caught\n"
    safe = next(r for r in trace.calls if r.name == "safe")
    assert safe.outcome_kind == "result"
    assert safe.outcome == '"caught"'


def test_a_deferred_result_change_is_what_gets_recorded(tx, tmp_path):
    """A user defer that rewrites a named result runs BEFORE ours, so the record
    carries the value the caller actually receives."""
    src = ('package main\n\nimport "fmt"\n\n'
           "func f() (n int) {\n\tdefer func() { n *= 10 }()\n\treturn 4\n}\n\n"
           "func main() { fmt.Println(f()) }\n")
    run, trace = _build_and_run(tx, tmp_path, src)
    assert run.stdout == "40\n"
    assert next(r for r in trace.calls if r.name == "f").outcome == "40"


def test_a_hard_exit_leaves_an_entry_with_no_completion(tx, tmp_path):
    """os.Exit skips every defer, so the `out` line is genuinely absent — which
    is exactly the signal two records per call exist to give."""
    src = ('package main\n\nimport "os"\n\n'
           "func leave() { os.Exit(3) }\n\n"
           "func main() { leave() }\n")
    run, trace = _build_and_run(tx, tmp_path, src)
    assert run.returncode == 3
    assert [c["name"] for c in trace.in_flight] == ["main", "leave"]


def test_goroutines_get_different_thread_tokens(tx, tmp_path):
    src = ('package main\n\nimport "sync"\n\n'
           "func work(i int) int { return i * 2 }\n\n"
           "func main() {\n"
           "\tvar wg sync.WaitGroup\n"
           "\tfor i := 0; i < 4; i++ {\n"
           "\t\twg.Add(1)\n"
           "\t\tgo func(n int) { defer wg.Done(); work(n) }(i)\n"
           "\t}\n\twg.Wait()\n}\n")
    run, trace = _build_and_run(tx, tmp_path, src)
    assert run.returncode == 0
    threads = {r.thread for r in trace.calls if r.name == "work"}
    assert len(threads) == 4, f"four goroutines, {len(threads)} thread token(s): {threads}"
    assert all(t.count(".") == 1 and all(part for part in t.split(".")) for t in threads)


def test_call_ids_differ_between_processes(tx, tmp_path):
    """Two processes started in the same second must not draw the same ids: the
    id is the only thing pairing an `in` with its `out`, and a sink seeded from
    the clock pairs records across process boundaries."""
    src = ('package main\n\n'
           "func tick(a int) int { return a + 1 }\n\n"
           "func main() { tick(1) }\n")
    ids = []
    for run in range(6):
        d = tmp_path / f"r{run}"
        d.mkdir()
        _, trace = _build_and_run(tx, d, src)
        ids.append(next(r for r in trace.calls if r.name == "tick").call_id)
    assert len(set(ids)) == len(ids), f"repeated call ids across processes: {ids}"


def test_long_values_are_capped_and_the_record_fits_pipe_buf(tx, tmp_path):
    params = ", ".join(f"a{i} string" for i in range(30))
    args = ", ".join('"' + "y" * 400 + '"' for _ in range(30))
    src = (f'package main\n\nfunc many({params}) string {{ return "{"z" * 400}" }}\n\n'
           f"func main() {{ many({args}) }}\n")
    run, _ = _build_and_run(tx, tmp_path, src)
    assert run.returncode == 0
    for line in (tmp_path / "debug.info").read_text(encoding="utf-8").splitlines():
        assert len(line.encode("utf-8")) + 1 <= 4096, f"{len(line) + 1} bytes > PIPE_BUF"
        json.loads(line)  # a torn or half-escaped line would not parse


def test_a_shortened_value_does_not_split_a_character(tx, tmp_path):
    """Half a character makes the whole JSON line undecodable, losing the record
    the ceiling exists to save."""
    big = "щ" * 900  # two bytes each, so a byte-count cut lands mid-character
    params = ", ".join(f"a{i} string" for i in range(30))
    args = ", ".join(f'"{big}"' for _ in range(30))
    src = (f"package main\n\nfunc many({params}) int {{ return 1 }}\n\n"
           f"func main() {{ many({args}) }}\n")
    run, _ = _build_and_run(tx, tmp_path, src)
    assert run.returncode == 0
    lines = (tmp_path / "debug.info").read_text(encoding="utf-8").splitlines()
    entry = next(json.loads(ln) for ln in lines
                 if json.loads(ln).get("fn") == "many" and json.loads(ln)["p"] == "in")
    assert entry["a"].endswith("…"), "a shortened field must say so"
    # Reading the file as UTF-8 above already fails on a character cut in half.
    # This pins the other half of the claim: nothing but whole `щ`s, the joiner
    # and the ellipsis survived, so no byte of a split character was kept either.
    assert set(entry["a"]) <= {"щ", ",", " ", "…", '"'}, sorted(set(entry["a"]))


def test_one_long_value_is_capped_on_its_own(tx, tmp_path):
    """The per-value ceiling, isolated from the per-record one.

    One 1000-byte argument makes a record of about 1150 bytes — well under the
    4096-byte record ceiling — so if `a` comes back short, only the per-value
    ceiling can have shortened it. Without this the record ceiling covered for a
    missing per-value ceiling, and a single huge argument would have gone into
    the file whole.
    """
    big = "y" * 1000
    src = ("package main\n\nfunc one(s string) int { return len(s) }\n\n"
           f'func main() {{ one("{big}") }}\n')
    run, trace = _build_and_run(tx, tmp_path, src)
    assert run.returncode == 0
    one = next(r for r in trace.calls if r.name == "one")
    assert one.args.endswith("…"), "a shortened value must say so"
    # 200 bytes of value, the two quotes Go's %q adds, and the ellipsis.
    assert len(one.args.encode("utf-8")) <= 200 + 2 + len("…".encode())


def test_duration_comes_from_a_monotonic_reading(tx):
    """`d` must not move when the wall clock is stepped.

    Go's time.Now() carries a monotonic reading and time.Since uses it — unless
    the value is stripped (`.Round(0)`) or the difference is taken from the wall
    fields (`UnixNano`). Stepping the clock needs root, so a test cannot make the
    two disagree; this pins the construct instead of the symptom, and is
    deliberately the weakest test in this file.
    """
    _, helper = tx.runtime_asset()
    assert "seconds := time.Since(c.t0).Seconds()" in helper
    assert "\tc.t0 = time.Now()\n" in helper
    assert ".Round(0)" not in helper
    assert "UnixNano" not in helper


def test_reported_duration_excludes_the_sinks_own_write(tx, tmp_path):
    """`d` must measure the call, not the logging: the duration clock starts
    after the entry record is written."""
    src = ('package main\n\n'
           "func tick(a int) int { return a + 1 }\n\n"
           "func main() {\n\tv := 0\n\tfor i := 0; i < 200; i++ { v = tick(v) }\n}\n")
    run, trace = _build_and_run(tx, tmp_path, src)
    assert run.returncode == 0
    ticks = sorted(r.duration for r in trace.calls
                   if r.name == "tick" and r.duration is not None)
    assert len(ticks) == 200
    assert ticks[len(ticks) // 2] < 8e-6, f"median {ticks[len(ticks) // 2] * 1e6:.2f} us"


def test_helper_compiles_inside_an_older_module(tx, tmp_path):
    """The helper is compiled inside whatever module it is dropped into, whose
    `go` directive can be well behind the toolchain. A construct newer than that
    directive stops the user's build with an error about OUR file."""
    (tmp_path / "go.mod").write_text("module old\n\ngo 1.18\n", encoding="utf-8")
    src = "package old\n\nfunc f(a int) int { return a + 1 }\n"
    res = tx.wrap_source(src, filename="f.go")
    (tmp_path / "f.go").write_text(res.code, encoding="utf-8")
    name, helper = tx.runtime_asset_for(res.code)
    (tmp_path / name).write_text(helper, encoding="utf-8")
    build = subprocess.run(["go", "build", "./..."], cwd=tmp_path, capture_output=True,
                           text=True, timeout=TIMEOUT,
                           env={**os.environ, "GOTOOLCHAIN": "local"})
    assert build.returncode == 0, build.stderr


def test_helper_beside_a_library_package_compiles(tx, tmp_path):
    """The whole reason `runtime_asset_for` exists: a helper still saying
    `package main` next to a library file does not compile, and nothing else in
    the tree would notice."""
    (tmp_path / "go.mod").write_text("module lib\n\ngo 1.21\n", encoding="utf-8")
    src = "package shapes\n\nfunc Area(w, h int) int { return w * h }\n"
    res = tx.wrap_source(src, filename="shapes.go")
    (tmp_path / "shapes.go").write_text(res.code, encoding="utf-8")
    name, helper = tx.runtime_asset_for(res.code)
    (tmp_path / name).write_text(helper, encoding="utf-8")
    build = subprocess.run(["go", "build", "./..."], cwd=tmp_path, capture_output=True,
                           text=True, timeout=TIMEOUT)
    assert build.returncode == 0, build.stderr


def test_the_shipped_helper_alone_would_not_compile_there(tx, tmp_path):
    """The other half of the same claim, so the test above cannot pass by luck."""
    (tmp_path / "go.mod").write_text("module lib\n\ngo 1.21\n", encoding="utf-8")
    (tmp_path / "shapes.go").write_text("package shapes\n\nfunc Area() int { return 1 }\n",
                                        encoding="utf-8")
    name, helper = tx.runtime_asset()
    (tmp_path / name).write_text(helper, encoding="utf-8")
    build = subprocess.run(["go", "build", "./..."], cwd=tmp_path, capture_output=True,
                           text=True, timeout=TIMEOUT)
    assert build.returncode != 0
    assert "package" in build.stderr


def test_wrapping_the_tools_own_go_sources(tx):
    """Wrapping the tool's own tree is the sharpest test it has. The emitter is
    ordinary Go with methods, generics-free signatures and named results."""
    from ouroboros.languages.go_lang import _EMITTER_SRC

    source = _EMITTER_SRC.read_text(encoding="utf-8")
    res = tx.wrap_source(source, filename=str(_EMITTER_SRC))
    assert res.functions_wrapped == 5
    unit = emit_ranges(res.code.encode("utf-8"), filename="emitter.go")
    assert unit.error_count == 0


def test_python_is_not_routed_to_go():
    """A guard against the registry mapping the wrong extension: `.go` is the
    only thing this backend owns."""
    assert transformer_for_path("a.py").language == "python"
    assert transformer_for_path("a.go").language == "go"
    assert sys.version_info >= (3, 12)


# --------------------------------------------------------------------------- #
# building the emitter: the paths a normal run never takes
# --------------------------------------------------------------------------- #


def test_a_cold_cache_builds_the_emitter_and_a_warm_one_does_not(tmp_path, monkeypatch):
    import ouroboros.languages.go_lang as go_lang

    monkeypatch.setattr(go_lang, "_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.delenv("OUROBOROS_GO_EMITTER", raising=False)
    emitter_path.cache_clear()
    try:
        built = emitter_path()
        assert os.path.isfile(built)
        assert os.path.dirname(built) == str(tmp_path / "cache")
        stamp = os.stat(built).st_mtime_ns

        emitter_path.cache_clear()
        assert emitter_path() == built
        assert os.stat(built).st_mtime_ns == stamp, "it was rebuilt needlessly"

        unit = emit_ranges(b"package p\n\nfunc f(a int) int { return a }\n",
                           filename="x.go", emitter=built)
        assert [fn.name for fn in unit.functions] == ["f"]
    finally:
        emitter_path.cache_clear()


def test_a_go_command_that_cannot_be_started_says_so(monkeypatch, tmp_path):
    import ouroboros.languages.go_lang as go_lang

    def refuse(*_args, **_kwargs):
        raise OSError("no such thing")

    monkeypatch.setattr(go_lang.subprocess, "run", refuse)
    with pytest.raises(GoEmitterError, match="cannot run the go command"):
        build_emitter(tmp_path / "out")


def test_a_build_that_never_finishes_says_so(monkeypatch, tmp_path):
    import ouroboros.languages.go_lang as go_lang

    def hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="go build", timeout=300)

    monkeypatch.setattr(go_lang.subprocess, "run", hang)
    with pytest.raises(GoEmitterError, match="timed out"):
        build_emitter(tmp_path / "out")


def test_a_parse_that_never_finishes_says_so(monkeypatch):
    """A hung helper is a toolchain problem, not a corrupted source: answering
    "your code is corrupt" is how a caller ends up rewriting a file that
    was fine."""
    import ouroboros.languages.go_lang as go_lang

    def hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="emitter", timeout=go_lang.EMIT_TIMEOUT)

    monkeypatch.setattr(go_lang.subprocess, "run", hang)
    with pytest.raises(GoEmitterError, match="did not finish within"):
        emit_ranges(b"package p\n", filename="slow.go", emitter="/bin/true")
