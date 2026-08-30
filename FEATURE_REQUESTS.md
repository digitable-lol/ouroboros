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

## Rust as the ninth language

- **Date:** 2026-08-30
- **Status:** open — reconnaissance done and working, nothing written into this tree yet.
- **Numbers below:** every one came off a run. The ones marked *(re-measured)* were
  taken again here, independently of the reconnaissance; the rest are the
  reconnaissance's own and are attributed where they are used.

**What is already proven.** A 122-line program, 14 functions wrapped, built with no
warnings, wrapped and unwrapped stdout byte-identical, 27 calls recorded, 0 in
flight, 0 malformed lines. Checked with our own reader rather than by eye
*(re-measured)*: `ouroboros trace` on the probe's `debug.info` answers
`calls_parsed: 27, malformed: 0, in_flight: []`.

### What to take, and where it lies

All of it sits under `/srv/tmp/razvedka-rust/`, **which is scratch space and gets
wiped — move it into the tree before anything else:**

| piece | file | lines |
| --- | --- | --- |
| boundary emitter, on `syn` | `emitter/src/main.rs` | 281 |
| record helper | `ouroboros_runtime.rs` | 378 |
| wrapper (locate-then-splice, reuses our `apply_edits`) | `wrap.py` | 93 |
| 18 hard cases | `trudno/orig.rs` | 132 |

The backend to copy is **Go, not Java or C#**: the Go helper also has to enter
someone else's tree. Sizes of the two nearest backends, code plus assets plus tests
*(re-measured)*: Go 1813 lines, C# 2188. Rust should land at 2200–2800 lines across
13–15 files — C# plus about a quarter.

### The five findings that cost money

1. **Wrapping one file requires editing another.** The helper needs a `mod` line in
   the crate root (`src/main.rs` or `src/lib.rs`); placed next to the wrapped file
   it resolves to the wrong path. Wrapping `mnogofailov/src/util.rs` worked only
   after `#[path = "ouroboros_runtime.rs"] mod __ouro_rt;` was added by hand to
   `src/main.rs`. **No other backend has this action.** Cost it as its own slice:
   150–250 lines with the checks — no crate root found, the line is already there,
   a crate carrying both `main.rs` and `lib.rs`.
2. **Wrapping a library changes its public face.** The helper's macros carry
   `#[macro_export]`, which puts them at the crate root and exports them. Shown with
   a consumer crate: `potrebitel` compiles `bibl::__ouro_repr!(&7i32)`, a macro that
   did not exist in `bibl` before the wrap. Fix: `pub(crate) use`.
3. **There is no local time in `std`.** The helper declares `localtime_r` as
   `extern "C"` and lays out `struct tm` by hand. As it stands it will not build on
   Windows. Carry that as unknown, not as solved.
4. **Under `-C panic=abort` there are no completions at all** — entries with no
   exits. `abort/debug.info` holds 2 entries and 0 exits *(re-measured)*. Document
   it the way the other languages document their holes.
5. **The parser does not come in the box.** `syn` sources have to be vendored.
   Measured here *(re-measured)*: `syn` 2.3 MB, and 3.0 MB with `proc-macro2`,
   `quote` and `unicode-ident`, all four pinned by the emitter's lockfile. The
   comparison in the reconnaissance note was wrong in our favour: the vendored
   `@babel/parser` is 2.0 MB, not 5.3, and the whole vendored `_js` tree is 4.8 MB.
   So Rust's parser is not cheaper than the JavaScript one — only cheaper than
   everything we vendor for JavaScript together.

### Known already — do not rediscover

- **Offsets are byte offsets** (`proc_macro2::Span::byte_range`) and Python must
  slice `bytes`, as it does for C and C++. Checked on characters outside the basic
  plane: the same offset counted in characters shifts exactly the way JavaScript
  used to corrupt files.
- **Gaps a full backend has to close.** Nested functions are not walked. `main` with
  a tail expression records `r: "()"` where it should record `(no value)`, the way
  Java, Go and C++ do *(re-measured: `run2/debug.info` shows `"r":"()"` for `main`)*.
  `const fn` is skipped on purpose — a helper call is not allowed in a const
  context; the reconnaissance counted 8444 of them in `std`.
- **`d` means something else for an async function**: it counts from the first poll
  and includes the idle between polls. A function that yields once around a 50 ms
  sleep records `d: 0.050` *(re-measured)*. Write that down, do not hide it.
- **stdout matches byte for byte, stderr does not.** A panic message carries the
  source file name, so the probe's `orig.rs` and `prog.rs` differ there — but line
  and column are identical (`93:9`), so the wrap shifts no line numbers. Wrap in
  place, and pin stderr with a test of its own.
- **Keep `cargo clippy -- -D warnings` in the set.** The reconnaissance hit a refusal
  on the helper itself. As the helper now stands both wrapped crates pass clippy
  *(re-measured, from a clean `target/`)* — the check stays anyway, because the
  helper lands in other people's crates, where clippy is a gate we do not control.

### Speed

| | Rust | C |
| --- | --- | --- |
| per wrapped call, microseconds | 6.3 | 18.6 |

*(re-measured)*: same binaries, 20 003 calls, median of five runs each, both within
the same minute on this machine. The published C figure is 19.9, so the machine is
comparable. Rust would be the cheapest of the nine. Parsing one file with `syn`:
2.6 ms median on the 132-line hard-case file, against the published 322.8 ms for
Java on a 202-line file — different files, but two orders apart.

### What checks it

`scripts/qa.sh` as it stands, plus `cargo clippy -- -D warnings` on a wrapped crate,
plus a wrapped-versus-unwrapped byte comparison of **both** stdout and stderr.

### What counts as done

- Ninth language in `ouroboros languages`, wired into the CLI and the MCP server.
- **100% of statements and branches on the new backend.** Java's and C#'s are at
  exactly that *(re-measured: all 29 measured files are at 100%)*. Every refusal
  branch is driven by a test: no `cargo`, the parser fails to build, the parser
  crashes, the parser prints something that is not JSON, parsing is refused, and
  every variable of the walk. This is the bulk of the work, not the wrapping.
- `docs/languages.md` shows Rust beside the other eight, from a real run.
- `docs/measurements.md` carries a Rust column, measured, not copied from here.
- `scripts/state_numbers.py --measure` re-run and merged, and
  `scripts/check_pages_live.py` answering 0 over HTTP.
- Packaging and the release notes say nine languages.

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
