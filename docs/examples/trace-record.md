---
title: A trace record
---

**English** · [Русский](trace-record.ru.md)

# A trace record

A real `debug.info` file, whole, off the run in the
[README](https://github.com/digitable-lol/ouroboros#four-steps). The program is
twenty lines of Python: three functions, two top-level calls — one that works,
one that divides by zero.

## The whole file

```jsonl
{"p":"in","t":"2026-08-28T23:39:45.166","id":"23192bfc-8625-453d-bd48-1af02ceec638","ci":-1,"th":"2864987.129949101195776","fn":"report","a":"['get 12', 'put 30', 'get 18']","k":""}
{"p":"in","t":"2026-08-28T23:39:45.166","id":"9f93c6ea-f76f-4768-ab35-cb41731e05f1","ci":-1,"th":"2864987.129949101195776","fn":"parse_line","a":"'get 12'","k":""}
{"p":"out","id":"9f93c6ea-f76f-4768-ab35-cb41731e05f1","fn":"parse_line","r":"('get', 12)","d":2e-06}
{"p":"in","t":"2026-08-28T23:39:45.166","id":"4fc1254d-6b4f-451f-8e5f-ddb4b1597ca9","ci":-1,"th":"2864987.129949101195776","fn":"parse_line","a":"'put 30'","k":""}
{"p":"out","id":"4fc1254d-6b4f-451f-8e5f-ddb4b1597ca9","fn":"parse_line","r":"('put', 30)","d":2e-06}
{"p":"in","t":"2026-08-28T23:39:45.166","id":"73eba9cc-20ae-4bd4-92d8-4e50e6dc2376","ci":-1,"th":"2864987.129949101195776","fn":"parse_line","a":"'get 18'","k":""}
{"p":"out","id":"73eba9cc-20ae-4bd4-92d8-4e50e6dc2376","fn":"parse_line","r":"('get', 18)","d":1e-06}
{"p":"in","t":"2026-08-28T23:39:45.166","id":"e668ee33-6d35-4bb8-9f96-cb44f488c93f","ci":-1,"th":"2864987.129949101195776","fn":"average","a":"[12, 30, 18]","k":""}
{"p":"out","id":"e668ee33-6d35-4bb8-9f96-cb44f488c93f","fn":"average","r":"20.0","d":2e-06}
{"p":"out","id":"23192bfc-8625-453d-bd48-1af02ceec638","fn":"report","r":"20.0","d":0.000346}
{"p":"in","t":"2026-08-28T23:39:45.166","id":"cb4e33f4-d0b2-4099-bcf2-044e4a88c1fa","ci":-1,"th":"2864987.129949101195776","fn":"report","a":"[]","k":""}
{"p":"in","t":"2026-08-28T23:39:45.166","id":"ef71eb89-6727-4cdf-a3b7-2ea54cff81e3","ci":-1,"th":"2864987.129949101195776","fn":"average","a":"[]","k":""}
{"p":"out","id":"ef71eb89-6727-4cdf-a3b7-2ea54cff81e3","fn":"average","x":"ZeroDivisionError: division by zero","d":3e-06}
{"p":"out","id":"cb4e33f4-d0b2-4099-bcf2-044e4a88c1fa","fn":"report","x":"ZeroDivisionError: division by zero","d":6.8e-05}
```

14 lines, 1887 bytes, 7 calls — about 270 bytes per call.

Look at the order: the entry line for `report` comes **first**, and its exit
line comes **tenth**, after everything `report` managed to call. You can see
the nesting from which calls landed between an entry and its exit.

## Where it is written

- One file, **append-only**, UTF-8, one JSON object per line.
- The path comes from the `OUROBOROS_DEBUG_INFO` environment variable. Not set —
  the file is `./debug.info` in the process's working directory.
- `ouroboros execute` sets `OUROBOROS_DEBUG_INFO` to `<draft>/debug.info`
  before the run — which is why instrumented code in any language writes into
  one and the same file.
- **Records do not go through standard output.** The runtime helper appends them
  straight to the file, so the program's own output stays clean.
- Every record is written in **one append**, and it stays small enough that
  lines cannot interleave: two processes or two threads will not tear each
  other's line.

## The keys

| key | on which line | what it means |
|---|---|---|
| `p` | both | entry (`"in"`) or completion (`"out"`) |
| `t` | entry | entry time: local ISO-8601, down to the millisecond |
| `id` | both | UUIDv4, one per call; this is what ties an entry to its exit |
| `ci` | entry | CPU core number; `-1` = "unknown" |
| `th` | entry | thread token; in Python it is `<process>.<thread number>` |
| `fn` | both | full function name — **on both lines on purpose** |
| `a` | entry | positional arguments, taken **on entry, before the body** |
| `k` | entry | keyword arguments as `name=value`; an empty string where a language has none |
| `r` | exit | the result on a normal return. Never together with `x` |
| `x` | exit | `<Type>: <message>` on an error. Never together with `r`. C has no exceptions, and its records only ever carry `r` |
| `d` | exit | duration in **seconds**, as a number; off a monotonic clock. A completion with an exception has `d` too |

The keys are short on purpose: there are a lot of lines, and spare bytes in each
one get expensive on a big trace.

**`fn` on both lines is deliberate.** If the trace broke off and the entry line
was lost, the orphaned exit line still says **what** came back.

**Arguments are taken on entry, before the body.** Checked: a function that
appends to the list handed to it still recorded `[1, 2]` — what it was called
with — and not what it turned the list into.

**The result is taken before it leaves.** The runtime helper picks it up and
writes it down before handing it to the caller.

## A hung call shows up directly

An entry line with **no** matching exit line under the same `id` means the call
went in and never came back — a hang, a crash or a hard exit.

You do not have to hunt for unpaired `id`s by hand: the reader has already done
it, and they sit in the `in_flight` field of every answer. This is exactly the
answer no "one line on completion" logging gives: a call that never completed is
simply absent from such a record, indistinguishable from a call that never
happened.

The duration is not arrived at by subtraction either: it is sitting ready in
the `d` field.

## Records that are not about calls

`ouroboros execute` adds one bookkeeping record per command it runs — a valid
JSON line whose `p` is neither `in` nor `out`:

```jsonl
{"p":"exec","cmd":["python3","stats.py"],"rc":0,"out":"20.0\n","err":""}
```

That keeps `debug.info` uniform JSONL and **the only place that says what was
run and how it ended**.

Parsing **skips** any valid JSON line whose `p` is neither `in` nor `out`. Only
lines that do not parse as JSON, or torn ones — say from a bad capture off a
serial port while working inside the system kernel — count as `malformed`.

## The limit on value length

Long values are held down by a short rendering of the value: in Python that is
`reprlib` with `maxstring = maxother = 200`, and lists, dicts and sets are cut
to their first ten elements
([`ouroboros/runtime.py:53`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L53)).
The other languages hold to the same limits.

A value is not cut just anywhere, and it cannot be restored from the remainder.
A neighbouring case is `<Foo object at 0x…>`: that is recorded, but it is the
object's identity, not its value.

## One schema, different dialects

The eight languages share one schema. The dialects — how a language renders a
value, how it writes a full name, how it prints a number — were left native on
purpose.

What that means in practice is on the [Languages](../languages.md) page.

## What the record leaves out by design

**Branches nobody entered.** The records describe a run, not a program.

**Intent.** A function returned `-1` — error code or a real answer?

**Meaning in the order of calls.** There is an entry time, a call index, a
thread and a duration. That one call came *after* another and answered the way
it did because of it is not recorded.

**The real cost of a call.** The `d` field measures an instrumented run.

In full — [Limits](../limits.md).
