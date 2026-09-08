---
title: The brain in flang
---

# The brain in flang

The trace reader used to be one Python file that did two different jobs: it read
a file and decoded JSON, and it decided what a trace means. This is the design
for splitting those, moving the deciding half into flang and leaving the rest
where it is.

The half that decides is the **brain**. It is written in
`ouroboros/brain/trace_brain.flang`, and a compiler checks it before anything
runs: the types, the proof that every function terminates, and the examples
written inside each function. The half that acts is the **shell** — the Python
that opens files, decodes JSON, keeps indexes and answers over MCP.

## Why the split falls where it does

flang has no input or output inside a function, in any form. A function cannot
read a file, cannot call a clock, cannot print. Work with the world is described
as a value — a plan the host executes — and this design uses none of that: the
host here is the existing Python, which already knows how to read a file.

So the border is not a matter of taste. Everything that touches the world stays
in Python because it cannot be anywhere else. Everything that is a decision
about values moves, because that is exactly what the compiler can check.

| Job | Where | Why there |
| --- | --- | --- |
| read `debug.info`, split it into lines | shell | reading is an effect |
| decode one line as JSON | shell | `json.loads` is an effect-free library the shell already has; a JSON reader in flang would be a second implementation to keep true |
| decide whether a line is worth decoding | **brain** | a rule about a string |
| decide whether a decoded object is a call event | **brain** | a rule about a value |
| build an entry, an exit, a completed call, an in-flight view | **brain** | the shape of every answer the reader gives |
| pair an exit with its entry | **brain** states the rule, shell keeps the index | flang has no maps; see "The index" below |
| cut an oversized value down | **brain** | arithmetic on an already-rendered string |
| render a live Python object as a string | shell | inspecting a live object is not something a pure function does |
| serve MCP, page results, run the CLI | shell | effects throughout |

## What the brain decides

Every rule the trace reader used to hold in Python, stated once, with its own
examples:

* **Which lines are worth decoding.** A line whose first non-space character is
  an opening brace. Anything else that carries text is counted as malformed —
  kernel boot spam, a line torn by two CPUs printing at once. A blank line is
  neither: it is not evidence of damage. Whitespace is the ASCII set Python's
  `str.strip` removes, spelled out in `«Is space»` rather than borrowed. The
  rule is read from the front and stops at the first thing that settles it: a
  brace at the front answers on its own, a space passes the question to the rest
  of the line. That order is the whole of the reader's speed — see "Speed"
  below.
* **What a call event is.** The phase field says `in` or `out`; well-formed JSON
  that says anything else is ignored silently, which is not the same as
  malformed.
* **What a completed call is made of.** An exit joined with the entry that
  repeats its id: where the timing, the arguments, the thread and the CPU come
  from, what happens when the entry is missing, and that the name is taken from
  the exit first so an orphaned exit still says what returned.
* **That a raise excludes a result**, and that an exit carrying neither has an
  outcome kind of the empty string — a third answer, not a missing one.
* **That a CPU index of minus one means unknown**, so an empty CPU column never
  reads as "every call ran on CPU zero".
* **What in flight means.** An entry whose id is not among the completed ones: a
  hang, a crash, or a capture that stopped mid-call.
* **How much of a value survives.** Two hundred characters per value, ten items
  per list, one record under 4096 bytes, and the shortening rule itself: a head,
  three dots, a tail, so the cut is visible in the middle rather than guessed at
  from a missing end.

Not every rule has a caller, and that is deliberate. `«Counts as malformed»`
says a blank line does not count and a line of boot spam does; the reader
implements it as one comparison against the answer `«Line kind»` already gave.
The rule is still worth stating where the compiler can hold it: when the blank
rule was broken on purpose, this function's examples went red on their own,
independently of the two in `«Line kind»`. The tree already works this way —
`docs/ouroboros.flang` is a description in flang that nothing calls and the
compiler checks.

## The contract between the two halves

**One bridge, not one per call site.** `ouroboros/brain/__init__.py` is the only
place in the tree where a Python value becomes a flang value or back. Scattering
that conversion is how two halves drift apart: each site grows its own idea of
what an absent duration looks like, and the rule stops living in one language.

