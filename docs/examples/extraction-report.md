---
title: Trace summary
---

**English** · [Русский](extraction-report.ru.md)

# Trace summary

What `trace-stats` and `trace` print, in full and with nothing cut. Everything
below is output from a real run of the same program from the
[README](https://github.com/digitable-lol/ouroboros#four-steps): three
functions, seven calls, two of them with an exception.

## `trace-stats` — the whole summary

```sh
ouroboros trace-stats debug.info
```

```json
{
  "ok": true,
  "path": "debug.info",
  "calls_parsed": 7,
  "malformed": 0,
  "total_calls": 7,
  "in_flight": [],
  "by_function": [
    {
      "name": "parse_line",
      "count": 3,
      "result": 3,
      "raised": 0,
      "unknown": 0,
      "duration_seconds": {
        "min": 1e-06,
        "max": 2e-06,
        "mean": 2e-06,
        "total": 5e-06,
        "count": 3
      }
    },
    {
      "name": "average",
      "count": 2,
      "result": 1,
      "raised": 1,
      "unknown": 0,
      "duration_seconds": {
        "min": 2e-06,
        "max": 3e-06,
        "mean": 2e-06,
        "total": 5e-06,
        "count": 2
      }
    },
    {
      "name": "report",
      "count": 2,
      "result": 1,
      "raised": 1,
      "unknown": 0,
      "duration_seconds": {
        "min": 6.8e-05,
        "max": 0.000346,
        "mean": 0.000207,
        "total": 0.000414,
        "count": 2
      }
    }
  ],
  "by_thread": [
    {
      "thread": "2864987.129949101195776",
      "count": 7,
      "functions": 3,
      "cpus": []
    }
  ],
  "duration_seconds": {
    "min": 1e-06,
    "max": 0.000346,
    "mean": 6.1e-05,
    "total": 0.000424,
    "count": 7
  },
  "timespan": {
    "first": "2026-08-28T23:39:45.166",
    "last": "2026-08-28T23:39:45.166",
    "seconds": 0.0,
    "timestamps_parsed": 7,
    "timestamps_unparsed": 0
  },
  "note": "counts/durations are over completed calls; `duration_seconds` are REAL per-call durations (exit−entry) from each call's `d`. `by_thread` groups calls by the `th` token (CPUs each thread ran on); empty for traces with no thread field. `in_flight` = entered (`p:in`) but never completed. `timespan` is first→last entry time."
}
```

### How to read it

**Top to bottom, not left to right.** The first four fields answer the question
"is it worth looking any further":

| field | what it means | when to worry |
|---|---|---|
| `calls_parsed` | how many calls completed | **0** — the instrumented code never ran |
| `malformed` | how many lines failed to parse | more than 0 — the picture is incomplete |
| `in_flight` | went in and never came back | non-empty — a hang, a crash or a hard exit |
| `total_calls` | the same count, but after filtering | less than `calls_parsed` — a filter took effect |

**`by_function` is sorted by call count**, frequent first. In every row `count`
breaks into three: `result` (returned), `raised` (raised) and `unknown` (there
is an exit line, but it does not say how the call ended; that happens with a
trace that broke off).

It shows at once: `average` was called twice, and **one of those raised**. That
is the trail of the bug.

**`duration_seconds` holds the real duration of each call**, taken from the `d`
field of each record, not from subtracting timestamps. That is why `min`/`max`
still mean something when every call fit inside one millisecond and
`timespan.seconds` is zero.

One thing apart: `report` runs a hundred times longer than `parse_line` on
average — but only because every other call is counted inside `report`. The
duration is the nested one, not the function's own.

**`by_thread`** — how many calls each thread made and which cores it ran on.
Here there is one thread and `cpus` is empty, because the run was Python: there
the core number is always "unknown"
([Languages](../languages.md#core-thread-and-clock)).

**`timespan`** — from the first entry to the last. `timestamps_unparsed` above
zero means some of the timestamps did not parse.

## `trace` — the records themselves, filtered

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

`matched` is how many records passed the filter in all; `returned` is how many
made it onto this page. When the two differ there is a `next_cursor`: hand it
back as the `--cursor` argument and the next page comes. `next_cursor` is
`null` — the records have run out.

`cpu: null` is the same "unknown" as `-1` in the raw record; the reader brings
them to one form.
