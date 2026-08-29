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

    synced = finish(proj).synced
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


def test_module_header_follows_the_extension_not_the_parse(tmp_path):
    """`.mjs` gets `import`, `.cjs` gets `require`, whatever the file looks like.

    Babel is asked to parse "unambiguous", so a file with neither `import` nor
    `export` — most files — comes back as a script. Believing that verdict put
    `require(...)` at the top of every `.mjs` file, and node killed all of them
    with "require is not defined in ES module scope": 16 of 17 programs in the
    equivalence corpus. Node decides from the name, so the backend must too.
    """
    tx = JavaScriptTransformer()
    src = "function add(a, b) { return a + b; }\n"
    assert 'import _ouro_rt from "./ouroboros_runtime.js"' in \
        tx.wrap_source(src, filename="prog.mjs").code
    assert 'require("./ouroboros_runtime.js")' in \
        tx.wrap_source(src, filename="prog.cjs").code


def test_plain_js_header_follows_the_nearest_package_json(tmp_path):
    """For a `.js` file the extension says nothing; node resolves the module
    system from the closest package.json, so reading the same file is the only
    way to agree with it."""
    tx = JavaScriptTransformer()
    src = "function add(a, b) { return a + b; }\n"
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    esm = tmp_path / "prog.js"
    esm.write_text(src, encoding="utf-8")
    assert 'import _ouro_rt from "./ouroboros_runtime.js"' in \
        tx.wrap_source(src, filename=str(esm)).code

    cjs_dir = tmp_path / "sub"
    cjs_dir.mkdir()
    (cjs_dir / "package.json").write_text('{"type": "commonjs"}', encoding="utf-8")
    assert 'require("./ouroboros_runtime.js")' in \
        tx.wrap_source(src, filename=str(cjs_dir / "prog.js")).code


def test_shebang_and_use_strict_keep_their_place(tmp_path):
    """Both only work while they are first: a `#!` below line 1 is not read by
    the kernel at all, and a demoted "use strict" silently stops applying — the
    program keeps running, in the other mode."""
    tx = JavaScriptTransformer()
    code = tx.wrap_source(
        '#!/usr/bin/env node\n"use strict";\nfunction f() { return 1; }\n',
        filename="prog.js").code
    lines = [ln for ln in code.splitlines() if ln.strip()]
    assert lines[0] == "#!/usr/bin/env node"
    assert lines[1] == '"use strict";'
    assert "ouroboros_runtime.js" in lines[2]

    body = tx.wrap_source(
        'function f() { "use strict"; return 1; }\n', filename="prog.js").code
    directive = body.index('"use strict"')
    assert body.index("__ouro_ctx") > directive
