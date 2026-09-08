---
title: Working with AI
---

**English** · [Русский](with-ai.ru.md)

# Working with AI

A model reading the source reasons about what **should** be happening. A model
reading call records knows what **was** happening.

The difference is not in how much there is to go on, but in which question has an
answer at all. The source shows what **can** happen: the branches written there,
the arguments declared there. The records show something else — which branches
ran, which arguments actually turned up, which calls hung and which raised. The
second is not in the source in any shape, and no amount of reading will get it
out.

The reverse holds too, and it belongs right next to it: **the records will not
tell you the code is wrong.** They tell you what it does. The conclusion "and it
ought to be otherwise" stays with the human.

## What to set up

1. Install the tool — [Install](install.md).
2. Add one block to the client's configuration:

   ```json
   { "mcpServers": { "ouroboros": { "type": "stdio", "command": "ouroboros-mcp" } } }
   ```

3. Check that the agent now has tools named `wrap_file`, `read_trace`,
   `trace_stats` and the rest — seventeen in all.
4. Give it a task. Ready-made wording — [below](#wording-to-give-the-agent).

## Seventeen tools, four groups

The server lays the operations out **directly**, with no dispatch layer in
between: the agent sees all seventeen names and calls them by name.

**Add call logging**

| tool | what it does |
|---|---|
| `wrap_code_snippet` | instruments a string of code you hand it, saving nothing |
| `wrap_file` | instruments a file in place |
| `wrap_functions` | instruments **only the named functions** — for hot and large files |

**Read the records**

| tool | what it does |
|---|---|
| `read_trace` | records, with filtering and reading in chunks |
| `trace_stats` | summary: how many calls, how many raised, how long they took |

**Draft**

| tool | what it does |
|---|---|
| `create_project` | starts a draft with a change history |
| `write_file` | instruments **before** saving; rejects what does not parse |
| `read_file`, `list_files` | look at what is in the draft |
| `execute` | runs a command, filling in the path to `debug.info` itself |
| `finish` | copies the draft into the clean copy; does **not** take instrumentation off |

**C and C++ through clangd** — choosing what to instrument in a large tree

| tool | what it does |
|---|---|
| `lint_file` | `clang-tidy` analysis |
| `symbol_search` | find a name across the whole tree |
| `document_symbols` | what a single file defines |
| `references` | who calls it |
| `call_hierarchy` | who calls whom, down the chain |
| `describe_symbol` | where it is defined and with what signature |

Every tool is marked with what it does to files: the reading ones are marked
safe, `wrap_file` and `finish` as rewriting, `execute` as running an arbitrary
command. The client sees this before the first call
([`ouroboros/mcp/server.py:665`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/mcp/server.py#L665)).

**The tables above are a short retelling. The full reference is taken off the
live server:** [MCP tool reference](mcp-tools.md) — for each of the seventeen
tools a title, a description, the argument schema, the response schema and a real
answer to a real call. It is not written by hand but printed from
[`docs/mcp-tools.json`](mcp-tools.json), which is captured in conversation with
the server by `scripts/probe/build-reference.sh`. If the retelling here and the
reference disagree — the reference is right.

## What it looks like in practice

A `wrap_file` call through the MCP server:

```json
{
  "ok": true,
  "path": "/srv/tmp/ouro-work/mcpdemo/stats.py",
  "language": "python",
  "functions_wrapped": 3,
  "runtime_header": "/srv/tmp/ouro-work/mcpdemo/ouroboros_runtime.py"
}
```

After the program has run — `trace_stats` over the same draft:

```json
{
  "ok": true,
  "calls_parsed": 5,
  "malformed": 0,
  "total_calls": 5,
  "by_function": [
    { "name": "parse_line", "count": 3, "result": 3, "raised": 0, "unknown": 0,
      "duration_seconds": { "min": 1e-06, "max": 2e-06, "mean": 2e-06, "total": 5e-06, "count": 3 } },
    { "name": "average", "count": 1, "result": 1, "raised": 0, "unknown": 0,
      "duration_seconds": { "min": 2e-06, "max": 2e-06, "mean": 2e-06, "total": 2e-06, "count": 1 } },
    { "name": "report", "count": 1, "result": 1, "raised": 0, "unknown": 0,
      "duration_seconds": { "min": 0.000351, "max": 0.000351, "mean": 0.000351, "total": 0.000351, "count": 1 } }
  ]
}
```

This is a real exchange with the server, not a sample.

## The order of work

```
create_project  →  write_file  →  execute  →  read_trace  →  finish
   the draft       instruments    runs the    read the       carry it
                   on the write   command     records        outside
```

Or shorter, without a draft: `wrap_file` → the agent runs the program itself →
`read_trace`.

**Hot files.** `wrap_file` on a file with a million calls a second drowns the
records you need in noise. An agent that was not told otherwise will sooner reach
for `wrap_file` — so on a hot file you name `wrap_functions` in the task
outright.

## Wording to give the agent

Works as it stands:

> Work out how `<file>` actually runs. Take Ouroboros: start a draft, instrument
> `<file>` (on a hot file use `wrap_functions` and name the functions), run it on
> real data, read the records.
>
> In the report say: how many calls were recorded, which functions ran and with
> which arguments, what came back, what raised and what never came back
> (`in_flight`). Separately — how many lines failed to parse (`malformed`).
>
> Draw no conclusions about whether the program is correct: the records say what
> the code did, not what it was supposed to do. If you see something odd — put it
> to me as a question, not as a statement.

## What the agent must check instead of taking on trust

**That anything got recorded at all.** `calls_parsed: 0` means the instrumented
code did not run, not that everything is clean. That is the first thing to look
at.

**Malformed lines.** `malformed` above zero — part of the records did not parse,
and the conclusions were drawn from an incomplete picture.

**Calls that never returned.** `in_flight` is an entry without an exit: a hang, a
crash or a hard exit. An empty list is good; a non-empty one needs explaining.

**Coverage.** A branch that was not entered gives no records and **does not
complain**. Nothing can be claimed from a trace about code that did not run.

**What exactly was recorded.** A record states "on this input, this came out". It
does not state that this is right. An agent that offers a trace as proof of
correctness is mistaken — and that is the most common mistake made with this
tool.

**Duration is not cost.** The `d` field measures the **instrumented** run. You
cannot read it as the cost of an ordinary run.

## The skill

The repository holds a skill, [`skill/SKILL.md`](https://github.com/digitable-lol/ouroboros/blob/main/skill/SKILL.md)
— a short description of the tool for an agent: what to install, how to connect
the server, all seventeen tools, the order of work, and the limits with measured
numbers. It describes **this** tool and nothing beyond it.
