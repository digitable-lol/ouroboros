---
title: Where it is discussed
---

**English** · [Русский](discussions.ru.md)

# Where it is discussed

Public threads about the tool, so that questions asked in one place can be
answered from what was said in another, and so that a claim made in public can
be traced back to the page it came from.

| where | what was posted | when |
|---|---|---|
| [r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/comments/1wcsqri/ouroboros_debuggertracer_for_llm_and_programmers/) | the tool, the eight languages, and the table of how much a trace adds to a model reading someone else's code | 10 September 2026 |

## What to do with a thread

Answer from the measurements rather than from memory. Every number quoted in
those threads has a page behind it:

| a question of the form | answer from |
|---|---|
| how much does it help a model | [Measurements](measurements.md#does-call-logging-help-you-understand-code-you-did-not-write) — four answerers, twelve programs, each question asked with and without the trace |
| what does it cost per call | [Measurements](measurements.md) — 20,002 calls, the machine and the commands are named |
| what can it not see | [Limits](limits.md) |
| does an agent actually pick it up | [bench/RESULTS.md](https://github.com/digitable-lol/ouroboros/blob/main/bench/RESULTS.md) for the run where it did not, and [bench/RESULTS_debug.md](https://github.com/digitable-lol/ouroboros/blob/main/bench/RESULTS_debug.md) for the one with a control arm where it did |

If a thread turns up a question the documentation cannot answer, that is a gap
in the documentation — write the answer into the page, then reply with it.
