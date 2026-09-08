**English** · [Русский](README.ru.md)

# Ouroboros

**Shows how the code actually ran: which functions were called, with which
arguments, what they returned and what they threw.**

The tool adds call logging to your source. The program then runs the way it
always did, and every call leaves two JSON lines behind — one on the way in, one
on the way out. You read those lines yourself, filter them from the shell, or
hand them to an AI agent.

Languages: Python, JavaScript/TypeScript, C, C++, Elixir, Go, Java, C#. One
record schema for all of them.

---

## Four steps

### 1. Install

```sh
uv tool install git+https://github.com/digitable-lol/ouroboros
```

```
Installed 2 executables: ouroboros, ouroboros-mcp
```

Homebrew is one line, and it brings Python itself:

```sh
brew install digitable-lol/tap/ouroboros
```

Either way you get `ouroboros` and `ouroboros-mcp`. There is also asdf, a build
from source, a single-file program and a container image — see
[Install](docs/install.md); the same page connects the tool to Claude Code and
Cursor as an MCP server.

### 2. Add call logging

Take an ordinary `stats.py`:

```python
"""Average request duration from a log."""


def parse_line(line):
    name, _, ms = line.partition(" ")
    return name, int(ms)


def average(values):
    return sum(values) / len(values)


def report(lines):
    pairs = [parse_line(l) for l in lines]
    return average([ms for _, ms in pairs])


if __name__ == "__main__":
    print(report(["get 12", "put 30", "get 18"]))
    print(report([]))
```

```sh
ouroboros wrap-file stats.py
```

```json
{"ok": true, "path": "stats.py", "language": "python", "functions_wrapped": 3, "runtime_header": "ouroboros_runtime.py"}
```

The file afterwards is the same file plus three `@_ouro_log` lines and one
import. Indentation, comments and the module docstring are untouched:

```python
"""Average request duration from a log."""
from ouroboros_runtime import log as _ouro_log


@_ouro_log
def parse_line(line):
    name, _, ms = line.partition(" ")
    return name, int(ms)


@_ouro_log
def average(values):
    return sum(values) / len(values)


@_ouro_log
def report(lines):
    pairs = [parse_line(l) for l in lines]
    return average([ms for _, ms in pairs])


if __name__ == "__main__":
    print(report(["get 12", "put 30", "get 18"]))
    print(report([]))
```

`ouroboros_runtime.py` appears next to it — the helper that writes the records.
It imports from the standard library only; there is nothing to install.

### 3. Run it the way you always did

```sh
python3 stats.py
```

```
20.0
Traceback (most recent call last):
  File "/srv/tmp/ouro-work/demo/stats.py", line 24, in <module>
    print(report([]))
          ~~~~~~^^^^
  File "/srv/tmp/ouro-work/demo/ouroboros_runtime.py", line 234, in wrapper
    result = fn(*args, **kwargs)
  File "/srv/tmp/ouro-work/demo/stats.py", line 19, in report
    return average([ms for _, ms in pairs])
  File "/srv/tmp/ouro-work/demo/ouroboros_runtime.py", line 234, in wrapper
    result = fn(*args, **kwargs)
  File "/srv/tmp/ouro-work/demo/stats.py", line 13, in average
    return sum(values) / len(values)
           ~~~~~~~~~~~~^~~~~~~~~~~~~
ZeroDivisionError: division by zero
```

Same return value, same exception as before the instrumentation. A `debug.info`
file has appeared next to the source.

