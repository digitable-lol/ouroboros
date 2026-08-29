"""Tests for the debug.info JSONL trace parser/query and the read_trace tool."""

from __future__ import annotations

import json

from ouroboros.mcp.server import tool_read_trace, tool_trace_stats
from ouroboros.trace import aggregate, load, parse, parse_timestamp, query


def _in(t, cid, name, a="", k="", *, ci=None, th=None):
    ev = {"p": "in", "t": t, "id": cid, "fn": name, "a": a, "k": k}
    if ci is not None:
        ev["ci"] = ci
    if th is not None:
        ev["th"] = th
    return json.dumps(ev) + "\n"


def _out(cid, name, *, r=None, x=None, d=0.0):
    o = {"p": "out", "id": cid, "fn": name}
    if x is not None:
        o["x"] = x
    else:
        o["r"] = r
    o["d"] = d
    return json.dumps(o) + "\n"


def _call(t, cid, name, a="", k="", *, r=None, x=None, d=0.0, ci=None, th=None):
    return _in(t, cid, name, a, k, ci=ci, th=th) + _out(cid, name, r=r, x=x, d=d)


SAMPLE = (
    _call("2026-06-15T10:00:00.001", "11111111-1111-4111-8111-111111111111",
          "add", "1, 2", r="3", d=0.000012)
    + _call("2026-06-15T10:00:00.002", "22222222-2222-4222-8222-222222222222",
            "Mod.boom", x="ValueError: nope", d=0.000004)
    + _call("uptime+5.123", "33333333-3333-4333-8333-333333333333",
            "pmap_segtab_activate", "pm=0xdead, l=0xbeef", r="(no value)", d=0.5)
)


def test_parse_basic():
    recs = parse(SAMPLE)
    assert len(recs) == 3
    assert recs[0].name == "add" and recs[0].args == "1, 2"
    assert recs[0].outcome_kind == "result" and recs[0].outcome == "3"
    assert recs[0].duration == 0.000012
    assert recs[1].outcome_kind == "raised" and "ValueError" in recs[1].outcome
    assert recs[2].name == "pmap_segtab_activate" and recs[2].duration == 0.5


def test_parse_ignores_surrounding_noise():
    """A kernel serial capture interleaves boot spam between JSONL lines."""
    noisy = ("[ 1.0] booting\n"
             + _call("2026-06-15T10:00:00.001", "aaaa1111-1111-4111-8111-111111111111",
                     "add", "1, 2", r="3", d=0.0)
             + "[ 2.0] random kernel msg\n"
             + "}{ torn line from concurrent printf\n")
    loaded = load(noisy)
    assert len(loaded.calls) == 1 and loaded.calls[0].name == "add"
    assert loaded.malformed == 3  # boot line, kernel msg, torn line


def test_exec_meta_record_is_not_malformed():
    text = SAMPLE + json.dumps({"p": "exec", "cmd": ["./a"], "rc": 0,
                                "out": "", "err": ""}) + "\n"
    loaded = load(text)
    assert len(loaded.calls) == 3 and loaded.malformed == 0


def test_in_flight_call_has_no_out():
    text = (_in("2026-06-15T10:00:00.001", "ffff1111-1111-4111-8111-111111111111", "hang")
            + _call("2026-06-15T10:00:00.002", "bbbb2222-2222-4222-8222-222222222222",
                    "done", r="ok", d=0.0))
    loaded = load(text)
    assert len(loaded.calls) == 1 and loaded.calls[0].name == "done"
    assert len(loaded.in_flight) == 1 and loaded.in_flight[0]["name"] == "hang"


def test_query_filters():
    recs = parse(SAMPLE)
    assert len(query(recs, function="pmap")) == 1
    assert len(query(recs, function="ADD")) == 1            # case-insensitive
    assert len(query(recs, outcome="raised")) == 1
    assert len(query(recs, contains="0xdead")) == 1
    assert query(recs, function="nonexistent") == []


def test_tool_read_trace_tail_and_limit(tmp_path):
    f = tmp_path / "debug.info"
    f.write_text(SAMPLE, encoding="utf-8")
    r = tool_read_trace(str(f))
    assert r["ok"] and r["calls_parsed"] == 3 and r["matched"] == 3

    r = tool_read_trace(str(f), function="pmap")
    assert r["matched"] == 1 and r["records"][0]["name"] == "pmap_segtab_activate"

    r = tool_read_trace(str(f), tail=1)
    assert r["returned"] == 1 and r["records"][0]["name"] == "pmap_segtab_activate"

    r = tool_read_trace(str(f), limit=2)
    # a full page with more remaining carries a next_cursor (the "more" signal)
    assert r["matched"] == 3 and r["returned"] == 2 and r["next_cursor"]