**Values crossing the boundary are flat.** flang has no null. Rather than
inventing one at the boundary, a record that has an optional field carries the
value and a presence flag beside it: `duration` with `has duration`, `cpu` with
`has cpu`. The bridge — once, in one method — turns each pair into the
`float | None` the rest of the Python already speaks. Inside the brain those
same facts are sum types (`«Span»`, `«Cpu»`, `«Outcome»`) and a `match` over
them has to cover every case, which is what makes forgetting one a compile
error rather than a `None` three layers down.

**Conversion, in both directions:**

| Python | flang |
| --- | --- |
| `None` | `null` |
| `bool` | flag — checked before `int`, because in Python a bool *is* an int and in flang a flag is not a number |
| `int`, `float` | number (always a float coming back: flang has no integers, and rounding on the way out would hide that) |
| `str` | string — checked before the sequence branch, or it would become a list of one-character strings |
| `Mapping` | record |
| `Sequence` | list |
| anything else | `TypeError`, named |

Back the other way a sum value becomes a small `Variant` carrying the case name
and its fields. The name is the whole decision — `match` in flang branches on it
— so the shell branches on it too.

**The evaluation context** belongs to one `Brain` instance, not to the module:
it carries mutable step and depth counters, and one shared counter across
threads would be a race for no gain. Its **step limit is switched off**, and
that is what `total` buys: the compiler proved every function in the brain
terminates, so the limit that exists to stop a non-terminating one has nothing
left to stop. The depth limit stays — it guards the C stack, which no proof
about flang can.

## The index

One rule is stated in flang and answered in Python, deliberately, and it is the
only one.

`«Is in flight»` says an entry is in flight when its id is not among the
completed ones. Written in flang it scans the ids, because flang has no maps —
linear per entry, quadratic over a trace. A hash set answers the same question
in one lookup, and for a trace of twenty thousand calls that is the difference
between a lookup and four hundred million comparisons.

So the reader keeps the set, and `tests/test_brain.py` holds the two answers
against each other on every sample trace it has. The fast path cannot quietly
stop meaning what the rule says; if it does, the tests go red rather than the
answers going wrong.

## What stays in Python, and why

* **`json.loads`.** A JSON reader could be written in flang. It would be a
  second implementation of something the standard library already does exactly,
  and every disagreement between them would be a bug in the trace reader. The
  brain decides which lines to hand over and what the decoded object means; the
  decoding itself is not a decision.
* **The record sink, `ouroboros/runtime.py`.** This one is not a choice. That
  module is copied verbatim into every instrumented project as
  `ouroboros_runtime.py`, and being stdlib-only is what makes the copy work.
  Importing the brain into it would drag 137 KB of printed flang into every
  draft project and break the property the file exists for. So the sink keeps
  its own copy of the bounding rule, and the brain holds the rule the copy has
  to match: `tests/test_brain.py` replays `_bounded` with every decision taken
  by the brain instead and requires the same bytes out.
* **Rendering a live value.** `reprlib` inspects Python objects. A pure function
  cannot. What the brain owns is the part that works on the string that comes
  out of it.
* **The MCP server, the CLI, the sandbox, the language backends.** Effects
  throughout, and nothing about them was in scope here.

## Printed at build time, or committed?

**Decision: committed**, under `ouroboros/brain/_flang/`, regenerated and
verified by `scripts/emit_brain.py`.

| | committed print | print at build time |
| --- | --- | --- |
| what a user needs to install the tool | pip | pip **and** a flang compiler |
| what a contributor needs | flang, only to change a rule | flang, always |
| cost per build | none | 4.2 s of compiler on every install and every CI job |
| what lands in the tree | 129,118 bytes of printed Python in two files, plus a 4,644-byte derived stub and a 119-byte source stamp | nothing |
| what rots | the committed print, if the source moves without it | nothing |
| what a compiler upgrade does | changes the print; the diff is visible in review | changes what ships, invisibly |

The deciding column is the first one. `ouroboros-logger` is installed with pip,
Homebrew and asdf by people who have never heard of flang, and a build-time
compiler would put a Node toolchain in front of all of them.

The cost of committing is that the print can go stale silently, so it is
machine-checked rather than trusted: `uv run python scripts/emit_brain.py
--check` prints into a temporary directory and compares byte for byte, and
`scripts/qa.sh` runs it. A stale print fails the gate and names the fix.

One line of the print is rewritten on the way in. The compiler emits
`import flang_runtime as rt`, which resolves only when the printed directory is
itself on `sys.path`; inside a package it has to be a package-relative import.
That single substitution lives in `scripts/emit_brain.py`, and the comparison
above covers it like every other byte.

