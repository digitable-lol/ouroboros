---
title: Getting started
---

**English** · [Русский](getting-started.ru.md)

# Getting started

This page is about **the order of work**: which commands exist, in what order
you call them, and what to check at the end. To install the tool, see [Install](install.md).

If you came here with a task in hand, start from it:

- [Trace code you did not write](trace-existing-code.md) — find out what happens
  in a project you did not write.
- [Working with AI](with-ai.md) — give the model what actually happened instead
  of reasoning from the source.

## Two ways to work

**Straight in your own tree.** Add call logging to a file, run it as usual,
read the records. Three commands, nothing on top:

```
wrap-file  →  run as usual  →  trace
```

**In a separate draft,** if you would rather not disturb your working tree. The
tool makes a directory with a change history and moves the finished work out:

```
create  →  write  →  execute  →  trace  →  finish
```

Both do the same thing to the source; the difference is who keeps the files.

## Commands

Seventeen in all. The first seven you need almost always, the rest as the case
arises.

### Add call logging

| command | what it does |
|---|---|
| `wrap-file <path>` | the whole file, in place |
| `wrap-file <path> --stdout` | the same, but printed; the file is left alone |
| `wrap-functions <path> <name>…` | **only the named functions**, everything else untouched |
| `wrap-snippet -l <language>` | code from stdin → to stdout |

```sh
ouroboros wrap-functions stats.py average
```

```json
{"ok": true, "path": "stats.py", "language": "python", "functions_requested": ["average"], "functions_wrapped": 1, "runtime_header": "ouroboros_runtime.py"}
```

`functions_requested` against `functions_wrapped` is where you see whether what
you named was found. Ask for three names, get one instrumented, and two of them
were not in the file — that never passes silently.

Here is what the result looks like — exactly one function touched:

```python
"""Average request duration from the log."""
from ouroboros_runtime import log as _ouro_log


def parse_line(line):
    name, _, ms = line.partition(" ")
    return name, int(ms)


@_ouro_log
def average(values):
    return sum(values) / len(values)


def report(lines):
    pairs = [parse_line(l) for l in lines]
    return average([ms for _, ms in pairs])
```

To see what you would get without touching the file, use `--stdout`:

```sh
echo 'int add(int a, int b) { return a + b; }' | ouroboros wrap-snippet -l c
```

```c
#include "ouroboros_runtime.h"
int add(int a, int b) {
	struct _ouro_call __ouro __attribute__((cleanup(_ouro_emit)));
	int __ouro_result;
	_ouro_enter(&__ouro, "add", "%d, %d", a, b);
 return  (__ouro_result = (a + b), _ouro_set_result(&__ouro, "%d", __ouro_result), __ouro_result); }
```