def test_tool_read_trace_pagination_walk(tmp_path):
    """Walking every page via next_cursor reproduces the unpaginated filtered set
    EXACTLY — no dupes, no gaps — and the last page has no next_cursor."""
    text = "".join(
        _call(f"2026-06-15T10:00:{i:02d}.000",
              f"{i:08d}-1111-4111-8111-111111111111", "f", str(i), r=str(i), d=0.0)
        for i in range(25))
    f = tmp_path / "debug.info"
    f.write_text(text, encoding="utf-8")

    full = tool_read_trace(str(f), limit=1000)["records"]
    assert len(full) == 25

    walked, cursor, pages = [], None, 0
    while True:
        r = tool_read_trace(str(f), cursor=cursor, limit=7)
        assert r["ok"]
        walked.extend(r["records"])
        pages += 1
        cursor = r["next_cursor"]
        if cursor is None:
            break
        assert pages <= 10  # 25/7 -> 4 pages; guard against a cursor that never ends
    assert walked == full                       # exact, in order
    ids = [rec["call_id"] for rec in walked]
    assert len(ids) == len(set(ids)) == 25      # no dupes, no gaps
    assert pages == 4


def test_tool_read_trace_bad_cursor_is_clean_error(tmp_path):
    f = tmp_path / "debug.info"
    f.write_text(SAMPLE, encoding="utf-8")
    r = tool_read_trace(str(f), cursor="not-a-real-cursor")
    assert r["ok"] is False and "invalid cursor" in r["error"]


def test_tool_read_trace_min_duration_and_regex(tmp_path):
    f = tmp_path / "debug.info"
    f.write_text(SAMPLE, encoding="utf-8")
    # only the 0.5s pmap call clears the slow-call threshold
    r = tool_read_trace(str(f), min_duration=0.1)
    assert r["returned"] == 1 and r["records"][0]["name"] == "pmap_segtab_activate"
    # regex across the qualified name
    r = tool_read_trace(str(f), function=r"^(add|Mod\.)", regex=True)
    assert {rec["name"] for rec in r["records"]} == {"add", "Mod.boom"}
    # a broken pattern is a clean error, not a crash
    r = tool_read_trace(str(f), function="(unclosed", regex=True)
    assert r["ok"] is False and "invalid regex" in r["error"]


def test_tool_read_trace_missing_file_is_clean_error(tmp_path):
    r = tool_read_trace(str(tmp_path / "nope.info"))
    assert r["ok"] is False and "cannot read" in r["error"]


def test_parse_timestamp_both_dialects():
    assert parse_timestamp("uptime+5.123") == 5.123
    iso = parse_timestamp("2026-06-15T10:00:00.500")
    iso2 = parse_timestamp("2026-06-15T10:00:01.500")
    assert iso is not None and round(iso2 - iso, 3) == 1.0
    assert parse_timestamp("garbage") is None


def test_aggregate_counts_and_real_durations():
    loaded = load(SAMPLE)
    agg = aggregate(loaded.calls, loaded.in_flight)
    assert agg["total_calls"] == 3
    names = {e["name"]: e for e in agg["by_function"]}
    assert names["add"]["count"] == 1 and names["add"]["result"] == 1
    assert names["Mod.boom"]["raised"] == 1
    # REAL per-call durations come straight off `d`
    assert names["pmap_segtab_activate"]["duration_seconds"]["max"] == 0.5
    assert agg["duration_seconds"]["count"] == 3
    assert agg["duration_seconds"]["max"] == 0.5
    # SAMPLE mixes ISO and uptime dialects; both parse, so timespan is present
    assert agg["timespan"]["timestamps_parsed"] == 3
    assert "REAL per-call durations" in agg["note"]


def test_aggregate_counts_repeated_calls():
    text = "".join(
        _call(f"2026-06-15T10:00:0{i}.000",
              f"{i}{i}{i}{i}{i}{i}{i}{i}-1111-4111-8111-111111111111",
              "f" if i % 2 else "g", str(i), r=str(i), d=0.1 * i)
        for i in range(4))
    loaded = load(text)
    agg = aggregate(loaded.calls, loaded.in_flight)
    by = {e["name"]: e["count"] for e in agg["by_function"]}
    assert by == {"f": 2, "g": 2}
    # 4 entries one second apart -> span 3.0s
    assert agg["timespan"]["seconds"] == 3.0


def test_tool_trace_stats_filtered(tmp_path):
    f = tmp_path / "debug.info"
    f.write_text(SAMPLE, encoding="utf-8")
    r = tool_trace_stats(str(f), outcome="raised")
    assert r["ok"] and r["total_calls"] == 1
    assert r["by_function"][0]["name"] == "Mod.boom"


# --- thread / CPU identity (the `ci`/`th` fields) ----------------------------

