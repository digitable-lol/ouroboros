"""Python backend — decorator-based instrumentation.

Strategy: parse with the stdlib ``ast`` (a *native* parser), locate every
``def``/``async def`` header, and splice a ``@_ouro_log`` decorator line in
front of it. The function body is never touched, so comments, multiple/early
returns, nested functions, lambdas and pre-existing ``try/finally`` all keep
working untouched — the decorator wraps the call at runtime instead.

A decorator (rather than the ``try/finally`` body rewrite used for C-like
languages) is the right tool here for three reasons: it avoids re-indenting the
body, it cannot lose comments, and it mirrors the macro-based function wrapping
the planned Elixir port will use.
"""

from __future__ import annotations

import ast

from .base import (
    CorruptedSourceError,
    Edit,
    Transformer,
    WrapResult,
    leading_whitespace,
    line_start_offsets,
)

#: Name the injected decorator is referenced by. Single leading underscore so a
#: reference inside a class body is NOT name-mangled (``@__log`` would become
#: ``_ClassName__log`` and raise NameError).
DECORATOR_NAME = "_ouro_log"
DECORATOR = f"@{DECORATOR_NAME}"

#: Import spliced at the top of any file that gains at least one decorator.
RUNTIME_IMPORT = f"from ouroboros_runtime import log as {DECORATOR_NAME}\n"


def _already_decorated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == DECORATOR_NAME:
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == DECORATOR_NAME:
            return True
    return False


def _import_offset(tree: ast.Module, source: str, starts: list[int]) -> int:
    """Character offset at which :data:`RUNTIME_IMPORT` may be spliced.

    Not simply 0. Two statements are required by the language to come first, and
    inserting ahead of them is wrong in different ways:

    * ``from __future__ import ...`` **must** be the first statement — putting the
      runtime import before it makes the file a ``SyntaxError`` ("from __future__
      imports must occur at the beginning of the file"), i.e. wrapping would hand
      back a file that no longer parses.
    * a module **docstring** stops being ``__doc__`` the moment any statement
      precedes it; it silently degrades to a bare string expression.

    So skip past the docstring and every ``__future__`` import, and insert at the
    start of the line after the last of them.
    """
    last_line = 0
    for i, node in enumerate(tree.body):
        is_docstring = (
            i == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if not (is_docstring or is_future):
            break
        last_line = max(last_line, node.end_lineno or node.lineno)
    if last_line == 0:
        return 0
    # Start of the line following the last must-stay-first statement. If that
    # statement ends the file, append at the very end instead of indexing past
    # the offset table.
    return starts[last_line + 1] if last_line + 1 < len(starts) else len(source)


def _has_runtime_import(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "ouroboros_runtime":
            return any(alias.asname == DECORATOR_NAME or alias.name == "log"
                       for alias in node.names)
    return False


class PythonTransformer(Transformer):
    language = "python"
    extensions = (".py",)

    def runtime_asset(self) -> tuple[str, str]:
        from pathlib import Path

        from .. import runtime as runtime_mod

        return "ouroboros_runtime.py", Path(runtime_mod.__file__).read_text(encoding="utf-8")

    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        if minimal:
            raise NotImplementedError("minimal probe mode is C-only (kernel ring sink)")
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise CorruptedSourceError("python", str(e), filename=filename) from e

        starts = line_start_offsets(source)
        edits: list[Edit[str]] = []
        wrapped = 0

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if only is not None and node.name not in only:
                continue  # selective mode
            if _already_decorated(node):
                continue
            # node.lineno points at the `def`/`async def` line (not at any
            # existing decorator), so inserting before that line places our
            # decorator innermost — closest to `def` — and it logs the actual
            # function regardless of other decorators.
            line_off = starts[node.lineno]
            indent = leading_whitespace(source, line_off)
            edits.append(Edit(line_off, line_off, f"{indent}{DECORATOR}\n"))
            wrapped += 1

        from .base import apply_edits

        if wrapped and not _has_runtime_import(tree):
            # Goes after the docstring / `__future__` imports (see
            # _import_offset), but before any decorator inserted at that same
            # offset (a function on the next line). apply_edits keeps
            # same-offset insertions in list order, so it goes to the front.
            off = _import_offset(tree, source, starts)
            edits.insert(0, Edit(off, off, RUNTIME_IMPORT))

        new_code = apply_edits(source, edits)
        return WrapResult(code=new_code, language=self.language, functions_wrapped=wrapped)
