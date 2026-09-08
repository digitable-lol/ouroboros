---
title: MCP tool reference
---

**English** · [Русский](mcp-tools.ru.md)

# MCP tool reference

**This page is recorded, not written.** `scripts/probe/render_reference.py` prints it from [`docs/mcp-tools.json`](mcp-tools.json), which is captured in a live conversation with the server: every tool here was declared by the server itself, every one of them was actually called, and the answer is quoted below word for word.

Rebuild:

```sh
scripts/probe/build-reference.sh
```

| | |
|---|---|
| server | `ouroboros-logger` version `0.5.0` |
| protocol | `2025-11-25` |
| started by | `ouroboros-mcp (pyproject [project.scripts])` |
| tools declared | **17** |
| captured | 2026-09-08T18:20:24 |

No tool was declared and left uncalled: every one of them has a real answer.

In the example paths `<work>` is the directory the capture ran in and `<python>` is the Python executable it was called with. Long strings and lists are trimmed, and the trimming is marked inside the value itself.

## What the server says about itself on connect

```
Ouroboros-Logger: guaranteed function-level logging instrumentation for code.

The loop is instrument -> run -> observe:
  1. instrument: wrap_code_snippet (in memory), wrap_file (whole file in place),
     or wrap_functions (only named functions — for hot/kernel paths). Or work in
     a sandbox: create_project, then write_file (wrap-on-save), execute, finish.
  2. run: execute the instrumented code (execute, or run it yourself); every
     wrapped call appends `in`/`out` JSONL records to a debug.info trace.
  3. observe: read_trace (structural query + pagination) and trace_stats
     (per-function counts + real durations). min_duration finds slow calls;
     in_flight surfaces hung/crashed ones.

Choosing WHAT to instrument in a large C/C++ tree — six clangd/clang-tidy tools,
listed here because a tool absent from these instructions does not get chosen:
symbol_search (find a name across the tree), document_symbols (what one file
defines), references (who uses it), call_hierarchy (who calls whom, transitively),
describe_symbol (where it is defined, with what signature), lint_file (clang-tidy
findings). Use them BEFORE wrap_functions to pick the functions worth wrapping,
instead of wrapping a whole hot file. They need `clangd` and `clang-tidy` on PATH
and, for anything cross-file, a compile_commands.json; without those they return
{ok: false} explaining what is missing, so it is safe to try one and read the answer.

Filesystem effects: wrap_file/wrap_functions overwrite the target file in place;
write_file/finish mutate the sandbox tree; execute runs arbitrary commands. The
read_* / list_files / trace / clangd tools never write. See SPEC.md for the trace
schema.
```

## Add call logging

### `wrap_code_snippet` — Wrap code snippet

Wrap a raw code string with function-level logging instrumentation.

**yes:** reads only; **no:** touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `code` | string | yes | — |
| `language` | string | yes | — |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "code": "def square(n):\n    return n * n\n",
  "language": "python"
}
```

Got (answer):

```json
{
  "ok": true,
  "language": "python",
  "functions_wrapped": 1,
  "code": "from ouroboros_runtime import log as _ouro_log\n@_ouro_log\ndef square(n):\n    return n * n\n"
}
```

</details>

### `wrap_file` — Instrument file in place

Instrument a source file in place; returns success/failure metrics.

Set ``minimal=True`` (C only) for the stackless depth-only probe on every
function — wrap a whole mechanism file to capture its full runtime call tree.

**yes:** overwrites what was there, calling again gives the same result; **no:** reads only, touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `path` | string | yes | — |
| `minimal` | boolean | no | `false` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "path": "<work>/m.py"
}
```

Got (answer):

```json
{
  "ok": true,
  "path": "<work>/m.py",
  "language": "python",
  "functions_wrapped": 1,
  "runtime_header": "<work>/ouroboros_runtime.py"
}
```

</details>

### `wrap_functions` — Instrument named functions in place

Instrument ONLY the named functions in a file in place (selective mode
for hot/kernel files where wrapping the whole file would flood the sink).

Set ``minimal=True`` (C only) for the stackless, depth-only probe — for
HOT/RECURSIVE/deeply-locked kernel functions where the full per-frame
struct blows the kernel stack or widens the fault surface.