# two threads racing on two CPUs through the same function — the SMP-trace shape
THREADED = (
    _call("uptime+1.001", "a0000000-0000-4000-8000-000000000001",
          "pmap_update", "pmap=0xA", r="(no value)", d=0.001, ci=0, th="100.1")
    + _call("uptime+1.002", "a0000000-0000-4000-8000-000000000002",
            "pmap_update", "pmap=0xB", r="(no value)", d=0.002, ci=3, th="200.1")
    + _call("uptime+1.003", "a0000000-0000-4000-8000-000000000003",
            "pmap_remove_all", "pmap=0xA", r="(no value)", d=0.001, ci=1, th="100.1")
)


def test_parse_populates_cpu_and_thread():
    recs = parse(THREADED)
    assert (recs[0].cpu, recs[0].thread) == (0, "100.1")
    assert (recs[1].cpu, recs[1].thread) == (3, "200.1")
    # the field round-trips through as_dict (what read_trace returns)
    assert recs[1].as_dict()["thread"] == "200.1"
    assert recs[1].as_dict()["cpu"] == 3


def test_cpu_minus_one_and_missing_map_to_none():
    """Userland emits ci=-1 (unavailable); pre-thread-identity traces omit ci/th."""
    text = (_call("2026-06-15T10:00:00.0", "c0000000-0000-4000-8000-000000000001",
                  "u", r="0", d=0.0, ci=-1, th="4242")        # userland sink
            + _call("2026-06-15T10:00:00.1", "c0000000-0000-4000-8000-000000000002",
                    "v", r="0", d=0.0))                        # legacy: no ci/th
    recs = parse(text)
    assert recs[0].cpu is None and recs[0].thread == "4242"   # -1 -> None, pid kept
    assert recs[1].cpu is None and recs[1].thread == ""       # absent -> None/""


def test_query_by_thread_isolates_one_thread():
    recs = parse(THREADED)
    th100 = query(recs, thread="100.1")
    assert {r.name for r in th100} == {"pmap_update", "pmap_remove_all"}
    assert all(r.thread == "100.1" for r in th100)
    assert len(query(recs, thread="200.1")) == 1
    assert query(recs, thread="nope") == []


def test_aggregate_by_thread_groups_and_lists_cpus():
    agg = aggregate(parse(THREADED))
    bt = {e["thread"]: e for e in agg["by_thread"]}
    assert bt["100.1"]["count"] == 2          # two calls on thread 100.1
    assert bt["100.1"]["functions"] == 2      # across two distinct functions
    assert bt["100.1"]["cpus"] == [0, 1]      # thread 100.1 ran on CPU 0 then 1
    assert bt["200.1"]["count"] == 1 and bt["200.1"]["cpus"] == [3]
    assert "groups calls by the `th` token" in agg["note"]


def test_aggregate_by_thread_empty_without_identity():
    """A trace with no `th` field yields an empty by_thread (no phantom groups)."""
    agg = aggregate(parse(SAMPLE))
    assert agg["by_thread"] == []


def test_in_flight_carries_thread_identity():
    text = _in("uptime+9.0", "f0000000-0000-4000-8000-000000000001",
               "stuck", ci=2, th="500.3")
    loaded = load(text)
    assert loaded.in_flight[0]["cpu"] == 2 and loaded.in_flight[0]["thread"] == "500.3"


def test_tool_read_trace_thread_filter(tmp_path):
    f = tmp_path / "debug.info"
    f.write_text(THREADED, encoding="utf-8")
    r = tool_read_trace(str(f), thread="100.1")
    assert r["ok"] and r["matched"] == 2
    assert all(rec["thread"] == "100.1" for rec in r["records"])
    assert {rec["cpu"] for rec in r["records"]} == {0, 1}


def test_tool_trace_stats_by_thread(tmp_path):
    f = tmp_path / "debug.info"
    f.write_text(THREADED, encoding="utf-8")
    r = tool_trace_stats(str(f))
    bt = {e["thread"]: e for e in r["by_thread"]}
    assert bt["100.1"]["count"] == 2 and bt["100.1"]["cpus"] == [0, 1]


# --------------------------------------------------------------------------- #
# Defect: `matched[-tail:]` with tail == 0 is `matched[0:]` — the WHOLE list.
# "keep the last 0 matches" handed back every match instead of none.
# --------------------------------------------------------------------------- #


def test_tool_read_trace_tail_zero_returns_nothing(tmp_path):
    f = tmp_path / "debug.info"
    f.write_text(SAMPLE, encoding="utf-8")

    r = tool_read_trace(str(f), tail=0)

    assert r["ok"] is True
    assert r["matched"] == 3          # the filters still matched all three
    assert r["returned"] == 0         # ...but the window keeps none of them
    assert r["records"] == []
    assert r["next_cursor"] is None


