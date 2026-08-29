"""Tests for the JavaScript/TypeScript backend (babel emitter + splice)."""

from __future__ import annotations

import shutil

import pytest

from ouroboros.languages import CorruptedSourceError, transformer_for_path
from ouroboros.languages.javascript import JavaScriptTransformer
from ouroboros.sandbox import Project, execute, finish, write_file
from ouroboros.trace import load

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


@pytest.fixture
def tx() -> JavaScriptTransformer:
    return JavaScriptTransformer()


def test_registry_resolves_by_extension():
    assert isinstance(transformer_for_path("a.ts"), JavaScriptTransformer)
    assert isinstance(transformer_for_path("a.jsx"), JavaScriptTransformer)


def test_example_sum_wraps_and_reparses(tx):
    res = tx.wrap_source("function sum(a, b) {\n    return a + b;\n}\n", filename="m.js")
    assert res.functions_wrapped == 1
    assert '_ouro_rt = require("./ouroboros_runtime.js")' in res.code
    assert "_ouro_rt.enter(\"sum\", [a, b])" in res.code
    # Wrapping the output again parses it (emitter runs first) and is a no-op.
    again = tx.wrap_source(res.code, filename="m.js")
    assert again.functions_wrapped == 0
    assert again.code == res.code


def test_nested_functions_each_wrapped(tx):
    src = "function outer(a) {\n    function inner(b) { return b + 1; }\n    return inner(a);\n}\n"
    res = tx.wrap_source(src, filename="m.js")
    assert res.functions_wrapped == 2
    assert res.code.count("_ouro_rt.enter(") == 2


def test_bare_return_handled(tx):
    res = tx.wrap_source("function f(x) {\n    if (x) return;\n    return x;\n}\n", filename="m.js")
    assert "__ouro_result = void 0" in res.code


def test_concise_arrow_skipped(tx):
    res = tx.wrap_source("const f = x => x + 1;\n", filename="m.js")
    assert res.functions_wrapped == 0
    assert "_ouro_rt" not in res.code


def test_esm_uses_import(tx):
    src = 'import {x} from "y";\nexport function g(a) { return a; }\n'
    res = tx.wrap_source(src, filename="m.js")
    assert 'import _ouro_rt from "./ouroboros_runtime.js";' in res.code


def test_typescript_annotations_wrap(tx):
    src = "function sum(a: number, b: number): number {\n    return a + b;\n}\n"
    res = tx.wrap_source(src, filename="m.ts")
    assert res.functions_wrapped == 1
    # type annotations are preserved (locate-then-splice never reprints)
    assert "a: number" in res.code


def test_corrupted_js_raises(tx):
    with pytest.raises(CorruptedSourceError) as ei:
        tx.wrap_source("function broken( {\n", filename="bad.js")
    assert ei.value.language == "javascript"


def test_runtime_asset_is_js(tx):
    name, src = tx.runtime_asset()
    assert name == "ouroboros_runtime.js"
    assert '"p":"in"' in src or 'p: "in"' in src


def test_end_to_end_via_sandbox_writes_debug_info(tmp_path):
    proj = Project.create(tmp_path / "site")
    out = write_file(
        proj,
        "main.js",
        "function greet(name) {\n    return 'hi ' + name;\n}\nconsole.log(greet('world'));\n",
    )
    assert out.wrapped and out.language == "javascript"
    # the JS runtime helper was dropped into the draft
    assert (proj.draft / "ouroboros_runtime.js").is_file()

    res = execute(proj, ["node", "main.js"])
    assert res.returncode == 0, res.stderr
    assert "hi world" in res.stdout

    loaded = load(proj.debug_info_path().read_text(encoding="utf-8"))
    assert loaded.malformed == 0 and not loaded.in_flight
    greet = [c for c in loaded.calls if c.name == "greet"]
    assert len(greet) == 1
    assert greet[0].outcome_kind == "result" and greet[0].outcome == '"hi world"'
    assert greet[0].duration is not None

    synced = finish(proj)
    assert "main.js" in synced
    assert "ouroboros_runtime.js" in synced


def test_end_to_end_logs_exception(tmp_path):
    proj = Project.create(tmp_path / "site")
    write_file(
        proj,
        "boom.js",
        "function bad() {\n    throw new TypeError('kaboom');\n}\ntry { bad(); } catch (e) {}\n",
    )
    execute(proj, ["node", "boom.js"])
    loaded = load(proj.debug_info_path().read_text(encoding="utf-8"))
    bad = [c for c in loaded.calls if c.name == "bad"]
    assert len(bad) == 1
    assert bad[0].outcome_kind == "raised" and bad[0].outcome == "TypeError: kaboom"
