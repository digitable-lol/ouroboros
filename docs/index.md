---
title: Ouroboros
---

**English** · [Русский](index.ru.md)

# Ouroboros

**Shows how the code actually ran: which functions were called, with which
arguments, what they returned and what they raised.**

The tool adds call logging to the source. The program runs as usual, and every
call leaves two JSON lines — one on the way in, one on the way out.

Languages: Python, JavaScript/TypeScript, C, C++, Elixir, Go, Java, C#. The
record schema is the same for all of them.

## Four steps

**Install:**

```sh
uv tool install git+https://github.com/digitable-lol/ouroboros
```

**Add call logging:**

```sh
ouroboros wrap-file stats.py
```

```json
{"ok": true, "path": "stats.py", "language": "python", "functions_wrapped": 3, "runtime_header": "ouroboros_runtime.py"}
```

**Run as usual:**

```sh
python3 stats.py
```

**Read:**

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

The whole thing, with the source and the output of every step, is in the
[repository README](https://github.com/digitable-lol/ouroboros#four-steps).

## What is in `debug.info`

Just an append-only file, one JSON object per line. Two lines per call, tied
together by a shared `id`:

```jsonl
{"p":"in","t":"2026-08-28T23:39:45.166","id":"e668ee33-…","ci":-1,"th":"2864987.129949101195776","fn":"average","a":"[12, 30, 18]","k":""}
{"p":"out","id":"e668ee33-…","fn":"average","r":"20.0","d":2e-06}
```

That gives the cheapest answer to "where is it hanging": an entry line with no
matching exit line — the call went in and never came back. [Every key
explained](examples/trace-record.md).

## Pages

| page | what it covers |
|---|---|
| [Install](install.md) | uv, Homebrew, asdf, from source, wiring up the MCP server |
| [Getting started](getting-started.md) | every command, the order of work, what people trip over |
| [Trace code you did not write](trace-existing-code.md) | step by step: what to do, what you see, how to read it |
| [Working with AI](with-ai.md) | the MCP server and its 17 tools |
| [Everyday development](in-development.md) | a program that goes silent, a regression, what not to do |
| [Languages](languages.md) | eight languages, how their records differ |
| [Measurements](measurements.md) | what each language records, what it costs, how to repeat it |
| [Limits](limits.md) | what the tool does not do — the most important page |
| [Why it exists](why.md) | what it is for and what work it takes off you |
| [What it looks like](examples/index.md) | records, summary, configuration — in full |
| [MCP tool reference](mcp-tools.md) | all 17 tools, recorded from a live conversation with the server |

## If you read only one page

Read [Limits](limits.md).

> Records capture **how the code behaved**, not how it is supposed to behave.

A program with a bug produces records in which the bug looks like the norm. The
tool will not tell you the code is wrong — it will tell you what the code does.

## Status

**The repository holds a working tool**, not just a description of one: the
`ouroboros-logger` package <!--state:version-->0.5.0<!--/state-->, a command line of 17 commands, an MCP server of 17
tools, <!--state:tests-->1072<!--/state--> tests. Everything shown on these
pages is output from real runs on an ordinary Linux machine; the output of all
eight languages was taken separately, and it can be taken again with one
command — [Measurements](measurements.md).

Installation was checked all the way through, not "looks about right":
`uv tool install`, `brew install digitable-lol/tap/ouroboros` together with
`brew test`, `asdf plugin add` together with `asdf install` — and after each one
the installed tool instrumented a file, ran it and read the records.

What is known to be broken: instrumentation is an edit to the source, not
watching from the outside, and it can change how the program behaves, not only
how long it takes. The cases that were checked, what has already been fixed, and
what stays as it is on purpose are collected in
[Limits](limits.md#where-instrumentation-changes-behaviour). The numbers behind
them are in [Measurements](measurements.md).

The whole repository: [digitable-lol/ouroboros](https://github.com/digitable-lol/ouroboros).

## License

BSD 2-Clause,
[text](https://github.com/digitable-lol/ouroboros/blob/main/LICENSE).
Copyright (c) 2026, Digitable (Marat Zimnurov).
