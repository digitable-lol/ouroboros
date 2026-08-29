"""Tests for the Python decorator-injection backend.

The cases that justify using a real parser (instead of regex) are the ones the
example files do *not* show: nested functions, early/multiple/bare returns,
async, and methods inside a class (where a ``__``-prefixed decorator would be
name-mangled and break).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from ouroboros.languages import CorruptedSourceError, transformer_for_language
from ouroboros.languages.base import line_start_offsets
from ouroboros.languages.python_lang import DECORATOR, PythonTransformer, _import_offset


@pytest.fixture
def tx() -> PythonTransformer:
    return PythonTransformer()


def _decorator_lines(code: str) -> int:
    return sum(1 for line in code.splitlines() if line.strip() == DECORATOR)


def test_registry_resolves_python():
    assert isinstance(transformer_for_language("python"), PythonTransformer)


def test_single_function(tx):
    res = tx.wrap_source("def sum(a, b):\n    return a + b\n")
    assert res.functions_wrapped == 1
    assert "from ouroboros_runtime import log as _ouro_log" in res.code
    assert _decorator_lines(res.code) == 1
    # output still parses
    ast.parse(res.code)


def test_output_keeps_comments_and_formatting(tx):
    src = "def f(x):\n    # keep me\n    return x  # trailing\n"
    res = tx.wrap_source(src)
    assert "# keep me" in res.code
    assert "# trailing" in res.code


def test_multiple_and_early_and_bare_returns(tx):
    src = (
        "def classify(x):\n"
        "    if x < 0:\n"
        "        return 'neg'\n"
        "    if x == 0:\n"
        "        return\n"  # bare return
        "    return 'pos'\n"
    )
    res = tx.wrap_source(src)
    assert res.functions_wrapped == 1  # one function, not one-per-return
    assert _decorator_lines(res.code) == 1
    ast.parse(res.code)


def test_nested_functions_each_decorated(tx):
    src = (
        "def outer(a):\n"
        "    def inner(b):\n"
        "        return b + 1\n"
        "    return inner(a)\n"
    )
    res = tx.wrap_source(src)
    assert res.functions_wrapped == 2
    assert _decorator_lines(res.code) == 2


def test_lambda_not_decorated(tx):
    res = tx.wrap_source("f = lambda x: x + 1\n")
    assert res.functions_wrapped == 0
    assert DECORATOR not in res.code


def test_async_function(tx):
    res = tx.wrap_source("async def fetch(url):\n    return await get(url)\n")
    assert res.functions_wrapped == 1
    ast.parse(res.code)


def test_method_in_class_is_not_name_mangled(tx):
    """A class method must get a single-underscore decorator that resolves."""
    src = "class C:\n    def m(self, x):\n        return x * 2\n"
    res = tx.wrap_source(src)
    assert res.functions_wrapped == 1
    # The decorator carries one leading underscore, so it is not mangled to
    # _C__... — verify the generated module compiles and runs.
    ns: dict = {}
    exec(  # noqa: S102 — exercising generated code under test
        res.code.replace(
            "from ouroboros_runtime import log as _ouro_log",
            "def _ouro_log(fn):\n    return fn",
        ),
        ns,
    )
    assert ns["C"]().m(3) == 6


def test_decorated_function_keeps_existing_decorator(tx):
    src = "@staticmethod\ndef d(a, b):\n    return a + b\n"
    res = tx.wrap_source(src)
    assert res.functions_wrapped == 1
    # our decorator is innermost (directly above def), staticmethod stays on top
    lines = [ln.strip() for ln in res.code.splitlines()]
    i_static = lines.index("@staticmethod")
    i_ours = lines.index(DECORATOR)
    i_def = next(i for i, ln in enumerate(lines) if ln.startswith("def d"))
    assert i_static < i_ours < i_def


def test_idempotent(tx):
    src = "def f(x):\n    return x\n"
    once = tx.wrap_source(src).code
    twice = tx.wrap_source(once)
    assert twice.functions_wrapped == 0
    assert twice.code == once
    assert _decorator_lines(twice.code) == 1


def test_no_functions_no_import(tx):
    src = "x = 1\ny = x + 2\n"
    res = tx.wrap_source(src)
    assert res.functions_wrapped == 0
    assert "ouroboros_runtime" not in res.code
    assert res.code == src


def test_corrupted_source_raises(tx):
    with pytest.raises(CorruptedSourceError) as ei:
        tx.wrap_source("def broken(:\n    return\n", filename="bad.py")
    assert ei.value.language == "python"
    assert ei.value.filename == "bad.py"


def test_unicode_identifiers_offsets(tx):
    """col/line offset math must survive non-ASCII before the def."""
    src = "# комментарий\ndef функция(параметр):\n    return параметр\n"
    res = tx.wrap_source(src)
    assert res.functions_wrapped == 1
    ast.parse(res.code)
    assert "# комментарий" in res.code


def test_future_import_stays_first(tx):
    """A `from __future__` import MUST remain the first statement: splicing the
    runtime import ahead of it produced a file that no longer parses
    ("from __future__ imports must occur at the beginning of the file")."""
    src = "from __future__ import annotations\n\n\ndef f(x):\n    return x + 1\n"
    res = tx.wrap_source(src)
    assert res.functions_wrapped == 1
    ast.parse(res.code)  # the whole point: the output still parses
    lines = res.code.splitlines()
    assert lines[0] == "from __future__ import annotations"
    assert "from ouroboros_runtime import log" in lines[1]


def test_module_docstring_stays_the_docstring(tx):
    """Any statement placed before a module docstring silently demotes it to a
    bare string expression, so the wrapped module loses its __doc__."""
    src = '"""Module docs."""\n\n\ndef f():\n    return 1\n'
    res = tx.wrap_source(src)
    assert res.functions_wrapped == 1
    tree = ast.parse(res.code)
    assert ast.get_docstring(tree) == "Module docs."


def test_docstring_and_future_import_together(tx):
    """Docstring first, then __future__, then ours — the order Python requires."""
    src = ('"""Docs."""\n'
           "from __future__ import annotations\n\n\ndef f():\n    return 1\n")
    res = tx.wrap_source(src)
    tree = ast.parse(res.code)
    assert ast.get_docstring(tree) == "Docs."
    lines = res.code.splitlines()
    assert lines[1] == "from __future__ import annotations"
    assert "from ouroboros_runtime import log" in lines[2]


def test_shebang_stays_on_line_one(tx):
    """The kernel reads `#!` only from byte 0. A shebang pushed to line 2 turns a
    runnable script into `Exec format error` — the wrap made the file unusable
    without changing a single character of its logic."""
    src = "#!/usr/bin/env python3\ndef f():\n    return 7\n"
    res = tx.wrap_source(src)
    assert res.functions_wrapped == 1
    assert res.code.splitlines()[0] == "#!/usr/bin/env python3"
    assert "from ouroboros_runtime import log" in res.code.splitlines()[1]


def test_coding_declaration_stays_in_the_first_two_lines(tx):
    """PEP 263 is honoured only on the first two lines; below that it is a
    comment and the file's declared encoding is silently ignored."""
    src = "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\ndef f():\n    return 7\n"
    res = tx.wrap_source(src)
    lines = res.code.splitlines()
    assert lines[0].startswith("#!")
    assert "coding" in lines[1]
    assert "from ouroboros_runtime import log" in lines[2]