**Three concessions the printed code needs**, each with the same reason — it is
compiler output, not code written here, and the tree already treats such things
as input rather than source:

* `ruff` skips `ouroboros/brain/_flang`, as it already skips the measurement
  samples and the captured runs.
* coverage skips it: its correctness is established by 91 examples the compiler
  runs, not by which of its branches a Python test happened to reach.
* `mypy --strict` reads a stub beside each printed module instead of the module.
  The stub for the brain is derived from the print itself by
  `scripts/emit_brain.py`, so it cannot drift. The stub for the flang runtime is
  written by hand and deliberately partial — it declares what the bridge uses
  and nothing else — and a test holds every name in it against the printed
  runtime, so a compiler upgrade that renames one turns the tests red instead of
  the types quietly lying.

## How this is checked

Four gates, and each one can fail on its own:

1. **The compiler.** `flang check ouroboros/brain/trace_brain.flang` — parsing,
   types, termination and the proof kernel. 39 functions, 10 types, all 39 with
   termination proved: 37 by composition and 2 by structure.
2. **The examples.** `flang test` — 91 examples, written inside the functions
   they are about, so a rule and its cases cannot be separated.
3. **The proof ledger.** `flang check --proof` — 5 propositions, 5 proved, 0 on
   a grid, 0 taken on faith. The five are the value limits and the record
   ceiling. Stronger claims are available but do not come back proved; see
   "Limits" below.
4. **The tree's own gate.** `scripts/qa.sh` — ruff, mypy, pytest, and the print
   check above.

A check that has never failed says nothing, so each rule was broken on purpose
and the failure recorded:

* **a broken rule.** Making `«Line kind»` call a blank line malformed fails both
  `flang check` and `flang test` with three `FLANG_EXAMPLE` findings — the two
  examples of that rule and one of the rule that reads it — each naming the
  function, the example, and both values.
* **broken types.** Making `«Call name»` answer with a length instead of a name
  fails `flang check` with `FLANG_TYPE`, naming the line and both branch types,
  and `flang emit` then writes no file at all: printing is refused before it
  starts.
* **a broken bridge.** Mis-spelling one field of the flat record in the bridge
  fails the Python tests with a `KeyError` naming the field.
* **a rule and its Python copy pulled apart.** Raising the record ceiling in the
  flang from 4096 to 8192 and printing again turns six Python tests red: the
  brain and the sink stop agreeing about how a record is bounded. The proved
  postcondition goes with them, so the compiler names it too.
* **a stale print.** Changing a rule and not printing again fails
  `emit_brain.py --check` by file name, with the command to run.
* **a source change the print never sees.** Rewording a `note`, and separately
  tightening a proved postcondition, each leave the print **byte-identical** —
  measured, not assumed — and each fails `--check` on the source digest, by file
  name and with the command to run. Before the digest existed both passed
  silently; that hole is what the digest was added to close.

## What `--check` asks

Two questions, and they fail apart.

**Does the print belong to this source?** The sha256 of `trace_brain.flang` is
committed beside the print, in `_flang/printed-from.txt`, and compared against
the source as it stands. This needs no compiler, so it is the half that runs
everywhere — including CI, which has flang 0.7.0 and not 0.7.14.

**Is the print what this compiler makes of that source?** `--check` prints into
a temporary directory and compares byte for byte. This needs the pinned
compiler, and says out loud when it is not there (return code 2).

The second question alone was what the gate used to ask, and it left a whole
class of edit unnoticed: a `note`, or a postcondition the proof kernel closes
and strips before printing, changes the source and not one byte of the print.
The compiler still judges such an edit — but only where a compiler is run, and
`qa.sh` on a machine without 0.7.14 ran none. The digest is what notices.

`--self-test` hands the digest check a source it did not print from and requires
a refusal, because a guard that has never gone red is indistinguishable from one
that cannot. `qa.sh` runs it before the check itself.

**Continuous integration does not run the compiler on the brain yet.** The
workflow that checks the Russian description installs flang from npm, which
today carries 0.7.0 and 0.7.3; the brain is written against 0.7.14, which is not
published. Until it is, the compiler gates run where a contributor has 0.7.14
installed, and `qa.sh` says so out loud rather than reporting a pass it did not
earn.

## Limits found

