"""JavaScript / TypeScript backend — ``try/finally`` body instrumentation.

JS has no decorator for plain functions, so (unlike Python) this backend follows
the ``try/finally`` + ``return (__result = expr)`` shape from example.md, but
routes the actual logging through the ``ouroboros_runtime.js`` helper instead of
``console.log`` (see SPEC.md).

All AST work is delegated to an external node range-emitter
(``_js/emitter.js``, using ``@babel/parser``); this module only splices text at
the byte ranges it returns — the Elixir-port-friendly "core just orchestrates"
shape. Concise-body arrow functions (``x => x + 1``) are skipped, the JS analogue
of skipping Python lambdas.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .base import (
    CorruptedSourceError,
    Edit,
    Transformer,
    WrapResult,
    apply_edits,
)

_JS_DIR = Path(__file__).parent / "_js"
_EMITTER = _JS_DIR / "emitter.js"
_RUNTIME_JS = _JS_DIR / "ouroboros_runtime.js"

#: Marker proving a file is already instrumented (makes wrapping idempotent).
_MARKER = "_ouro_rt"

_IMPORT_MODULE = f'import {_MARKER} from "./ouroboros_runtime.js";\n'
_IMPORT_SCRIPT = f'const {_MARKER} = require("./ouroboros_runtime.js");\n'

# Extensions handled by the babel typescript/jsx plugins.
_EXT_TO_KIND = {
    ".js": "js",
    ".mjs": "mjs",
    ".cjs": "cjs",
    ".jsx": "jsx",
    ".ts": "ts",
    ".tsx": "tsx",
}

#: Extensions whose module system node decides from the name alone, whatever the
#: file's own syntax looks like.
_EXT_TO_MODULE_SYSTEM = {".mjs": "module", ".cjs": "script"}


def _package_type(filename: str | None) -> str | None:
    """``"module"`` / ``"commonjs"`` from the nearest ``package.json``, else None.

    For a plain ``.js`` file the extension says nothing; node resolves the
    module system from the closest ``package.json`` above it. Reading the same
    file is the only way to agree with node about what the header should be.
    """

    if not filename:
        return None
    try:
        here = Path(filename).resolve().parent
        for d in (here, *here.parents):
            pkg = d / "package.json"
            if pkg.is_file():
                declared = json.loads(pkg.read_text(encoding="utf-8")).get("type")
                return declared if declared in ("module", "commonjs") else "commonjs"
    except (OSError, ValueError):
        return None
    return None


def _module_system(filename: str | None, parsed_source_type: str | None) -> str:
    """Decide between an ESM ``import`` header and a CommonJS ``require`` header.

    The parser's own verdict is the *last* resort, not the first. Babel is asked
    to parse "unambiguous", so a file with neither ``import`` nor ``export`` —
    which is most files — comes back as a script even when node will load it as
    an ES module. Emitting ``require`` into it produces a program that dies with
    "require is not defined in ES module scope". The name decides first (``.mjs``
    / ``.cjs``), then the nearest ``package.json``, and only then the syntax.
    """

    ext = Path(filename).suffix.lower() if filename else ""
    by_ext = _EXT_TO_MODULE_SYSTEM.get(ext)
    if by_ext is not None:
        return by_ext
    if ext in (".js", ".jsx"):
        declared = _package_type(filename)
        if declared is not None:
            return "module" if declared == "module" else "script"
    return "module" if parsed_source_type == "module" else "script"


class JavaScriptTransformer(Transformer):
    language = "javascript"
    extensions = tuple(_EXT_TO_KIND)

    def runtime_asset(self) -> tuple[str, str]:
        return "ouroboros_runtime.js", _RUNTIME_JS.read_text(encoding="utf-8")

    # ---- emitter -------------------------------------------------------- #
    def _emit_ranges(self, source: str, kind: str, filename: str | None) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                ["node", str(_EMITTER), kind],
                input=source,
                cwd=str(_JS_DIR),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError as e:  # node missing
            raise CorruptedSourceError(
                "javascript", f"node executable not found: {e}", filename=filename
            ) from e
        if proc.returncode != 0:
            raise CorruptedSourceError(
                "javascript", f"emitter crashed: {proc.stderr.strip()}", filename=filename
            )
        data: dict[str, Any] = json.loads(proc.stdout)
        if not data.get("ok"):
            raise CorruptedSourceError("javascript", data.get("error", "parse failed"),
                                       filename=filename)
        return data

    # ---- transform ------------------------------------------------------ #
    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        if minimal:
            raise NotImplementedError("minimal probe mode is C-only (kernel ring sink)")
        kind = "js"
        if filename:
            kind = _EXT_TO_KIND.get(Path(filename).suffix.lower(), "js")

        data = self._emit_ranges(source, kind, filename)

        # Idempotency: a file already carrying our exact runtime import is left
        # untouched. Matching the full import line (not the bare `_ouro_rt`
        # token) avoids silently skipping a file that merely mentions the token.
        if _IMPORT_MODULE.strip() in source or _IMPORT_SCRIPT.strip() in source:
            return WrapResult(code=source, language=self.language, functions_wrapped=0)

        edits: list[Edit[str]] = []
        wrapped = 0
        for fn in data["functions"]:
            if not fn.get("isBlock"):
                continue  # concise-body arrows: skipped, like Python lambdas
            if only is not None and fn["name"] not in only:
                continue  # selective mode
            wrapped += 1
            name_lit = json.dumps(fn["name"])
            params = ", ".join(fn["params"])
            # A leading ";" when the insertion point sits just after the
            # function's own directive prologue: a directive without a
            # semicolon ("use strict" then a newline) would otherwise run into
            # our `const`.
            lead = ";" if fn.get("hasDirectives") else ""
            entry = (
                f"{lead} const __ouro_ctx = {_MARKER}.enter({name_lit}, [{params}]);"
                f" let __ouro_result, __ouro_threw = false; try {{"
            )
            exit_ = (
                f" }} catch (__ouro_e) {{ __ouro_threw = true;"
                f" {_MARKER}.exit_throw(__ouro_ctx, __ouro_e); throw __ouro_e; }}"
                f" finally {{ if (!__ouro_threw) {_MARKER}.exit(__ouro_ctx, __ouro_result); }}"
            )
            edits.append(Edit(fn["bodyStart"], fn["bodyStart"], entry))
            edits.append(Edit(fn["bodyEnd"], fn["bodyEnd"], exit_))

            for ret in fn["returns"]:
                if ret["argStart"] is not None:
                    # Replace the gap between `return` and its argument so the
                    # `(` lands on the same logical line — avoids ASI inserting
                    # a semicolon after a bare `return` on its own line.
                    edits.append(Edit(ret["keywordEnd"], ret["argStart"], " (__ouro_result = ("))
                    edits.append(Edit(ret["argEnd"], ret["argEnd"], "))"))
                else:
                    edits.append(Edit(ret["keywordEnd"], ret["keywordEnd"],
                                      " (__ouro_result = void 0)"))

        if wrapped:
            header = (_IMPORT_MODULE
                      if _module_system(filename, data.get("sourceType")) == "module"
                      else _IMPORT_SCRIPT)
            # Below the `#!` line and the file's own "use strict" (see
            # emitter.js): a shebang only works from byte 0, and a directive
            # only counts while it is still the first statement.
            at = int(data.get("headerStart") or 0)
            edits.insert(0, Edit(at, at, ("\n" + header) if at else header))

        new_code = apply_edits(source, edits)
        return WrapResult(code=new_code, language=self.language, functions_wrapped=wrapped)