**yes:** overwrites what was there, calling again gives the same result; **no:** reads only, touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `path` | string | yes | — |
| `functions` | array | yes | — |
| `minimal` | boolean | no | `false` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "path": "<work>/m2.py",
  "functions": [
    "square"
  ]
}
```

Got (answer):

```json
{
  "ok": true,
  "path": "<work>/m2.py",
  "language": "python",
  "functions_requested": [
    "square"
  ],
  "functions_wrapped": 1,
  "runtime_header": "<work>/ouroboros_runtime.py"
}
```

</details>

## Read the records

### `read_trace` — Query trace records

Query a debug.info trace (the JSONL in/out call records the runtime emits)
structurally. Filter by function/contains/outcome, min_duration (slow calls,
seconds), thread (exact `th` token — one thread out of a concurrent trace;
each record carries cpu/thread), or regex. Read in parts: a page carries
next_cursor when more matches remain — pass it back as cursor for the next
page; its absence means end. limit is the page-size hint (≤1000); tail windows
to the last N first (tail must be >= 0; tail=0 returns nothing). Slow calls
(min_duration) + hung calls (in_flight) cover "what went wrong". The read
side of instrument -> run -> observe.

**yes:** reads only; **no:** touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `path` | string | yes | — |
| `function` | string or null | no | `null` |
| `contains` | string or null | no | `null` |
| `outcome` | string or null | no | `null` |
| `min_duration` | number or null | no | `null` |
| `thread` | string or null | no | `null` |
| `regex` | boolean | no | `false` |
| `tail` | integer or null | no | `null` |
| `cursor` | string or null | no | `null` |
| `limit` | integer | no | `200` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "path": "<work>/debug.info"
}
```

Got (answer):

```json
{
  "ok": true,
  "path": "<work>/debug.info",
  "calls_parsed": 1,
  "malformed": 0,
  "matched": 1,
  "returned": 1,
  "next_cursor": null,
  "in_flight": [],
  "in_flight_truncated": false,
  "records": [
    {
      "index": 0,
      "started": "2026-06-15T10:00:00.001",
      "call_id": "abc",
      "name": "square",
      "args": "6",
      "kwargs": "",
      "outcome_kind": "result",
      "outcome": "36",
      "duration": 1.2e-05,
      "cpu": null,
      "thread": ""
    }
  ]
}
```

</details>

### `trace_stats` — Aggregate trace statistics

Aggregate a debug.info trace: per-function call counts (by outcome) and
REAL per-call durations (min/max/mean/total from each call's `d`), plus
by_thread (per-thread counts + CPUs each thread ran on), in_flight and the
entry timespan. Same filters as read_trace, incl. min_duration (seconds),
thread, and regex.

**yes:** reads only; **no:** touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `path` | string | yes | — |
| `function` | string or null | no | `null` |
| `contains` | string or null | no | `null` |
| `outcome` | string or null | no | `null` |
| `min_duration` | number or null | no | `null` |
| `thread` | string or null | no | `null` |
| `regex` | boolean | no | `false` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "path": "<work>/debug.info"
}
```

Got (answer):

```json
{
  "ok": true,
  "path": "<work>/debug.info",
  "calls_parsed": 1,
  "malformed": 0,
  "total_calls": 1,
  "in_flight": [],
  "by_function": [
    {
      "name": "square",
      "count": 1,
      "result": 1,
      "raised": 0,
      "unknown": 0,
      "duration_seconds": {
        "min": 1.2e-05,
        "max": 1.2e-05,
        "mean": 1.2e-05,
        "total": 1.2e-05,
        "count": 1
      }
    }
  ],
  "by_thread": [],
  "duration_seconds": {
    "min": 1.2e-05,
    "max": 1.2e-05,
    "mean": 1.2e-05,
    "total": 1.2e-05,
    "count": 1
  },
  "timespan": {
    "first": "2026-06-15T10:00:00.001",
    "last": "2026-06-15T10:00:00.001",
    "seconds": 0.0,
    "timestamps_parsed": 1,
    "timestamps_unparsed": 0
  },
  "note": "counts/durations are over completed calls; `duration_seconds` are REAL per-call durations (exit−entry) from each call's `d`. `by_thread` groups calls by the `th` token (CPUs each thread ran on); empty for traces with no ... (+102 chars)"
}
```

</details>

## Draft workspace

### `create_project` — Create draft project

Create a draft (`draft/`) git project under the given base path.

A base left over from a build that used the previous directory names is
adopted as it is, rather than a fresh draft being made beside it; either
way the answer says which layout it found.

**yes:** calling again gives the same result; **no:** reads only, overwrites what was there, touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `base` | string | yes | — |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "base": "<work>/site"
}
```

Got (answer):

```json
{
  "ok": true,
  "base": "<work>/site",
  "draft": "<work>/site/draft",
  "clean": "<work>/site/clean"
}
```

</details>