* **Speed, and where it actually went.** Reading a trace is four times slower
  than it was, and it used to be seven and a half. On the fixed sample —
  39,640 lines, 19,600 completed calls, built by `scripts/measure/trace_reading.py`
  — the Python-only reader takes 0.149 s, the brain as first merged took 1.133 s,
  and the brain as it stands takes 0.605 s. Every answer is identical, held
  against the previous implementation on that sample and on twenty-one smaller
  ones.

  The first guess was the boundary: five or so crossings per line, each of them
  a cost paid whether or not the line turns out to be a record. **The guess was
  wrong, and measuring it is what said so.** One crossing — a flang function that
  gives back what it was handed — costs 0.23 µs. The reader makes 138,240 of
  them over that sample: **32 ms of the 1.133 s, under 3 %**. Handing the brain
  a whole trace in one call could not have won more than that, and would have
  cost a list of 39,640 flang strings to build and 39,640 variants to read back.
  Sifting the lines in Python before the boundary was rejected for the same
  reason and one more: the prize is those same 32 ms, and the price is the rule
  "which lines are worth decoding" living in two languages again — which is the
  one thing this whole split was made to stop.

  All the rest was one rule doing far more work than it says. `«Line kind»` was
  written trim-then-look: trim the leading space, trim the trailing space, then
  ask what the first character is. Trimming the trailing end cannot change what
  the first character is, and it cost a `length`, a `char` and an `«Is space»` on
  every line of the file; `«Is space»` itself is ten runtime operations, and it
  was asked about a brace before a brace was ever compared with a brace. Stated
  the way it reads instead — a brace at the front answers, a space passes the
  question on, anything else is damage — the same rule with the same answers
  costs **1.12 µs a line where it cost 11.7 µs**. Five measurements, in the order
  they were made:

  | | the sample reads in |
  | --- | --- |
  | before the brain | 0.149 s |
  | the brain as first merged | 1.133 s |
  | `«Line kind»` walked from the front instead of trimmed | 0.688 s |
  | the bridge: `from_flang` settles a scalar in one comparison, `_text` drops a `str` of a string | 0.658 s |
  | `«Line kind»` asks about the brace before anything else | 0.605 s |

  The absolute seconds belong to one machine and one moment — a busier one gave
  0.153 s, 1.188 s and 0.634 s for the same three, measured back to back. The
  ratio is what survives: about four times the Python-only reader, where it was
  about seven and a half.

  What is left is not one bad rule but the price of the target. The brain now
  costs 411 ms of those 605 ms, spread evenly over rules that each sit near the
  floor of printed Python: about 0.3 µs per runtime operation, because every
  intermediate value is a `Value` object. `«Completed call»` is 5.5 µs for a
  thirteen-field answer, `«Entry from»` 4.3 µs for eight; neither has anything
  obvious left in it.

  **The `c` target was considered and cannot be reached here.** flang prints to
  nine targets and `c` is one of them, and at roughly 10 ns an operation it would
  turn those 411 ms into something like 16 ms. Two things stop it. The pinned
  compiler's distribution carries only the Python runtime — `flang emit --target
  c` refuses for want of runtime sources it does not ship — so the print cannot
  be made by the compiler this print is pinned to. And a C print needs a
  toolchain at install time, which is exactly what the "committed print" decision
  above was taken to avoid: `ouroboros-logger` is installed with pip, Homebrew
  and asdf by people who have never heard of flang.
* **A proof and a count are not the same word.** `«Record bytes limit» is at
  most 4096` is proved: the goal is closed, so the kernel computes it. `the cut
  is never longer than the limit asked for` is not — it comes back as a grid of
  one value, meaning nobody looked for a counterexample. Claims about a function
  of its argument need a theorem written by hand, and none is written here.
* **No maps.** See "The index".
* **What the per-value limits do not promise.** Two hundred characters bounds a
  rendered *string* and ten items bounds a container's *items*; a container of
  ten long values renders far past two hundred characters, and nothing in those
  two rules says otherwise. What actually bounds a written record is the 4096
  ceiling and the halving. Writing this down cost a test that first asserted the
  opposite and failed.
* **A zero-argument function can only be called when its name begins with a
  capital letter**; with a lowercase name the call does not resolve and the
  compiler reports an unbound name. Every function in the brain is therefore
  capitalised.
* **The printed runtime is documented in Russian**, because the compiler writes
  it that way. It is compiler output under `_flang/`, not a page and not code
  written here, and the documentation-language gate reads pages.
