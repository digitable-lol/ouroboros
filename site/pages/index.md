---
title: What it is
tagline: Ouroboros records what a program actually did — one line when a function is entered, one when it returns.
---

::: hero
# Your program printed a number. Ouroboros tells you where that number came from.

A shop program was asked for the price of an order and answered
**`Total: 51.80`**. The customer had been promised free delivery over 50.00, and
their goods came to 52.00. They were charged for delivery anyway.

The printed line does not say why. The source code says what the program *may*
do — every branch, every case, including the ones that did not happen on this
run. Neither of them says what the program *did* on **this** run, with **these**
numbers.

Ouroboros answers that, and only that. It adds recording to your own source
file. You then run the program the ordinary way, and every call leaves two lines
behind: one when it is entered, one when it comes back.
:::

::: cards
**Not a debugger.** Nothing to attach to, nothing to step through. The program runs at full speed, unattended, on a server if that is where the problem is.

**Not a profiler.** It answers *what was this called with and what did it answer*, not *where did the time go* — though every line carries its own duration.

**Not a log statement.** You do not decide in advance what is worth printing, and you do not go back and delete it later from forty places.
:::

## The four steps, on the shop program

This is the whole tool. Four commands, and the output below each one is the real
output of running it — not an illustration.

### 1. Install it

```sh
uv tool install git+https://github.com/digitable-lol/ouroboros
```

```
Installed 2 executables: ouroboros, ouroboros-mcp
```

There is a Homebrew formula too — `brew install digitable-lol/tap/ouroboros` —
and an asdf plugin, a single-file build and a container image. All of them are
on the [install page]({{repo}}/blob/main/docs/install.md).

### 2. Point it at the file

Here is the program. It is twenty-nine lines and it has no logging in it at
all.

{{source: site/examples/shop.py | site/examples/shop.py, before}}

```sh
ouroboros wrap-file shop.py
```

{{capture: shop-wrap.json}}

It found {{fact: shop.wrapped_functions}} functions and put recording into each
of them. The file is the same file — same lines, same order, same comments, same
docstring — plus one import at the top and one marker line above each function.
Nothing else moved. A helper file, `ouroboros_runtime.py`, is dropped next to it;
that is the piece that does the actual writing, and it imports nothing but the
standard library.

