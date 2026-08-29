# Benchmark results — Experiment B′ (opaque-state debugging), 3 arms

**TL;DR.** With a clean control arm, the tool's contribution is **isolated and
clearly positive in the right comparison, but not a free win overall.** When the
agent debugs with a runtime-trace strategy, getting that trace from the tool
(auto `debug.info`) is far cheaper than producing it by hand (print statements):
**−52% output tokens, −36% wall-clock, −14% cost** vs the same strategy without
the tool. *But* on this small, readable program, just reading the code (the
no-hint baseline) beats both trace-based approaches — so the tool pays off only
when a trace is actually needed. All 9 runs succeeded and found the same bug.

## Why three arms

Experiment 1 (transparent output) → zero benefit. A first B′ pass (baseline vs
ouroboros) looked positive, but the ouroboros arm's system prompt carried a
stage-by-stage debugging *strategy* the baseline lacked — so "tool" and
"strategy hint" were fused. A control isolates them:

| arm        | strategy hint | the tool (auto trace) |
|------------|---------------|------------------------|
| baseline   | no            | no                     |
| strategy   | **yes**       | no                     |
| ouroboros  | yes           | **yes**                |

- `strategy` vs `baseline` → isolates the **hint**.
- `ouroboros` vs `strategy` → isolates the **tool** (same strategy; only the
  trace source differs: hand-written prints vs auto `debug.info`).

## Numbers (claude-sonnet-4-6, N=3/arm, all ✅ sample+hidden)

| arm | turns | output tok (range) | cost USD | wall-clock ms (range) | debug.info |
|-----|-------|--------------------|----------|-----------------------|-----------|
| baseline  | 6.3 | 967 (727–1386)  | 0.1100 | 20466 (19091–21455) | — |
| strategy  | 7.0 | 1653 (1614–1673)| 0.1253 | 26385 (23965–30118) | — |
| ouroboros | 6.0 | 789 (733–828)   | 0.1083 | 17031 (16246–17481) | 1662 ch |

## Findings

1. **The tool, isolated, is a clean win — no tail artifact.** `ouroboros` vs
   `strategy` (identical strategy, only the trace source differs): output tokens
   789 vs 1653 (**−52%**), wall-clock 17.0 s vs 26.4 s (**−36%**), cost \$0.108
   vs \$0.125 (**−14%**). The ranges don't overlap on any metric — this is a
   level effect, not luck. Reading a 1662-char `debug.info` is much cheaper than
   the agent instrumenting every function with prints and re-running.
2. **The strategy hint alone *hurt*.** `strategy` vs `baseline`: +71% output
   tokens (1653 vs 967), +29% wall-clock, +\$0.015. Told to check each stage's
   output, the agent hand-instrumented the whole pipeline — expensive precisely
   because it lacked the tool. This is the apples-to-apples case the tool wins.
3. **But plain code-reading beat *both* trace approaches.** The no-hint
   `baseline` just read 5 short functions, spotted `max`→`sum`, and fixed it —
   cheaper than `ouroboros` on cost (\$0.110 vs \$0.108 ≈ tie) and not far on
   tokens. On code this small, a trace is unnecessary, so the cheapest path is to
   not trace at all.
4. **The read cost is real but is dwarfed by the cost of *manual* tracing.**
   Against "read the code" the tool is ~neutral; against "trace by hand" it is
   decisively cheaper. The honest framing depends on which alternative you
   compare to.

## Honest boundary

- **Proven:** if a bug needs a runtime trace to localize, the tool delivers that
  trace far more cheaply than the agent doing it by hand (prints) — a clean,
  controlled ~2× output-token and ~1.5× wall-clock advantage.
- **Also proven:** on small, readable code a trace isn't needed at all, and
  code-reading beats every trace-based approach — so the tool is not a blanket
  win; it's conditional on the trace being *worth* producing.
- **Still untested (the genuinely favorable regime):** large/opaque systems
  where code-reading does not scale, so *some* trace is mandatory. There the
  baseline collapses toward the `strategy` arm (you must instrument), and the
  tool's "auto-trace vs hand-trace" advantage (Finding 1) should dominate. The
  `debug.info` read cost also grows with program size, so the net at scale is an
  open question this harness can't reach cheaply.
- **Caveats:** N=3 (per-run ranges reported, not just means); headless wall-clock
  is noisy though the arm separation here is wide and consistent; tool use was
  forced (pre-instrumented + directed) — Experiment 1 showed it is not adopted
  spontaneously when a cheaper check exists.

## Bottom line for the project

The tool earns its place exactly where it was designed to: **trace-necessary
debugging**, as a cheaper substitute for manual instrumentation — not as a
general accelerant. It is overhead on transparent or small-and-readable tasks.
The next worthwhile measurement is a genuinely large/opaque codebase, which is
where Findings 1 and the read-cost growth actually trade off.