def test_runtime_module_is_never_instrumented(tx):
    """The sink must stay out of its own loop: instrumented code imports the
    decorator FROM this module, so wrapping it makes the file import itself."""
    from ouroboros import runtime as runtime_mod

    src = pathlib.Path(runtime_mod.__file__).read_text(encoding="utf-8")
    res = tx.wrap_source(src, filename="ouroboros_runtime.py")
    assert res.functions_wrapped == 0
    assert res.code == src


def test_a_qualified_decorator_of_ours_counts_as_already_decorated(tx):
    """The decorator can arrive as a bare name or through the module it came
    from. Both are ours, and neither may be doubled — a function decorated
    twice logs every call twice, which reads as the function being called
    twice."""

    src = ("import ouroboros_runtime\n"
           "\n"
           "@ouroboros_runtime._ouro_log\n"
           "def f(x):\n"
           "    return x\n")

    res = tx.wrap_source(src, filename="m.py")

    assert res.functions_wrapped == 0
    assert res.code == src


def test_an_existing_runtime_import_is_not_added_a_second_time(tx):
    """A file half-instrumented by hand, or wrapped through a different route,
    already has the import. A second one is a duplicate line, not a failure —
    but it is noise in a diff, and it says the tool did not look."""

    src = ("from ouroboros_runtime import log as _ouro_log\n"
           "\n"
           "def f(x):\n"
           "    return x\n")

    res = tx.wrap_source(src, filename="m.py")

    assert res.functions_wrapped == 1
    assert res.code.count("from ouroboros_runtime import") == 1
    assert ast.parse(res.code)


def test_a_file_without_a_trailing_newline_still_parses_after_wrapping(tx):
    """When everything above the insertion point is a docstring that runs to the
    last byte, the import is appended at the very end — straight onto the last
    line unless a newline is put in first, which would make the file
    unparseable."""

    src = 'def f(x):\n    return x\n\n"""tail docstring, no trailing newline"""'

    res = tx.wrap_source(src, filename="m.py")

    assert res.functions_wrapped == 1
    ast.parse(res.code)                     # the point: it still parses
    assert "from ouroboros_runtime import" in res.code


@pytest.mark.parametrize("source,expected_line", [
    ("", None),
    ("import os\n", 0),
    ('"""doc"""\n', 1),
    ("#!/usr/bin/env python\n", 1),
    ("#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n", 2),
    ('"""doc"""\nfrom __future__ import annotations\n', 2),
])
def test_the_import_offset_lands_after_everything_that_owns_the_top(source, expected_line):
    """`_import_offset` is asked where the runtime import may go, and answers
    for files that are nothing but header — where the answer is the end of the
    file. `wrap_source` never sees such a file with a function in it, so this is
    asked of the function directly rather than through a wrap that cannot
    happen.
    """

    tree = ast.parse(source)
    starts = line_start_offsets(source)

    off = _import_offset(tree, source, starts)

    if expected_line is None or expected_line == 0:
        assert off == 0
    else:
        assert off == len(source)          # header-only: the end is all there is
        assert source[:off].count("\n") == expected_line
