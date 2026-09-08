---
title: Limits
---

**English** · [Русский](limits.ru.md)

# Limits

This page matters more than the others. A tool whose limits are not named is
more dangerous than no tool at all: people start using it where it lies.

## The main limitation

> Records capture **how the code behaved**, not how it ought to behave.

A bug that has worked for years looks in the records exactly like correct
behaviour. Nothing in a record marks a call as wrong — it just says that the
function was called with these arguments and this came out.

Hence a consequence worth saying out loud: **conclusions grown from records
inherit the program's bugs as law.** If the program rounded the wrong way, that
becomes an assertion of "this is how it should be", and from then on that is
what gets tested.

## What records cannot see by design

**Whatever did not run.** A branch you never entered gives no records — **and
does not complain**. Records describe a run, not a program. This is the most
underrated of the limits: absence of records reads as "all quiet here", when it
means "nobody has been here".

**Intent.** A function returned `-1` — is that an error code or a real answer?
The record does not say.

**Calls through a function value in Go.** A `func(x int) int { ... }` written
into a variable or passed as an argument **is not instrumented**, and its calls
never appear in the records at all. The decision is the same one as skipping
Python's `lambda`, but the difference for the reader is real: in Python a nested
`def` is instrumented and shows up in the records (`outer.<locals>.inner`),
while in Go code that means the same thing stays silent. Only declared functions
and methods are instrumented. From a single record you cannot tell "never
called" from "called but not recorded" — and that is exactly the substitution
the first point above is about.

**Meaning in the order of calls.** What is written down is the entry time (`t`),
the call number (`id`), the thread (`th`) and the duration (`d`) — so *when* and
*where* a call happened can be reconstructed. But that it came *after* another
one and **therefore** gave this answer is recorded by nothing.

**The real cost of a call.** The `d` field measures the **instrumented** run,
which takes longer than the normal one. The `d` clock starts **after** the entry
line is written, so the cost of writing that line is not in `d` — but everything
else instrumentation added is. Records do not replace a profiler.

**Values longer than 200 characters.** A long value is cut down to a short
rendering of the value (in Python — `reprlib` with `maxstring=maxother=200`,
[`ouroboros/runtime.py:53`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L53)).
The tail is not recoverable. Lists, dicts and sets are cut by element count as
well — the first ten.

