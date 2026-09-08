---
title: Examples
tagline: The instrumented file in full, what every field of a record means, a run that crashes, and the same call recorded on all eight languages.
---

# Examples, with the real output of every command

Every block on this page is the output of an actual run on an actual machine,
taken by [`site/examples/capture.py`]({{repo}}/blob/main/site/examples/capture.py)
and re-taken whenever the tool changes. Nothing here is typed by hand or tidied
up afterwards. The programs are short on purpose; the mechanism is the same at
any size.

## The file after wrapping

This is the shop program from the [front page](index.html), after
`ouroboros wrap-file shop.py`, in full:

{{capture: shop-wrapped.py | site/examples/shop.py, after}}

Compare it to [the file before](index.html#the-four-steps-on-the-shop-program).
What changed:

- one import line at the top;
- one line, `@_ouro_log`, above each of the four functions;
- **nothing else.** Same line count inside the bodies, same comments, same
  docstrings, same order.

Two details of that placement are not decoration. The import goes **below** the
module docstring, because a string put above it stops being the docstring and
becomes an ordinary expression — silently. In JavaScript the same import goes
below `"use strict"`, because that directive only works while it is first; when
it stops being first the program keeps running, with different rules. Both of
those were bugs here once, and both are now held down by tests. **The beginning
of a file is not neutral ground**, and most of what can go wrong with this tool
goes wrong there.

The decorator sits closest to `def` — inside any other decorators — so what gets
recorded is the function itself, not a wrapper around it.

## What each field means

A call writes two lines. The keys are short because a long trace is a big file.

| key | on which line | what it is |
|---|---|---|
| `p` | both | Phase: `in` when the call is entered, `out` when it comes back. |
| `t` | `in` | When it was entered. Local time, milliseconds. |
| `id` | both | A unique id for this one call. It is what joins the two lines. |
| `fn` | both | The function's name, qualified the way that language qualifies names. |
| `a` | `in` | The positional arguments, as text, **snapshotted on entry** — so a function that modifies its own arguments still shows what it was handed. |
| `k` | `in` | The named arguments, as `name=value`. Languages without them write `""`. |
| `r` | `out` | What it returned. Never present together with `x`. |
| `x` | `out` | What it threw: `Type: message`. Never present together with `r`. |
| `d` | `out` | How long the call took, in seconds, off a monotonic clock. |
| `th` | `in` | Which process and thread it ran on. The second half is that language's own token — an OS thread, a goroutine, a BEAM process. |
| `ci` | `in` | Which CPU core. Always `-1`, meaning *unknown*, in every one of the eight — see [limits](limits.html#what-a-trace-cannot-see-by-construction). |

Two consequences of that table are worth stating plainly.

**An `in` with no matching `out` is a call that never came back** — a hang, a
crash, a hard exit. That is a design decision rather than an accident: a single
line written at completion could not record a call that never completes.
`ouroboros trace` lists them under `in_flight`.

**Argument names are nowhere in the table.** `a` holds values only, in all eight
languages. Three of the backends do know the names at wrap time and used to
write them, which made the field mean one thing in three languages and another
in two, and made cross-language reading impossible. The names are still in your
source next to the function; they are genuinely absent from the trace.

The whole schema, with the reasoning behind each decision, is
[SPEC.md]({{repo}}/blob/main/SPEC.md).

## Reading a trace

`ouroboros trace` parses the file and answers with something plainer than the
raw lines. `ouroboros trace-stats` answers with one row per function instead —
how often it was called, how it ended, and the real durations:

{{capture: shop-trace-stats.json | the first two functions of the four}}

The filters are the point of having a reader at all. The first five work on
both commands:

| you want | ask for | on |
|---|---|---|
| one function | `--function delivery` | both |
| calls mentioning a value | `--contains 46.8` | both |
| only the calls that threw | `--outcome raised` | both |
| only the slow ones | `--min-duration 0.5` | both |
| one thread | `--thread 4065334.128322166978432` | both |
| the last twenty | `--tail 20` | `trace` only |

`--regex` turns `--function` and `--contains` into patterns on either command.
`--limit` and `--cursor`, on `trace` only, page through a trace too big to
answer at once.

## A run that crashes

Same program, an order with a typo in it. Without any recording at all, this is
what you get:

{{capture: shop-crash-stderr.txt | python3 shop.py tea mugg}}

That names the line, which is useful and often enough. What it does not say is
*which values* got there — and in a real system the bad value is usually born
several calls above the line that finally chokes on it.

The trace does say. Filtered to just the calls that ended badly:

```sh
ouroboros trace debug.info --outcome raised
```

{{capture: shop-crash-trace.json}}

Two calls, not one: `subtotal` raised `KeyError: 'mugg'`, and so did `total`,
which was inside it. Both records carry the arguments they were handed, so the
bad value is visible at the point it entered the program rather than at the
point it detonated.

Notice also that the stack trace above now has frames from the helper
(`ouroboros_runtime.py`, line 234) between yours. That is one of the things
wrapping always changes, and it is listed as such on the
[limits page](limits.html#what-wrapping-always-changes).

## The same call on eight languages

One function, `add(2, 3)`, returning `5`. Written eight times, in eight
languages, compiled or interpreted by eight different toolchains — and recorded
into the same shape. These eight blocks came out of one run of
`capture.py`, which uses the project's own cross-language test helpers, so they
are eight halves of one measurement rather than eight anecdotes.

{{capture: add-python.jsonl | Python}}

{{capture: add-javascript.jsonl | JavaScript}}

{{capture: add-c.jsonl | C}}

{{capture: add-cpp.jsonl | C++}}

{{capture: add-elixir.jsonl | Elixir}}

{{capture: add-go.jsonl | Go}}

{{capture: add-java.jsonl | Java}}

{{capture: add-csharp.jsonl | C#}}

### What is identical, and what is deliberately not

Identical, in all eight: the keys, their order, their meaning, two lines per
call, `a` as `2, 3` — values without names — and `ci` as `-1`.

Different, on purpose:

- **The qualified name.** `add` in Python, C, JavaScript, Go and Elixir;
  `Prog.add` in Java and C#, because a method there lives in a class. C++ writes
  `Class::method` for a method. Elixir writes the bare name even though the
  function is in a module.
- **The thread token.** A thread id in Python and C, a worker number in
  JavaScript (`0` for the main one), a goroutine number in Go, a JVM thread in
  Java, a managed thread in C#, and a BEAM process — `#PID<0.95.0>` — in Elixir.
- **How numbers print.** Python writes `2e-06`, JavaScript writes `0.00005`, C
  writes `0.000000`, for durations of the same order. That is each language's
  own rendering of a number, not different units.

The rule the project holds itself to is: **pin the schema, not the dialect.** A
cross-language test parses two traces, removes the dialect fields, and compares
what is left field by field.

## Wrapping less than a whole file

`wrap-file` instruments everything in the file. Two narrower commands exist, and
both matter more than they sound:

```sh
ouroboros wrap-functions app.py parse_row load_config
```

Only the functions you name. This is the right tool for a hot path, and the only
sane tool for **recursion**: the Python decorator adds a stack frame per call, so
deep recursion that fitted before wrapping may not fit after — wrap the callers,
not the recursive function itself. The numbers are on the
[limits page](limits.html#what-instrumenting-costs-you).

```sh
ouroboros wrap-snippet --language python < snippet.py
```

Reads code on standard input and writes the instrumented version to standard
output, touching no files at all. Useful for looking at what the tool would do
before letting it do it.

## Driving it from an AI agent

The same three steps — instrument, run, read — are exposed over MCP as
{{state: mcp_tools}} tools, so a coding agent can check what its own code did
instead of predicting it. There is a sandbox mode as well: `create_project`
makes a draft copy, `write_file` instruments on save, `execute` runs a command
inside the draft with the trace file already pointed at, and `finish` copies the
result back out.

Two things about `finish` are worth knowing before you use it, and both are in
[limits](limits.html#what-comes-back-out-of-the-sandbox): the instrumentation
comes back out with your code — the answer says so in as many words — and files
that look machine-made are left behind, which includes an image your own program
drew. Every skipped file is named, with its reason. Read that list.

Setup for Claude Code and Cursor is on the
[install page]({{repo}}/blob/main/docs/install.md), and the tools themselves are
listed in [docs/mcp-tools.md]({{repo}}/blob/main/docs/mcp-tools.md) — a page that
is itself printed from a real conversation with the server rather than written.