def test_tool_read_trace_negative_tail_is_rejected(tmp_path):
    """A negative tail used to fall through to "no window at all" and quietly
    return every match — the opposite of what the caller asked for."""
    f = tmp_path / "debug.info"
    f.write_text(SAMPLE, encoding="utf-8")

    r = tool_read_trace(str(f), tail=-1)

    assert r["ok"] is False
    assert "tail" in r["error"]


def test_tool_read_trace_tail_larger_than_match_count(tmp_path):
    """A tail bigger than the trace is not an error — it just keeps everything."""
    f = tmp_path / "debug.info"
    f.write_text(SAMPLE, encoding="utf-8")

    r = tool_read_trace(str(f), tail=99)

    assert r["ok"] is True and r["returned"] == 3


# --------------------------------------------------------------------------- #
# What the parser does with lines that are not whole records.
#
# The sink promises one record per append, under PIPE_BUF, so several processes
# can share one debug.info. When that promise is broken — an older capture, a
# serial console, a writer that is not ours — the parser has to say how many
# lines it could not use, not quietly drop them: a silent drop reads exactly
# like "the function was never called".
# --------------------------------------------------------------------------- #

def test_a_torn_line_is_counted_not_dropped():
    loaded = load(SAMPLE + '{"p":"out","id":"44444444-4444-4444-8444-4444444')

    assert len(loaded.calls) == 3
    assert loaded.malformed == 1


def test_blank_lines_are_not_malformed():
    """A trailing newline, or the blank line a shell adds, is not a lost record."""

    loaded = load("\n   \n\t\n" + SAMPLE + "\n\n")

    assert len(loaded.calls) == 3
    assert loaded.malformed == 0


def test_an_out_record_that_says_neither_result_nor_exception():
    """A completion carrying no `r` and no `x` — a truncated write, or a sink
    that stopped mid-record. The call did finish, so it is a call; what it
    answered is unknown, and `unknown` is what the aggregate must say."""

    text = (_in("2026-06-15T10:00:00.001", "55555555-5555-4555-8555-555555555555", "f")
            + json.dumps({"p": "out", "id": "55555555-5555-4555-8555-555555555555",
                          "fn": "f", "d": 0.1}) + "\n")

    loaded = load(text)

    assert loaded.malformed == 0
    assert loaded.calls[0].outcome_kind == "" and loaded.calls[0].outcome == ""
    stats = aggregate(loaded.calls)
    assert stats["by_function"][0]["unknown"] == 1
    assert stats["by_function"][0]["result"] == 0


def test_an_unparseable_uptime_is_no_timestamp_rather_than_a_crash():
    """The kernel sink writes `uptime+SEC.MS`. A capture that lost the digits
    still has usable call records; only its place on the clock is gone."""

    assert parse_timestamp("uptime+5.5") == 5.5
    assert parse_timestamp("uptime+") is None
    assert parse_timestamp("uptime+later") is None


def test_contains_can_be_a_regular_expression():
    """`--contains` searches args, kwargs and outcome. As a regex it is the way
    to ask for a shape — a pointer, an errno — rather than a literal."""

    recs = parse(SAMPLE)

    hits = query(recs, contains=r"0x[0-9a-f]+", regex=True)

    assert [r.name for r in hits] == ["pmap_segtab_activate"]
    assert query(recs, contains=r"0x[0-9a-f]+", regex=False) == []


def test_a_function_whose_calls_carry_no_duration_gets_no_duration_block():
    """`d` is missing in traces from a sink that predates it. Reporting a
    made-up zero there would read as "instant", which is a different claim."""

    text = (_in("2026-06-15T10:00:00.001", "66666666-6666-4666-8666-666666666666", "f")
            + json.dumps({"p": "out", "id": "66666666-6666-4666-8666-666666666666",
                          "fn": "f", "r": "1"}) + "\n")

    stats = aggregate(load(text).calls)

    assert stats["by_function"][0]["count"] == 1
    assert stats["by_function"][0]["duration_seconds"] is None
    assert stats["duration_seconds"] is None


def test_a_thread_that_reported_no_cpu_is_still_grouped():
    """`ci` is absent on every platform without `sched_getcpu`. The thread view
    is still the point; the CPU column is simply empty."""

    text = _call("2026-06-15T10:00:00.001", "77777777-7777-4777-8777-777777777777",
                 "f", r="1", d=0.1, th="10.20")

    stats = aggregate(load(text).calls)

    assert stats["by_thread"] == [
        {"thread": "10.20", "count": 1, "functions": 1, "cpus": []}]


def test_no_timespan_when_nothing_carries_a_readable_timestamp():
    text = _call("", "88888888-8888-4888-8888-888888888888", "f", r="1", d=0.1)

    stats = aggregate(load(text).calls)

    assert stats["total_calls"] == 1
    assert stats["timespan"] is None
