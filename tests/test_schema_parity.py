"""Record-schema parity across all six backends.

SPEC.md is only worth something if every backend writes the *same* record for
the same call. Six hand-written sinks (Python, JavaScript, C, C++, Elixir, Go)
that must agree byte for byte drift silently: nothing fails, the traces just stop
being comparable, and a cross-language question like "show me every call longer
than a millisecond" quietly returns the wrong set.

So this runs one identical program — ``add(2, 3) -> 5`` plus a raising call —
through each backend end to end (real transformer, real compiler/interpreter,
real ``debug.info``) and compares the parsed records field by field.

Fields split into three groups:

* **fixed** (``p``, ``fn``, ``a``, ``k``, ``r``) — must be equal everywhere;
* **shaped** (``t``, ``id``, ``th``, ``ci``, ``d``) — the value is per-run, but
  the *shape* is contractual and is asserted with a pattern, because a field
  that silently degrades (a timestamp that loses its milliseconds, a thread
  token that drops the pid) passes any test that merely blanks it out;
* **dialect** (the repr of a raised value) — per SPEC.md deliberately native to
  each language, so only the ``"<Type>: <message>"`` shape is required.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from ouroboros.languages import transformer_for_language

TIMEOUT = 180

#: `t`: ISO-8601 local time with millisecond precision (SPEC.md §2).
_T_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$")
#: `id`: a UUIDv4.
_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
#: `th`: "<process>.<thread>" — both halves, so a token that identifies only the
#: process (or only the thread) cannot pass. The thread half is each language's
#: own token (an OS tid, a worker id, a BEAM pid), so it is not constrained
#: further than "non-empty and no whitespace".
_TH_RE = re.compile(r"[^.\s]+\.\S+$")
#: `x`: "<Type>: <message>" — the exception's own type name, then the message.
#: Pinned per language because the type names are native; what must hold
#: everywhere is that both halves are there.
_X_TYPE = {"python": "ValueError", "javascript": "Error",
           "cpp": "std::runtime_error", "elixir": "ArgumentError",
           # Go has no exception type: the panic value's own type stands in, and
           # `panic("bad")` panics with a string.
           "go": "string"}


def _sources(lang: str) -> tuple[str, str, str]:
    """(source, filename, extra tail appended AFTER instrumentation)."""
    if lang == "python":
        return (
            "def add(a, b):\n    return a + b\n\n\n"
            'def boom():\n    raise ValueError("bad")\n',
            "prog.py",
            "\nadd(2, 3)\ntry:\n    boom()\nexcept ValueError:\n    pass\n",
        )
    if lang == "javascript":
        return (
            "function add(a, b) { return a + b; }\n"
            'function boom() { throw new Error("bad"); }\n',
            "prog.js",
            "add(2, 3);\ntry { boom(); } catch (e) {}\n",
        )
    if lang == "c":
        return (
            "int add(int a, int b) { return a + b; }\n",
            "prog.c",
            "\nint main(void) { add(2, 3); return 0; }\n",
        )
    if lang == "cpp":
        return (
            "#include <stdexcept>\n"
            "int add(int a, int b) { return a + b; }\n"
            'int boom() { throw std::runtime_error("bad"); }\n',
            "prog.cpp",
            "\nint main() { add(2, 3); try { boom(); } catch (...) {} }\n",
        )
    if lang == "elixir":
        return (
            "defmodule M do\n  def add(a, b), do: a + b\n"
            '  def boom, do: raise(ArgumentError, "bad")\nend\n',
            "prog.exs",
            "\nM.add(2, 3)\ntry do\n  M.boom()\nrescue\n  _ -> :ok\nend\n",
        )
    if lang == "go":
        return (
            "package main\n\nfunc add(a, b int) int { return a + b }\n\n"
            'func boom() { panic("bad") }\n',
            "prog.go",
            "\nfunc main() {\n\tadd(2, 3)\n"
            "\tdefer func() { _ = recover() }()\n\tboom()\n}\n",
        )
    raise AssertionError(lang)


def _records(lang: str, root) -> list[dict]:
    src, fname, tail = _sources(lang)
    tx = transformer_for_language(lang)
    code = tx.wrap_source(src, filename=fname).code
    asset = tx.runtime_asset()
    if asset is not None:
        root.joinpath(asset[0]).write_text(asset[1], encoding="utf-8")
    if lang == "elixir":
        code = f'Code.require_file("{asset[0]}")\n' + code
    root.joinpath(fname).write_text(code + tail, encoding="utf-8")

    debug = root / "debug.info"
    env = {**os.environ, "OUROBOROS_DEBUG_INFO": str(debug)}
    if lang == "go":
        # The Go helper is a sibling file of the same package, not an import, so
        # it is named on the build command line rather than resolved from the
        # source. `go build` also wants its flags ahead of the file list.
        subprocess.run(["go", "build", "-o", "prog.bin", fname, asset[0]], cwd=root,
                       check=True, capture_output=True, timeout=TIMEOUT)
        argv = ["./prog.bin"]
    elif lang in ("c", "cpp"):
        cc = ["gcc", "-std=gnu11"] if lang == "c" else ["g++", "-std=c++17"]
        subprocess.run([*cc, fname, "-o", "prog.bin"], cwd=root, check=True,
                       capture_output=True, timeout=TIMEOUT)
        argv = ["./prog.bin"]
    else:
        argv = {"python": [sys.executable], "javascript": ["node"],
                "elixir": ["elixir"]}[lang] + [fname]
    subprocess.run(argv, cwd=root, check=True, capture_output=True,
                   env=env, timeout=TIMEOUT)
    return [json.loads(ln) for ln in debug.read_text(encoding="utf-8").splitlines() if ln.strip()]


_TOOL = {"python": None, "javascript": "node", "c": "gcc", "cpp": "g++",
         "elixir": "elixir", "go": "go"}
_LANGS = tuple(_TOOL)


def _skip_unless_available(lang: str) -> None:
    tool = _TOOL[lang]
    if tool is not None and shutil.which(tool) is None:
        pytest.skip(f"{tool} not installed")


@pytest.mark.parametrize("lang", _LANGS)
def test_entry_record_matches_the_contract(lang: str, tmp_path) -> None:
    """The `in` line of ``add(2, 3)``: fixed fields equal, shaped fields shaped."""
    _skip_unless_available(lang)
    recs = _records(lang, tmp_path)
    entry = next(r for r in recs if r["p"] == "in" and r["fn"] == "add")
    assert set(entry) == {"p", "t", "id", "ci", "th", "fn", "a", "k"}
    assert entry["fn"] == "add"
    assert entry["a"] == "2, 3", "`a` carries positional values, `k` carries names"
    assert entry["k"] == ""
    assert _T_RE.match(entry["t"]), f"`t` needs millisecond precision, got {entry['t']!r}"
    assert _ID_RE.match(entry["id"]), entry["id"]
    assert _TH_RE.match(entry["th"]), f"`th` is <process>.<thread>, got {entry['th']!r}"
    assert isinstance(entry["ci"], int)
    if lang == "python":
        # Python reads a real CPU index where the platform offers one
        # (os.sched_getcpu, Linux), and -1 where it does not.
        assert entry["ci"] >= -1
    else:
        # The other four have no CPU source in userland, and -1 is the contract's
        # "unknown". Elixir used to put the BEAM scheduler id here — a number that
        # looks like a CPU index and is not one (schedulers migrate), so a reader
        # comparing `ci` across languages was comparing two different things.
        assert entry["ci"] == -1, f"{lang} must report -1 when it cannot read a CPU"


@pytest.mark.parametrize("lang", _LANGS)
def test_completion_record_matches_the_contract(lang: str, tmp_path) -> None:
    _skip_unless_available(lang)
    recs = _records(lang, tmp_path)
    entry = next(r for r in recs if r["p"] == "in" and r["fn"] == "add")
    out = next(r for r in recs if r["p"] == "out" and r["id"] == entry["id"])
    assert set(out) == {"p", "id", "fn", "r", "d"}
    assert out["r"] == "5"
    assert isinstance(out["d"], (int, float)) and out["d"] >= 0


@pytest.mark.parametrize("lang", [lg for lg in _LANGS if lg != "c"])
def test_raised_record_names_the_type_and_message(lang: str, tmp_path) -> None:
    """C has no exceptions; the other four must say WHICH error and WHY. A bare
    "(exception)" is a record that cannot be acted on."""
    _skip_unless_available(lang)
    recs = _records(lang, tmp_path)
    raised = [r for r in recs if r["p"] == "out" and "x" in r]
    assert raised, "no raised completion recorded"
    assert raised[0]["x"] == f"{_X_TYPE[lang]}: bad", (
        f"`x` must name the type and the message, got {raised[0]['x']!r}")


@pytest.mark.parametrize("lang", _LANGS)
def test_records_fit_under_pipe_buf(lang: str, tmp_path) -> None:
    """SPEC.md §1: every record is written with one append and bounded well under
    PIPE_BUF, so two processes sharing one debug.info cannot interleave a line.
    A record that overruns it is torn by the kernel, and the parser then counts
    the two halves as malformed and drops them — data lost without a warning."""
    _skip_unless_available(lang)
    for line in _long_call_lines(lang, tmp_path):
        assert len(line.encode("utf-8")) + 1 <= 4096, (
            f"{lang}: {len(line.encode('utf-8')) + 1} bytes > PIPE_BUF"
        )


def _long_call_lines(lang: str, root) -> list[str]:
    """Run one call with 30 long arguments and return the raw record lines."""
    big = "y" * 400
    if lang == "python":
        params = ", ".join(f"a{i}" for i in range(30))
        args = ", ".join(f'"{big}"' for _ in range(30))
        src = f"def many({params}):\n    return \"{big}\"\n"
        tail = f"\nmany({args})\n"
        fname = "prog.py"
    elif lang == "javascript":
        params = ", ".join(f"a{i}" for i in range(30))
        args = ", ".join(f'"{big}"' for _ in range(30))
        src = f'function many({params}) {{ return "{big}"; }}\n'
        tail = f"many({args});\n"
        fname = "prog.js"
    elif lang in ("c", "cpp"):
        params = ", ".join(f"const char *a{i}" for i in range(30))
        args = ", ".join(f'"{big}"' for _ in range(30))
        head = "#include <stdio.h>\n" if lang == "c" else "#include <string>\n"
        src = f'{head}const char *many({params}) {{ return "{big}"; }}\n'
        tail = (f"\nint main(void) {{ many({args}); return 0; }}\n" if lang == "c"
                else f"\nint main() {{ many({args}); }}\n")
        fname = "prog.c" if lang == "c" else "prog.cpp"
    elif lang == "go":
        params = ", ".join(f"a{i} string" for i in range(30))
        args = ", ".join(f'"{big}"' for _ in range(30))
        src = f'package main\n\nfunc many({params}) string {{ return "{big}" }}\n'
        tail = f"\nfunc main() {{ many({args}) }}\n"
        fname = "prog.go"
    else:
        params = ", ".join(f"a{i}" for i in range(30))
        args = ", ".join(f'"{big}"' for _ in range(30))
        src = f'defmodule M do\n  def many({params}), do: "{big}"\nend\n'
        tail = f"\nM.many({args})\n"
        fname = "prog.exs"

    tx = transformer_for_language(lang)
    code = tx.wrap_source(src, filename=fname).code
    asset = tx.runtime_asset()
    if asset is not None:
        root.joinpath(asset[0]).write_text(asset[1], encoding="utf-8")
    if lang == "elixir":
        code = f'Code.require_file("{asset[0]}")\n' + code
    root.joinpath(fname).write_text(code + tail, encoding="utf-8")

    debug = root / "long.info"
    env = {**os.environ, "OUROBOROS_DEBUG_INFO": str(debug)}
    if lang == "go":
        subprocess.run(["go", "build", "-o", "long.bin", fname, asset[0]], cwd=root,
                       check=True, capture_output=True, timeout=TIMEOUT)
        argv = ["./long.bin"]
    elif lang in ("c", "cpp"):
        cc = ["gcc", "-std=gnu11"] if lang == "c" else ["g++", "-std=c++17"]
        subprocess.run([*cc, fname, "-o", "long.bin"], cwd=root, check=True,
                       capture_output=True, timeout=TIMEOUT)
        argv = ["./long.bin"]
    else:
        argv = {"python": [sys.executable], "javascript": ["node"],
                "elixir": ["elixir"]}[lang] + [fname]
    subprocess.run(argv, cwd=root, check=True, capture_output=True,
                   env=env, timeout=TIMEOUT)
    return [ln for ln in debug.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.mark.parametrize("lang", _LANGS)
def test_reported_duration_excludes_the_sinks_own_write(lang: str, tmp_path) -> None:
    """`d` must measure the call, not the logging.

    SPEC.md §4 puts the entry write outside the measured span deliberately. C had
    it inside — the clock started before the `in` record and stopped after the
    completion record's ``fopen`` — so every C duration carried the cost of two
    file appends: a median of 10 microseconds for a one-line function, against
    C++'s 0 for the same function. Nothing failed; the numbers were simply not
    comparable between languages, and a filter like "show calls slower than a
    millisecond" returned a different set depending on which backend wrote the
    trace. The measured medians for this function are 0-3 us across the six
    backends, so the bound below has real headroom over a correct sink and no
    room at all for a leaked file open.
    """
    _skip_unless_available(lang)
    durations = sorted(r["d"] for r in _repeated_call_records(lang, tmp_path)
                       if r["p"] == "out")
    assert len(durations) == 200
    median = durations[len(durations) // 2]
    assert median < 8e-6, f"{lang}: median reported duration {median * 1e6:.2f} us"


@pytest.mark.parametrize("lang", ["c", "cpp"])
def test_call_ids_differ_between_processes(lang: str, tmp_path) -> None:
    """Two processes started in the same second must not draw the same call ids.

    SPEC.md lets several processes append to one debug.info, and `id` is the only
    thing pairing an `in` with its `out`. The C++ helper seeded rand() from the
    clock alone, so two runs a fraction of a second apart produced the *same*
    uuid sequence — 20 of 20 pairs collided. Records then pair across processes,
    and the one thing two records per call exist for (an `in` with no `out` =
    a call that never returned) stops being visible.
    """
    _skip_unless_available(lang)
    ids = []
    for run in range(6):
        d = tmp_path / f"r{run}"
        d.mkdir()
        ids.append(_repeated_call_records(lang, d, calls=1)[0]["id"])
    assert len(set(ids)) == len(ids), f"{lang}: repeated call ids across processes: {ids}"


def _repeated_call_records(lang: str, root, calls: int = 200) -> list[dict]:
    """Run ``tick`` `calls` times in one process and return its records."""
    progs = {
        "python": ("prog.py", "def tick(a):\n    return a + 1\n",
                   f"\nv = 0\nfor _ in range({calls}):\n    v = tick(v)\n"),
        "javascript": ("prog.js", "function tick(a){ return a + 1; }\n",
                       f"let v = 0;\nfor (let i = 0; i < {calls}; i++) v = tick(v);\n"),
        "c": ("prog.c", "int tick(int a){ return a + 1; }\n",
              f"\nint main(void){{ int v=0; for(int i=0;i<{calls};i++) v=tick(v); return 0; }}\n"),
        "cpp": ("prog.cpp", "int tick(int a){ return a + 1; }\n",
                f"\nint main(){{ int v=0; for(int i=0;i<{calls};i++) v=tick(v); }}\n"),
        "elixir": ("prog.exs", "defmodule M do\n  def tick(a), do: a + 1\nend\n",
                   f"\nEnum.reduce(1..{calls}, 0, fn _, v -> M.tick(v) end)\n"),
        "go": ("prog.go", "package main\n\nfunc tick(a int) int { return a + 1 }\n",
               f"\nfunc main() {{\n\tv := 0\n"
               f"\tfor i := 0; i < {calls}; i++ {{\n\t\tv = tick(v)\n\t}}\n\t_ = v\n}}\n"),
    }
    fname, src, tail = progs[lang]
    tx = transformer_for_language(lang)
    code = tx.wrap_source(src, filename=fname).code
    asset = tx.runtime_asset()
    if asset is not None:
        root.joinpath(asset[0]).write_text(asset[1], encoding="utf-8")
    if lang == "elixir":
        code = f'Code.require_file("{asset[0]}")\n' + code
    root.joinpath(fname).write_text(code + tail, encoding="utf-8")

    debug = root / "debug.info"
    env = {**os.environ, "OUROBOROS_DEBUG_INFO": str(debug)}
    if lang == "go":
        subprocess.run(["go", "build", "-o", "tick.bin", fname, asset[0]], cwd=root,
                       check=True, capture_output=True, timeout=TIMEOUT)
        argv = ["./tick.bin"]
    elif lang in ("c", "cpp"):
        cc = ["gcc", "-std=gnu11"] if lang == "c" else ["g++", "-std=c++17"]
        subprocess.run([*cc, fname, "-o", "tick.bin"], cwd=root, check=True,
                       capture_output=True, timeout=TIMEOUT)
        argv = ["./tick.bin"]
    else:
        argv = {"python": [sys.executable], "javascript": ["node"],
                "elixir": ["elixir"]}[lang] + [fname]
    subprocess.run(argv, cwd=root, check=True, capture_output=True,
                   env=env, timeout=TIMEOUT)
    return [json.loads(ln) for ln in debug.read_text(encoding="utf-8").splitlines() if ln.strip()]