Every language is instrumented its own way: Python gets a decorator over the
function, C gets `__attribute__((cleanup))`, C++ a scope guard, JavaScript
`try/finally`, Elixir a redefined `def`, Go `defer` with named returns, Java and
C# `try/catch/finally`. The records come out the same.
[Languages](languages.md), [design/example.md](https://github.com/digitable-lol/ouroboros/blob/main/design/example.md).

### Read the records

| command | what it does |
|---|---|
| `trace <file>` | the records themselves, with filters |
| `trace-stats <file>` | a summary: how many calls, how many raised, how long they ran |

Both filter the same way:

| argument | what it selects |
|---|---|
| `--function`, `-f` | by function name |
| `--contains`, `-c` | by arguments or by what was returned |
| `--outcome result\|raised\|unknown` | returned / raised / unclear |
| `--min-duration <seconds>` | slow calls only |
| `--thread <thread>` | one thread out of the shared record |
| `--regex` | treat `-f` and `-c` as search patterns |
| `--tail N`, `-n N` | the last N only |
| `--limit`, `--cursor` | read in pages (`next_cursor` from the previous one) |

Ready answers to the two most frequent questions:

```sh
ouroboros trace debug.info --outcome raised        # what raised, and with what
ouroboros trace debug.info --min-duration 0.1      # what ran longer than 0.1 s
```

"Where is it stuck" is not a filter: the answer always carries an `in_flight`
field — calls that have an entry line and no exit line.

### Working in a draft

| command | what it does |
|---|---|
| `create <path>` | makes `<path>/draft/` with a change history |
| `write <path> <file>` | adds the records **before** saving, content from stdin |
| `execute <path> -- <command>` | runs inside the draft, filling in the path to `debug.info` itself |
| `finish <path>` | moves the draft into the sibling `<path>/clean/` |

```sh
ouroboros create /srv/tmp/probe
```

```json
{"ok": true, "base": "/srv/tmp/probe", "draft": "/srv/tmp/probe/draft", "clean": "/srv/tmp/probe/clean"}
```

#### A project made before these directories were renamed

Those two directories used to be called `черновик` and `чистовик` — Russian for
draft and clean. A project made by an earlier release still has them on disk,
with its change history inside, so `create` **opens that project as it is**
rather than starting an empty `draft/` beside it and reporting success:

```json
{"ok": true, "draft": "…/черновик", "clean": "…/чистовик", "legacy_layout": true,
 "legacy_note": "This project still uses the previous directory names …"}
```

Rename them whenever it suits you — `mv черновик draft`, and `mv чистовик clean`
if that one exists. The git history lives inside the directory and travels with
it. If both a `draft/` and a `черновик/` are present, `draft/` is the one used
and `legacy_note` names the other one instead of passing over it in silence.

```sh
ouroboros execute /srv/tmp/probe -- python3 stats.py
```

Besides the call records, `execute` appends one line about the command itself to
the same file — what ran, with what exit code and what output:

```jsonl
{"p":"exec","cmd":["python3","stats.py"],"rc":0,"out":"20.0\n","err":""}
```

The reader skips that line: its `p` is neither `in` nor `out`, so it is not a
call. It does not count as a malformed line either.

Each `write` leaves its own entry in the history:

```
ouroboros: write stats.py (+3 wrapped)
ouroboros: init draft
```

### The rest — C and C++ through clangd

`lint`, `symbols`, `doc-symbols`, `refs`, `callers`, `describe`. You need them
when you have to pick **what exactly** to instrument in a large C tree: find a
function by name, see who calls it, and only then name it in `wrap-functions`.
They require `clang-tidy` and `clangd` on the machine.

## What happens to code that does not parse

It is not saved. Not "saved as is", not "saved halfway" — not saved:

```sh
printf 'def broken(:\n    return 1\n' | ouroboros write /srv/tmp/probe bad.py
```

```
[python] corrupted source in bad.py: invalid syntax (<unknown>, line 1)
```

Exit code 1, no file in the draft, no entry in the history. Parsing is done by
the language's own parser — `ast` for Python, libclang for C and C++,
`@babel/parser` for JavaScript, `Code.string_to_quoted` for Elixir, `go/parser`
for Go, the JDK compiler for Java, Roslyn for C# — so no separate code checker
is needed: if it did not parse, it is corrupted
([`ouroboros/languages/base.py:18`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/languages/base.py#L18)).

## What `finish` moves and what it leaves behind

Checked by running it, not deduced from the description. `finish` copies the
draft into the clean copy, leaving behind whatever a machine makes again:
`.git`, `debug.info`, tool caches, built binaries and crash dumps
([`ouroboros/sandbox/sync.py:29-82`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/sandbox/sync.py#L29-L82)).

The literal answer, on the same project as above:

```json
{
  "ok": true,
  "clean": "…/clean",
  "synced": [".gitignore", "ouroboros_runtime.py", "stats.py"],
  "skipped": [],
  "instrumentation_removed": false,
  "note": "The copy is instrumented, exactly like the draft: …"
}
```

At that moment the draft also held `__pycache__/ouroboros_runtime.cpython-313.pyc`,
left there by the run — it did not travel to the clean copy.

Three consequences follow, worth knowing in advance:

1. **Call logging is not stripped on the move**, and the
   `instrumentation_removed: false` field says so outright. The clean copy holds
   the same instrumented file as the draft. The tool has no inverse operation
   and cannot have one: `write_file` instruments **before** saving, so the
   author's original text is neither in the draft nor in the change history. You
   strip instrumentation from your own version control, or by hand.
2. **The `ouroboros_runtime.py` helper travels with the code** — and rightly so:
   without it the instrumented file will not run.
3. **What did not travel is named in `skipped`.** Build the program right in the
   draft and the binary stays there instead of going to the clean copy. The rule
   cannot tell the compiler's output from a file the program was asked to
   produce: both were made by a machine. So it drops them and **names** what it
   dropped — look at the list and take by hand whatever you needed.

The same project, but the program is built in the draft and a picture is put
next to it:

```json
{
  "synced": [".gitignore", "notes.csv", "ouroboros_runtime.h", "prog.c", "run"],
  "skipped": [
    {"path": "prog", "reason": "looks built (compiled-format signature, or a NUL byte in the first 8 KiB) and has no source extension"},
    {"path": "prog.o", "reason": ".o: build output, remade by rebuilding"},
    {"path": "real.png", "reason": "looks built (compiled-format signature, or a NUL byte in the first 8 KiB) and has no source extension"}
  ]
}
```

Note that `run` — a shell script with no extension and the executable bit set —
did travel. The executable bit is not taken as a sign, because an ordinary
script has it too; what counts is the content.

## Two things people trip over the first time

**A hot file.** `wrap-file` on a file with a million calls a second drowns the
records you want in noise and slows the program noticeably. For files like that
there is `wrap-functions` — only the named functions are instrumented. For C
there is also `--minimal`: a lighter record that keeps no call frame, for hot
and recursive functions.

**Running it again spoils nothing.** Instrumentation is idempotent: `wrap-file`
on an already instrumented file will not add a second decorator. But
`debug.info` is append-only — if you want a clean record, delete the file before
the run.

## What to check before calling the work done

- The `malformed` field in the `trace` answer is 0. Otherwise some lines did not parse.
- The `in_flight` field is empty — or you know why those calls never returned.
- `calls_parsed` is not zero. Zero means the instrumented code simply never ran.
- The run touched the branches you are about to draw conclusions about. A trace
  says nothing about what did not run, and does not warn you about it.
- Nobody is assuming that a record with no exceptions means a correct program.

The last two are not a formality. Why exactly: [Limits](limits.md).
