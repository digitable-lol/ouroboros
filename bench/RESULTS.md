# Benchmark results — Ouroboros-Logger value, first pass

**TL;DR — honest negative, with a precise boundary.** On a task whose failures
are fully diagnosable from the final output, the agent did **not** adopt the
instrumentation tool — and that was the *rational* choice, because a complete
output oracle was available. With no opaque intermediate state to inspect,
function-boundary logs were redundant. Both arms succeeded 100%; the only
difference was a harness artifact (a larger system prompt). This says nothing
about the tool's designed niche: opaque, stateful systems where the wrong value
is born mid-pipeline and the final output doesn't reveal which function did it.

## Setup

- **Task** (frozen before runs): `bench/task/spec.md` — a multi-stage billing
  pipeline (parse → normalize units → rate → aggregate → tiered discount → round
  → sort). Failure class = wrong-value-propagation between functions.
- **Arms**, identical model / prompt / tools except one variable:
  - `baseline` — Bash/Read/Write/Edit/Glob/Grep.
  - `ouroboros` — same tools + a system-prompt section describing the `ouroboros
    wrap-file` → run → read `debug.info` workflow + SKILL.md, and the runtime
    helper pre-seeded in the workdir.
- **Model:** claude-sonnet-4-6. **N = 3 per arm**, interleaved (cache fairness).
- **Success judged first:** the agent's `report.py` must reproduce `sample.log`
  output AND a held-out `hidden.log` (catches hardcoding). Tokens compared only
  across equal outcomes.
- Harness: `bench/run_bench.py`. Raw per-run JSON under `bench/runs/`.

## Per-run numbers

| arm | run | success | turns | output tok | cache_creation tok | cost USD | dur ms | used tool? | debug.info chars |
|-----|-----|---------|-------|-----------|--------------------|----------|--------|-----------|------------------|
| baseline  | 1 | ✅ | 7 | 2116 | 11860 | 0.144 | 31221 | no | 0 |
| ouroboros | 1 | ✅ | 7 | 2199 | 30948 | 0.255 | 36011 | no | 0 |
| baseline  | 2 | ✅ | 7 | 1942 | 11738 | 0.140 | 40033 | no | 0 |
| ouroboros | 2 | ✅ | 7 | 1929 | 12143 | 0.152 | 33098 | no | 0 |
| baseline  | 3 | ✅ | 7 | 2097 | 11842 | 0.143 | 28880 | no | 0 |
| ouroboros | 3 | ✅ | 7 | 2262 | 12484 | 0.150 | 33142 | no | 0 |

Means: baseline output 2052 tok / $0.142 / 33.4 s; ouroboros output 2130 tok /
$0.186 / 34.1 s. (The ouroboros cost mean is skewed by run 1's cold
cache-creation of the larger system prompt; output tokens — the real work — are
within noise of baseline.)

## Findings

1. **No adoption — but rationally so.** 0/3 ouroboros runs touched the tool
   (`report.py` never instrumented, `debug.info` never written). Sonnet wrote
   `report.py` directly and verified against `sample_expected.csv` — like
   baseline. This is not "models refuse the tool": with a complete output oracle
   available and the tool a heavier instrument-and-read path, skipping it is the
   correct choice. The narrow, evidence-backed claim: **the model did not adopt
   the tool when a direct output check was sufficient.** Adoption where the
   output is *not* a sufficient oracle is untested here.
2. **The failures were transparent, not the task "easy".** The real axis is not
   difficulty — it is whether a failure is diagnosable from the final output (or
   a traceback). This task is a pure input→output transform with the expected
   output handed over, so the loop is "diff CSV vs expected, fix." A logged
   `parse_line('acme storage 1500 GB') → ('acme', 30.0)` is exactly what the
   final CSV already shows — redundant. A *bigger* billing pipeline has the same
   property; scaling it up would reproduce this negative, not escape it.
3. **The "overhead" is a harness artifact.** The one measurable difference was
   run 1's cold cache-creation of the larger system prompt (we injected SKILL.md
   there) — our choice, not an intrinsic tool cost. The honest line: **output
   tokens — the real work — were within noise; both arms did the same thing.**

## Honest boundary of the claim

What is **proven**: function-boundary logging earns nothing on transparent
input→output tasks whose failures are visible in the final output, and a capable
model will not reach for it there.

What is **untested**: its designed niche — large/stateful systems where runtime
data flow is *opaque* and the wrong value is born mid-pipeline without surfacing
in the final output or a traceback. This benchmark cannot speak to that, because
the task it used has the opposite shape.

To measure *value-when-used* the next task must have **opaque intermediate
state + forced tool use**, gated by a **free pre-check**: take a seeded
wrong-value bug, look at the final output and the traceback, and ask "can I
localize this *without* runtime logs?" If yes, the tool cannot win — don't spend.
Only run if the bug is genuinely invisible from output + traceback, and net the
debug.info *read* cost against the debugging saved.

## Qualitative aside

The 8 MCP tools are *deferred* in a nested headless `claude` (loaded via
ToolSearch). A capable model handles it; a weak one (haiku) flailed and claimed
it "couldn't call MCP tools." This is why arm B used the CLI, not MCP — to
measure the instrument→observe workflow rather than tool-loading plumbing.