**Positional argument names — in none of the eight languages.** The `a` field
holds values only, everywhere: `2, 3`. In C, C++ and Elixir the signature is
parsed during instrumentation and the names are known, and these three used to
write the string `a=2, b=3` into `a` — but then the field meant one thing in
three languages and another in two, and you could not line up a record from one
language against a record from another. Now there are no names anywhere, and
from a record alone they cannot be recovered. The full account is on the
[Languages](languages.md#where-argument-names-come-from) page.

**Core numbers — in none of the eight languages.** In a normal program the `ci`
field is always `-1`, in the parsed form `null`. In Python because CPython has
no `os.sched_getcpu` function at all; in the rest because their environments
offer no portable way to find out the core. A real number happens only in a C
build inside the operating system kernel. The `th` thread label is there
everywhere.

## What the tool cannot do

Not "cannot yet" — cannot: there is no such command.

| what you want | how it actually is |
|---|---|
| take instrumentation back off | there is no reverse command; you revert it from version control or by hand |
| replay a recorded call | the arguments are recorded, but you feed them back in yourself |
| compare two runs | there is `trace`, after that the usual file-diff tools |
| find out coverage | records speak about what ran; about what did not run they say nothing |
| thin the recording out | two lines per instrumented call, and that is all |

Thinning is worth a separate word, because people often expect it: a sampled
recording — "one line with the call depth once a minute" — is not here. Two
lines per instrumented call, and that is all.

## What travels to the clean copy

Verified by a run. `finish` copies the draft into the clean copy, leaving behind
whatever a machine makes again: `.git`, `debug.info`, tool caches, built
binaries, object files and core dumps after a crash
([`ouroboros/sandbox/sync.py:29-82`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/sandbox/sync.py#L29-L82)).

So:

- **instrumentation travels out** — the clean copy holds the same instrumented
  file. The `finish` response says so outright, with the field
  `instrumentation_removed: false`;
- the helper (`ouroboros_runtime.py` and its siblings) travels too, and rightly
  so: without it the instrumented code will not run;
- **what did not travel is named** in the `skipped` field, with a reason for
  each file.

### The weak spot in the rule you need to know about

There is no telling a compiler's output from a file the program **was asked** to
make: both are machine-produced, and both come back if you run it again.
`finish` has no sign that would separate them.

So the rule discards by the look of the content — and **a picture drawn by your
own program will be discarded along with the built binary.** A measurement over
the 246 source files of this repository gives zero false rejections, but that is
about source files, not about data.

Hence the only protection: `finish` does not stay silent. Every discarded file
is named in `skipped` with a reason, and taking back what you need by hand is
your job. If you do not look at that list, you will find out about the loss
later and the hard way.

Splitting the draft from the clean copy **does not protect you from
instrumentation getting out.** Look with your own eyes at what travels.

## Where instrumentation changes behaviour

**This is the tool's main open weakness, and it cannot be passed over in
silence.**

Instrumentation is not observation from the outside, it is **an edit to the
source**. Usually the edit is harmless: on the examples in this documentation
the program returns the same and throws the same. But it is not always harmless,
and the cases below were verified by runs here.

### Verified and fixed

Every case that used to be listed here as broken is fixed and covered by tests.
Verified by a run on this tree.

**JavaScript lost strict mode.** The helper's import line was inserted **above**
the `"use strict"` directive, and a directive that stops being first stops
taking effect: the program kept running and ran differently, with no error and
no warning. Now, on the same program:

```
before instrumentation: THREW: ReferenceError
after instrumentation:  THREW: ReferenceError
```

**C++ would not compile on a braced-list return.** `return {1, 2, 3};` turned
into `return _ouro::capture(__ouro, ({1, 2, 3}));`, which the compiler does not
accept. Now such a return compiles and runs.

**Python and `from __future__`.** The import line was inserted above
`from __future__ import annotations`, and the file stopped parsing at all
(`SyntaxError: from __future__ imports must occur at the beginning of the file`).
And `wrap-file` answered `"ok": true`.

**Python and the module docstring.** Any line put before the module docstring
turns it into an ordinary string expression, and `__doc__` becomes `None`.
Silently.

**Python, `#!` and the encoding line.** The kernel reads `#!` only from byte
zero, and the PEP 263 encoding declaration works only in the first two lines; an
import inserted above them made an executable script unrunnable, and the
declared encoding inert.

**C, C++ and JavaScript in a nested folder.** The helper was put at the root of
the draft, while the instrumented source looks for it **next to itself**:
`#include "ouroboros_runtime.h"` and `import ... from "./ouroboros_runtime.js"`
resolve the path from the file they are written in. Instrumenting `src/main.c`
produced a file that does not compile — with the answer `"ok": true`. Now the
helper is put next to the file. Verified in four languages, flat and nested.

### Verified: this stays, and it is a choice

The four things below are not breakage, they are a deliberate price. There is no
plan to fix them, and here is why.

**An uncaught panic in Go prints something different.** This is the only place
across all eight languages where instrumentation changes the program's
**output**, so it stands here first. To record the kind and the text of a panic,
the closure calls `recover()` and then panics again — otherwise the `x` field
would be left without a kind and without a text, and there would be nothing to
read such a record by. Because of the re-panic, a program that panics and **is
caught by nobody** prints to the error stream

```
panic: bad [recovered, repanicked]
```

instead of `panic: bad`, and a closure frame is added to the call trace.
Verified by a run: **the exit code is the same (2), the normal output is the
same, and every panic caught by the program itself or by its caller arrives with
the same value** — so what changes is exactly the text of the message about an
unhandled crash. It would have been different without instrumentation anyway:
the call trace includes line numbers, and instrumentation shifts them. Removing
this without losing the `x` field is impossible: Go gives you no way to learn a
panic's value without intercepting it.

**C++ does not record a returned object of class type** — the `r` field holds
`(no value)`. To record it, the return would have to be routed through the
helper, and that cancels the copy elision C++17 **guarantees**: a program
counting its own constructors starts printing a move that was not there without
instrumentation, and a type with deleted copy and move simply stops compiling.
Observability lost to transparency on purpose: a tool that changes the behaviour
of what it measures is useless. Arguments, duration and the fact of an exception
are recorded as usual; numbers, pointers and references are recorded in full.

**Argument names are in no language's records.** In detail — in
[Languages](languages.md#where-argument-names-come-from). Briefly: a single
schema for eight languages is possible only if `a` means the same thing
everywhere.

**Rendering a value calls the program's own code.** To record an argument you
have to ask it how it looks — and that is a call into code you wrote:
`__repr__` in Python, `toJSON` in JavaScript, `ToString` in Java and C#. If such
a method counts its calls or changes something, the instrumented program behaves
differently from the uninstrumented one. Verified by a run in three languages at
once — the program prints whether its rendering was called:

```
as is       : repr calls in the program itself: 0
instrumented: repr calls in the program itself: >0
```

There is nothing to fix this with: recording a value without asking the value is
impossible. The one thing done here is that **a throw inside such a method does
not bring the program down**: it is caught, and `<Type toString threw ...>` goes
into the record. That is also why such a program is not part of the equivalence
set: it breaks the main promise by design, not by oversight.

### The price of safety: strings in C and C++

**A `const char *` is printed as an address, not as content.** This is the most
noticeable thing C records lost, and it was done on purpose.

Printing such a pointer as a string means assuming that behind it lies text with
a zero at the end. The type promises no such thing. A `put_one(const char *p)`
function called as `put_one(&c)` for a single character is ordinary, correct C;
and the instrumented copy read past `c` up to the first zero, which turns up
somewhere further in memory.

Verified with a bounds checker: the uninstrumented program is clean, the
instrumented one gives `stack-buffer-overflow ... READ of size 2` — in C inside
`vsnprintf`, in C++ inside `strlen`. That is, instrumentation was introducing
undefined behaviour into someone else's program — exactly what the tool promises
not to do. Separately bad: it broke the people who run their own tests under a
bounds checker — it would point at the instrumentation instead of at their bug.

Printing the content safely is impossible: C has no type that means "there is
definitely a string here", and a length limit does not save you — the read still
goes past the edge of a short object. So the content is not printed at all. How
to bring it back without losing safety is written down in `FEATURE_REQUESTS.md`.

**Python instrumentation adds a stack frame on every call.** A wrapper stands
between the caller and the callee, so recursion that fitted before
instrumentation may not fit after. Measured on a self-calling function:

| recursion limit | depth without instrumentation | depth with instrumentation |
|---|---|---|
| 200 | 199 | 95 |
| 1000 | 999 | 495 |

That is about twice as shallow — the wrapper takes a second frame on every call.
As long as the mechanism is a decorator, this cannot be removed. For deep
recursion, point `wrap_functions` not at the recursive function itself but at
the ones that call it. How to reproduce it —
[Measurements](measurements.md#recursion-depth-in-python).

### What follows from this

All four have one cause in common: **the top of a file is not a neutral place.**
In Python, JavaScript and other languages the first lines carry a special
meaning, and inserting something before them changes the language the rest of
the file is written in. Where the tool accounts for that, all is well; where it
does not account for it yet — see above.

The practical conclusion, worth following always:

> **Run your tests after instrumentation, not only before it.** If they are
> green on the instrumented code too, the edit turned out harmless for your
> program. If not, you learned it from your own tests and not from a user.

No exhaustive search for what else can break was done here, and there is no
claiming that the list above is complete.

### What else Go pays

The two things that cost Go more than the other languages stand higher up: an
uncaught panic prints something different —
["this stays, and it is a choice"](#verified-this-stays-and-it-is-a-choice) —
and function values are not instrumented at all —
["what records cannot see by design"](#what-records-cannot-see-by-design).
Here is the smaller stuff; all of it verified by runs, and none of it changes
either the exit code or the normal output.

**The method receiver is not among the arguments.** Only declared arguments go
into the `a` field: for `func (c *Calc) Bump(by int)` it records `by`, but not
`c`. C++ behaves the same way with `this`; Python, the other way round, writes
`self` as the first argument.

**`go vet` may find one complaint more.** If a struct with a lock inside
(`sync.Mutex`) passes through an instrumented function by value, the helper gets
a copy of it, and `copylocks` notes that. It affects neither `go build` nor
`go test`: `copylocks` is not in the set of checks `go test` runs by itself —
verified by a run on a module with such a function, `go test` passes both before
instrumentation and after.

Worth knowing separately, **how Go renders a value**: through `%v`, and that
calls the value's own `String()`/`Error()` and dereferences a pointer to a
struct. This is Go's native printing rule and of the same kind as Python's
`__repr__` call, but there is a consequence: the helper reads what the
instrumented function may only have stored, so under `go test -race` a data race
can surface that was not there in the uninstrumented program.

### What instrumentation always changes

- **Time.** See above about `d`.
- **Stack traces.** Helper frames (`wrapper`) appear in them, between yours.
  Visible right in the output in the
  [README](https://github.com/digitable-lol/ouroboros#3-run-it-the-way-you-always-did).
- **Bytes written to disk.** Two lines per call. Measured on one and the same
  `add(a, b)` function, 20 002 calls: from 238 bytes per call in JavaScript to
  270 in C++ ([Measurements](measurements.md#record-volume)). The short record
  form for C (`--minimal`) gives 38 bytes per call, but writes only the entry
  line.
- **The contents of the file.** An instrumented file stays instrumented until
  you revert it: there is no reverse command, and `finish` does not do one.

## The measured limit of usefulness

Not everything that can be recorded is worth recording. On the project's bench
([bench/RESULTS.md](https://github.com/digitable-lol/ouroboros/blob/main/bench/RESULTS.md))
a task was taken where the bug is fully visible in the final output, and the
agent was given the tool. It did not reach for it **in any of the three runs** —
and that was the right choice: when the expected output is lying right there,
comparing output is cheaper than instrumenting and reading records.

Hence an honest boundary: **call records pay off where the bug is not visible in
the final output or in the stack trace.** Before you spend, ask yourself whether
your bug can be pinned down without them. If it can — do not spend.

The opposite is what the tool was conceived for: large systems with opaque
intermediate state, where a wrong value is born in the middle of a chain and
never reaches the final output. The bench **did not measure** that, and there is
so far no ground for saying it wins there.

## When the tool does not fit at all

- You need to know what the code **should** do. Ask a person or a specification.
- You need to prove the code is correct. Records cannot do that by design.
- The bug is visible in the output or in the stack trace. Looking there is
  cheaper.
- The program is non-deterministic in timing and ordering, and you want
  repeatability. You will get the records; you will not be able to repeat them.
- A hot path where every microsecond counts and everything has to be
  instrumented. Two records per call are not free.

## What to take away

Two thoughts this page was written for.

**Records taken from a program with a bug make the bug the norm.** A discount
counter with `>` where the rule calls for `>=` gives records whose boundary case
is simply wrong. Nothing in the records will say so: they honestly show what the
code did. Leaning on facts does not by itself give you truth — the rule has to
be written by a person who knows the domain, not by the one who fitted it to the
observations.

**The workload is the sample.** Everything you see in the records is what
happened on the stream of calls you ran. A branch you did not enter gives zero
records and zero warnings. More records from the wrong stream do not help.
