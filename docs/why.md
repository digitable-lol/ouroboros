---
title: Why it exists
---

**English** · [Русский](why.ru.md)

# Why it exists

## The question the source does not answer

The source answers the question "what **can** happen". It shows every branch,
every declared argument, every possible path. It does not show which of them
happen.

The question a person actually has is almost always a different one: **what is
going on in here?** Which functions are alive, what they are called with, what
they return, where it hangs. The source holds no answer to that in any form,
and no amount of reading will get one out of it.

A run answers it. Ouroboros makes that answer cheap: one command before, one
after.

## Three jobs it works for

### 1. Making sense of code you did not write

Reading forty thousand lines is expensive. Running it and looking at which
functions are actually called, and with what, is cheap. It often turns out
there are three live paths, the rest never runs at all, and three quarters of
the reading was not needed.

Step by step — [Trace code you did not write](trace-existing-code.md).

### 2. A bug the output does not show

A stack trace says **where** it broke. The record says **what it was called
with** — and not only the function that broke, but the one that called it, and
the one that called that.

An example from the [README](https://github.com/digitable-lol/ouroboros#4-read-the-records):
the stack shows a division by zero in `average`. The records show that `average`
was called with `[]`, and that `report`, which called it, got `[]` too. The bug
is not in `average` — it is where the empty list was born.

A special case of the same job: **the program runs for hours and says nothing.**
An entry line with no matching exit line — the call went in and never came back.
The question "where is it hanging" turns into "find the unpaired `id`s", and the
tool has already done that: they are in the `in_flight` field.

### 3. A snapshot of "this is how it was"

Run before the change, run after, compare the records. The answer is not "the
tests are red" but **these forty calls now behave differently** — with the
arguments and the results from both sides. Especially useful where the tests are
green and the behaviour changed: a test checks what someone thought up in
advance, records show what actually happened.

Step by step — [Everyday development](in-development.md).

## What it is not

**Not a profiler.** A profiler measures time; this records behaviour. The `d`
field measures the instrumented run and is not the real cost.

**Not a debugger with breakpoints.** The program does not stop, nothing waits
for a human, the run goes as usual. That matters where stopping is not an
option: under load, inside a system kernel, in someone else's environment.

**Not coverage.** Coverage says a line ran. Records say **what it was called
with and what came out**.

**Not a proof of correctness.** This is the most important distinction, and it
has [a page of its own](limits.md).

## Where it does not pay off

Worth saying up front, because it has been measured. On a task where the bug is
fully visible in the final output, the tool gives nothing: the agent that was
handed it did not use it once in three runs — and was right. When the expected
output is right there, comparing output is cheaper.

The numbers and the analysis — [bench/RESULTS.md](https://github.com/digitable-lol/ouroboros/blob/main/bench/RESULTS.md).

The rule is simple: **before you instrument, ask whether the output and the
stack trace already pin the bug down.** If they do, do not spend the effort.

## The snake biting its own tail

The name is not decoration. The tool has an intent beyond making sense of code,
and it is that the chain closes:

```
the program runs
      ↓
records capture how it behaved
      ↓
the records turn into examples
      ↓
the examples make the specification checkable
      ↓
the specification checks that same program
```

The program produces the material for checking itself. Nothing has to be
invented — what has to stop is throwing away what happens anyway.

The bridge that makes the third step is a layer on top, described in the skill
[`skill/SKILL.md`](https://github.com/digitable-lol/ouroboros/blob/main/skill/SKILL.md);
**it is not part of this repository**. What is here is the first two steps:
instrumentation and records.

The thought the whole thing was started for: an invariant says what never
happens, an example shows what does — and it is the second one you can
generalise from. The skill backs it with numbers from work on synthesising
specifications; they are not quoted here, because this repository cannot
reproduce them.

And the caveat without which the previous paragraph is dangerous: examples
inherit the distribution of the workload they were taken from, and the bugs of
the program they were taken from. [Limits](limits.md).