> That is **not always** true. Instrumentation edits the source, and an edit can
> change what a program does, not only how long it takes. The cases we found are
> in [Limits](docs/limits.md#where-instrumentation-changes-behaviour). Run your
> tests after instrumenting, not only before.

### 4. Read the records

What was thrown, and with which arguments:

```sh
ouroboros trace debug.info --outcome raised
```

```json
{
  "ok": true,
  "path": "debug.info",
  "calls_parsed": 7,
  "malformed": 0,
  "matched": 2,
  "returned": 2,
  "next_cursor": null,
  "in_flight": [],
  "in_flight_truncated": false,
  "records": [
    {
      "index": 5,
      "started": "2026-08-28T23:39:45.166",
      "call_id": "ef71eb89-6727-4cdf-a3b7-2ea54cff81e3",
      "name": "average",
      "args": "[]",
      "kwargs": "",
      "outcome_kind": "raised",
      "outcome": "ZeroDivisionError: division by zero",
      "duration": 3e-06,
      "cpu": null,
      "thread": "2864987.129949101195776"
    },
    {
      "index": 6,
      "started": "2026-08-28T23:39:45.166",
      "call_id": "cb4e33f4-d0b2-4099-bcf2-044e4a88c1fa",
      "name": "report",
      "args": "[]",
      "kwargs": "",
      "outcome_kind": "raised",
      "outcome": "ZeroDivisionError: division by zero",
      "duration": 6.8e-05,
      "cpu": null,
      "thread": "2864987.129949101195776"
    }
  ]
}
```

Here is what the stack trace does not tell you: `average` was called **with an
empty list**, and it got there from `report`, which was also called with an
empty one. Not "where it broke" — "what it was called with".

A summary over all the calls at once:

```sh
ouroboros trace-stats debug.info
```

```json
  "by_function": [
    { "name": "parse_line", "count": 3, "result": 3, "raised": 0, "unknown": 0,
      "duration_seconds": { "min": 1e-06, "max": 2e-06, "mean": 2e-06, "total": 5e-06, "count": 3 } },
    { "name": "average",    "count": 2, "result": 1, "raised": 1, "unknown": 0,
      "duration_seconds": { "min": 2e-06, "max": 3e-06, "mean": 2e-06, "total": 5e-06, "count": 2 } },
    { "name": "report",     "count": 2, "result": 1, "raised": 1, "unknown": 0,
      "duration_seconds": { "min": 6.8e-05, "max": 0.000346, "mean": 0.000207, "total": 0.000414, "count": 2 } }
  ],
```

*(output trimmed: only `by_function` is shown; the whole of it is in
[What it looks like](docs/examples/trace-record.md))*

Everything above is the output of real runs on an ordinary Linux machine with
Python 3.12.13. The paths in the stack trace are the directory the run happened
in.

---

## What is inside `debug.info`

An append-only file, one JSON object per line. **Two lines per call**, tied
together by a shared `id`:

```jsonl
{"p":"in","t":"2026-08-28T23:39:45.166","id":"e668ee33-…","ci":-1,"th":"2864987.129949101195776","fn":"average","a":"[12, 30, 18]","k":""}
{"p":"out","id":"e668ee33-…","fn":"average","r":"20.0","d":2e-06}
```

| key | where | what |
|---|---|---|
| `p` | both | `in` — the call was entered, `out` — it left (returned or threw) |
| `t` | `in` | time of entry |
| `id` | both | call number; this is what ties the two lines together |
| `ci` | `in` | CPU core number; `-1` if it is not available |
| `th` | `in` | thread: `<process id>.<thread id>` |
| `fn` | both | function name (C++ and Go add the class or type name) |
| `a` | `in` | positional arguments, captured **before** the body runs |
| `k` | `in` | keyword arguments |
| `r` | `out` | what was returned |
| `x` | `out` | what was thrown: `<Type: message>`; mutually exclusive with `r` |
| `d` | `out` | how long the call took, in seconds |

That gives you the cheapest possible answer to "where is it hanging": **an entry
line with no matching exit line** — a call that went in and never came back.
`trace` and `trace-stats` collect those separately, under `in_flight`.

The full breakdown of the keys and the decisions behind them is in
[SPEC.md](SPEC.md).

---

## Three more things you will want on day one

**Do not instrument a hot file whole** — the records you need will drown. Pick
functions by name:

```sh
ouroboros wrap-functions parser.c parse_header parse_body
```

**A separate draft** if you would rather not touch your working tree: `create`
makes a directory with its own history, `write` adds the logging as it saves,
`execute` runs the program and passes the path to `debug.info` itself, and
`finish` copies the result into a sibling directory.

```sh
ouroboros create /srv/tmp/probe
ouroboros write /srv/tmp/probe stats.py < stats.py
ouroboros execute /srv/tmp/probe -- python3 stats.py
```

**Code that does not parse is not saved.** `write` refuses instead of leaving a
half-instrumented file on disk.

All 17 commands are in `ouroboros --help`; the walkthrough is in
[Getting started](docs/getting-started.md).

---

## What it is for

**"I have no idea what is going on here."** Someone else's project, legacy code,
yesterday's bug that will not reproduce. Reading forty thousand lines is
expensive; running the thing and looking at which functions are alive, what they
are called with and what they return is cheap.

→ [Trace code you did not write](docs/trace-existing-code.md)

**"The AI writes my code and does not know how it runs."** A model reading the
source reasons about what **should** happen. A trace shows which branches ran,
which arguments turned up, and which calls threw. The tool doubles as an MCP
server — `ouroboros-mcp`, 17 tools.

→ [Working with AI](docs/with-ai.md)

## What the tool does not do

> A trace records **how the code behaved**, not how it was supposed to behave.

A buggy program produces a trace in which the bug looks like the norm. That is
not a footnote, it is the design: [Limits](docs/limits.md).

One more thing, measured on a bench: on tasks where the bug is fully visible in
the final output, call records add nothing, and the agent does not reach for
them — 0 runs out of 3. The negative result, with the exact boundary, is in
[bench/RESULTS.md](bench/RESULTS.md).

---

## What is in the repository

| directory | what is there |
|---|---|
| [`ouroboros/`](ouroboros/) | the package itself: CLI, MCP server, draft workspace, languages |
| [`tests/`](tests/) | tests: <!--state:tests-->1074<!--/state--> |
| [`bench/`](bench/) | the bench and its results |
| [`packaging/`](packaging/) | single-file program, image, Homebrew formula, asdf plugin |
| [`docs/`](docs/) | the pages, the same ones that are published |
| [`design/`](design/) | the original brief and the per-language study of how to instrument |
| [`SPEC.md`](SPEC.md) | the record-format contract, shared by all eight languages |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | how it works inside and how to add a language |
| [`docs/ouroboros.flang`](docs/ouroboros.flang) | the same tool described in Russian as a program the flang compiler checks |

Build and check it yourself:

```sh
git clone https://github.com/digitable-lol/ouroboros && cd ouroboros
uv sync
scripts/qa.sh    # ruff, mypy, pytest
```

## Pages

| page | about |
|---|---|
| [Install](docs/install.md) | uv, Homebrew, asdf, from source, connecting the MCP server |
| [Getting started](docs/getting-started.md) | every command, the working order, what trips people up |
| [Trace code you did not write](docs/trace-existing-code.md) | step by step: what to do, what you see, how to read it |
| [Working with AI](docs/with-ai.md) | the MCP server and its 17 tools |
| [Everyday development](docs/in-development.md) | a silent program, a regression, what not to do |
| [Languages](docs/languages.md) | eight languages and how their records differ |
| [Measurements](docs/measurements.md) | what each language records, what it costs, how to repeat it |
| [Limits](docs/limits.md) | what the tool does not do, and why that is deliberate |
| [Why it exists](docs/why.md) | what it is for and which work it takes off you |
| [What it looks like](docs/examples/index.md) | records, summary, configuration — in full |
| [MCP tool reference](docs/mcp-tools.md) | all 17 tools, recorded from a live conversation with the server |

The same pages are published at <https://digitable-lol.github.io/ouroboros/>.

## The Russian description is a program

[`docs/ouroboros.flang`](docs/ouroboros.flang) says the same things in Russian,
but as a program rather than as prose: eight languages, eleven record keys, two
lines per call, `r` and `x` mutually exclusive, hanging calls, the two ways to
instrument, the corruption gate.

The [flang](https://github.com/digitable-lol/flang) compiler checks it. Types,
termination proved for all 28 functions, and 40 examples that live inside the
functions themselves — including the arithmetic: two lines per call, never fewer
open calls than zero, and eight entry keys plus six exit keys minus eleven keys
in total equals the three that appear in both lines.

```sh
npm i -g @digitable-lol/flang
flang check docs/ouroboros.flang    # 28 functions, 28 with proven termination, 5 types
flang test  docs/ouroboros.flang    # 40 examples, 40 passed
```

Prose drifts from the code the day after it is merged, and drifts silently.
There is nothing here to drift: lying in this file means failing to compile it.

## State

Version <!--state:version-->0.5.0<!--/state-->. Tests: <!--state:tests-->1074<!--/state--> of <!--state:tests-->1074<!--/state-->, branch coverage <!--state:coverage_percent-->100<!--/state--> % (`ruff`, `mypy --strict`, `pytest`). Installation
is checked end to end, not eyeballed: `uv tool install`;
`brew install digitable-lol/tap/ouroboros` together with `brew test` — the short
line taps the formula repository itself, verified from nothing: tap removed, old
version uninstalled, installed again; `asdf plugin add` together with
`asdf install 0.5.0` on a clean data directory. After each one the installed
tool instrumented real files **in Java and in C#**, they compiled, they ran,
they produced byte-for-byte the same output as before instrumentation, and the
records were read back by the same tool. All eight languages were run
separately.

**Sameness of behaviour is measured, not assumed.** 151 programs in eight
languages are run twice — clean and instrumented — and three outcomes are
compared: output, exit code, exception. There were 16 mismatches out of 79;
there are now 0. The cases once listed here as broken (a lost `"use strict"` in
JavaScript, an uncompilable `return {1, 2, 3}` in C++) are closed and verified by
a run.

The tool instruments **itself**: 23 files of its own source, 248 functions, not
one parse failure, and the instrumented copy passes 990 tests out of 991. The
one failure is not a defect: the test lets the instrumented tool read a trace
file and, **by the same path**, tells it to write its own records there, so it
appends two calls of its own and counts five instead of three. The numbers come
from a run on this tree, not copied from the last one.

**What stays a price rather than a defect.** C++ does not record a returned
object of class type — recording it would lose the copy elision the language
guarantees. No language records argument names. Python instrumentation adds a
stack frame per call, so deep recursion gets half as deep. Each one has a
measurement and an explanation in
[Limits](docs/limits.md#where-instrumentation-changes-behaviour).

## Licence

BSD 2-Clause — full text in [LICENSE](LICENSE).

The skill file [`skill/SKILL.md`](skill/SKILL.md) is under the same licence.
