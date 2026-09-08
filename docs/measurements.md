---
title: Measurements
---

**English** · [Русский](measurements.ru.md)

[← back to contents](index.md)

# Measurements

Here are the numbers taken from actual runs, plus everything needed to repeat
those runs: the sources of the sample programs, the commands and the output as
it came out.

Not one number on this page was copied from somewhere else. Run the same thing
yourself and you will get your own numbers — they will differ, because the
machine differs — but the ratios between languages should hold.

One difference from the files on disk, stated once so it does not surprise you
later: the sample programs in `scripts/measure/samples/` carry Russian string
literals, and this page shows them translated — "division by zero", "by design",
"total" — in the sources and in the records alike. Everything else is exactly
what came out: the numbers, the commands, the keys, the durations.

## What was measured

The same work in all eight languages: a function `add(a, b)` called 20 000 times
in a loop, plus a function that raises an exception (in Go, panics), plus an
enclosing function. That is **20 002 calls**. C++ has 20 003 — there is one more
call there, the one returning an object of class type; Java and C# also have
20 003, because `main` gets instrumented too: those languages have no file level
where launch code could be appended after instrumentation, so `main` is
instrumented along with everything else.

For each language we took:

- how long the program runs **without** instrumentation;
- how long it runs **with** instrumentation;
- how many bytes of records come out, and how much that is per call;
- what exactly lands in a record and what does not.

## The machine

| what | value |
|---|---|
| processor | AMD EPYC 7742; `nproc` says 256 |
| system | Ubuntu 26.04 LTS, Linux kernel 7.0.0-29-generic, x86_64 |
| file system of the working directory | ext4 |
| Python | 3.14.4 |
| Node | v26.7.0 |
| gcc / g++ | 15.2.0 |
| clang / clangd / clang-tidy | 21.1.8 |
| Elixir | 1.20.3, Erlang/OTP 29 |
| JDK | OpenJDK 26-internal (`javac 26-internal`) |
| .NET | SDK 10.0.110 |
| Go | 1.26.5 |
| ouroboros | `ouroboros-logger` 0.4.0 — the version installed when the measurement was taken |

Both summary tables below come from **a single run**, all eight languages at
once. Their earlier values were taken on this same machine with earlier
releases, and on every re-run the old languages land within a few percent
(Python 66.2 → 63.5 → 58.2 µs, JavaScript 20.8 → 20.3 → 20.4, C 20.3 → 19.8 →
19.9, C++ 22.8 → 22.0 → 22.2), except Elixir, whose measurement was always the
shakiest. So a table must never be assembled from different runs: only rows
taken together can be compared with each other.

**This matters for reading the numbers.** For six helpers out of eight every
record is an `open`, an append and a `close` of the file, so the measurements
run into the file system, not into the language. The Java and C# helpers keep
the file open and are therefore cheaper. On a machine with a slow disk all the
time numbers will grow, and the ratios between languages will stay.

## How to repeat it

Everything on this page is taken with one command:

```sh
scripts/measure/run.sh
```

It copies the samples from `scripts/measure/samples/` into a working directory
**twice** — as they are and instrumented — builds both copies where a build is
needed, runs them seven times each and prints the finished tables. A language
whose build tool is missing on the machine is skipped, and it says so plainly.
The repeat count is set by `OUROBOROS_MEASURE_REPEATS`.

The measuring script itself is twenty lines (`scripts/measure/measure.py`):

```python
# measure.py <name> <repeats> <trace file or "-"> <working directory> -- <command...>
times = []
for _ in range(repeats):
    if trace != "-" and os.path.exists(trace):
        os.remove(trace)
    t0 = time.perf_counter()
    subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    times.append(time.perf_counter() - t0)
```

The trace file is deleted **before every** repeat, otherwise the volume adds up
over all repeats at once. That is an easy mistake to make: the first run here
gave 280 028 lines instead of 40 004 for exactly that reason.

Each measurement is 7 repeats; the tables show the median.

## Run time

Median of 7 repeats, in seconds. "Overhead per call" is the difference divided
by the number of calls.

| language | uninstrumented | instrumented | overhead | overhead per call |
|---|---|---|---|---|
| Python | 0.0202 s | 1.1837 s | 1.1635 s | 58.2 µs |
| JavaScript | 0.0366 s | 0.4454 s | 0.4088 s | 20.4 µs |
| C | 0.0017 s | 0.3988 s | 0.3972 s | 19.9 µs |
| C++ | 0.0021 s | 0.4469 s | 0.4448 s | 22.2 µs |
| Elixir | 0.7909 s | 4.6060 s | 3.8151 s | 190.7 µs |
| Go | 0.0228 s | 0.6013 s | 0.5785 s | 28.9 µs |
| Java | 0.0332 s | 0.4518 s | 0.4186 s | 20.9 µs |
| C# | 0.0416 s | 0.3535 s | 0.3118 s | 15.6 µs |
| C, minimal form (`--minimal`) | 0.0017 s | 0.1547 s | 0.1530 s | 7.7 µs |
| Go, without the goroutine id | 0.0228 s | 0.5501 s | 0.5272 s | 26.4 µs |

All eight rows come from one run of `scripts/measure/run.sh` — see above for why
a table must never be assembled from several.

What you can see here:

