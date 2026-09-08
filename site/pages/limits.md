---
title: Limits
tagline: What Ouroboros cannot see, cannot do, and changes about your program — with the measurements behind each claim.
---

# Limits

This is the page that matters. A tool whose edges are not drawn is worse than no
tool: people use it where it lies to them.

Everything below is either a measured run on this machine or a link to the run
that produced it. Where something is believed but not measured, it says so.

## The limit everything else follows from

> A trace records **how the code behaved**, not how it was supposed to behave.

A bug that has shipped for three years looks exactly like correct behaviour.
Nothing in a record marks a call as wrong; it says the function was called with
these values and answered with that one.

The consequence is worth spelling out, because it bites hardest exactly where
traces are most useful: **conclusions grown from a trace inherit the program's
mistakes as law.** If the code rounds the wrong way, the trace shows the wrong
rounding as the norm — and if you then write a test from the trace, you have
pinned the bug in place.

## What a trace cannot see, by construction

**Anything that did not run.** A branch nobody entered leaves no records — **and
does not complain**. This is the most under-rated limit on the page, because
absence reads like "all quiet here" when it means "nobody went here". A trace
describes one run, never the program.

**Intent.** A function returned `-1`. Is that an error code or a real answer?
Not in the trace.

**Calls through a function value in Go.** A `func(x int) int { ... }` stored in a
variable or passed as an argument is **not wrapped**, and its calls never appear
at all. The decision matches skipping `lambda` in Python, but the effect on a
reader is different: Python's nested `def` *is* wrapped and does show up
(`outer.<locals>.inner`), while the same-shaped Go code is silent. From one
trace you cannot tell "never called" from "called, not recorded".

**Why one call led to another.** Entry time, call id, thread and duration are all
there, so *when* and *where* can be reconstructed. That a call happened *because*
of an earlier one is recorded by nothing.

**The true cost of a call.** The `d` field measures the **instrumented** run,
which is slower than the real one. The clock starts after the entry line is
written, so that one write is excluded — everything else the wrapping added is
not. A trace is not a profiler.

