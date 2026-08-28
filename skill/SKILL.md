---
name: ouroboros-tracing
description: Use when a specification, a test suite or a regression corpus needs examples and there is working code that already produces them. Ouroboros instruments source (Python, JS/TS, C, C++, Elixir) so running it emits a structured call trace, and the bridge turns that trace into FTS `пример` blocks with file/function/call-uuid provenance. Covers the three-tool MCP routing protocol, the draft/clean sandbox workflow, what the bridge refuses to convert and why it never guesses, how representative cases are chosen so thresholds get bracketed, and the hard limit that a trace records how code behaved rather than how it should — so a specification grown from one inherits the program's bugs as law. Use it before hand-writing examples, and never as a source of `правило` or `свойство`.
version: 1.0.0
author: Digitable
license: Apache-2.0
platforms: [linux, macos]
metadata:
  digit:
    tags: [ouroboros, tracing, instrumentation, fts, examples, mcp, provenance]
    category: software-development
    related_skills: [fts-gate, fts, digit-tools-core, verified-answers]
---

# Ouroboros Tracing

## Overview

Writing examples is the expensive part of an executable specification, and it is
the part that decides how well the specification generalises. In the synthesis
experiment behind `ga/`, quadrupling the sample cut the train/holdout gap from
68.8 to 11.2 percentage points, while adding `свойство` invariants moved it by
1.1–4.3. **Examples are the scarce input.**

Ouroboros makes them a by-product of running the code. It instruments a source
file, the instrumented copy writes one JSONL record per call entry and exit into
`debug.info`, and the bridge turns those records into `пример` blocks.

What each side is entitled to supply is not negotiable:

| Block | Source | Why |
|---|---|---|
| `пример` | the trace | it is an observation, and observations are what a trace has |
| `правило` | a person, or a synthesiser | the law is what is being sought, not what was seen |
| `свойство` | a person | an invariant is a claim about all runs, not a summary of some |

## When to Use

- A specification, test corpus or regression suite needs input→output examples
  and a working implementation exists.
- You are about to hand-write examples for behaviour that already runs.
- You need to know what a program actually does on real traffic — which branches
  run, which arguments occur, which calls hang or raise.
- A specification's examples must be auditable back to a real execution.

Do not use it to discover what code *should* do, to generate `свойство` blocks,
or as evidence that a program is correct. See § Pitfalls — the limit there is
the whole point, not a footnote.

## Prerequisites

Python ≥ 3.12. `libclang` only for the C/C++ backends; Node for JS/TS. The MCP
server needs no other service. Register it beside the other Digit servers:

```json
{ "mcpServers": { "ouroboros": {
    "type": "stdio", "command": "uv",
    "args": ["run", "--directory", "<path>/ouroboros-src", "ouroboros-mcp-router"] } } }
```

## The routing protocol

Nineteen operations are exposed as **three** tools, so nineteen schemas do not
sit in every turn. Two cheap steps, then execute — the same shape as
`digit-tools-core`:

1. `ouroboros_capabilities` — the group index (`instrument`, `sandbox`, `trace`,
   `fts`, `clang`).
2. `ouroboros_describe` with one group — the input schemas of its operations.
3. `ouroboros_invoke` with `operation` and `arguments`.

Do not call `ouroboros_invoke` for an operation whose schema you have not read.
Failures come back as structured results with the schema attached, not as
exceptions; only a malformed protocol call is an MCP-level error.

## The workflow

```
create_project  ->  write_file  ->  execute  ->  read_trace  ->  fts_extract_examples
   draft repo      wrap-on-save     runs it     inspect it        пример blocks
```

`create_project` makes a draft (`черновик`) git repo. `write_file` instruments
the file **before** saving it and commits one revision per operation; code that
does not parse is **rejected, not saved**. `execute` runs a command with
`OUROBOROS_DEBUG_INFO` pointed at the draft. `finish` mirrors the draft to the
clean tree (`чистовик`), without `.git` or `debug.info`.

Use `wrap_functions` rather than `wrap_file` on a hot or large file: wrapping
everything floods the sink and the useful records drown.

## What the bridge refuses

Refusal is the feature. A fabricated example is worse than a missing one,
because it corrupts the fitness function that consumes it, silently. Every
dropped call is counted by reason in the extraction report:

| Reason | What it means |
|---|---|
| `truncated-repr` | the repr hit the 200-char cap; the tail is gone and is not reconstructed |
| `unparseable-repr` | `<Foo object at 0x…>` — an identity, not a value |
| `non-scalar` | a list or dict where FTS takes a scalar |
| `mixed-field-type` | one parameter took an int on one call and a string on another |
| `no-parameter-name` | positional args with no signature to name them; `арг1` is not invented |
| `number-not-representable` | no exact FTS spelling (the grammar has no exponent form) |
| `empty-name` | an empty string — FTS has no empty string literal |
| `raised-call` | the call threw; `ожидается результат равен` asserts a value, and an exception is not one |

Parameter names come from an explicit list, or from a Python signature read off
the source. **TypeScript and C signatures are not read automatically** — pass
`param_names` or every call is refused.

## Choosing which calls become examples

A thousand calls do not make a thousand examples. Identical calls collapse to
one (the count is kept in the provenance), and the survivors are ranked:
range extremes first, then coverage of every boolean value and every distinct
exception, then **behaviour breaks** — pairs of adjacent points where the output's
slope changes, which bracket a threshold — then farthest-point spread.

This targets a measured failure: a synthesised discount specification put its
threshold at 8000 instead of 10000 because the sample had no point between 8000
and 12000. Trace selection brackets the same threshold to within 50.

Each emitted example carries the file, function, call UUID and selection reason
as `//` comments. An example without provenance is an unfalsifiable claim.

## Pitfalls

- **A trace records how the code behaved, not how it should.** Measured: a
  discount calculator with `>` where the rule says `>=` produces a trace whose
  boundary case is simply wrong. Rules written from the domain make the gate
  refuse that document (`PROPERTY_VIOLATION`); rules fitted to the trace make it
  **certified** — a proof certificate for a bug. Grounding in facts does not
  yield truth. This is why `свойство` stays human-written.
- **The workload is the sample.** Examples inherit the distribution of the
  traffic. On a shipping task, examples from evenly-spread traffic fitted their
  training set at 88.5 % and scored 21.4 % on the holdout, and adding four times
  more of them did not move either number. More examples from the wrong
  distribution do not help.
- **A trace covers only executed branches.** A branch nothing exercised produces
  no examples and no warning that it is missing.
- **Examples alone are always refused.** A document with `пример` blocks and no
  `правило` computes its initial value for every input; the gate answers
  `PROPERTY_VIOLATION`. That is correct: the examples are the obligation, not the
  answer.
- **Not every function has an FTS shape.** FTS rules are conditions over an
  object's fields; a converter or a lookup has no compact rule form. Real case:
  `arabicToRoman` from the tools catalog yields clean examples, and every
  specification built around them is refused — one of them as `NON_EXHAUSTIVE`.
- **Instrumentation changes the program's timing**, not its results. Do not read
  `d` durations from an instrumented run as the uninstrumented cost.

## Verification

- [ ] The extraction report was read: `skipped_by_reason` is empty or understood.
- [ ] Every example carries a `// trace:` provenance comment with a call UUID.
- [ ] `правило` and `свойство` were written by a person, not lifted from the trace.
- [ ] The traced workload covers the branches the specification claims to describe.
- [ ] The specification was put through `fts-gate check`, and a refusal was read
      rather than worked around.
- [ ] Nobody is treating "the certificate is green" as "the program is correct".