**The record costs more than the language.** Four languages out of eight —
JavaScript, C, C++ and Java — fit into 20–22 microseconds per call, although in
themselves they differ by whole multiples. For three of them that is the price
of opening a file, appending a line and closing it, twice per call; Java keeps
the file open — and still lands in the same band. Go is next to them, but not
inside the band: 28.9 microseconds.

**C# is the cheapest of all** — 15.6 microseconds. The reason is not the
language: its helper, like Java's, keeps the file open and appends to it, while
the other six open and close the file for every record.

**Python is three times dearer** — 58 microseconds. The wrapper there is in
Python itself, and every record is assembled through `json.dumps` and `reprlib`.

**Go is 28.9 microseconds, and 2.5 of them are the `th` field.** The last row of
the table is the same program with the same helper, only with the goroutine-id
lookup replaced by a constant; the difference between the two measurements is
the price of that field. The remainder, 26.4 microseconds, sits about a quarter
above the 20–22 band of JavaScript, C, C++ and Java — so the conclusion "the
record costs more than the language" holds for Go by order of magnitude (Go is
twice cheaper than Python and six times cheaper than Elixir), but Go does not
land inside the tight band of the four. The `th` field is taken apart
[below](#go).

**Elixir is the dearest of all** — about 190 microseconds per call. Part of that
is BEAM itself: without instrumentation its startup takes 0.79 seconds, while in
the other seven languages the whole program fits into hundredths.

> **Elixir has the shakiest measurement.** Individual runs gave medians from 4.11
> to 4.81 seconds, that is an overhead per call from 161 to 202 microseconds — a
> spread of about a quarter. Within a single run the repeats scatter too: from
> 4.05 to 5.02 seconds. Hence "about 190 microseconds", not "190.7"; here you can
> trust the order of magnitude, but not the third digit. In the other languages
> the repeats agreed within a few percent: Java 0.438–0.476 s, C# 0.351–0.364 s.

**The minimal record form for C is three times cheaper than the full one** — 7.7
microseconds against 19.9. It writes one line instead of two and does not build a
call frame. What that costs you is below, in the C section.

**You cannot compute "how many times slower" from this table.** It comes out
between 6 (Elixir) and 313 (C), and neither figure says anything about the tool:
the sample program does **nothing** but make calls, so the ratio only shows how
fast the empty loop was in that language. The meaningful number is the overhead
per call, and for seven languages out of eight it lies between 15.6 and 58.2
microseconds. In a real program, where the function computes something useful,
the share of the overhead is smaller by however many times the function itself
costs more than those microseconds.

## Record volume

Same run, 20 002 calls (20 003 for C++, Java and C#).

| language | lines | total bytes | bytes per record | bytes per call |
|---|---|---|---|---|
| Python | 40 004 | 5 050 377 | 126.2 | 252.5 |
| JavaScript | 40 004 | 4 730 756 | 118.3 | 236.5 |
| C | 40 004 | 4 968 026 | 124.2 | 248.4 |
| C++ | 40 006 | 5 388 341 | 134.7 | 269.4 |
| Elixir | 40 004 | 5 088 052 | 127.2 | 254.4 |
| Go | 40 004 | 4 868 011 | 121.7 | 243.4 |
| Java | 40 006 | 5 028 311 | 125.7 | 251.4 |
| C# | 40 006 | 5 028 310 | 125.7 | 251.4 |
| C, minimal form | 20 002 | 760 078 | 38.0 | 38.0 |

The volume barely wanders between runs: for C, C++, Elixir and Go it repeats
**byte for byte**, for Python and JavaScript it differs by hundredths of a
percent — the duration goes into the record there, and `2e-06` is shorter than
`0.000012`.

**Two lines per call** — in and out — for everyone except the minimal form for
C: there it is one.

**From 236.5 to 269.4 bytes per call** with short arguments (two integers). C++
is the dearest, because names are written out in full there: `demo::add` instead
of `add`. Go is 243 bytes, closer to the low end: its duration is printed with
six digits, like C's, and a goroutine id is shorter than a thread id. Java and C#
differ by **one byte** over five megabytes — they write the same thing.
Long arguments will push this up to the ceiling of 4096 bytes per record, after
which values start being truncated.

A practical rule of thumb: **a million calls is roughly a quarter of a
gigabyte**.

## Python

The sample file before instrumentation:

```python
"""Sample for the measurement: addition, division and accumulation."""
import sys

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000


def add(a, b):
    return a + b


def div(a, b):
    return a / b


def main():
    total = 0
    for i in range(N):
        total = add(total, i)
    try:
        div(1, 0)
    except ZeroDivisionError:
        pass
    return total


print(main())
```

Instrumenting:

```sh
ouroboros wrap-file add.py
```

```json
{"ok": true, "path": "add.py", "language": "python", "functions_wrapped": 3, "runtime_header": "ouroboros_runtime.py"}
```

The result is three `@_ouro_log` decorators and one import line. **The import
line landed below the module docstring**, not above it:

```python
"""Sample for the measurement: addition, division and accumulation."""
from ouroboros_runtime import log as _ouro_log
import sys
```

This was checked not by eye but by running it: the file before instrumentation
and the file after were loaded into the same process, and both were asked for
their `__doc__`.

```
plain    __doc__ = 'Sample for the measurement: addition, division and accumulation.'
wrapped  __doc__ = 'Sample for the measurement: addition, division and accumulation.'
```

Next to the instrumented file an `ouroboros_runtime.py` appeared — the runtime
helper the instrumented code takes its decorator from.

Records:

```jsonl
{"p":"in","t":"2026-08-29T08:30:14.227","id":"780b1d14-…","ci":-1,"th":"1175211.128643830919680","fn":"main","a":"","k":""}
{"p":"in","t":"2026-08-29T08:30:14.227","id":"7b0bd3b0-…","ci":-1,"th":"1175211.128643830919680","fn":"add","a":"0, 0","k":""}
{"p":"out","id":"7b0bd3b0-…","fn":"add","r":"0","d":2e-06}
{"p":"out","id":"e81a5517-…","fn":"div","x":"ZeroDivisionError: division by zero","d":5e-06}
```

**What got recorded:** the function name, argument values by position, the
returned value, the duration, the thread mark, the entry time, the call id. The
exception was recorded together with its type and message.

**What did not:** argument names (`a` holds `0, 0`, not `a=0, b=0`) and the core
id (`ci` is always `-1`).

**What it cost:** 58.2 microseconds and 252.5 bytes per call.

## JavaScript

The sample function:

```js
function add(a, b) {
  return a + b;
}
```

After instrumentation:

```js
function add(a, b) { const __ouro_ctx = _ouro_rt.enter("add", [a, b]); let __ouro_result, __ouro_threw = false; try {
  return (__ouro_result = (a + b));
 } catch (__ouro_e) { __ouro_threw = true; _ouro_rt.exit_throw(__ouro_ctx, __ouro_e); throw __ouro_e; } finally { if (!__ouro_threw) _ouro_rt.exit(__ouro_ctx, __ouro_result); }}
```

The body is not rewritten — a `try` is put around it, and every `return` turns
into an assignment followed by a return. Early exits, several `return`s and a
`try/finally` that was already there keep working.

Records:

```jsonl
{"p":"in","t":"2026-08-29T08:30:58.319","id":"01ace5dd-…","ci":-1,"th":"1214487.0","fn":"add","a":"0, 0","k":""}
{"p":"out","id":"01ace5dd-…","fn":"add","r":"0","d":0.000046}
{"p":"out","id":"8ab042d5-…","fn":"div","x":"RangeError: division by zero","d":0.000113}
```

**What got recorded:** the same as for Python. The message in the samples is not
ASCII — this page shows it translated — and it went into the record exactly as it
came, with no `\u` escaping.

**What did not:** argument names, the core id. The `k` field is always empty —
JavaScript has no keyword arguments.

**The thread mark** is `1214487.0`: the process id and the worker thread number.
For the main thread it is `0`.

**What it cost:** 20.4 microseconds and 236.5 bytes per call — its records are
shorter than those of the other seven, and only C# and C are cheaper in time.

## C

The sample file:

```c
static long add(long a, long b)
{
	return a + b;
}

static const char *name(const char *who)
{
	return who;
}
```

After instrumentation:

```c
static long add(long a, long b)
{
	struct _ouro_call __ouro __attribute__((cleanup(_ouro_emit)));
	long __ouro_result;
	_ouro_enter(&__ouro, "add", "%ld, %ld", a, b);

	return  (__ouro_result = (a + b), _ouro_set_result(&__ouro, "%ld", __ouro_result), __ouro_result);
}
```

The format for each argument is chosen from the parsed type: `%ld` for `long`,
`%d` for `int`, `%p` for any pointer — **including `const char *`, which is
printed as an address, not as its contents**
([why](limits.md#the-price-of-safety-strings-in-c-and-c)). The record string is
assembled **at instrumentation time**, when the types are known.

The exit is caught through `__attribute__((cleanup))`, a gcc and clang
extension. The exit record appears on any path out: `return`, `goto`, end of
body.

### What C cannot record

A separate file with different kinds of return:

```c
struct point { int x, y; };
static struct point make(int x, int y) { … }   /* returns a struct */
static void nothing(int n) { … }               /* returns nothing */
static double half(double v) { … }
static unsigned char byte(unsigned char c) { … }
```

Records:

```jsonl
{"p":"out","id":"83961f25-…","fn":"make","r":"(no value)","d":0.000000}
{"p":"out","id":"cdf9f985-…","fn":"nothing","r":"(no value)","d":0.000000}
{"p":"out","id":"eb1cc1ff-…","fn":"byte","r":"7","d":0.000000}
{"p":"out","id":"bf9b221d-…","fn":"half","r":"2.500000","d":0.000000}
```

**A returned struct is not recorded** — the `r` field holds `(no value)`. A
struct has no `printf` format, and the tool did not invent one. The arguments,
the duration and the fact of the call itself are recorded as usual.

**A function that returns nothing** also gives `(no value)` — here that is simply
the truth.

**C has no exceptions**, so there is never an `x` field in its records — only
`r`.

**C rounds the duration down to a microsecond** (`%ld.%06ld`), so calls shorter
than a microsecond show `0.000000`. You can see it in the records above: four
calls out of five came out as exactly zero, and only `main` was non-zero. For
picking out slow calls that does not get in the way; for measuring fast ones it
gets in the way completely.

### The minimal record form

`--minimal` is a separate, stripped-down form, meant for hot and recursive
functions and for building inside an operating-system kernel:

```sh
ouroboros wrap-file minimal.c --minimal
```

```c
static long add(long a, long b)
{
	char __ouro __attribute__((cleanup(_ouro_min_exit))) = _ouro_min_enter("add");

	return a + b;
}
```

There is no call frame, there is a single marker byte. Records:

```jsonl
{"p":"in","dep":0,"ci":-1,"fn":"main"}
{"p":"in","dep":1,"ci":-1,"fn":"add"}
{"p":"in","dep":1,"ci":-1,"fn":"add"}
```

**There are no arguments here, no returned value, no call id, no time, no
duration — and no exit line.** There is a name and a nesting depth (`dep`).

**Which has an important consequence:** the record reader does **not count**
such lines as calls. Every unpaired entry line becomes an "in-flight call". On
20 002 calls `ouroboros trace-stats` answers:

```json
{
  "calls_parsed": 0,
  "total_calls": 0,
  "in_flight": [ { "name": "main", "call_id": "", "started": "", … }, … ]
}
```

Twenty thousand records in the in-flight list and not a single parsed call. The
minimal form is good for seeing the **call tree** — who called whom and how
deep — but the usual record-reading tools do not apply to it.

What it gives in return: 7.7 microseconds instead of 19.9 and 38 bytes per call
instead of 248.

**Only C has the minimal form.** In the other languages it answers with a
refusal:

```json
{"ok": false, "error": "minimal probe mode is C-only (kernel ring sink)", "language": "python"}
```

## C++

A sample file with a namespace and a returned object:

```cpp
namespace demo {
struct Point { int x, y; };
long add(long a, long b) { return a + b; }
Point make(int x, int y) { return Point{x, y}; }
double div(double a, double b) { … }
}
```

After instrumentation:

```cpp
long add(long a, long b)
{
	std::ostringstream __ouro_args; __ouro_args << _ouro::repr(a) << ", " << _ouro::repr(b);
	_ouro::Scope __ouro("demo::add", __ouro_args.str());
	try {

	return _ouro::capture(__ouro, (a + b));

	} catch (...) { __ouro.note(); throw; }
}
```

The exit is caught by a scope guard — an object whose destructor writes the exit
line. Separately there is a `catch (...)`, which records the exception type and
immediately rethrows it: during stack unwinding a destructor cannot find out
which exception is in flight, and a `catch` can.

**The name is written in full** — `demo::add`, not `add`. Hence the extra bytes:
269 per call against 248 for C.

### What C++ cannot record

The `make` function returns `Point` — an object of class type, by value. In the
instrumented code **nothing appeared** around its `return`:

```cpp
Point make(int x, int y)
{
	…
	return Point{x, y};      /* no _ouro::capture */
	…
}
```

The record:

```jsonl
{"p":"out","id":"185855d5-…","fn":"demo::make","r":"(no value)","d":0.000000}
```

**This is a deliberate choice, not an unfinished corner.** Passing such a return
through the helper would mean cancelling the copy elision that C++17
**guarantees**: a program counting its own constructors would start printing a
move that was not there without instrumentation, and a type with deleted copy
and move would simply stop compiling. A tool that changes the behaviour of what
it measures is useless — so here observability lost to transparency on purpose.

Numbers, pointers and references are recorded in full. Arguments, the duration
and the fact of an exception — always.

An exception is recorded together with its type:

```jsonl
{"p":"out","id":"a3bfa016-…","fn":"demo::div","x":"std::runtime_error: division by zero","d":0.000077}
```

## Elixir

The sample module:

```elixir
defmodule Sample do
  def add(a, b), do: a + b

  def ratio(_a, 0), do: raise(ArithmeticError, "division by zero")
  def ratio(a, b), do: a / b

  def run(n) do
    …
  end
end
```

Instrumentation inserts **one line per module**:

```elixir
defmodule Sample do
  use Ouroboros.Trace
  def add(a, b), do: a + b
```

```json
{"ok": true, "path": "add.ex", "language": "elixir", "functions_wrapped": 1, "runtime_header": "ouroboros_trace.ex"}
```

> **`functions_wrapped: 1` here means one module, not one function.** In Elixir
> instrumentation goes by whole modules: `use Ouroboros.Trace` redefines `def`
> and `defp`, and every function of the module gets instrumented at once. In the
> other seven languages that number means functions. Do not compare it across
> languages.

The same fact means that **you cannot pick out individual functions in Elixir**:

```sh
ouroboros wrap-functions add.ex add
```

```json
{"ok": false, "error": "[elixir] corrupted source in a.ex: selective `only=` is unsupported for the Elixir (module-granular) backend", "language": "elixir"}
```

**Build order matters.** `use Ouroboros.Trace` is expanded at build time, so the
`ouroboros_trace.ex` helper must be compiled **before** any module that uses it:

```sh
elixirc -o ebin ouroboros_trace.ex add.ex
```

Records:

```jsonl
{"p":"in","t":"2026-08-29T08:33:21.133","id":"5061aa72-…","ci":-1,"th":"1293945.#PID<0.95.0>","fn":"add","a":"0, 0","k":""}
{"p":"out","id":"5061aa72-…","fn":"add","r":"0","d":0.000003}
{"p":"out","id":"a735ea12-…","fn":"ratio","x":"ArithmeticError: division by zero","d":0.021161}
```

**The thread mark** is `1293945.#PID<0.95.0>`: the operating-system process id
and the BEAM process id. Both halves are needed: the OS process id cannot tell
two BEAMs apart, and the BEAM process id cannot tell apart two nodes writing
into the same file.

**The function name is written short** — `add`, not `Sample.add`. The module
name is not in the record.

**`ratio` is instrumented in both clauses** — the one that raises and the one
that divides. Every clause is instrumented separately; guards and default values
pass through untouched.

## Go

The sample file before instrumentation:

```go
// Sample for the measurement: addition and a function that panics.
package main

import (
	"fmt"
	"os"
	"strconv"
)

func add(a, b int) int { return a + b }

func boom() int { panic("by design") }

func main() {
	n := 20000
	if len(os.Args) > 1 {
		if v, err := strconv.Atoi(os.Args[1]); err == nil {
			n = v
		}
	}
	total := 0
	for i := 0; i < n; i++ {
		total = add(total, i)
	}
	func() {
		defer func() { _ = recover() }()
		boom()
	}()
	fmt.Println("total", total)
}
```

Instrumenting:

```sh
ouroboros wrap-file add.go
```

```json
{"ok": true, "path": "add.go", "language": "go", "functions_wrapped": 3, "runtime_header": "ouroboros_runtime.go"}
```

What `add` turns into:

```go
func add(a, b int) (__ouro_r0 int) {
	__ouro_ctx := _ouroEnter("add", a, b)
	defer func() {
		if __ouro_p := recover(); __ouro_p != nil {
			_ouroPanicked(__ouro_ctx, __ouro_p)
			panic(__ouro_p)
		}
		_ouroReturned(__ouro_ctx, __ouro_r0)
	}()
 return a + b }
```

**The return is not rewritten.** Instead the returned value is given the name
`__ouro_r0`, and the closure reads it after `return` has assigned it. The type
of the signature does not change, so callers, interfaces and function values
work as before.

**There is no import line.** The helper is a file in the same package: Go cannot
import a neighbouring file, which means nothing needs inserting above the
header. Both files have to be built:

```sh
go build -o add add.go ouroboros_runtime.go
```

Records:

```jsonl
{"p":"in","t":"2026-08-29T23:50:53.477","id":"3bf958d0-…","ci":-1,"th":"2388843.1","fn":"main","a":"","k":""}
{"p":"in","t":"2026-08-29T23:50:53.477","id":"432672b6-…","ci":-1,"th":"2388843.1","fn":"add","a":"0, 0","k":""}
{"p":"out","id":"432672b6-…","fn":"add","r":"0","d":0.000000}
{"p":"out","id":"0aa87493-…","fn":"boom","x":"string: by design","d":0.000006}
```

**The panic is recorded with its type and its text** — `string: by design`.
Go has no exceptions of its own, so the type is taken from the panic value
itself: `panic("…")` panics with a string, while running off the end of a slice
would give `runtime.boundsError`.

**The thread mark** is `2388843.1`: the operating-system process id and the
goroutine id.

### What the goroutine id costs

There is exactly one way to learn a goroutine id in Go: parse the first line of
your own stack trace, the one `runtime.Stack` prints. The runtime gives no call
of its own for it, and a portable way to ask for the operating-system thread id
does not exist either — `syscall.Gettid` is Linux-only.

The trouble is that `runtime.Stack` walks the **whole** stack, however small the
buffer. Taken from a run (`goroutine_id.go` from the samples, 30 000 repeats at
each depth):

| stack depth | µs per `runtime.Stack` call |
|---|---|
| 0 | 2.655 |
| 10 | 9.554 |
| 30 | 21.649 |
| 100 | 67.220 |
| 300 | 87.562 |

So the price of the `th` field **grows with the stack depth of the program**. In
the measurement above, where `add` is called from `main`, it came out at 2.5
microseconds per call (28.9 against 26.4 for the same program with a helper
where the goroutine id is replaced by a constant). In a deeply recursive program
it will be noticeably more.

It is paid exactly once per call, on the entry line, and **before** the duration
clock starts — which is why it does not land in the `d` field.

## Java

The sample class:

```java
public class Add {
	static long add(long a, long b)
	{
		return a + b;
	}
	…
}
```

Instrumentation puts a `try/catch/finally` into the body and wraps the return
into a temporary of the method's own type:

```java
	static long add(long a, long b)
	{ ouroboros.OuroborosRuntime.Ctx __ouro_ctx = ouroboros.OuroborosRuntime.enter("Add.add", new java.lang.Object[]{a, b}); long __ouro_result = 0L; try {
		return (__ouro_result = a + b);
	 } catch (java.lang.Throwable __ouro_e) { ouroboros.OuroborosRuntime.exitThrow(__ouro_ctx, __ouro_e); throw __ouro_e; } finally { ouroboros.OuroborosRuntime.exit(__ouro_ctx, __ouro_result); }}
```

Records:

```jsonl
{"p":"in","t":"2026-08-30T02:20:32.438","id":"333229db-…","ci":-1,"th":"750478.3","fn":"Add.run","a":"20000","k":""}
{"p":"out","id":"99258afb-…","fn":"Add.div","x":"java.lang.ArithmeticException: division by zero","d":0.003388}
```

**Nothing is inserted at the top of the file.** The helper is called by its full
name — `ouroboros.OuroborosRuntime` — so instrumentation never has to decide
where an import line may go. In JavaScript that single line took three separate
rules (below `#!`, below the file's own directives, `import` or `require`), and
every one of them was a bug first. In Java the question need not be asked, and
it is not; the price is a longer call site.

**Not one inserted snippet contains a newline.** Line numbers in the
instrumented file are the same as in the original, so the stack trace of an
uncaught exception matches line for line. The parity suite checks this directly:
the program catches its own exception and prints `method_name:line_number` for
each of its own frames.

**A temporary of the method's own type, not a generic helper.** The simple thing
came first: `return ouroboros.OuroborosRuntime.ret(__ouro_ctx, expression)`. On
the method `char asChar() { return 65; }` that stopped compiling:

```
inferred type does not conform to upper bound(s)
    inferred: Integer
    upper bound(s): Character,Object
```

A generic helper infers its type **from the argument**, not from the method, and
`65` becomes an `Integer`, which no longer narrows to `char`. A temporary
declared with the very type written on the method returns the expression into an
assignment of the right type — and there a constant narrowing is legal again. As
a bonus, expressions with a target type (lambdas, `switch`, the ternary) keep
their original target.

**A bare `return;` is not touched at all.** The exit line is written by the
outermost `finally`, whichever way the body left, so `void` methods and
constructors get no return edits at all.

## C#

The sample class is the same sum, with instrumentation of the same shape:

```csharp
	static long Sum(long a, long b)
	{ Ouroboros.OuroborosRuntime.Ctx __ouro_ctx = Ouroboros.OuroborosRuntime.Enter("Add.Sum", new object[]{a, b}); try {
		return Ouroboros.OuroborosRuntime.Ret<long>(__ouro_ctx, a + b);
	 } catch (System.Exception __ouro_e) { Ouroboros.OuroborosRuntime.ExitThrow(__ouro_ctx, __ouro_e); throw; } finally { Ouroboros.OuroborosRuntime.ExitPending(__ouro_ctx); }}
```

Records:

```jsonl
{"p":"in","t":"2026-08-30T02:20:39.785","id":"38c8b15d-…","ci":-1,"th":"751111.1","fn":"Add.Run","a":"20000","k":""}
{"p":"out","id":"c52bf2df-…","fn":"Add.Div","x":"System.DivideByZeroException: division by zero","d":0.000544}
```

**The type on `Ret<long>` is written out, not inferred.** For the same reason as
the temporary in Java: without it `return null;` and `return x => x + 1;` give

```
The type arguments for method 'OuroborosRuntime.Ret<T>(OuroborosRuntime.Ctx, T)'
cannot be inferred from the usage.
```

The type taken is the one written on the member itself (for `async`, the one
unwrapped from `Task<T>`/`ValueTask<T>`, because `return` carries exactly `T`),
and it is substituted verbatim, in the same file and the same namespace, so it
resolves exactly as before.

**A bare `throw;`, with no argument.** Rethrowing what was caught
(`throw __ouro_e;`) would reset the exception's throw site to the wrapper line,
and the observed program would print a different trace **only because it is
being observed**. A bare `throw;` does not do that: the trace of an uncaught
exception matches line for line.

**Not everything can be instrumented, and that is said out loud.** Five kinds of
member are left alone, each one because the instrumented version does not
compile:

| what | what the compiler says |
|---|---|
| a member with `yield` | `CS1626: Cannot yield a value in the body of a try block with a catch clause` |
| a return by reference (`ref int M()`) | `CS8150: By-value returns may only be used in methods that return by value` |
| a pointer in an argument or a return | `CS0306: The type 'int*' may not be used as a type argument` |
| a ref struct (`Span<int>`) | `CS9244: The type 'ReadOnlySpan<char>' may not be a ref struct …` |
| an expression-bodied property (`int P => x;`) | it would compile, but expanding it means rewriting how the member is built, not cutting into a body |

For every such member the instrumentation returns a warning with the reason
instead of staying silent:

```
Program.UseView: left alone (ref-struct-parameter)
```

An `out` argument is the only thing that drops out partially: the member is
instrumented, but the argument itself does not make it into the entry snapshot,
because on entry it is not yet assigned (`CS0269`).

## Parser cost in Java and C#

Python ships a parser with the language, and so does Go; for JavaScript
`@babel/parser` is packed into the package, for C and C++ it is `libclang`. In
Java and C# a parser costs nothing to obtain either, but in different ways, and
the price differs.

| | Java | C# |
|---|---|---|
| where the parser comes from | `javax.tools` + `com.sun.source` — inside the JDK itself | Roslyn — inside the .NET SDK package |
| does anything get downloaded | no | no |
| building the helper, once per machine | **0.73 s** | **1.87 s** |
| size of what gets built | **15 561 bytes**, 5 files | **32.3 MB**, 35 files |
| one parse of a 202-line file | **median 322.8 ms** (20 runs, 308–359) | **median 89.6 ms** (20 runs, 85–95) |

**Java: almost nothing to store, but every parse is expensive.** Fifteen
kilobytes is the helper and nothing else: the parser is already in the JDK. On
the other hand, every parse starts its own Java virtual machine, and those 323
milliseconds are mostly its startup, not the parsing.

**C#: the other way round.** Thirty-two megabytes is two Roslyn libraries copied
next to the helper; the helper itself among them is 0.02 MB. On the other hand
parsing is three and a half times faster.

In neither case is anything pulled from the network: both the JDK and the .NET
SDK have to be on the machine anyway to build the instrumented code. The path to
Roslyn is not written down in the tree — it is worked out from what
`dotnet --list-sdks` prints, and the target framework is likewise derived from
the installed SDK rather than nailed to `net10.0`.

## Recursion depth in Python

Instrumentation in Python is a decorator, that is, a wrapper stands between the
caller and the callee, and every call takes **two** stack frames instead of one.

The sample function counts how deep it got:

```python
reached = 0


def descend(n):
    global reached
    if n > reached:
        reached = n
    return descend(n + 1)
```

Runs with different limits:

```
deep_plain: recursion limit 200, deepest level reached 199
deep:       recursion limit 200, deepest level reached 95
deep_plain: recursion limit 1000, deepest level reached 999
deep:       recursion limit 1000, deepest level reached 495
```

**Roughly half as deep.** A program with a tightly fitted recursion limit can
run into it after instrumentation where it did not before. As long as the
mechanism is a decorator, this cannot be removed. For deep recursion, instrument
not the recursive function itself but the ones that call it.

## JavaScript strict mode

The `"use strict"` directive only works while it is first. A helper import line
placed above it would silently switch strict mode off — the program would keep
running, but differently.

The sample program assigns to an undeclared variable: in strict mode that is a
`ReferenceError`, without it, the silent creation of a global.

```
before instrumentation: THREW: ReferenceError
after instrumentation:  THREW: ReferenceError
```

The instrumented file:

```js
"use strict";
const _ouro_rt = require("./ouroboros_runtime.js");
```

The import landed **below** the directive. The mode is preserved.

## Returning a braced list

`return {1, 2, 3};` in C++ used to turn into
`return _ouro::capture(__ouro, ({1, 2, 3}));`, which the compiler does not
accept: a braced list wrapped in parentheses is not an expression.

Now such a return is left as it is:

```cpp
std::vector<int> three()
{
	_ouro::Scope __ouro("three", "");
	try {

	return {1, 2, 3};

	} catch (...) { __ouro.note(); throw; }
}
```

```
COMPILED
3
```

At the price of the value not being recorded:

```jsonl
{"p":"out","id":"8d43618d-…","fn":"three","r":"(no value)","d":0.000001}
```

## Does call logging help you understand code you did not write

Everything above is about the price of the records. This section is about what
that price buys: **hand a language model a program written by someone else, ask
what it actually did, and see how much the records add over the source alone.**

The experiment lives in `scripts/measure/trace-help/`, is re-taken with one
command and is described there in `README.md` — with the full output of every
run, the setup and the measure declared before the first run.

### How it is set up

Twelve programs in six languages, two per language, written the way other
people's code looks: branching depends on the data, some functions are not
called at all in this run, somewhere an exception shows up. Five questions each,
about what happened in that one run: how many times a function was called, what
it returned, what it was called with, whether it was called at all, who raised
the exception.

Sixty questions, each asked twice:

| group | what the answerer is given |
|---|---|
| control | the whole source, the exact run command with arguments, the program output |
| experiment | the same plus the `debug.info` of that same run |

The control group gets everything you need to work the answer out yourself. The
right answers were written down by hand while the programs were being composed
and checked against a run: `build.py` pulls the same values out of the records
and fails on a mismatch. Answers are graded by `grade.py`, which is fed only the
question number and the answer text — it does not know whether there was a trace.

Spread: a 95-percent interval from resampling over programs (the common name for
the trick is bootstrap). The threshold was declared in advance: a difference
counts as a finding only if zero falls outside the interval.

### Who answered

| who answered | answers | correct without the trace | correct with the trace | difference | interval |
|---|---|---|---|---|---|
| `qwen3.5:4b`, 4.7 billion weights | 600 | 44.0% | 78.3% | +34.3 | 19.7 … 47.7 |
| `qwen2.5:14b-instruct`, 14.8 billion | 600 | 61.0% | 84.7% | +23.7 | 12.3 … 35.0 |
| `qwen3:32b`, 32.8 billion | 600 | 66.7% | 90.3% | +23.6 | 10.3 … 36.3 |
| a Claude Opus 5 subagent | 120 | 95.0% | 98.3% | +3.3 | **0.0 … 8.3** |

The three models under ollama were taken on a machine with an RTX 6000 Ada,
temperature 0.8, seed = the repeat number, the same in both groups, internal
reasoning switched off in all three. Five repeats; between repeats the share of
correct answers wanders by 1.4–3.6 points, that is, noticeably less than the
difference.

**On the strong model the gain is not shown.** For the agents zero falls inside
the interval — by the threshold declared in advance that is not a finding. The
setup: every question goes to a separate agent that knows nothing about the
experiment, nor that there are two groups, nor which one it is in. Of the 120
answers four are wrong, and all four are "no" instead of `false`: the right
value in the wrong shape. On these twelve programs the strong model knows the
answer without the trace as well. The agents had one repeat, not five, so the
interval is wider; "no gain" here means "the gain is not shown".

### Where there is no gain

Numbers for `qwen2.5:14b-instruct`:

| what the question is about | answers per group | without the trace | with the trace |
|---|---|---|---|
| what a given call returned | 140 | 46.4% | 87.1% |
| what the function was called with | 10 | 50.0% | 100.0% |
| how many times a function was called | 90 | 58.9% | 68.9% |
| whether a function was called at all | 55 | 100.0% | 100.0% |

The last row is zero, and it repeated for three answerers out of four. The whole
gain sits on questions about values: the trace answers "what happened" and adds
almost nothing to "what is written".

By language (`qwen2.5:14b-instruct` / `qwen3:32b`, 50 answers per language per
model):

| language | without the trace | with the trace | bigger model, without | bigger model, with |
|---|---|---|---|---|
| Python | 50.0% | 100.0% | 46.0% | 94.0% |
| JavaScript | 52.0% | 96.0% | 50.0% | 94.0% |
| C | 52.0% | 70.0% | 74.0% | 78.0% |
| C++ | 58.0% | 74.0% | 60.0% | 80.0% |
| Elixir | 70.0% | 84.0% | 74.0% | 100.0% |
| Go | 84.0% | 84.0% | 96.0% | 96.0% |

**Go shows no gain at all — on both models.** Fifty answers per language is a
hint, not a conclusion: for C++ one run gave 58.0 → 74.0%, and another the same
evening gave 58.0 → 64.0%.

### When the trace does not fit in the prompt

Everything above is about short programs, where the trace fits whole. A separate
run checks the opposite case: four of those same twelve programs are run on an
input hundreds of times bigger. The trace comes out at **about 1.62 million
characters against 4 670 characters of source — 347 times more**, and what goes
into the prompt is a clip of 8 000 characters: the beginning and the end, with
the middle cut out and a line "N lines cut out here" in its place.

Thirty-six questions, 360 answers, `qwen2.5:14b-instruct`: 13.9% correct without
the trace against 37.2% with the clip, interval 15.0 … 30.6. What helps is
exactly the part that survived:

| where the answer sits | answers per group | without the trace | with the clip |
|---|---|---|---|
| the call needed survived | 55 | 9.1% | 65.5% |
| the call needed fell into the cut-out middle | 65 | 0.0% | 10.8% |
| "how many times called" — the whole trace is needed | 40 | 0.0% | 10.0% |
| "was it called at all" — the function is not in the trace | 20 | 100.0% | 100.0% |

The "survived / cut out" labelling is not done by eye: `long.py` knows exactly
which lines were dropped and looks at whether the entry and the exit of the call
in question fell among them.

The practical conclusion: **handing over the whole trace is worth it while it is
a few times longer than the source. When it is hundreds of times longer,
instrument named functions rather than the whole file
(`ouroboros wrap-functions`), and do not clip the trace at the edges.**

### The cost in characters

On the short programs: 13 070 characters of source against 48 777 characters of
records — 3.73 times more (from 2.45 to 6.16 across programs, 3.52 in the
middle). The prompt to the model grows from 1 623 characters to 5 904, three and
a half times.

## What was not measured here

An honest list of what stayed outside this page. Every item is a place where
claiming anything would be making it up.

- **Work inside an operating-system kernel.** The C header has a branch for
  building inside the NetBSD kernel — with its own ring buffer, `printf(9)` and
  `getnanouptime(9)` — and that is exactly where `ci` becomes a real core id.
  That branch was not built and not run here. Everything said about it in the
  documentation was read in the source.
- **Multi-threaded and multi-process runs.** All the measurements are
  single-threaded. The schema promises that several processes can write into one
  file without tearing lines, and the helpers have a 4096-byte-per-record
  ceiling for that. This was not verified.
- **Long arguments and value truncation.** All the sample calls took two short
  numbers. How truncation behaves on a record longer than 4096 bytes was not
  looked at here.
- **A real program.** The sample program does nothing but make calls. On a real
  task the share of the overhead will be different.
- **TypeScript.** Only JavaScript was instrumented, although the same language
  declares the `.ts` and `.tsx` extensions.
- **Multi-threaded writing in Java and C#.** In both helpers the file is opened
  once and kept open, and writing goes under a lock. A program with several
  threads was not run in the measurements.
- **A long-lived parser.** Both Java and C# start a new process for every parse.
  How much a permanently living parser would give was not measured.
- **The price of the `th` field in Go on a deep stack.** The curve above was
  taken with a separate program, not through the logging helper: in the
  measurement with the helper the stack was always shallow (`add` is called from
  `main`). What a call costs at a depth of a hundred frames in a real recursive
  program was not measured here.
- **Other machines.** One processor, one system, one file system.
- **The usefulness of the trace on real code.** The experiment about usefulness
  was taken on twelve made-up programs of about fifty lines each. What the trace
  gives on working code was not measured here.
- **Other model families.** All three models under ollama are from the qwen
  family. There is one strong model, with one repeat.
- **Instrumenting named functions instead of the whole file.** The long
  experiment measures clipping the trace at the edges. `ouroboros wrap-functions`
  does not take part in the experiment, although the conclusion points right at
  it.
