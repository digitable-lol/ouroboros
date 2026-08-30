# Ouroboros — Feature Requests

Running log of capabilities the Ouroboros MCP **doesn't yet have but was needed
during real use**. The rule (see global memory `ouroboros-use-always`): dogfood
the MCP as much as possible and **never silently work around a gap** — record it
here, then implement it.

When you hit a missing capability, append an entry:

```
## <short title>
- **Date:** YYYY-MM-DD
- **Needed:** what you were trying to do that the current tools couldn't.
- **Why:** the real task / value behind it.
- **Workaround used:** what you did instead in the meantime (be honest), or "none".
- **Status:** open | in progress | done (commit <sha>)
```

Keep entries newest-first. Close an entry (don't delete it) when implemented, so
the history of what the tools learned to do stays visible.

---

## Readable `const char *` arguments in C/C++ traces, without the out-of-bounds read

**What was lost and why.** C and C++ used to print a `const char *` argument as
its contents (`%s` in C, `std::string(s)` in C++). That was the single most
useful thing in a C trace — and it was an out-of-bounds read whenever the
pointer was not a NUL-terminated string. `put_one(const char *p)` called as
`put_one(&c)` on one char is ordinary, correct C; the wrapped copy read past `c`
to the first zero anywhere in memory. Proven with AddressSanitizer: the
unwrapped program is clean, the wrapped one reports
`stack-buffer-overflow ... READ of size 2` inside `vsnprintf` (C) and inside
`strlen` (C++). Instrumentation was introducing undefined behaviour into a
program that had none — the one thing this tool promises not to do — and it also
broke anyone running their tests under a sanitizer, which then reports the
wrapped program instead of their bug.

Both now print the address. Safe, and much less useful.

**Why it cannot simply be made safe.** No C type means "really a
NUL-terminated string". Bounding the read (`%.200s`, `strnlen`) does not help:
it still reads up to the bound, which is still past the end of a one-byte
object. Probing to the end of the page is practically safe but formally still
undefined, and a sanitizer still reports it — which defeats the point.

**What would actually work: make it opt-in.** A wrap-time flag (alongside
`minimal`) that turns string rendering back on, for a caller who knows their
`const char *` are strings. Default stays the safe rendering. The cost is
plumbing a second flag through `Transformer.wrap_source`, all six backends, the
CLI and the MCP tools — which is why it is written down here rather than done in
passing.

## More clangd LSP capabilities for navigation (candidate — we use only workspace/symbol so far)
- **Date:** 2026-06-15
- **Observed:** clangd's `initialize` advertises ~30 capabilities; we drive ONE
  (`workspace/symbol` → `symbol_search`) plus clang-tidy. The rest split cleanly:
  - **Mission-relevant (worth adding), best→least:**
    1. **Call hierarchy** (`textDocument/prepareCallHierarchy` + `callHierarchy/incomingCalls`
       / `outgoingCalls`) — who-calls-this / what-this-calls. The single best fit: it tells
       you exactly which functions to `wrap_functions` to capture a call path.
    2. **References** (`textDocument/references`) — every call site of a symbol (hotness,
       callers) — informs target choice and reading a trace's callers.
    3. **Document symbols** (`textDocument/documentSymbol`) — all symbols in ONE file;
       enumerate `wrap_functions` candidates per-file. Needs NO project index (cheaper/faster
       than workspace/symbol — just `didOpen` + request).
    4. **Definition / declaration / typeDefinition** — jump to where a symbol is defined.
    5. **Hover** — type/signature/doc of a symbol (mild context value).
  - **IDE-only, deliberately SKIP** (no place in a headless instrument→observe MCP):
    completion, formatting / onType / range formatting (we never reprint code — locate-then-
    splice), inlayHints, semanticTokens, foldingRange, rename, signatureHelp, selectionRange,
    documentHighlight, documentLink, codeAction/`clangd.applyTweak` (auto-refactor conflicts
    with our no-reprint principle).
- **Reuse:** the `_Clangd` JSON-RPC client + seed-`didOpen` + index-wait already exist in
  `clangtools/clangd.py`; call-hierarchy/references need a position (file+line+col) which
  documentSymbol or symbol_search can supply — so these compose.
- **Status:** DONE (2026-06-15) — built the four navigation tools (`document_symbols`,
  `references`, `call_hierarchy`, `describe_symbol`) in `clangtools/clangd.py`, wired into the
  MCP server + CLI (`doc-symbols`, `refs`, `callers`, `describe`). Callers pass a symbol NAME;
  the position is resolved internally via `documentSymbol` (no raw line/col). QA gate green
  (ruff + mypy-strict + 141 tests); 17 tools total, all titled/annotated; SEP-1303 intact. The
  IDE-only LSP features remain deliberately skipped (see list above). Verified live on gpu.
- **clangd version (RESOLVED 2026-06-15):** clangd 18 implemented `incomingCalls` but NOT
  `outgoingCalls` (`-32601`). Installed the **clangd 22.1.0 standalone** on gpu
  (`/opt/clangd_22.1.0`, symlinked `/usr/local/bin/clangd` — on the MCP's PATH, ahead of the
  distro `clangd-18`); `find_tool` prefers bare `clangd` so it's picked. `outgoing` now works
  live. The clean "build does not support outgoing" message remains as a graceful fallback for
  any host still on an older clangd. `find_tool` lists extended to clangd-22/21/20 +
  clang-tidy-22/21/20.
- **Index honesty + big trees (2026-06-15):** the index-dependent tools now return
  `index_complete` (False = clangd's background index didn't finish within `index_timeout`, so
  results may be PARTIAL — never a silent under-report) and expose `index_timeout` (raise it for
  a fresh huge tree like ROS ≈14.7k files; the on-disk index cache warms so a retry completes).

## C/C++ static-analysis diagnostics + smart cross-file symbol search (clangd / clang-tidy)
- **Date:** 2026-06-15
- **Needed:** two capabilities the MCP lacks for C/C++ work:
  1. **Static-analysis diagnostics** — surface real bugs (use-after-free, `if (a = b)`,
     dead stores, perf traps) in a file, not just the parse-error gate we already have.
  2. **Smart cross-file symbol search** — find symbols/definitions/references across a
     whole tree (NetBSD/ROS = ~14.7k files) to pick `wrap_functions` targets, instead of
     grepping raw text.
- **Why:** "кричит, что есть проблемы" + "умный поиск" — makes the MCP actually
  authoritative on C/C++ quality, and finds instrumentation targets fast. North-star is
  a top-tier MCP (see memory `ouroboros-godtier`).
- **Official tool:** **clangd** (LLVM's official C/C++ language server, LSP over stdio)
  and its embedded engine **clang-tidy**. Both use the SAME `compile_commands.json` that
  `treeflags.py` already discovers via `.ouroboros.json` → `compdb`. Strong seam.
- **Probe findings (2026-06-15, clang-tidy-18 on an instrumented .c):**
  - clang-tidy DOES catch bugs the libclang parse gate (`clangbridge.gate_diagnostics`) misses
    (`bugprone-assignment-in-if-condition`, `clang-diagnostic-parentheses`,
    `clang-analyzer-deadcode.DeadStores` on `if (a = b)`). Real marginal value.
  - Header resolution works IF we reuse the C backend's flags + `_drop_runtime_asset`
    + `-I _C_DIR` (no "ouroboros_runtime.h not found").
  - GOTCHA: our own instrumentation injects `__ouro`/`__ouro_result`, which trip
    `bugprone-reserved-identifier` (leading `__` is reserved in C). The tool MUST filter
    this self-inflicted noise — ideally a before/after diff (lint original vs instrumented,
    report only diagnostics NOT introduced by the wrap) so we never report phantom problems.
- **Plan (phased — Phase 2 needs explicit go-ahead):**
  - **Phase 1 — `lint_file` via clang-tidy SUBPROCESS (no LSP client needed):** reuse
    `treeflags` flags + runtime-asset drop + `-I _C_DIR`; run clang-tidy; parse diagnostics;
    filter/diff out the `__ouro*` reserved-id noise. Stateless, per-call — fits the
    "project re-opened per call" model. gpu already has `clang-tidy-18`/`clang-18` → no
    install needed. Highest value, lowest complexity.
  - **Phase 2 — `symbol_search` via clangd LSP (`workspace/symbol`):** needs a hand-rolled
    JSON-RPC/stdio client (~50 lines, no new dep), a background index (minutes to build) or
    a prebuilt `clangd-indexer` index, and a PERSISTENT clangd keyed on project root — this
    breaks the stateless-per-call model, so it's a real architectural decision. **gpu has NO
    clangd installed** → feature is inert on the ROS tree until clangd is installed there
    (like we installed OTP/Elixir).
- **Workaround used meanwhile:** none — recorded before building (per the standing rule),
  not routed around.
- **Implementation notes (clangd `workspace/symbol`):** clangd does NOT index the compile
  DB until a file is opened. The tool `didOpen`s the first compile-DB entry to kick off the
  tree-wide background index, replies to clangd's `window/workDoneProgress/create` server
  request (else it stalls), waits for the indexing `$/progress` `end`, then queries. The
  index persists to `.cache/clangd`, so later calls are fast (keeps the per-call model).
- **Status:** DONE — both phases shipped.
  - Tools: `lint_file` (clang-tidy subprocess) and `symbol_search` (clangd LSP), in
    `ouroboros/clangtools/` (`lint.py`, `clangd.py`, `flags.py`), wired into the MCP server +
    CLI (`lint`, `symbols`), tests in `tests/test_clangtools.py`. QA gate green (ruff +
    mypy-strict + 134 tests). clangd-18 installed on gpu; both verified live on the deployed
    package. (Live `mcp__ouroboros__*` exposes them after the client reconnects.)
