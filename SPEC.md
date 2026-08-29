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
  `PIPE_BUF` (4096 bytes on Linux), so concurrent processes/threads cannot
  interleave one line. Per-value short reprs are not enough to guarantee this —
  a call with thirty arguments overruns it — so every helper also enforces a
  per-record ceiling: if the assembled line is too long, it shortens the value
  fields (`a`, `k`, `r`, `x`, never `fn`/`id`/`t`) until the line fits and marks
  each shortened one with a trailing `…`. Truncation never splits a UTF-8
  character, because half a character would make the whole line undecodable.

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
- **C++ gives up `r` for class types returned by value.** Routing such a return
  through a capture helper costs the copy elision C++17 guarantees: a program
  that counts its own constructors then prints a move it never printed
  unwrapped, and a type with copy and move both `= delete` stops compiling
  outright. Observability loses to transparency here — the record still carries
  the arguments, the duration and whether the call threw; only the returned
  object's repr is `(no value)`. Scalars, pointers and references are captured
  normally.
- **`x` is filled from a `catch (...)` clause in C++, not from the scope
  guard.** During stack unwinding `std::current_exception()` is null, so a
  destructor cannot say which exception left the function; a clause that records
  and immediately rethrows can, and leaves control flow unchanged.
- **Parameter NAMES are not recorded, in any language.** `a` carries values only
  (`"2, 3"`), because that is what the table above says it is, and the split
  between `a` and `k` is the thing that lets one schema describe five languages.
  C, C++ and Elixir build the record at wrap time, where the signature is parsed
  and the names ARE known, and earlier wrote `"a=2, b=3"` into `a`; that made
  those three disagree with Python and JS about what the field means, and the
  cross-language comparison could not be written. The names are not lost to the
  reader — `fn` identifies the function, and its signature is in the source — but
  they are genuinely absent from the trace, and a tool reading only the trace
  cannot recover them.
- **The Python decorator costs one stack frame per wrapped call.** The wrapper
  sits between caller and callee, so recursion that fitted before the wrap can
  overflow after it. Measured with `setrecursionlimit(200)`: greatest reachable
  depth 198 unwrapped, 95 wrapped — 2.08x shallower. This is not removable while
  the mechanism is a decorator. Deep recursion should be wrapped with
  `wrap_functions` on the non-recursive callers, not on the recursive function.
- **Duration clock starts AFTER the entry write**, so the entry-write overhead is
  excluded from the measured call duration.

## 5. The corruption gate

A backend that cannot parse its input raises `CorruptedSourceError`. The sandbox
turns that into a **rejected write** — the file is not saved and not committed.
This is the prototype's realization of "the filesystem refuses to persist
low-quality code", and it needs no LSP/YouCompleteMe: a failed native parse is
itself the corruption signal.
