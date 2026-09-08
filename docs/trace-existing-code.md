---
title: Trace code you did not write
---

**English** · [Русский](trace-existing-code.ru.md)

# Trace code you did not write

You have landed in a project you did not write. Forty thousand lines, a dozen
layers, and exactly one question: **what actually runs here?**

Reading the source is expensive, and it answers a different question. The source
shows what **can** happen. A run answers what did happen: which functions were
called, with which arguments, what they returned, which raised, and which never
came back at all. Time and again it turns out there are three live paths and the
rest never ran once — and three quarters of the reading was unnecessary.

Here is what to do, step by step. Everything shown is output from real runs.

## Step 0. Do not wreck someone else's tree

Instrumentation **rewrites the source in place**. In someone else's project you
settle that up front, not afterwards. Two ways:

**A branch of your own** in version control — instrument, run, throw the branch
away. The easiest way if the project is already under version control.

**The tool's draft** — if there is no version control, or the tree must not be
touched:

```sh
ouroboros create /srv/tmp/probe
```

```json
{"ok": true, "base": "/srv/tmp/probe", "draft": "/srv/tmp/probe/draft", "clean": "/srv/tmp/probe/clean"}
```

The path is yours to pick; inside it the tool sets up the draft directory
`draft/` with a change history and prepares a place for the clean copy
`clean/`. From there files go into the draft through `ouroboros write` and are
run through `ouroboros execute`. The original stays untouched.

## Step 1. Choose what to instrument

**This is the main step, and it is where people go wrong most often.**

| command | what it instruments | when to take it |
|---|---|---|
| `wrap-file` | the whole file | the file is small and not hot |
| `wrap-functions` | only the named functions | always, when the file is large or hot |

**Instrumenting everything is not an option.** On a file with a million calls a
second, `wrap-file` drowns the records you need in noise: the useful ones are
there, but finding them in the stream is impossible. On top of that, every call
is two records written to disk, and on a hot path that shows.

```sh
ouroboros wrap-functions parser.c parse_header parse_body
```

```json
{"ok": true, "path": "parser.c", "language": "c", "functions_requested": ["parse_body", "parse_header"], "functions_wrapped": 2, "runtime_header": "ouroboros_runtime.h"}
```

Check `functions_requested` against `functions_wrapped`: if you named three and
two were instrumented, one of the names was not found in the file.

How to pick functions in a project that is not yours: take the entry point and a
layer or two below it, run, see who got called — then instrument the next layer.
Two runs over ten functions each are cheaper than one over a thousand.

For C and C++ the tool helps you choose: `ouroboros doc-symbols <file>` shows
what the file defines, `ouroboros callers <file> <name>` shows who calls that
function. Both need `clangd` on the machine.

**Do the arithmetic before you take fright.** A record is two JSON lines per
call. In the run from the [README](https://github.com/digitable-lol/ouroboros#4-read-the-records)
seven calls gave 14 lines and 1887 bytes — about **270 bytes per call** with
short arguments. A hundred thousand calls is roughly 27 MB. Your number will be
different: the length depends on how long your arguments and results are (both
are truncated at 200 characters). Measure on a small run and multiply.

## Step 2. Instrument

```sh
ouroboros wrap-file stats.py
```

```json
{"ok": true, "path": "stats.py", "language": "python", "functions_wrapped": 3, "runtime_header": "ouroboros_runtime.py"}
```

The source is **not reprinted**: the parser is used only to find the boundaries
of the functions, and after that short pieces are inserted into the original
text. Indentation, comments, blank lines and style stay as they were.

Code that does not parse **is not saved**:

```
[python] corrupted source in bad.py: invalid syntax (<unknown>, line 1)
```

There is no half-instrumented state.

## Step 3. Run it the usual way

With the same command the project is always started with:

```sh
python3 stats.py
```

Nothing stops, nothing waits for a human, there are no breakpoints. The trace
file turns up next to it — `debug.info`; to put it somewhere else, set
`OUROBOROS_DEBUG_INFO`.

**Run it under a real load.** The records capture what happened; on three
made-up requests exactly what you made up will happen, and that is not how live
paths are found.

## Step 4. Read

Start with the summary — it answers "what is alive at all":

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
  "by_thread": [
    { "thread": "2864987.129949101195776", "count": 7, "functions": 3, "cpus": [] }
  ],
  "timespan": {
    "first": "2026-08-28T23:39:45.166", "last": "2026-08-28T23:39:45.166",
    "seconds": 0.0, "timestamps_parsed": 7, "timestamps_unparsed": 0
  }
```

One thing is plain right away: `average` was called twice, and once it
**raised**. Now the details of that call:

```sh
ouroboros trace debug.info --outcome raised
```

```json
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
    }
```

`args: "[]"` — that is what a stack trace does not have. The stack says
**where** it broke; the record says **what it was called with**.

## What to read in the records

**Who is alive.** The list of functions in `by_function` is the list of what
runs. Everything else in the file is either dead or untouched by your load; the
records will not tell those two apart.

**What they are called with.** The real arguments, not the ones from the
project's README examples. This is usually where it comes out that a parameter
declared optional always arrives filled in.

Arguments are taken **on entry, before the body**. A function that spoils its own
input still recorded what it was called with. Verified:

```python
def mutate(items):
    items.append(99)
    return len(items)