**Anything past 200 characters of a value.** Long values are shortened to a short
representation (Python's `reprlib` with `maxstring=maxother=200`); lists, dicts
and sets are also cut to their first ten items. The tail is not recoverable.

**Argument names — in all eight languages.** The `a` field carries values only:
`2, 3`. C, C++ and Elixir do parse the signature at wrap time and used to write
`a=2, b=3`, which made one field mean two different things and made cross-
language comparison impossible. They no longer do. The names are in your source
next to the function; a tool reading only the trace cannot get them back.

**Which CPU core — in all eight languages.** The `ci` field is `-1` everywhere,
meaning *unknown*, and the reader turns that into `null`. There is no portable
way to ask in these runtimes, and inventing a plausible number was refused: an
earlier version put the BEAM scheduler id there, which looked like a core number
without being one. A real core number exists in exactly one place: a C build
inside an operating-system kernel.

## What the tool simply cannot do

Not "not yet" — there is no such command.

| what people expect | what is actually there |
|---|---|
| unwrap it again | there is no reverse command. Revert it from version control, or by hand |
| replay a recorded call | the arguments are written down; feeding them back in is your job |
| diff two runs | there is `trace`, and after that your ordinary diff tools |
| tell you coverage | records describe what ran; about what did not, they are silent |
| sample instead of recording everything | two lines per wrapped call, always. There is no "one line a minute" mode |

Sampling is the one people ask for most, so it is worth being blunt: there is no
sampled trace here. Two lines per wrapped call, and that is all.

## Where wrapping changes how your program behaves

**This is the tool's main unresolved weakness, and it cannot be talked around.**
Wrapping is not observation from outside — it is an edit to your source. Usually
a harmless one: on the examples on this site the program returns the same values
and throws the same exceptions. Not always, though, and the cases below were
each reproduced by a run.

### Four that stay this way, on purpose

**An uncaught panic in Go prints something different.** This is the only place in
all eight languages where wrapping changes a program's **output**. To record the
type and text of a panic, the deferred closure calls `recover()` and then panics
again — otherwise the `x` field would have neither. So a program that panics and
is caught by nobody prints

```
panic: bad [recovered, repanicked]
```

instead of `panic: bad`, with one extra frame in the stack. Measured: the exit
code is unchanged (2), ordinary output is unchanged, and every panic the program
catches itself arrives as the same value. Removing this would mean giving up the
`x` field: Go offers no way to see a panic's value without catching it.

**C++ does not record a returned object of class type** — `r` is `(no value)`.
Routing such a return through a capture helper would defeat the copy elision
C++17 *guarantees*: a program that counts its own constructors would start
printing a move that never happened, and a type with copy and move both deleted
would stop compiling. Observability lost to transparency deliberately — a tool
that changes what it measures is worthless. Arguments, duration and whether the
call threw are all still recorded; numbers, pointers and references are recorded
in full.

**Rendering a value calls your own code.** To write an argument down you have to
ask it how it looks — `__repr__` in Python, `toJSON` in JavaScript, `ToString` in
Java and C#, `%v` in Go. If that method counts its calls or changes something, the
wrapped program behaves differently from the unwrapped one. Measured across three
languages at once, on a program that reports whether its own repr was called:

```
plain     : repr called by the program itself: 0
wrapped   : repr called by the program itself: >0
```

There is no fix: writing a value down without asking the value is not possible.
The one thing that *is* handled is that a throw inside such a method does not
take the program down — it is caught, and the record says
`<Type toString threw ...>`.

**C and C++ print `const char *` as an address, not as text.** This is the single
most useful thing a C trace lost, and it was given up on purpose. Printing that
pointer as a string assumes there is NUL-terminated text behind it, and the type
promises nothing of the sort: `put_one(const char *p)` called as `put_one(&c)`
for one character is ordinary, correct C, and the wrapped copy read past `c` to
the first zero anywhere in memory. Proven with AddressSanitizer: the unwrapped
program is clean, the wrapped one reports
`stack-buffer-overflow ... READ of size 2`. Instrumentation was introducing
undefined behaviour into a program that had none — the one thing this tool
promises not to do — and it broke anyone running their own tests under a
sanitizer. Bounding the read does not help; no C type means "definitely a
string". Bringing it back as an opt-in flag is written up in
[FEATURE_REQUESTS.md]({{repo}}/blob/main/FEATURE_REQUESTS.md).

### One place where the output does not compile

**A C# `ref struct` declared in another file of the same project is invisible.**
The C# backend reads the file it was given and does not resolve names across
files. Platform ref structs (`Span`, `ReadOnlySpan` and friends) are known by
name, and one declared in the file being wrapped is read from its declaration —
but one declared *elsewhere* goes unnoticed, its member gets wrapped, and the
project stops compiling. This is the only place in the whole tool where wrapping
can emit code that does not build.

### What was broken here before, and is now held down by tests

Not a boast — a map of where this class of bug lives, so you know where to look
if you hit a new one. Every one of these was silent: the tool answered
`"ok": true` and the program was subtly different.

- JavaScript lost `"use strict"`, because the helper import went in above the
  directive and a directive that is not first stops applying.
- C++ would not compile a braced return, `return {1, 2, 3};`.
- Python broke on `from __future__ import annotations`, which must be first.
- Python turned the module docstring into an ordinary expression, so `__doc__`
  became `None`.
- Python broke `#!` lines and PEP 263 encoding declarations.
- C, C++ and JavaScript in a subdirectory produced a file that could not build,
  because the helper was written to the project root and the instrumented source
  looks for it beside itself.

They share one cause: **the beginning of a file is not neutral ground.** No
exhaustive search for the rest has been done, and claiming the list is complete
would be false.

> **So run your own tests after wrapping, not only before.** Green on the
> instrumented copy means the edit was harmless *for your program* — which is
> the only version of that question that can actually be answered.

## What wrapping always changes

- **Time.** See `d` above, and the table below.
- **Stack traces.** Helper frames appear between yours. Visible in the crash on
  the [examples page](example.html#a-run-that-crashes).
- **Bytes on disk.** Two lines per call. Measured below.
- **The contents of your file.** It stays instrumented until you revert it. There
  is no undo, and the sandbox's `finish` is not one.

## What instrumenting costs you

Measured on this machine with the project's own
[`scripts/measure/run.sh`]({{repo}}/blob/main/scripts/measure/run.sh): the same
program — `add(a, b)` called twenty thousand times, plus a call that throws —
built and run seven times with the recording and seven times without. The table
is the median of those runs.

{{table: speed}}

**Read the last column, not a ratio.** "How many times slower" comes out between
6 and 300 here and means nothing: the program under test does nothing except
call a function, so the ratio mostly reports how fast an empty loop is in that
language. The honest number is the added microseconds per call, and in a real
program — where the function does actual work — the share of that overhead
shrinks by however much more expensive the real work is.

**Most of the cost is the writing, not the language.** Six of the eight helpers
open the file, append and close it, twice per call. Java's and C#'s keep it open,
which is why C# comes out cheapest of all — not because C# is fast.

**Elixir is the outlier** at about
{{measured: Elixir.added_us_per_call}} microseconds, and it is also the least
stable measurement here: repeats spread by about a quarter. Trust the order of
magnitude, not the third digit.

{{table: volume}}

Two lines per call, {{measured: JavaScript.bytes_per_call}} to
{{measured: C++.bytes_per_call}} bytes per call with two short arguments.
**A million calls is roughly a quarter of a gigabyte.** Long arguments push a
record up to the 4096-byte ceiling, past which values start being shortened.

### Recursion in Python gets half as deep

The Python mechanism is a decorator, so the wrapper takes a stack frame of its
own between caller and callee. Measured again for this page, with a function
that counts how deep it got:

```
limit 200,  deepest reached 199     plain
limit 200,  deepest reached 95      wrapped
limit 1000, deepest reached 999     plain
limit 1000, deepest reached 495     wrapped
```

**About half as deep.** A program with a tightly fitted recursion limit can hit
it after wrapping where it did not before. While the mechanism is a decorator
this is not removable — so for deep recursion, wrap the callers with
`wrap-functions` rather than the recursive function itself.

## The machine these numbers came from

Timings are machine numbers. Yours will differ; the relationships between the
languages should not.

{{table: machine}}

Six of the eight helpers write by opening and closing a file per record, so these
measurements are bounded by the filesystem as much as by the language. On a
machine with a slow disk every time above grows, and the ordering stays.

## Support is not equally deep in all eight languages

All eight write the same record. What differs is how much of your code they can
reach, and what each one costs.

| language | where it is thin |
|---|---|
| **Python** | The most exercised backend. `lambda` is skipped. Recursion gets half as deep (above). |
| **JavaScript / TypeScript** | Arrow functions with a short body (`x => x + 1`) are skipped. An `async` function records the promise it returned, not what the promise resolves to. |
| **C** | No exceptions in the language, so a C trace never has an `x` field — only `r`. Pointers, `const char *` included, are recorded as addresses. Some return kinds record `(no value)`. |
| **C++** | Class-type return values are `(no value)`, as is a braced return. The receiver (`this`) is not among the arguments. |
| **Elixir** | `fn` is recorded without its module name. **Build order matters**: the recording module must compile before anything that uses it. Slowest of the eight by a factor of six. |
| **Go** | Function values are not wrapped at all, silently. The method receiver is not among the arguments. An uncaught panic prints differently. `go vet` may report one extra `copylocks` finding when a value with a lock inside passes through. `go` is needed to *wrap*, not just to build. |
| **Java** | Abstract, native and interface declarations are skipped — there is nothing to instrument. Lambdas and anonymous classes are not wrapped themselves. Parsing costs a JVM start each time: the project measures a median of 322.8 ms for one 202-line file. |
| **C#** | Five member kinds are skipped because the wrapped form would not compile — `yield`, `ref` returns, pointers, `ref struct`, expression-bodied properties — and each skip is reported with its reason. An `out` argument is not in the entry snapshot. A `ref struct` from another file is the one case that can break the build. |

**Rust is not supported.** There is a reconnaissance write-up in
[FEATURE_REQUESTS.md]({{repo}}/blob/main/FEATURE_REQUESTS.md) with working code
outside the tree — and a plan is not a feature.

**flang is not supported.** No extension maps to a backend; the tool does not
mention it in its source, its list of languages, or its spec.

**The kernel build is compiled, not run.** The C header can build inside an
operating-system kernel — a NetBSD 11.0_RC4 riscv64 module compiles clean against
real kernel headers with `-Werror`. Running it there did not happen: the rump
kernel's module loader has no relocations for riscv64. **Stack safety, re-entry
and volume inside a kernel are therefore unverified**, and are the one part of
this project that should be treated as untested rather than tested.

## What comes back out of the sandbox

`finish` copies the draft to the clean tree and leaves behind what a machine can
make again: `.git`, `debug.info`, tool caches, built binaries, object files,
core dumps.

Two consequences:

- **The instrumentation comes out with your code.** The clean copy holds the
  wrapped file, and the answer says so in as many words:
  `instrumentation_removed: false`. The helper comes too, and must — without it
  the wrapped code does not run.
- **A file that looks machine-made is left behind, and that includes yours.**
  There is no way to tell a compiler's output from a file your program was *asked*
  to produce; both are made by a machine, both come back if you run it again. So
  **an image your own program drew is dropped along with the object files.** The
  rule was measured against 246 source files in this repository with zero wrong
  refusals — but that is about source files, not about data.

The defence is that `finish` does not go quiet: every dropped file is named in
`skipped`, with a reason. If you do not read that list, you find out later and
the hard way.

## Where it has been measured not to pay off

On the project's own benchmark
([bench/RESULTS.md]({{repo}}/blob/main/bench/RESULTS.md)) an agent was given a
task where the bug is fully visible in the program's final output, and given this
tool. It did not use it **in any of three runs** — and that was the right call:
when the expected output is right there, comparing output is cheaper than
wrapping and reading a trace.

So the honest boundary is: **traces pay off where the bug is not visible in the
final output or the stack trace.** Before spending anything, ask whether your bug
localises without them. If it does, do not spend.

The opposite case — big systems with opaque intermediate state, where a wrong
value is born mid-chain and never reaches the output — is what the tool was built
for, and **the benchmark did not measure that.** There is no ground yet for
claiming it wins there.

A separate experiment did measure whether a trace helps a language model answer
questions about a program it has not seen: sixty questions, twelve programs, six
languages, asked with and without the trace. Small models gained a lot (+34, +24,
+24 percentage points). A strong agent did not: 95.0% without, 98.3% with, and
zero inside the confidence interval — by the threshold declared before the first
run, **no gain was shown.** The numbers and the method are in
[docs/measurements.md]({{repo}}/blob/main/docs/measurements.md).

## When not to use this at all

- You need to know what the code **should** do. Ask a person or a specification.
- You need to prove the code is correct. Records cannot do that, by construction.
- The bug is visible in the output or the stack trace. Look there; it is cheaper.
- The program is non-deterministic in time and ordering and you want
  repeatability. You will get records. You will not get the same ones twice.
- A hot path where every microsecond counts and everything has to be wrapped.
  Two records a call is not free.

## Two things worth carrying away

**Records taken from a program with a bug make the bug the norm.** A discount
counter with `>` where the rule says `>=` produces records whose boundary case is
simply wrong, and nothing in them will say so. Grounding yourself in facts does
not by itself give you truth — the rule has to be written by someone who knows
the domain, not by someone fitting it to observations.

**Your workload is your sample.** What you see is what happened on the run you
made. A branch nobody entered gives zero records and zero warnings. More records
from the wrong workload does not help.
