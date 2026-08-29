# Ouroboros-Logger — Cross-language contract

This is the **load-bearing contract** of the project. "Universal cross-language
logging" only means something if all backends emit a *byte-identical* record
format into the same sink. Every backend (Python, JS/TS, C, C++, Elixir) targets
the JSONL schema below exactly. example.md shows the *wrapping mechanism* per
language; this file defines the *log sink and record schema* they all share.

## 1. The sink: `debug.info`

- A single append-only UTF-8 file, **one JSON object per line** (JSONL).
- Its path is taken from the environment variable **`OUROBOROS_DEBUG_INFO`**;
  if unset, the helper falls back to `./debug.info` in the process CWD.
- The `execute` sandbox operation sets `OUROBOROS_DEBUG_INFO` to
  `<draft>/debug.info` before spawning the child, so instrumented code in any
  language writes to the same file.
- **Logging does NOT go through stdout.** example.md uses `console.log` /
  `Console.WriteLine` / `std::cout` only to illustrate the wrap; the real
  backends ship a tiny runtime helper (the analogue of `ouroboros_runtime.py`)
  that appends records directly to `OUROBOROS_DEBUG_INFO`.
- Each record is written with a single `O_APPEND` write and bounded well under
  `PIPE_BUF`, so concurrent processes/threads cannot interleave one line.

## 2. The record schema (two lines per call)

Each call appends **two lines**, paired by `id`: an **entry** line (`"p":"in"`)
when the call is entered, and a **completion** line (`"p":"out"`) when it returns
or raises. Keys are intentionally short to keep the file compact.

```jsonl
{"p":"in","t":"<started>","id":"<call_id>","fn":"<qualified_name>","a":"<arg_reprs>","k":"<kwarg_reprs>"}
{"p":"out","id":"<call_id>","fn":"<qualified_name>","r":"<result_repr>","d":<seconds>}
{"p":"out","id":"<call_id>","fn":"<qualified_name>","x":"<ExcType: message>","d":<seconds>}
```

An entry line (`"p":"in"`) with **no** matching completion (`"p":"out"` with the
same `id`) means the call entered but never returned — a hang, crash, or
hard-exit. Per-call **duration** is read directly off the completion line's `d`
(no pairing arithmetic needed).

Key rules, identical across languages:

| Key   | On    | Definition |
|-------|-------|------------|
| `p`   | both  | Phase: `"in"` (entered) or `"out"` (returned/raised). |
| `t`   | `in`  | Entry timestamp. ISO-8601 local, millisecond precision (userland); `uptime+SEC.MS` (kernel). Captured at **entry**. |
| `id`  | both  | A UUIDv4, unique per call; the join key between `in` and its `out`. |
| `fn`  | both  | Qualified name (Python `__qualname__`, C++ `Class::method`, C# `Type.Method`). On **both** lines so an `out` orphaned by a truncated capture still says what returned. |
| `a`   | `in`  | Positional args, comma-separated, each language's short repr, **snapshotted at entry** (before the body runs). |
| `k`   | `in`  | Named args as `name=repr`, comma-separated (languages without kwargs emit `""`). |
| `r`   | `out` | Result repr on normal return. Mutually exclusive with `x`. |
| `x`   | `out` | `<ExcType>: <message>` on error (raise/throw/exit). Mutually exclusive with `r`. C has no exceptions, so the C sink only ever emits `r`. |
| `d`   | `out` | Duration in **seconds** as a JSON number, between entry and completion. From a monotonic clock where the platform offers one (kernel `getnanouptime`, Python `perf_counter`, JS `hrtime`, C++ `steady_clock`, Elixir `monotonic_time`). A `raised`/`x` completion carries `d` too. |

String values (`a`/`k`/`r`/`x`/`fn`/`t`) are emitted as JSON string literals with
the mandatory escaping (`"`, `\`, control chars). The C/C++ backends hand-roll
this (`_ouro_jesc`) so the kernel build needs no JSON library. Long values are
bounded by each language's short-repr (Python `reprlib` with
`maxstring=maxother=200`); backends should match these limits.

**Per-language dialects (intentionally NOT unified):** the *repr dialect* (Python
`'x'` vs JS `"x"`), the *qualified-name dialect* (Python `Outer.<locals>.add`,
JS bare `add`, C# `Type.Method`, C++ `Class::method`), and the *number rendering*
of `d` (Python `json` → `1e-06`, JS → `0.000001` for the same value) are each
language's native form. The cross-language equivalence test
(`tests/test_cross_language.py`) parses both traces and compares the records
after normalizing these dialect fields out. Pin the schema, not the dialect.

## 3. Other records

The sandbox `execute` step appends one **meta** record per command run — a
well-formed JSON line whose `p` is neither `in` nor `out`:

```jsonl
{"p":"exec","cmd":["./app"],"rc":0,"out":"...","err":"..."}
```

so `debug.info` stays uniformly line-delimited JSON and remains the single place
to read "what ran and why". The trace parser **skips** any well-formed JSON line
whose `p` is not `in`/`out` (it is not a call event); only non-JSON / torn lines
are counted as `malformed`.

## 4. Deliberate design decisions

- **JSONL, not freetext.** Structured values mean consumers `json.loads` each
  line instead of grepping multi-line blocks; an `out` line is self-describing
  (carries `fn`), so a capture truncated at a panic still parses cleanly. Short
  keys keep the per-line overhead small for high-volume/kernel traces.
- **Two events per call: entry + completion.** The `in` line is written when the
  call is entered; the `out` line when it returns or raises, carrying the real
  `d`. This makes a call that **hangs, segfaults, or hard-exits** observable —
  its `in` has no matching `out` — which a single-line-at-completion design
  could not record. Cost, accepted: two sink writes per call (double the
  volume); for selective/kernel instrumentation of a few cold functions this is
  fine.
- **Arguments are snapshotted at entry**, so a function that mutates its inputs
  still logs the values it was actually called with.
- **Return value captured before it leaves the wrapper** — the Python analogue
  of example.md's `return (__result = expr)` invariant. The decorator captures
  `result` and logs it before returning to the caller.
- **Duration clock starts AFTER the entry write**, so the entry-write overhead is
  excluded from the measured call duration.

## 5. The corruption gate

A backend that cannot parse its input raises `CorruptedSourceError`. The sandbox
turns that into a **rejected write** — the file is not saved and not committed.
This is the prototype's realization of "the filesystem refuses to persist
low-quality code", and it needs no LSP/YouCompleteMe: a failed native parse is
itself the corruption signal.
