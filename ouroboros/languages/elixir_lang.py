"""Elixir backend — inject `use Ouroboros.Trace` per module.

The BEAM-native analogue of the Python decorator: instead of rewriting function
bodies, we splice one `use Ouroboros.Trace` into each module, and the macro (in
the shipped runtime ``ouroboros_trace.ex``) overrides ``def``/``defp`` to wrap
every clause. Module locations come from an external emitter that uses the real
Elixir parser (``Code.string_to_quoted``) — consistent with the JS babel and
C/C++ libclang emitters.

Unlike the C/C++ backends, the Elixir emitter reports line/character columns, so
this backend splices on ``str`` (not bytes).

Compile ordering: ``ouroboros_trace.ex`` must be compiled/loaded before any
instrumented module (the macro expands at compile time).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .base import (
    CorruptedSourceError,
    Edit,
    Transformer,
    WrapResult,
    apply_edits,
    line_start_offsets,
)

_EX_DIR = Path(__file__).parent / "_elixir"
_EMITTER = _EX_DIR / "emit.exs"
_RUNTIME = _EX_DIR / "ouroboros_trace.ex"

_USE = "use Ouroboros.Trace"
_INJECT = "\n  " + _USE


class ElixirTransformer(Transformer):
    language = "elixir"
    extensions = (".ex", ".exs")

    def runtime_asset(self) -> tuple[str, str]:
        return "ouroboros_trace.ex", _RUNTIME.read_text(encoding="utf-8")

    def _emit(self, source: str, filename: str | None) -> list[tuple[int, int]]:
        try:
            proc = subprocess.run(
                ["elixir", str(_EMITTER)],
                input=source, cwd=str(_EX_DIR),
                capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError as e:
            raise CorruptedSourceError("elixir", f"elixir not found: {e}", filename=filename) from e
        out = proc.stdout.splitlines()
        if not out or out[0] != "ok":
            detail = out[0] if out else (proc.stderr.strip() or "parse failed")
            raise CorruptedSourceError("elixir", detail, filename=filename)
        positions = []
        for line in out[1:]:
            ln, col = line.split()
            positions.append((int(ln), int(col)))
        return positions

    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        if minimal:
            raise NotImplementedError("minimal probe mode is C-only (kernel ring sink)")
        if _USE in source:  # already instrumented
            return WrapResult(code=source, language=self.language, functions_wrapped=0)
        if only is not None:
            # The Elixir backend instruments at MODULE granularity (a `use`
            # injected per module wraps all its defs), so per-function selection
            # has no meaning here. Be explicit rather than silently wrap-all.
            raise CorruptedSourceError(
                "elixir", "selective `only=` is unsupported for the Elixir "
                "(module-granular) backend", filename=filename)

        positions = self._emit(source, filename)
        starts = line_start_offsets(source)
        edits: list[Edit[str]] = []
        for ln, col in positions:
            # col is the 1-based column of the `do` keyword; insert just after it.
            off = starts[ln] + (col - 1) + len("do")
            edits.append(Edit(off, off, _INJECT))

        new_code = apply_edits(source, edits)
        # functions_wrapped here counts modules instrumented (each wraps all its defs).
        return WrapResult(code=new_code, language=self.language, functions_wrapped=len(positions))
