---
title: Everyday development
---

**English** · [Русский](in-development.ru.md)

# Everyday development

Cases where the tool pays for itself in a single run. Reading code somebody else
wrote has a page of its own:
[Trace code you did not write](trace-existing-code.md).

## The program runs for hours and says nothing

The most common and the most galling case: the process is working, the processor
is busy, and from the outside a live run and a hung one look the same.

**The records answer this directly, and the answer takes no arithmetic.** Every
call gives two lines tied together by a shared `id`: entry (`"p":"in"`) and
completion (`"p":"out"`). An entry line that has **no** matching exit line means
exactly one thing: the call went in and never came back — a hang, a crash or a
hard exit.

You do not have to hunt for unpaired `id`s by hand: the tool has already done
it. Both `trace` and `trace-stats` return an `in_flight` field:

```json
  "in_flight": [],
  "in_flight_truncated": false,
```

Empty — every call returned. Not empty — there is the function name, the call
number, the entry time, the thread and the core. You also see which function was
called last and with which arguments: `fn`, `a` and `k` stand in the entry line,
which is already written — you do not have to wait for the call to finish to
learn something.

This is exactly why there are two records and not one. The price is named
plainly: two records per call means twice the volume. And here is what was
bought: a call that hung, crashed or exited hard **cannot be recorded at all**
by a “one line on completion” scheme — it is simply absent from it and
indistinguishable from a call that never happened.

**In a multi-threaded program** look at `th` and `ci`. To pick out one thread —
`--thread <label>`. A per-thread summary is in `trace-stats`:

```json
  "by_thread": [
    { "thread": "2864987.129949101195776", "count": 7, "functions": 3, "cpus": [] }
  ],
```

> `cpus` is empty here because the run was on Python, where the core number is
> always “unknown”. Details — [Languages](languages.md#core-thread-and-clock).

**So as not to drown in records.** Instrumenting a hot file whole will not do:
what you need drowns in the noise. Instrument selected functions —
`wrap-functions` instead of `wrap-file`.

**Count before objecting about volume.** The usual objection — “it will come out
in gigabytes” — is almost always a feeling, not a count. A measurement: seven
calls gave 14 lines and 1887 bytes, that is about **270 bytes per call** with
short arguments. A hundred thousand calls is roughly 27 MB. Your number will be
different (the length depends on your arguments and results, which are cut off
at 200 characters) — measure on a small run and multiply.

## “It used to work”

Run before the change, run after, compare the records. What comes out is not
“the tests are red” but **these forty calls now behave differently** — with the
arguments and results of both sides.

The comparing is yours: the tool has no “collate two runs” command. There is
`trace`, and after that the usual tools for comparing files.

**Zero out the fields that change on their own**, otherwise every record without
exception comes out different: `t` (entry time), `id` (call number), `d`
(duration), and also `ci` and `th`. Everything else — `fn`, `a`, `k`, `r`, `x` —
should not change from run to run, and their divergence is the answer.

Like this, for example:

```sh
ouroboros trace debug.info --limit 1000 \
  | jq -S '[.records[] | {name, args, kwargs, outcome_kind, outcome}]' > before.json
```

Separately useful on changes where the tests are green but the behaviour moved:
a test checks what somebody thought up in advance, the records show what was.

## Debugging the same spot twice

The records keep the function **and its arguments**. Which means the call where
everything broke can be pulled out and repeated at your place, without
reproducing the whole setting.

In detail, together with the caveats about hidden state, on the page
[Trace code you did not write](trace-existing-code.md#replay-the-call-that-broke).

## Finding what is slow

```sh
ouroboros trace debug.info --min-duration 0.1
```

Leaves only calls longer than 0.1 seconds. The `trace-stats` summary meanwhile
gives `min`/`max`/`mean`/`total` for each function — and those are the real
durations of every call from the `d` field, not a subtraction of timestamps.

Remember the limitation: these are durations of the **instrumented** run. Fit
for “which function is suspiciously long”, not fit for “what this costs in
production”.

## What was run

`ouroboros execute` appends one service record to `debug.info` for every command
it ran — the command itself, the return code and the output:

```jsonl
{"p":"exec","cmd":["python3","stats.py"],"rc":0,"out":"20.0\n","err":""}
```

That way the file stays uniform JSONL and **the single place where it is written
down what was run and how it ended**. The reader skips such lines: their `p` is
neither `in` nor `out`, they are not call events. Only lines that do not parse
as JSON, or torn ones, count as `malformed`.

## What not to do

**Instrumenting everything in a row on a hot path.** The useful records will
drown, and the program will slow down noticeably. Instrument selected functions.

**Forgetting that instrumentation stays in the code.** It does not come off by
itself, and `finish` does not take it off either. Before anything goes out,
look with your own eyes: `git status`, `git diff`, and look as well for the
runtime helper file next to the instrumented one.

**Reading `d` as the real price of a call.** Instrumentation changes how long
the program runs, so `d` is the duration of the instrumented run, not of an
ordinary one.

**Letting `debug.info` pile up between runs.** The file is append-only. If you
need a clean record — delete it before the run, otherwise the previous run mixes
into the current one and `calls_parsed` will lie.

**Taking the records for proof.** They say what was, not what ought to be. More
on that — [Limits](limits.md).
