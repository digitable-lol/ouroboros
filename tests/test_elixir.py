"""Tests for the Elixir backend (use-injection + def/defp-override macro)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

from ouroboros.languages import CorruptedSourceError, transformer_for_path
from ouroboros.languages.elixir_lang import ElixirTransformer
from ouroboros.trace import load

has_elixir = shutil.which("elixir") is not None
pytestmark = pytest.mark.skipif(not has_elixir, reason="elixir not available")


@pytest.fixture
def tx() -> ElixirTransformer:
    return ElixirTransformer()


def test_registry_resolves_elixir():
    assert isinstance(transformer_for_path("a.ex"), ElixirTransformer)
    assert isinstance(transformer_for_path("a.exs"), ElixirTransformer)


def test_basic_wrap(tx):
    res = tx.wrap_source("defmodule M do\n  def f(x), do: x\nend\n", filename="m.ex")
    assert res.functions_wrapped == 1
    assert "use Ouroboros.Trace" in res.code
    assert res.code.index("use Ouroboros.Trace") < res.code.index("def f")


def test_nested_modules_each_get_use(tx):
    src = ("defmodule A do\n  def f(x), do: x\n  defmodule B do\n    def g(y), do: y\n  end\nend\n")
    res = tx.wrap_source(src, filename="m.ex")
    assert res.functions_wrapped == 2
    assert res.code.count("use Ouroboros.Trace") == 2


def test_idempotent(tx):
    once = tx.wrap_source("defmodule M do\n  def f(x), do: x\nend\n", filename="m.ex").code
    again = tx.wrap_source(once, filename="m.ex")
    assert again.functions_wrapped == 0
    assert again.code == once


def test_corrupted_elixir_raises(tx):
    with pytest.raises(CorruptedSourceError):
        tx.wrap_source("defmodule M do\n  def f(x), do:\n", filename="bad.ex")


def test_runtime_asset(tx):
    name, src = tx.runtime_asset()
    assert name == "ouroboros_trace.ex"
    assert "defmacro __using__" in src and '"p":"in"' in src


def test_end_to_end_compile_and_run(tmp_path):
    """Compile the trace module first, then a wrapped module, run it, check logs.

    Hard cases in one module: multiple clauses, guard, default arg, defp, raise.
    """
    name, runtime = ElixirTransformer().runtime_asset()
    (tmp_path / name).write_text(runtime, encoding="utf-8")
    src = (
        "defmodule Calc do\n"
        "  def fact(0), do: 1\n"
        "  def fact(n) when n > 0, do: n * fact(n - 1)\n"
        "  def addmul(a, b, c \\\\ 10), do: a * b + c\n"
        "  defp helper(x), do: x + 1\n"
        "  def use_helper(x), do: helper(x)\n"
        "  def boom(n) when n < 0, do: raise(\"neg\")\n"
        "  def boom(n), do: n\n"
        "end\n"
    )
    wrapped = ElixirTransformer().wrap_source(src, filename="calc.ex")
    assert wrapped.functions_wrapped == 1
    (tmp_path / "calc.ex").write_text(wrapped.code, encoding="utf-8")
    (tmp_path / "run.exs").write_text(
        "Calc.fact(4)\nCalc.addmul(2, 3)\nCalc.addmul(2, 3, 100)\n"
        "Calc.use_helper(41)\n(try do Calc.boom(-1) rescue _ -> :ok end)\nCalc.boom(7)\n",
        encoding="utf-8",
    )
    debug = tmp_path / "debug.info"
    env = {**os.environ, "OUROBOROS_DEBUG_INFO": str(debug)}
    subprocess.run(["elixirc", "ouroboros_trace.ex"], cwd=tmp_path, check=True,
                   capture_output=True, env=env)
    # Compile the WRAPPED module on its own and assert the instrumentation
    # introduces NO compiler warning. The `boom(n) when n < 0` clause always
    # raises (body type none()); a naive `res = <body>` wrapper trips Elixir's
    # set-theoretic type checker ("pattern will never match") — a warning the
    # un-instrumented clause never had. The wrapper launders the body through
    # the opaque-closure boundary `__run__(fn -> body end)`, confining the
    # none() to the closure, so a clean stderr here is the regression guard.
    cc = subprocess.run(["elixirc", "-pa", ".", "calc.ex"], cwd=tmp_path,
                        check=True, capture_output=True, env=env, text=True)
    assert "will never match" not in cc.stderr, cc.stderr
    assert "none()" not in cc.stderr, cc.stderr
    assert cc.stderr.strip() == "", cc.stderr
    subprocess.run(["elixir", "-pa", ".", "run.exs"],
                   cwd=tmp_path, check=True, capture_output=True, env=env)
    loaded = load(debug.read_text(encoding="utf-8"))
    assert loaded.malformed == 0                 # every line is well-formed JSON

    def outcomes(name):
        return [c.outcome for c in loaded.calls if c.name == name]

    assert "24" in outcomes("fact")              # fact(4)
    addmul = [c for c in loaded.calls if c.name == "addmul"]
    # `a` carries values only (SPEC.md); the default arg shows up as the
    # trailing 10 of "8, 2, 10", not as "c=10".
    assert any(c.args.endswith(", 10") for c in addmul)  # default arg applied
    assert {"16", "106"} <= {c.outcome for c in addmul}
    assert any(c.name == "helper" for c in loaded.calls)  # defp wrapped
    boom = [c for c in loaded.calls if c.name == "boom"]
    # `x` is "<Type>: <message>" (SPEC.md), not the catch kind plus a struct repr.
    assert any(c.outcome_kind == "raised" and c.outcome == "RuntimeError: neg"
               for c in boom)                    # boom(-1)
    # every completed call carries a real duration
    assert all(c.duration is not None for c in loaded.calls)
    # Concurrency identity: `th` is "<os pid>.<beam process>" — both halves, the
    # same shape every other backend emits, so a reader can tell two BEAM nodes
    # sharing one debug.info apart. `ci` is -1: the scheduler id that used to sit
    # there is not a CPU index (schedulers migrate), and passing it off as one
    # made the field mean something different in Elixir than in the other four.
    assert all(re.fullmatch(r"\d+\.#PID<[\d.]+>", c.thread) for c in loaded.calls)
    assert all(c.cpu is None for c in loaded.calls)


def test_the_minimal_probe_is_refused_here_by_name(tx):
    """`--minimal` is the C kernel ring-sink probe. Ignoring it silently would
    return an ordinarily-wrapped module to someone who asked for the stackless
    one precisely because the ordinary one will not do."""

    with pytest.raises(NotImplementedError, match="C-only"):
        tx.wrap_source("defmodule M do\n  def f(x), do: x\nend\n",
                       filename="m.ex", minimal=True)


def test_selecting_single_functions_is_refused_rather_than_ignored(tx):
    """This backend instruments per MODULE: one injected `use` wraps every def
    in it. There is no way to honour a request for one function, so it says so
    instead of wrapping everything and reporting success."""

    with pytest.raises(CorruptedSourceError, match="module-granular"):
        tx.wrap_source("defmodule M do\n  def f(x), do: x\n  def g(x), do: x\nend\n",
                       filename="m.ex", only={"f"})


def test_a_missing_elixir_is_named_as_the_reason(tx, monkeypatch, tmp_path):
    """The most common way this backend fails on a fresh machine. PATH is really
    emptied — elixir really cannot be found."""

    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(CorruptedSourceError, match="elixir not found"):
        tx.wrap_source("defmodule M do\n  def f(x), do: x\nend\n", filename="m.ex")
