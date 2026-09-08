---
title: Languages
---

**English** · [Русский](languages.ru.md)

# Languages

Eight languages. The record schema is one and the same for all of them; how the
record gets inserted and the small details differ. Everything in the tables
below is taken from real runs on this machine, not derived from the docs.

| language | extensions | how the record is inserted | what the machine needs |
|---|---|---|---|
| Python | `.py` | an `@_ouro_log` decorator on the function | nothing beyond Python |
| JavaScript / TypeScript | `.js .mjs .cjs .jsx .ts .tsx` | `try/finally` in the body | `node` |
| C | `.c .h` | `__attribute__((cleanup))` | `gcc`/`clang` |
| C++ | `.cpp .cc .cxx .hpp .hh .hxx` | a scope guard (RAII) | `g++`/`clang++` |
| Elixir | `.ex .exs` | `use Ouroboros.Trace`, `def` redefined | `elixir` |
| Go | `.go` | named returns and `defer` | `go` (needed for instrumenting too) |
| Java | `.java` | `try/catch/finally` in the body | JDK (needed for instrumenting too) |
| C# | `.cs` | `try/catch/finally` in the body | .NET SDK (needed for instrumenting too) |

`libclang` for parsing C and C++ and `@babel/parser` for parsing JavaScript are
packed inside the package — you do not have to install them separately. Parsing
Go needs nothing external at all: `go/parser` comes with the language itself.

Parsing of C and C++ runs in a separate, non-Python program: it reads the
source, finds the boundaries of bodies and returns, and prints them as JSON,
while Python only cuts along those numbers. The same split was already there for
JavaScript (`node`) and Elixir, and Go works exactly the same way:
`ouroboros/languages/_go/emitter.go` prints the boundaries of bodies and
signatures, and Python cuts the bytes. The parser for C and C++ is written in C,
the one for Go in Go, and both are built once on the machine the first time you
instrument, which is why C, C++ and Go need their build tool already at
instrumentation time, not only at build time. For the other languages the
compiler and `node` are still needed only to **build and run** the instrumented
code.

The list the tool prints itself:

```sh
ouroboros languages
```

```json
{"languages": ["python", "javascript", "c", "cpp", "elixir", "go", "java", "csharp"]}
```

## The same call in all eight

`add(2, 3) → 5`, captured separately on this machine. This is the proof that the
schema really is one: the keys, the order and the meaning of the fields match,
and only what should differ differs.

**Python**

```jsonl
{"p":"in","t":"2026-08-29T08:58:33.966","id":"99ef2d63-a1d9-40af-a086-5c558e778213","ci":-1,"th":"1989681.137722432983552","fn":"add","a":"2, 3","k":""}
{"p":"out","id":"99ef2d63-a1d9-40af-a086-5c558e778213","fn":"add","r":"5","d":2e-06}
```

**JavaScript**

```jsonl
{"p":"in","t":"2026-08-29T08:58:34.641","id":"c272c9da-a6ee-4d88-8cfb-ed0968834ba5","ci":-1,"th":"1990605.0","fn":"add","a":"2, 3","k":""}
{"p":"out","id":"c272c9da-a6ee-4d88-8cfb-ed0968834ba5","fn":"add","r":"5","d":0.000053}
```

**C**

```jsonl
{"p":"in","t":"2026-08-29T08:58:35.376","id":"f7872c35-a195-4711-b9df-2e5866b48d46","ci":-1,"th":"1990883.1990883","fn":"add","a":"2, 3","k":""}
{"p":"out","id":"f7872c35-a195-4711-b9df-2e5866b48d46","fn":"add","r":"5","d":0.000000}
```

**C++** (a method of class `Calc`)

```jsonl
{"p":"in","t":"2026-08-29T08:58:36.789","id":"3b25a49a-b150-4936-8a5e-3ab62a0f017c","ci":-1,"th":"1991611.138599134574464","fn":"Calc::add","a":"2, 3","k":""}
{"p":"out","id":"3b25a49a-b150-4936-8a5e-3ab62a0f017c","fn":"Calc::add","r":"5","d":0.000000}
```

**Elixir** (a function of module `Calc`)

```jsonl
{"p":"in","t":"2026-08-29T08:58:40.106","id":"5dc72a4c-49b4-4170-851f-468a7e7f24c3","ci":-1,"th":"1993355.#PID<0.95.0>","fn":"add","a":"2, 3","k":""}
{"p":"out","id":"5dc72a4c-49b4-4170-851f-468a7e7f24c3","fn":"add","r":"5","d":0.000011}
```

**Go** (a method on type `Calc`)