The whole instrumented file is on the [examples page](example.html#the-file-after-wrapping).

### 3. Run the program the way you always run it

```sh
python3 shop.py tea mug kettle
```

{{capture: shop-run.txt}}

Same answer, same command, no flags, no wrapper process. That matters more than
it sounds: whatever you already do to run the thing — a test suite, a container,
a cron job, a server — keeps working, and the recording happens inside it.

### 4. Read what happened

Beside the program there is now a file called `debug.info`. It has
{{fact: shop.trace_lines}} lines in it, two for each of the
{{fact: shop.trace_calls}} calls that happened:

{{capture: shop-debug-info.jsonl | debug.info, exactly as written}}

You do not have to read that shape. `ouroboros trace` turns it into something
plainer, and can filter it — by function, by argument, by how long a call took,
by whether it blew up:

{{capture: shop-trace.json | the first two of the four calls}}

## What the four calls say

Read the `r` values — what each call answered — in the order they happened:

| the call | it was given | it answered |
|---|---|---|
| `subtotal` | `['tea', 'mug', 'kettle']` | `52.0` |
| `discount` | `52.0` | `5.2` |
| `delivery` | **`46.8`** | `5.0` |
| `total` | `['tea', 'mug', 'kettle']` | `51.8` |

The goods came to 52.00. The discount was worked out from 52.00, correctly. And
then the delivery rule — *free from 50.00* — was asked about **46.80**, the
amount left *after* the discount. Under 50, so delivery was charged.

Nobody wrote that down anywhere. It is not in the printed total, and you would
have to read the program in exactly the right order to see it. It is simply what
the run did, and the run wrote it down.

> **What the trace does not do is tell you this is a bug.** Maybe delivery is
> supposed to be charged on the discounted amount; plenty of shops work that
> way. A trace shows behaviour, never intent — a rule that has been wrong for
> three years looks exactly like a rule that is right. The judgement stays with
> the person who knows the business. This is the most important sentence on the
> site, and it is the first line of the [limits](limits.html) page too.

## How it works

Three views of the same mechanism: what happens step by step, where the tool
sits among the things you already have, and how it decides what to record.

### From your command to the written line

{{diagram: pipeline | Wrapping happens once, at your keyboard. Recording happens on every call, inside your own process. Nothing watches from outside.}}

The important part of that picture is the middle: **the recording runs inside
your program**, as ordinary code in your own file. There is no agent, no daemon,
no port, no ptrace, no permissions to grant. That is also the reason for the
whole of the [limits](limits.html) page: code that is spliced into your source
is code that can change how your source behaves.

### Where the tool sits

{{diagram: context | Ouroboros talks to two things: your source tree and the toolchain that already builds it. The trace file is the only thing it produces.}}

The toolchain in that picture is yours, unchanged. Ouroboros does not carry its
own compilers. It asks the language's own parser where each function begins and
ends — `libclang` for C and C++, Babel for JavaScript, `go/parser` for Go, the
JDK's own parser for Java, Roslyn for C# — and then edits bytes at those exact
offsets. The two parsers that are not already on your machine, `libclang` and
Babel, ship inside the package.

An AI coding agent can drive the same thing over MCP: {{state: mcp_tools}} tools,
the same wrap-run-read loop, so the agent reads what its code did rather than
guessing.

### What gets recorded, and what quietly does not

{{diagram: decision | Every "skipped" branch on the left is a real gap in the trace. Most of them are silent, which is why they are worth knowing before you rely on a trace being complete.}}

Three of those branches deserve saying out loud, because they produce a trace
that looks complete and is not:

- **A lambda in Python, an arrow function with a short body in JavaScript, a
  `func` value in Go** — none of them are wrapped. Their calls never appear.
- **A branch you did not run** leaves nothing behind, and nothing says so. An
  empty trace for a function means "not called on this run", never "fine".
- **Five kinds of C# member** — `yield`, `ref` returns, pointers, `ref struct`,
  expression-bodied properties — are skipped, because the instrumented form
  would not compile. That one is *not* silent: the answer names them.

## Eight languages, one shape of record

The point of supporting eight languages is that the record is the *same* in all
eight, so one reader answers questions across a system built out of several.

| language | file kinds | how the recording is put in | what has to be on the machine |
|---|---|---|---|
| Python | `.py` | a decorator above the function | nothing beyond Python |
| JavaScript / TypeScript | `.js .mjs .cjs .jsx .ts .tsx` | `try/finally` inside the body | `node` |
| C | `.c .h` | `__attribute__((cleanup))` | `gcc` or `clang` |
| C++ | `.cpp .cc .cxx .hpp .hh .hxx` | a scope guard (RAII) | `g++` or `clang++` |
| Elixir | `.ex .exs` | `use Ouroboros.Trace`, redefining `def` | `elixir` |
| Go | `.go` | named returns and `defer` | `go`, for wrapping as well as building |
| Java | `.java` | `try/catch/finally` inside the body | a JDK, for wrapping as well |
| C# | `.cs` | `try/catch/finally` inside the body | the .NET SDK, for wrapping as well |

Here is the same call, `add(2, 3)`, recorded on the two ends of that list. All
eight are on the [examples page](example.html#the-same-call-on-eight-languages),
taken in one run:

{{capture: add-python.jsonl | Python}}

{{capture: add-csharp.jsonl | C#}}

Same keys, same order, same meaning. What differs is deliberate: C# writes the
class into the name (`Prog.add`), Python does not; each language renders values
and durations its own way. The [record schema](example.html#what-each-field-means)
is fixed; the dialect is each language's own.

**flang is not supported**, in case you came from there: no extension maps to a
backend, and the tool does not mention it anywhere. There is an idea for it, and
an idea is not a feature.

## What it costs

Two lines per call, written to one file. That is cheap per call and not free in
bulk, so here are both halves, measured on this machine today by
`scripts/measure/run.sh` — the same program, twenty thousand calls, run seven
times with and seven times without the recording.

{{table: speed}}

The number that means something is the last column. The ratio "how many times
slower" does not: the program being measured does nothing except call a
function, so the ratio mostly measures how fast an empty loop is in that
language. **Seven of the eight land between
{{measured: C#.added_us_per_call}} and {{measured: Python.added_us_per_call}}
microseconds of extra time per call**, and Elixir is the outlier at
{{measured: Elixir.added_us_per_call}}.

Most of that is not the language. It is opening a file, appending a line and
closing it, twice per call — which is why C, C++, JavaScript and Java land within
a few microseconds of each other despite being nothing alike.

{{table: volume}}

Between {{measured: JavaScript.bytes_per_call}} and
{{measured: C++.bytes_per_call}} bytes per call, with two short arguments. In
round numbers: **a million calls is about a quarter of a gigabyte.** Wrap a hot
loop and you will notice; wrap the twenty functions you actually have a question
about, and you will not.

The one dial is C's `--minimal` form: one line instead of two, no call frame,
{{measured: C, short form (--minimal).added_us_per_call}} microseconds and
{{measured: C, short form (--minimal).bytes_per_call}} bytes per call. It exists
for kernel builds, and it gives up the completion line — so you lose return
values, durations and exceptions.

The full machine those numbers came from is on the
[limits page](limits.html#the-machine-these-numbers-came-from).

## Before you decide to use it

The honest version of this list is a whole page — [limits](limits.html) — and it
is the page worth reading before the others. The short form:

- **It records what happened, not what should have happened.** A bug that has
  worked for years looks exactly like correct behaviour.
- **What did not run leaves nothing, and nothing warns you.** Absence of records
  reads like "all quiet here"; it means "nobody went here".
- **There is no undo command.** Instrumented code stays instrumented until you
  revert it yourself, from version control or by hand.
- **Argument names are not recorded, in any language** — values only. The
  function's own signature is still in your source, but a tool reading the trace
  alone cannot recover them.
- **Wrapping edits your source, so it can change behaviour.** Four cases where
  it does are documented rather than hidden: an uncaught Go panic prints a
  different message, C++ does not record returned class objects, C and C++ print
  string pointers as addresses rather than contents, and rendering a value calls
  your own `__repr__` / `toString`.
- **Run your own tests after wrapping, not just before.** If they are green on
  the instrumented copy, the edit was harmless for your program. That is the
  check that actually settles it.

## Where to go next

- [Examples](example.html) — the instrumented file in full, the same call on all
  eight languages, what every field means, and a run that crashes.
- [Limits](limits.html) — everything the tool cannot do, everywhere it changes
  behaviour, and when not to use it at all.
- [The repository]({{repo}}) — source, {{state: tests}} tests,
  {{state: coverage_percent}}% statement and branch coverage over
  {{state: total_units}} units.
- [SPEC.md]({{repo}}/blob/main/SPEC.md) — the record schema all eight backends
  are held to, and why each decision was made.