### `write_file` — Wrap-on-save into draft

Wrap-on-save a file into the draft and commit it (rejects unparseable code).

**no:** reads only, overwrites what was there, calling again gives the same result, touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `base` | string | yes | — |
| `rel_path` | string | yes | — |
| `content` | string | yes | — |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "base": "<work>/site",
  "rel_path": "main.py",
  "content": "def square(n):\n    return n * n\n\nprint(square(6))\n"
}
```

Got (answer):

```json
{
  "ok": true,
  "rel_path": "main.py",
  "language": "python",
  "functions_wrapped": 1,
  "wrapped": true,
  "committed": true
}
```

</details>

### `read_file` — Read file from draft

Read a file from the draft.

**yes:** reads only; **no:** touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `base` | string | yes | — |
| `rel_path` | string | yes | — |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "base": "<work>/site",
  "rel_path": "main.py"
}
```

Got (answer):

```json
{
  "ok": true,
  "content": "from ouroboros_runtime import log as _ouro_log\n@_ouro_log\ndef square(n):\n    return n * n\n\nprint(square(6))\n"
}
```

</details>

### `list_files` — List files in draft

List tracked files in the draft.

**yes:** reads only; **no:** touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `base` | string | yes | — |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "base": "<work>/site"
}
```

Got (answer):

```json
{
  "ok": true,
  "files": [
    ".gitignore",
    "main.py",
    "... 1 more"
  ]
}
```

</details>

### `execute` — Execute command in draft

Run a command in the draft; runtime info is funneled to debug.info.

**yes:** overwrites what was there, touches something beyond its own arguments; **no:** reads only, calling again gives the same result

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `base` | string | yes | — |
| `command` | array | yes | — |
| `timeout` | number or null | no | `null` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "base": "<work>/site",
  "command": [
    "<python>",
    "main.py"
  ]
}
```

Got (answer):

```json
{
  "ok": true,
  "returncode": 0,
  "stdout": "36\n",
  "stderr": "",
  "debug_info": "<work>/site/draft/debug.info"
}
```

</details>

### `finish` — Copy draft into the output tree

Copy the draft (`draft/`) into the output tree (`clean/`).

The copy KEEPS the logging instrumentation — there is no un-instrument
step, and this is not one. write_file wraps code before saving it, so no
un-instrumented copy of the source exists anywhere to restore. Left
behind: .git, debug.info, and build output (__pycache__, *.pyc, *.beam,
tool caches). Wipes the output tree first, then rebuilds it.

**yes:** overwrites what was there, calling again gives the same result; **no:** reads only, touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `base` | string | yes | — |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "base": "<work>/site"
}
```

Got (answer):

```json
{
  "ok": true,
  "clean": "<work>/site/clean",
  "synced": [
    ".gitignore",
    "main.py",
    "... 1 more"
  ],
  "skipped": [],
  "instrumentation_removed": false,
  "note": "The copy is instrumented, exactly like the draft: this step publishes the draft, it does not un-instrument it. Left behind: .git, debug.info, tool caches, and anything that looks built (compiled binaries, object files, c... (+125 chars)"
}
```

</details>

## C and C++ through clangd

### `lint_file` — Lint C/C++ file (clang-tidy)

Static-analyse a C/C++ file with clang-tidy — real bugs the parse gate
can't see (use-after-free, `if (a = b)`, dead stores, perf traps). Uses the
same compile_commands.json as the instrumenter; filters the `__ouro`
reserved-identifier noise our own instrumentation injects. `checks` overrides
the default clang-tidy check set.

**yes:** reads only; **no:** touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `path` | string | yes | — |
| `checks` | string or null | no | `null` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "path": "<work>/lib.c"
}
```

Got (answer):

```json
{
  "ok": true,
  "path": "<work>/lib.c",
  "language": "c",
  "tool": "/usr/bin/clang-tidy",
  "checks": "bugprone-*,clang-analyzer-*,performance-*,clang-diagnostic-*",
  "diagnostics": [],
  "counts": {
    "error": 0,
    "warning": 0
  },
  "filtered_instrumentation_noise": 0,
  "filtered_out_of_file": 0
}
```

</details>

### `symbol_search` — Search C/C++ symbols (clangd)

Smart cross-file C/C++ symbol search via clangd's workspace/symbol — find
functions/types/vars by name across a whole tree (to pick wrap_functions
targets) instead of grepping. `root` is the project dir; optional
`compile_commands_dir` points clangd at the build's compile_commands.json.
First call on a fresh tree pays background-index cost (cached to disk after);
raise `index_timeout` (seconds) for a very large tree. The result's
`index_complete` is False if indexing didn't finish (results may be partial).