```jsonl
{"p":"in","t":"2026-08-29T23:51:58.977","id":"db3d3f3a-7adc-427a-b8e6-6bb3afca159e","ci":-1,"th":"2396561.1","fn":"Calc.add","a":"2, 3","k":""}
{"p":"out","id":"db3d3f3a-7adc-427a-b8e6-6bb3afca159e","fn":"Calc.add","r":"5","d":0.000001}
```

**Java** (a method of class `Prog`)

```jsonl
{"p":"in","t":"2026-08-30T15:46:18.232","id":"41d5bdd2-cbb5-442f-b92f-553e2f553fdd","ci":-1,"th":"2695782.3","fn":"Prog.add","a":"2, 3","k":""}
{"p":"out","id":"41d5bdd2-cbb5-442f-b92f-553e2f553fdd","fn":"Prog.add","r":"5","d":0.000006}
```

**C#** (a method of class `Prog`)

```jsonl
{"p":"in","t":"2026-08-30T15:46:44.287","id":"52fc4c08-16c7-4344-b33c-27718921bc05","ci":-1,"th":"2696617.1","fn":"Prog.add","a":"2, 3","k":""}
{"p":"out","id":"52fc4c08-16c7-4344-b33c-27718921bc05","fn":"Prog.add","r":"5","d":0.000243}
```

What you can see straight away, without reading the source:

