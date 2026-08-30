# Architecture (Python prototype)

Three layers, one cross-language contract ([SPEC.md](SPEC.md)). Designed so the
later Elixir port is a re-implementation of the same shapes, not a redesign.

```
                 ┌─────────────────────────────────────────────┐
   AI agent ───► │  MCP server (stdio)   |   CLI (Executor)     │   ouroboros/mcp, ouroboros/cli
                 └───────────────┬─────────────────────┬────────┘
                                 │                     │
                    ┌────────────▼──────────┐   ┌──────▼───────────────┐
                    │  sandbox              │   │  languages           │
                    │  draft(черновик)+git  │   │  locate-then-splice   │
                    │  CRUD / execute /     │◄──┤  transformers         │   ouroboros/sandbox,
                    │  finish(→чистовик)    │   │  + corruption gate    │   ouroboros/languages
                    └───────────┬───────────┘   └──────────┬───────────┘
                                │                          │
                         debug.info  ◄────────────  ouroboros_runtime.py
                         (ШАБЛОН records)            (injected, stdlib-only)
```

## Layers

1. **`ouroboros/languages`** — pluggable per-language transformers.
   - **Locate-then-splice is the cardinal rule**: the native parser is used only
     to find node byte-ranges; the original source text is never reprinted, so
     comments and formatting survive every write. `base.py` holds `Edit` /
     `apply_edits` / line-offset helpers; `registry.py` maps extension→backend.
   - **Python backend** (`python_lang.py`): injects a `@_ouro_log` decorator
     above each `def`/`async def` (single leading underscore — `@__log` would be
     name-mangled inside a class). The body is never touched, so multiple/early
     returns, nested functions, lambdas and existing `try/finally` just work.
   - A parse failure raises `CorruptedSourceError` — the corruption gate.

2. **`ouroboros/sandbox`** — the draft/clean workspace. **No filesystem-watching
   daemon**; the CRUD operations *are* the write path.
   - `create` → `<base>/черновик/` git repo + bundled runtime + `.gitignore`.
   - `write_file` → wrap-on-save, one commit per op (`--allow-empty` keeps the
     invariant on identical re-writes); unparseable code is rejected.
   - `execute` → subprocess with `OUROBOROS_DEBUG_INFO` pointed at the draft;
     runtime records + a framed stdout/stderr section land in `debug.info`.
   - `finish` → mirror `черновик` → `чистовик`, minus `.git` and `debug.info`.

3. **`ouroboros/mcp` + `ouroboros/cli`** — two front-ends over identical engine
   functions. Tools: `wrap_code_snippet`, `wrap_file` (brief-mandated) plus
   `create_project` / `write_file` / `read_file` / `list_files` / `execute` /
   `finish`. Tool impls are plain dict-returning functions (unit-testable);
   FastMCP just registers them.

## Adding a language backend

Each new language is an external JSON helper + a thin `Transformer`:

1. Write a small **range-emitter** in the native ecosystem (node/`@babel/parser`
   for JS/TS, `_clang/emitter.c` over libclang for C and C++, real
   `Code.string_to_quoted` for Elixir, `_go/emitter.go` over `go/parser` for Go,
   Roslyn for C#) that reads source on stdin
   and prints function-header / return byte-ranges as JSON on stdout. This is the
   Elixir-port-friendly shape: the core only orchestrates detect → ranges →
   splice → log. Every backend shipped today has one, so no backend holds a
   parser inside the process any more.
2. Implement a `Transformer` subclass that shells out to the emitter, builds
   `Edit`s, and applies them — reusing `apply_edits` unchanged. For C and C++
   that subclass is thinner still: `ClangTransformer` in `clangbridge.py` holds
   the wrap loop both share, and each language supplies only the text it injects.
3. Ship a runtime helper (the language's analogue of `ouroboros_runtime.py`)
   that appends the exact [SPEC.md](SPEC.md) `ШАБЛОН` block to
   `OUROBOROS_DEBUG_INFO`. **Not** via stdout. If the language resolves siblings
   by directory rather than by import — Go does — the helper has to be told
   which package it is joining: override `runtime_asset_for(source)` instead of
   `runtime_asset()`, and the callers that hold the wrapped text will use it.
4. Register it in `registry.py`.

## Status

- **Python**: complete end-to-end (decorator injection).
- **JS/TS**: complete end-to-end (`_js/emitter.js` babel range-emitter +
  `try/finally` splice + `ouroboros_runtime.js`). Wraps `.js/.mjs/.cjs/.jsx/.ts/.tsx`;
  routes logging through the helper, not `console.log`. Known limits: concise-body
  arrows skipped; the runtime import assumes the helper sits beside the file
  (draft root) and a CommonJS-or-ESM `default` import; async functions log the
  returned Promise, not the awaited value.
- **C**: complete end-to-end (`c_lang.py`, `__attribute__((cleanup))`).
  Type-directed arg/return formatting — the specifier per type (`%d`/`%ld`/`%p`/
  guarded `%s`…) is read by the range emitter, not by the backend — one block per
  call on any exit path (return/goto/fall-through), runtime header
  `_c/ouroboros_runtime.h`. Validated by gcc compile+run. Userland only.
- **C++**: complete end-to-end (`cpp_lang.py`, RAII ScopeGuard).
  Generic value repr via `operator<<`/SFINAE, qualified names (`ns::C::m`),
  exception-aware exit (`std::uncaught_exceptions`), `capture()` for any return
  type, runtime header `_cpp/ouroboros_runtime.hpp`. Validated by g++ compile+run.
- **C — kernel sink**: `_c/ouroboros_runtime.h` now has an `#ifdef _KERNEL`
  branch (printf(9)/snprintf(9), getnanouptime(9), cprng_strong32(9), shrunk
  per-frame struct, sink recursion guard). The generated code is identical for
  both contexts. **Scoped for selective opt-in functions, not blanket** (kernel
  stack / printf volume / reentrancy). Validated locally for *record formatting
  only* via a userland shim (`-D_KERNEL -DOURO_KERNEL_TEST`).
  **On-target (NetBSD 11.0_RC4 riscv64):** a test LKM using this header
  **compiles clean against the real matched kernel headers** with full kernel
  flags (`-ffreestanding -nostdinc -mcmodel=medany -D_KERNEL -Werror
  -Wsystem-headers`) → a valid `.kmod`; API/freestanding/cleanup-attribute
  confirmed on the real kernel. Runtime in a rump (userspace) kernel is **blocked
  on riscv64** (`panic: kobj_reloc: not supported on this architecture` — rump's
  module loader lacks riscv64 relocation; not our code). Runtime-safety
  (stack/reentrancy/volume) remains unproven — options: a static rump component,
  or a live modload of the ABI-matched `.kmod`.
- **Elixir**: complete end-to-end (`elixir_lang.py`). BEAM-native analogue of the
  Python decorator: an external emitter (real `Code.string_to_quoted`) locates
  modules; the backend splices `use Ouroboros.Trace`; the shipped runtime
  (`_elixir/ouroboros_trace.ex`) overrides `def`/`defp` so every clause is
  wrapped independently (guards/defaults pass through; args via `binding()`;
  raises/throws/exits caught). Validated by compile+run incl. a hard module
  (multiple clauses, guards, default args, `defp`, raise). Compile-order: the
  trace module must be compiled before any module that `use`s it.
- **Go**: complete end-to-end (`go_lang.py`). The parser ships with the language,
  so `_go/emitter.go` over `go/parser` needs nothing installed beyond a Go
  toolchain; it is built once per machine into the user's cache
  (`OUROBOROS_GO_EMITTER` points at a prebuilt one instead). Instrumentation is a
  `defer`red closure at the top of each body plus **named results** in the
  signature, so no `return` is rewritten and `return f()` forwarding several
  results needs no special case. There is no import line at all: the helper
  (`_go/ouroboros_runtime.go`) is a file of the same package, which is why a
  `//go:build` constraint and a package doc comment stay exactly where the
  language requires them. The closure `recover()`s to name the panic and then
  re-panics, so an UNCAUGHT panic prints `[recovered, repanicked]` on stderr
  where the plain program printed one `panic:` line — exit status, stdout and
  every recovered panic are unchanged. Function literals are skipped, the Go
  analogue of skipping Python lambdas.
- **Java**: complete end-to-end (`java_lang.py`, `try/catch/finally` splice).
  The range emitter (`_java/Emitter.java`) is the compiler that already ships
  inside the JDK — `javax.tools` + `com.sun.source` — so the parser costs no
  download at all; the emitter itself is built once per machine into the user's
  cache (`OUROBOROS_JAVA_EMITTER` points at a prebuilt one instead). Methods and
  constructors with a body are wrapped; a constructor's entry text goes AFTER its
  explicit `super()`/`this()` call, which the JLS requires to stay first. Returns
  go through a temp declared with the member's own return type
  (`return (__ouro_result = expr)`), not through a generic helper: a generic
  helper infers its type argument from the argument rather than from the method
  and stops compiling on `char f() { return 65; }`. No import is spliced — the
  helper (`_java/OuroborosRuntime.java`) is named in full — so the file header is
  never touched. No inserted text contains a newline, so line numbers, and hence
  stack traces, are unchanged. Lambdas and anonymous-class bodies are not wrapped
  themselves and their `return`s are not attributed to the enclosing method.
  Validated by javac compile + run.
- **C#**: complete end-to-end (`csharp_lang.py`, `try/catch/finally` splice).
  The range emitter (`_csharp/Emitter.cs`) uses Roslyn taken from inside the
  installed .NET SDK — located from `dotnet --list-sdks`, never a hard-coded
  path, and nothing is downloaded; the target framework is derived from the SDK
  too, so a machine whose newest SDK is 9 still builds it. Expression bodies
  (`int M() => e;`) are expanded into blocks without reprinting the expression.
  Five kinds of member are left alone because wrapping them does not compile —
  iterators (CS1626), `ref` returns (CS8150), pointers (CS0306), `ref struct`
  types (CS9244) and expression-bodied properties — and each is reported as a
  warning rather than silently skipped. `out` parameters are kept but left out of
  the entry snapshot (CS0269). Rethrow is a bare `throw;` so the exception keeps
  its original throw site. Known hole: a `ref struct` declared in a DIFFERENT
  file of the same project is invisible to a syntax-only parse, and a member
  using one is wrapped into code that does not build. Validated by dotnet build
  + run.

C and C++ talk to libclang **out of process**, through one native range emitter
(`_clang/emitter.c`) shared by both: it parses, and prints body ranges, parameter
types-as-specifiers, the result's capture plan and every `return`'s extent as
JSON. Neither backend imports `clang.cindex`; both splice bytes at the offsets
that come back. They splice on BYTE offsets and parse with discovered system
include paths; the corruption gate rejects on Error-severity diagnostics. Real
NetBSD-tree files need that tree's `-I/-D` flags + target headers (validate on
`ssh netbsd`) — the validated path here is AI-authored self-contained userland
code.

The emitter is compiled once per machine into the user's cache on first use
(`OUROBOROS_CLANG_EMITTER` points at a prebuilt one instead). It needs a C
compiler, which a host that instruments C already has, and no llvm development
headers: it declares the slice of libclang's ABI it uses in
`_clang/libclang_api.h` and opens the shared object with `dlopen`. Those
declarations are not trusted — a test builds the emitter both ways, against them
and against the host's real `<clang-c/Index.h>`, and requires identical output.

Suite: <!--state:tests-->999<!--/state--> tests,
<!--state:coverage_percent-->100<!--/state-->% coverage (statements **and**
branches, `pytest --cov`). Validated languages: Python, JS/TS, C, C++, Elixir, Go, Java, C#
(all by compile+run where applicable). MCP tools declared by the server:
<!--state:mcp_tools-->17<!--/state-->.

Those numbers are written by `scripts/state_numbers.py --measure`, not by hand,
and `scripts/qa.sh` fails if they drift from `docs/state.json`. Both wrong
figures described below got in because a person typed them and no one recomputed
them; typing them is now not how they get here.

Coverage is measured with branches, which is the number that means something
here: line coverage counts an `if` as covered once either side runs, and most of
what can go wrong in this codebase is a side that never ran.

An earlier revision of this file claimed 91%. That figure was never reachable.
Measured on the tree as it then stood, coverage was 66.3%, and `clangtools/`
alone accounted for 387 of the 859 uncovered statement-and-branch units — so even
with `clangtools/` at a perfect 100%, the whole product topped out at **81.5%**.
The 91% was 9.5 points above the ceiling, not 25 points above the current state:
no amount of work on the untested part could have produced it.

That ceiling has since been passed, because the parts it was computed over were
tested rather than argued about. All 29 measured files are at 100% of statements
and branches: `clangtools/`, `sandbox/`, `mcp/server.py`, `cli.py`, `trace.py`,
`runtime.py`, and every backend under `languages/` together with its support
modules.

What is left uncovered — <!--state:uncovered_units-->0<!--/state-->
statement-and-branch units out of <!--state:total_units-->3683<!--/state-->.

The last 15 closed in three different ways, and the ways are worth separating,
because only one of them is "write a test".

**Seven were paths this machine never takes**, in the C and C++ backends
(`cpp_lang.py` 4, `c_lang.py` 2, `clangbridge.py` 1). Each is reached by
supplying the answer the host does not give, and each test was shown failing
with its guard removed:

- three replies from `g++` about where it looks for system headers that this
  machine does not produce — a named directory that does not exist, a list not
  closed by `End of search list.`, and no `g++` on the machine at all;
- a name matching the libclang search pattern that is not a file: a dangling
  `libclang-20.so.1` left behind by a removed llvm package, which the search has
  to step over instead of handing to the parser;
- a bare `return;` inside a value-returning function, which standalone parsing
  rejects at the corruption gate but in-tree parsing only records as a leftover
  remark, so the skip runs there.

**Five were an argument that did not hold.** `runtime.py` `_cpu` reads
`os.sched_getcpu`, which is Linux-only and present only if the interpreter was
built against a libc that offers it; neither CPython here has it. This file used
to say that reaching the success path would mean substituting `os`, which
measures the substitute. That reasoning was wrong: the path does run on the
machines traces are collected on, and what lands in `ci` there is worth pinning
rather than leaving to whatever the host happens to have. Both readings are
checked now — the CPU index reaching the record, and an `OSError` out of the
syscall being written as unknown without taking the traced program down with it.

**Three were dead code, and were deleted rather than covered.** `_bounded`
halved the longest field inside `for _ in range(64)`; the counter can only run
out on a record with all four shrinkable fields large at once, and no real
record has more than two of them filled, so that exit was unreachable. The loop
now asks the question the counter stood in for — is there anything left to
shrink — and both exits are live and under test. `python_lang.py` `wrap_source`
carried a clause adding a newline when the runtime import landed at the very end
of a file that has none; a sweep of 61 880 generated files produced no wrap that
puts the import there, because everything owning the top of a file must sit
above the first function. The clause is gone.

Two hiding places were found and removed while measuring this. `mcp/server.py`'s
`main` carried `# pragma: no cover - run() blocks`; it does not block forever —
over stdio it returns when the client closes the input — and the note had kept
the entry point, and the fact that nothing checked the chosen transport ever
reached `run`, out of the measurement. A whole session now goes through it in
`tests/test_cli.py`. `trace.py` had a "not a dict" branch below a guard that
admits only lines starting with `{`: a JSON document that starts with `{` is an
object or a parse error, so the branch was one no input could reach.

Coverage on this machine is measured with every external tool present — node,
elixir, gcc, clangd, clang-tidy — and nothing skips. On a machine missing them,
tests skip silently and the figure means much less; see the `clangtools/` note
below, which is the same point.

`clangtools/` reaching 100% required installing the binaries it wraps — clangd and
clang-tidy — without which eight of its tests skip and the package sits at 22%.
**The tests are only meaningful with those binaries present**: this package is a
wrapper around two external programs, so a run with them absent measures almost
nothing. CI must install them, not skip. `packaging/Dockerfile` installs both and
fails the build if either is missing — before that it shipped neither, so six of
the seventeen tools were declared by the server and broken in every built image.

## Run

```bash
uv sync
uv run pytest --cov            # tests
uv run ouroboros languages     # CLI
uv run ouroboros-mcp           # MCP server over stdio
```