**yes:** calling again gives the same result; **no:** reads only, overwrites what was there, touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `query` | string | yes | — |
| `root` | string | yes | — |
| `compile_commands_dir` | string or null | no | `null` |
| `limit` | integer | no | `100` |
| `index_timeout` | number | no | `60.0` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "query": "ouro_helper",
  "root": "<work>",
  "compile_commands_dir": "<work>"
}
```

Got (answer):

```json
{
  "ok": true,
  "query": "ouro_helper",
  "root": "<work>",
  "index_complete": true,
  "matched": 1,
  "returned": 1,
  "symbols": [
    {
      "name": "ouro_helper",
      "kind": "function",
      "container": "",
      "file": "<work>/lib.c",
      "line": 1
    }
  ]
}
```

</details>

### `document_symbols` — List symbols in C/C++ file (clangd)

List every symbol defined in ONE C/C++ file (functions, types, vars) — the
per-file menu of wrap_functions candidates. Needs no project index, so it is
fast. Returns name/kind/line for each.

**yes:** reads only; **no:** touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `path` | string | yes | — |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "path": "<work>/lib.c"
}
```

Got (answer):

```json
{
  "ok": true,
  "path": "<work>/lib.c",
  "count": 1,
  "symbols": [
    {
      "name": "ouro_helper",
      "kind": "function",
      "line": 1
    }
  ]
}
```

</details>

### `references` — Find references to C/C++ symbol (clangd)

Every call/use site of `symbol` (defined in `path`) across the tree — who
calls it / how hot it is, to choose instrumentation targets. Cross-file, so it
waits for clangd's background index. `compile_commands_dir` points at the build's
compile_commands.json; raise `index_timeout` for a large tree. `index_complete`
in the result is False if indexing didn't finish (results may be partial).

**yes:** calling again gives the same result; **no:** reads only, overwrites what was there, touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `path` | string | yes | — |
| `symbol` | string | yes | — |
| `compile_commands_dir` | string or null | no | `null` |
| `limit` | integer | no | `200` |
| `index_timeout` | number | no | `60.0` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "path": "<work>/lib.c",
  "symbol": "ouro_helper",
  "compile_commands_dir": "<work>"
}
```

Got (answer):

```json
{
  "ok": true,
  "symbol": "ouro_helper",
  "index_complete": true,
  "matched": 0,
  "returned": 0,
  "references": []
}
```

</details>

### `call_hierarchy` — C/C++ call hierarchy (clangd)

Callers (direction='incoming') or callees ('outgoing') of a C/C++ function —
the sharpest tool for choosing what to wrap_functions along a call path. `symbol`
is defined in `path`; cross-file, waits for the background index (raise
`index_timeout` for a large tree). NOTE: 'outgoing' needs clangd ≥ ~19; older
builds report a clean unsupported error.

**yes:** calling again gives the same result; **no:** reads only, overwrites what was there, touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `path` | string | yes | — |
| `symbol` | string | yes | — |
| `direction` | string | no | `"incoming"` |
| `compile_commands_dir` | string or null | no | `null` |
| `index_timeout` | number | no | `60.0` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "path": "<work>/lib.c",
  "symbol": "ouro_helper",
  "direction": "incoming",
  "compile_commands_dir": "<work>"
}
```

Got (answer):

```json
{
  "ok": true,
  "symbol": "ouro_helper",
  "direction": "incoming",
  "index_complete": true,
  "calls": []
}
```

</details>

### `describe_symbol` — Describe C/C++ symbol (clangd)

Definition location + hover (type/signature/doc) of a C/C++ `symbol` in
`path` — navigate to where it's defined and see its signature.

**yes:** calling again gives the same result; **no:** reads only, overwrites what was there, touches something beyond its own arguments

**Arguments**

| argument | type | required | default |
|---|---|---|---|
| `path` | string | yes | — |
| `symbol` | string | yes | — |
| `compile_commands_dir` | string or null | no | `null` |

<details><summary>The real call and the real answer</summary>

Called:

```json
{
  "path": "<work>/lib.c",
  "symbol": "ouro_helper",
  "compile_commands_dir": "<work>"
}
```

Got (answer):

```json
{
  "ok": true,
  "symbol": "ouro_helper",
  "definition": {
    "file": "<work>/lib.c",
    "line": 1
  },
  "hover": "function ouro_helper\n\n→ int\nParameters:\n- int x\n\nint ouro_helper(int x)"
}
```

</details>