```

```jsonl
{"p":"in", …,"fn":"mutate","a":"[1, 2]","k":""}
{"p":"out", …,"fn":"mutate","r":"3","d":1e-06}
```

`[1, 2]` is what got recorded — what it was called with — while what came back
was `3`, the length of the already-changed list.

**Not one of the eight languages puts argument names in the record.** The `a`
field holds values and nothing else:

| language | field `a` (by position) | field `k` (keyword) |
|---|---|---|
| Python | values only: `'world'`, `[1, 2]` | `greeting='hello', loud=True` |
| JavaScript | values only: `2, 3` | empty |
| C | values only: `2, 3` | empty |
| C++ | values only: `2, 3` | empty |
| Elixir | values only: `2, 3` | empty |
| Go | values only: `2, 3` | empty |

The name of a positional argument cannot be recovered from the record anywhere —
look at the function signature in the source: the `fn` field names the function,
and its signature sits right there in the file. In C, C++, Elixir and Go the
signature is parsed during instrumentation and the names are known, but they are
deliberately left out: otherwise the `a` field would mean one thing in some
languages and another in the rest, and matching a record from one language
against a record from another would stop being possible. More on this in
[Languages](languages.md#where-argument-names-come-from).

**What was returned and what was raised.** `r` and `x` are mutually exclusive.
To pick out the raised ones — `--outcome raised`.

**Who did not come back.** An entry line with **no** matching exit line under the
same `id` means the call went in and never returned: a hang, a crash or a hard
exit. Nothing to work out by hand — such calls sit in the `in_flight` field of
every answer. That is how you find the place where the program hangs, and the
same thing tells "hanging" from "running slowly".

**Who is slow.** `--min-duration 0.1` leaves only calls longer than 0.1 seconds.

**Who ran at the same time as whom.** `th` is the thread label, `ci` the core
number. They show which thread did what; without them two interleaved sequences
of calls look like a single meaningless one. `--thread <label>` leaves one
thread.

> In an ordinary program the `ci` field is `-1` in **all eight** languages, and
> `null` once parsed. In Python that is because CPython has no
> `os.sched_getcpu` at all, and the runtime helper honestly writes "unknown"
> ([`ouroboros/runtime.py:75`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L75));
> in the rest, because there is no portable way to learn the core in their
> environments. A real core number happens only in a C build inside an operating
> system kernel. The thread label `th` is there always and everywhere, and always
> has two parts — process and thread; the breakdown by language is in
> [Languages](languages.md#core-thread-and-clock).

**What was run.** `ouroboros execute` appends one line per command to the same
file:

```jsonl
{"p":"exec","cmd":["python3","stats.py"],"rc":0,"out":"20.0\n","err":""}
```

That keeps `debug.info` the one place where it is written down what was run and
how it ended. The reader skips such lines and does not count them as malformed.

**Duration is not cost.** The `d` field measures the **instrumented** run. You
cannot read `d` as the cost of an ordinary run, and it is no replacement for a
profiler. `d` starts counting **after** the entry line is written, so the cost of
that write is not in `d` — but everything else the instrumentation added is
([`ouroboros/runtime.py:311`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L311)).

## Replay the call that broke

A record holds the function **and its arguments**. Which means the call
everything fell apart on can be lifted out and repeated at your desk — as many
times as you like, without reproducing the whole setting. That is exactly what is
missing when yesterday's bug will not reproduce today.

Lifting it out and plugging it in is hand work: the tool has no "play this call
again" command.

The less hidden state a language has, the better this works. In a language
without mutable variables **the function plus its arguments is the entire state
of the call**. In languages with mutable state you have to restore that as well.

## If you need a snapshot of how it was

The same run does as a snapshot of behaviour before a change. Run before, run
after, compare the records — and the answer is not "the tests are red" but
**these forty calls now behave differently**, with the arguments and results of
both sides.

The tool has no separate "compare two runs" command: there is `trace`, and after
that the usual tools for comparing files.

When comparing, keep in mind the fields that change from run to run of their own
accord: `t`, `id`, `d`, and also `ci` and `th`. They get blanked out before the
comparison — otherwise every single record comes out different.

## Clean up after yourself

Instrumentation **does not come off by itself**. The tool has no reverse
command, and `finish` does not take it off either — it carries out exactly what
lies in the draft, minus `.git` and `debug.info`.

It is removed the way any other edit is: `git checkout` on the files touched,
or by throwing the branch away, or by deleting the draft. Do not forget
`ouroboros_runtime.py` (or `.h`, `.js`, `.hpp`, `.ex`, `.go`) — it sits next to
the instrumented file.

## What the records will not answer

**What the code is supposed to do.** They say what it does.

**Whether that is right.** A bug that has worked for years looks in the records
exactly like correct behaviour.

**What happens in a branch that was not entered.** There will be no records, and
no warning either.

**Intent.** A function returned `-1` — an error code or a real answer? That is
not in the record.

In full — [Limits](limits.md).
