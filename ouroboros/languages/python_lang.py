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
import re

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


#: PEP 263 encoding declaration. Only honoured by the interpreter on the first
#: two lines, so nothing may be spliced above it.
_CODING_RE = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*[-_.a-zA-Z0-9]+")


def _import_offset(tree: ast.Module, source: str, starts: list[int]) -> int:
    """Character offset at which :data:`RUNTIME_IMPORT` may be spliced.

    Not simply 0. Four things claim the top of a Python file, and pushing any of
    them down changes the program in a different way:

    * ``#!`` — the kernel reads a shebang only from byte 0, so a script whose
      ``#!`` moved to line 2 stops being runnable: ``Exec format error``.
    * a PEP 263 ``coding:`` comment — the interpreter honours it only on the
      first two lines, so a demoted one stops selecting the encoding. The
      second line counts only when the first is blank or a comment, which is
      the rule the interpreter itself applies: it refuses to parse a file whose
      line 1 is code and whose line 2 says ``coding:`` ("no encoding
      declared"). A comment that merely mentions the word there is an ordinary
      comment, and treating it as a declaration pushed the insertion point past
      the file's own functions.
    * a module **docstring** — it stops being ``__doc__`` the moment any
      statement precedes it; it silently degrades to a bare string expression.
    * ``from __future__ import ...`` **must** be the first statement — putting
      the runtime import before it makes the file a ``SyntaxError`` ("from
      __future__ imports must occur at the beginning of the file"), i.e.
      wrapping would hand back a file that no longer parses.

    So skip past all four and insert at the start of the line after the last of
    them; 0 when the file has none.
    """

    last_line = 0
    lines = source.splitlines()
    if lines and lines[0].startswith("#!"):
        last_line = 1
    first = lines[0].strip() if lines else ""
    scan = 2 if (not first or first.startswith("#")) else 1
    for i in range(min(scan, len(lines))):
        if _CODING_RE.match(lines[i].encode("utf-8", "replace")):
            last_line = max(last_line, i + 1)
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
    # Start of the line after the last must-stay-on-top construct; if that was
    # the final line, append at the very end rather than index past the table.
    return starts[last_line + 1] if last_line + 1 < len(starts) else len(source)


def _already_decorated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == DECORATOR_NAME:
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == DECORATOR_NAME:
            return True
    return False


#: Module-level flag by which the runtime helper declares itself off limits.
RUNTIME_SENTINEL = "OUROBOROS_RUNTIME_MODULE"


def _is_runtime_module(tree: ast.Module) -> bool:
    """True for the logging helper itself (it sets :data:`RUNTIME_SENTINEL`).

    Instrumented code imports the decorator from this module, so instrumenting
    it makes it import itself — every wrapped program then dies on its first
    line with a circular import.
    """

    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        for t in targets:
            if isinstance(t, ast.Name) and t.id == RUNTIME_SENTINEL:
                return True
    return False


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

        if _is_runtime_module(tree):
            return WrapResult(code=source, language=self.language, functions_wrapped=0)

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
            # Below the shebang / coding line / docstring / __future__ imports
            # (see _import_offset), but above any decorator inserted at that
            # same offset (a function on the next line): apply_edits keeps
            # same-offset insertions in list order, so it goes to the front.
            # The offset is always the start of a line, never the end of the
            # file: everything that owns the top of a file (shebang, coding
            # line, docstring, __future__) must sit above the first function,
            # so a file with a function to wrap always has a line left below
            # the header.
            off = _import_offset(tree, source, starts)
            edits.insert(0, Edit(off, off, RUNTIME_IMPORT))

        new_code = apply_edits(source, edits)
        return WrapResult(code=new_code, language=self.language, functions_wrapped=wrapped)
