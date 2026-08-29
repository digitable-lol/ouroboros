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
            entry = (
                f" const __ouro_ctx = {_MARKER}.enter({name_lit}, [{params}]);"
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
            header = _IMPORT_MODULE if data.get("sourceType") == "module" else _IMPORT_SCRIPT
            edits.insert(0, Edit(0, 0, header))

        new_code = apply_edits(source, edits)
        return WrapResult(code=new_code, language=self.language, functions_wrapped=wrapped)
