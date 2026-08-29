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
   for JS/TS, libclang for C++, Roslyn for C#) that reads source on stdin and
   prints function-header / return byte-ranges as JSON on stdout. This is the
   Elixir-port-friendly shape: the core only orchestrates detect → ranges →
   splice → log.
2. Implement a `Transformer` subclass that shells out to the emitter, builds
   `Edit`s, and applies them — reusing `apply_edits` unchanged.
3. Ship a runtime helper (the language's analogue of `ouroboros_runtime.py`)
   that appends the exact [SPEC.md](SPEC.md) `ШАБЛОН` block to
   `OUROBOROS_DEBUG_INFO`. **Not** via stdout.
4. Register it in `registry.py`.

## Status

- **Python**: complete end-to-end (decorator injection).
- **JS/TS**: complete end-to-end (`_js/emitter.js` babel range-emitter +
  `try/finally` splice + `ouroboros_runtime.js`). Wraps `.js/.mjs/.cjs/.jsx/.ts/.tsx`;
  routes logging through the helper, not `console.log`. Known limits: concise-body
  arrows skipped; the runtime import assumes the helper sits beside the file
  (draft root) and a CommonJS-or-ESM `default` import; async functions log the
  returned Promise, not the awaited value.
- **C**: complete end-to-end (`c_lang.py`, libclang + `__attribute__((cleanup))`).
  Type-directed arg/return formatting from the AST (`%d`/`%ld`/`%p`/guarded `%s`…),
  one block per call on any exit path (return/goto/fall-through), runtime header
  `_c/ouroboros_runtime.h`. Validated by gcc compile+run. Userland only.
- **C++**: complete end-to-end (`cpp_lang.py`, libclang + RAII ScopeGuard).
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
- **C#**: not yet wired (dotnet 10 SDK present at `~/.dotnet`).

C/C++ splice on BYTE offsets (libclang) and parse with discovered system include
paths; the corruption gate rejects on Error-severity diagnostics. Real NetBSD-tree
files need that tree's `-I/-D` flags + target headers (validate on `ssh netbsd`)
— the validated path here is AI-authored self-contained userland code.

Suite: ~105 tests, 91% coverage. Validated languages: Python, JS/TS, C, C++,
Elixir (all by compile+run where applicable).

## Run

```bash
uv sync
uv run pytest --cov            # tests
uv run ouroboros languages     # CLI
uv run ouroboros-mcp           # MCP server over stdio
```