- **`a` is the same in all eight** — `"2, 3"`, values only, no names. It was not
  always so: C, C++ and Elixir used to write `"a=2, b=3"` here, and the field
  meant one thing in three languages and another in two. Worked out
  [below](#where-argument-names-come-from).
- **`ci` is `-1` everywhere** — "unknown". A real core number happens only in
  the C build inside an operating-system kernel.
- **`th` is two parts everywhere** — process and thread — but the second part
  belongs to each language: Python has the thread number, C the native thread
  number, Elixir the BEAM process number (`#PID<0.95.0>`), JavaScript the worker
  thread number (`0` for the main one), Go the goroutine number, Java the JVM
  thread number, C# the managed thread number.
- **`fn` diverges, and that is right.** C++ writes `Calc::add` — the full name
  with the class. Go writes `Calc.add` — with the type as well, but through a
  dot, the way the Go runtime itself names the method. Elixir writes plain
  `add`, without the module name, even though the function lives in module
  `Calc` too. Each language names a function the way that language does.
- **`d` is printed differently** for close values: Python `2e-06`, JavaScript
  `0.000053`. That is a difference in how numbers are printed, not in units. In
  C and C++ the duration is rounded to a microsecond, so here it is `0.000000`.
- **`r` is `"5"` everywhere** — a string, by design: the record holds a
  rendering of the value, not the value itself.

## One schema, different dialects

The dialects are **a decision, not an unfinished job**.

All eight write the same schema into one `debug.info` file: the same keys, the
same two lines per call, the same meaning for every field. Your trace parsing is
one for all languages.

The **dialects**, on the other hand, are deliberately left native. From [SPEC.md](https://github.com/digitable-lol/ouroboros/blob/main/SPEC.md):

> Dialects are deliberately not reduced to one: how a language renders a value,
> how it writes a full name, how it prints a number — each keeps its native
> form. **Pin the schema, not the dialect.**

The check `tests/test_cross_language.py` parses traces from two languages and
compares the records after stripping the dialect.

### How the full name is written

Taken from runs:

| language | what is in field `fn` |
|---|---|
| Python, method | `Box.put` |
| Python, nested function | `outer.<locals>.inner` |
| C++, method | `Calc::add` |
| Go, method on a value | `Calc.add` |
| Go, method on a pointer | `(*Calc).Bump` |
| C | `add` |
| JavaScript | `mul` |
| Elixir | `add` — **without the module name** |
| Java, method | `Calc.add` |
| Java, constructor | `Calc.Calc` |
| Java, method of an anonymous class | `Calc.$anon.run` |
| C#, method | `Calc.Add` |
| C#, property getter | `Counter.Value.get` |
| C#, indexer getter (`this[]`) | `Counter.this[].get` |
| C#, custom addition (`operator +`) | `Vec.operator+` |

### How a value is rendered

Python writes a string as `'world'`, JavaScript as `"world"`. The same duration
looks like `1e-06` in Python and `0.000001` in JavaScript. You cannot compare
records from different languages head-on: strip the dialect first.

## Where argument names come from

Short answer: **from nowhere. Not one of the eight languages puts argument names
in the record.**

The table below is **printed by a run**, not written by hand: the same call
`add(2, 3)` is built and run in each of the eight languages, and whatever ended
up in the record ends up here. To rebuild it:
`uv run python scripts/schema_facts.py --measure`.

<!--schema-facts-->
| language | field `a` (positional) | field `k` (keyword) |
|---|---|---|
| Python | `2, 3` | empty |
| JavaScript | `2, 3` | empty |
| C | `2, 3` | empty |
| C++ | `2, 3` | empty |
| Elixir | `2, 3` | empty |
| Go | `2, 3` | empty |
| Java | `2, 3` | empty |
| C# | `2, 3` | empty |
<!--/schema-facts-->

This exact table once became a lie — and not from an edit to the page, but from
someone else's edit in the language handlers. That is why it is no longer
written by hand.

It was not always so. In C, C++ and Elixir the record line is assembled **at
instrumentation time**, when the signature has been parsed and the names are
known, and they used to write the string `a=2, b=3` into `a`. That reads more
easily — and it destroyed the single schema: field `a` meant one thing in three
languages and another in two, and there was no way to match a record from one
language against a record from another. The spec splits `a` (values by position)
and `k` (named, as `name=value`) word for word, and there is no other way to
bring eight languages down to one line.

**What it costs.** In all eight languages the name of a positional argument
cannot be recovered from the record. Look at the signature in the source: field
`fn` names the function, and its signature sits next to it in the file. But a
tool that reads **only** the record has no access to the names — that is a real
loss, not a cosmetic detail.

Python always writes keyword arguments with their names — they are in `k`, and
there the name is present by the definition of the field.

## Core, thread and clock

`ci` (core number) and `th` (thread mark) sit in the `in` line. The values below
are taken word for word from records captured by runs on Linux; how to reproduce
them — [Measurements](measurements.md).

| language | `ci` (core) | `th` (thread), how it looks | what it is made of | clock for `d` |
|---|---|---|---|---|
| Python | `-1` | `1175211.128643830919680` | `<process>.<thread>` | `perf_counter` |
| JavaScript | `-1` | `1214487.0` | `<process>.<worker thread>`, `0` for the main one | `hrtime` |
| C (ordinary) | `-1` | `1218993.1218993` | `<process>.<native thread>` | `clock_gettime(CLOCK_MONOTONIC)` |
| C++ | `-1` | `1252160.138039566587776` | `<process>.<native thread>` | `steady_clock` |
| Elixir | `-1` | `1293945.#PID<0.95.0>` | `<OS process>.<BEAM process>` | `monotonic_time` |
| Go | `-1` | `2396561.1` | `<process>.<goroutine>` | `time.Since` (monotonic clock inside `time.Now`) |
| Java | `-1` | `3101052.3` | `<process>.<JVM thread>` | `System.nanoTime` |
| C# | `-1` | `3103557.1` | `<process>.<managed thread>` | `Stopwatch.GetTimestamp` |

**In an ordinary program `ci` is `-1` in all eight languages**, and becomes
`null` when parsed. That is not a bug but an honest "unknown": there is no
portable way to learn the core number in these runtimes, and nobody made one up.

> **Why `-1` in Python.** The runtime helper tries to call
> `os.sched_getcpu()`, and returns `-1` when it is missing
> ([`ouroboros/runtime.py:75`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L75)).
> CPython has no such function at all — checked on 3.12.13 and 3.14.4 on Linux.
> Neither `import os; os.sched_getcpu` nor `dir(os)` finds it, so the `-1`
> branch is the only one that ever runs.

> **Why `-1` in Elixir.** This used to hold the BEAM scheduler number, and that
> number looked like a core number without being one: schedulers move between
> cores, so a reader comparing this field with `ci` from other languages was
> comparing two different quantities. Elixir now writes `-1` too.

> **Why `-1` in Go.** There is no portable way to ask for the core number in Go:
> `syscall.Gettid` exists only on Linux, and even it gives no processor number.
> Taking the operating-system thread number instead would put a quantity of a
> different kind into field `ci` — exactly the mistake Elixir had made with the
> scheduler number.

**The one place where `ci` is a real core number is the C build inside an
operating-system kernel**: there `ci` comes from `cpu_index(curcpu())`, `th`
from `<pid>.<lid>` of the current `lwp`, and the clock is `getnanouptime(9)`.
This was read out of the header `ouroboros/languages/_c/ouroboros_runtime.h`
(the `#ifdef _KERNEL` branch); **it has not been checked here by a run inside a
kernel**, unlike the whole rest of the table.

**C has no exceptions**, so field `x` never appears in its records — only `r`.
One more trait of C and C++: they assemble the JSON line themselves, without
pulling in a library, so that the kernel build drags nothing extra along.

## What each language can and cannot do

**Python.** The most road-tested of the eight. The decorator goes **closest to
`def`**, that is inside all the other decorators, and logs the function itself.
The body is untouched — which is why several `return`s, early exits, nested
functions and a hand-written `try/finally` keep working. `lambda` is skipped.
The module docstring and `from __future__ import` stay first, as the language
requires (both breakages happened here and are closed by checks).

**JavaScript / TypeScript.** Arrow functions with a short body (`x => x + 1`)
are skipped — the same decision as skipping `lambda` in Python. Async functions
log the promise they return, not what it resolves to. The runtime helper is
looked up next to the file.

> **Strict mode survives.** The helper's import line goes **below**
> `"use strict"`, so the directive stays first and keeps working.
> This was a fix: the import used to be inserted above, the directive stopped
> being first and silently switched off. Checked by a run — a program that threw
> `ReferenceError` on assigning an undeclared variable before instrumentation
> throws it afterwards too; how to reproduce —
> [Measurements](measurements.md#javascript-strict-mode).

**C.** The record is placed at every exit — `return`, `goto`, end of body. The
format for an argument is chosen from the parsed type (`%d`, `%ld`, `%p`).
**Pointers, `const char *` included, are printed as an address, not as
contents** — why, is worked out in
[Limits](limits.md#the-price-of-safety-strings-in-c-and-c). There is a separate
lightweight record shape for hot and recursive functions — `--minimal`: no call
frame kept, only the name and the depth. It is meant for the kernel build.

**C++.** A value is rendered through `operator<<` when there is one. An exit by
exception is told apart from an ordinary one through
`std::uncaught_exceptions`. The name is written in full — `ns::Class::method`.

> **A braced-list return compiles.** `return {1, 2, 3};` used to turn into
> `return _ouro::capture(__ouro, ({1, 2, 3}));`, which the compiler does not
> accept. Now such a return is left as it is: the value is not logged (field `r`
> will hold `(no value)`), but the file builds and runs. Checked by a run —
> [Measurements](measurements.md#returning-a-braced-list).

**Elixir.** `use Ouroboros.Trace` redefines `def` and `defp`, so every clause of
a function is instrumented separately; guards and default values pass straight
through, arguments are taken from `binding()`, and `raise`/`throw`/`exit` are
caught. **Compile order matters:** the logging module must be compiled before
any module that uses it.

**Go.** The record is placed at every exit through `defer`: an ordinary return,
a `panic`, running off the end of the body. Returns are **not rewritten at
all** — instead every return value in the signature is given a name
(`__ouro_r0` and on), and the closure reads them after `return` has assigned
them. That is why `return f()`, where `f` returns several values at once, works
without a single exception to the rule, while in C and JavaScript every return
site has to be rewritten.

> **There is no helper import.** The helper is a file in the same package, not a
> library you import: Go cannot import a neighbouring file. So nothing is
> inserted above the file header, and `//go:build` together with the package
> clause stay where they are, as the language requires. The price is that the
> helper must declare the same package as the instrumented file: if it stays
> `package main` next to a library file, the build fails. This is closed by the
> checks `test_go.py::test_helper_beside_a_library_package_compiles` and its
> pair.

> **An uncaught panic prints something else.** To log the kind and the text of
> the panic, the closure calls `recover()` and then `panic()`s again. The exit
> code, the ordinary output and any caught panic are unchanged by this, but the
> message of an uncaught panic in the error stream becomes
> `panic: bad [recovered, repanicked]` instead of `panic: bad`. It would differ
> even without instrumentation: a stack trace carries line numbers, and
> instrumentation shifts them.

> **The helper builds with old Go too.** It lands in someone else's tree and is
> built by whatever Go is there, so it deliberately holds nothing newer than
> 1.18 — no range over an integer, no built-in `min`/`max`. Checked by two runs:
> on Go 1.26.5 and on Go 1.19.8 from Debian inside the `packaging/Dockerfile`
> image the instrumented program built and logged the same thing. There is also
> the check `test_go.py::test_helper_compiles_inside_an_older_module`: it builds
> an instrumented file in a module that says `go 1.18`.

Three more Go traits, taken from runs:

- **Function values are not instrumented.** A `func(x int) int { ... }` put in a
  variable or passed as an argument is skipped — the same decision as skipping
  `lambda` in Python.
- **The method receiver does not become an argument.** Field `a` gets only the
  declared arguments, without the `c` from `func (c *Calc) Bump(...)` — as in
  C++, where `this` is not logged either. Python, on the contrary, writes `self`
  as the first argument.
- **`go vet` finds one complaint more** when a value with a lock inside
  (`sync.Mutex`) passes through an instrumented function: it is handed to the
  helper by value. Neither `go build` nor `go test` is affected — the
  `copylocks` check is not in the set `go test` runs by itself; checked by a run.

**Java.** Methods and constructors that have a body are instrumented; abstract
ones, native ones and bodiless declarations in an interface are skipped — there
is nothing to touch there. In a constructor the record goes **after** the
`super(...)` or `this(...)` call, which must stay first. Lambdas and anonymous
classes are not instrumented themselves, and their `return` does not count as an
exit from the enclosing method. There is no helper import at all: it is called
by its full name `ouroboros.OuroborosRuntime`, so the file header stays
untouched.

> **Line numbers are preserved.** Not one inserted piece contains a newline, so
> the instrumented file has as many lines as the original, and the stack trace
> of an uncaught exception matches line for line. This is checked in the parity
> suite by a program that prints the line numbers of its own frames.

> **A return goes into a temporary of its own type.** It would be simpler to
> pass it through a generic helper, and at first that is what happened — until
> on `char f() { return 65; }` the compiler said "inferred: Integer, upper
> bound(s): Character". A generic helper infers its type from the argument, not
> from the method. A temporary declared with the type written on the method
> returns the expression into an assignment of the right type, where narrowing a
> constant is legal again.

**C#.** The same arrangement as Java, plus expression bodies get unfolded:
`int M() => a + b;` becomes a block, and the text of the expression itself is
not rewritten by a single character. A rethrow is a bare `throw;`, so the
exception keeps the place it was thrown from.

> **Five kinds of member are left untouched**, because the instrumented version
> would not compile: members with `yield` (`CS1626`), returns by reference
> (`CS8150`), pointers (`CS0306`), ref structs (`CS9244`) and expression-bodied
> properties. For every such member the instrumentation returns a warning with
> the reason — they do not vanish silently. An `out` argument does not make it
> into the snapshot of arguments on entry: it is not assigned yet there
> (`CS0269`).

> **Someone else's ref struct from another file is invisible.** Parsing goes by
> spelling only, a name is not resolved to its declaration. The platform's ref
> structs (`Span`, `ReadOnlySpan` and the like) are known by name; ones declared
> in the file being instrumented are read from the declaration. One declared in
> **another** file of the same project stays unnoticed, the member that uses it
> gets instrumented and stops compiling. This is the only place in the whole
> tool where instrumentation can produce code that does not compile.


## Working for an operating-system kernel

The C header can build inside a kernel: instead of the usual write to a file
there is its own ring buffer, `printf(9)`, `getnanouptime(9)`, a smaller call
frame and reentry protection. The generated code is **one and the same** in both
cases.

Checked on NetBSD 11.0_RC4 riscv64: a trial kernel module with this header
**compiles clean** against the real kernel headers with the full set of flags
(`-ffreestanding -nostdinc -D_KERNEL -Werror -Wsystem-headers`). Running it in a
rump userspace kernel on riscv64 **did not work** — the rump module loader
cannot do relocations for riscv64 (`panic: kobj_reloc: not supported on this
architecture`), and that is not about our code. Safety at runtime — stack,
reentry and volume — **remains unchecked**. The details are in
[ARCHITECTURE.md](https://github.com/digitable-lol/ouroboros/blob/main/ARCHITECTURE.md).

A shared ring across several translation units works: the check
`tests/test_c.py::test_shared_ring_across_two_tus` builds two files into one
ring buffer and gets nesting that crosses the file boundary:

```
=== ouroboros ring dump: 2 records (total seen 2) ===
{"p":"in","dep":0,"ci":0,"fn":"fa"}
{"p":"in","dep":1,"ci":0,"fn":"fb"}
=== ouroboros ring end ===
```

## flang — not supported

flang is not mentioned in the tool **even once**: it is not in the list of
languages, not in
[SPEC.md](https://github.com/digitable-lol/ouroboros/blob/main/SPEC.md), not in
the source. A flang file cannot be instrumented — no parser matches the
extension.

The project does intend to support flang, and the idea is not instrumentation:
in flang every call that can recurse passes through a single runtime function,
so the records can be taken from there without rewriting anything. But that is
planned work, not a property of the tool, and describing it here as a feature
would be untrue.

The variables `FLANG_WATCH` and `FLANG_PULSE`, if you have heard of them, are
part of flang itself. They have nothing to do with Ouroboros, and it does not
read them.
