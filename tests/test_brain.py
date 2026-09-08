"""Tests for the flang brain and the one bridge that carries values across.

Three jobs here, and only the first is ordinary unit testing.

1. The bridge itself: what a Python value becomes in flang and back.
2. The two places where a rule lives in flang and a faster copy of it runs in
   Python. Those copies are the only way the two halves can drift apart without
   anything failing, so each one is held against the rule it copies:
   ``load``'s hash index against ``«Is in flight»``, and the sink's record
   bounding against ``«Longest field»``/``«Halved»``/``«Within shrink budget»``.
3. The stub the type checker reads for the printed runtime, against the printed
   runtime itself — a partial stub that quietly stops matching would make
   ``mypy --strict`` agree with a module that no longer has those names.
4. The stamp that says which source the committed print was made from. It is
   the only check on the print that runs without a compiler, so it is the only
   one that runs everywhere, and it is what catches an edit the print never
   sees.
"""

from __future__ import annotations

import json

import pytest

from ouroboros import runtime as sink
from ouroboros.brain import Brain, Variant, from_flang, to_flang
from ouroboros.brain._flang import flang_runtime as rt
from ouroboros.trace import load

BRAIN = Brain()

STUB = "ouroboros/brain/_flang/flang_runtime.pyi"


# ── the bridge ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [None, "text", "", 0.5, -3.0, True, False,
                                   [], [1.0, "two", False],
                                   {}, {"a": 1.0, "b": ["c", None]}])
def test_a_value_survives_the_round_trip(value):
    assert from_flang(to_flang(value)) == value


def test_an_integer_comes_back_as_a_float():
    """flang has no integers, and the bridge does not pretend otherwise."""

    back = from_flang(to_flang(7))
    assert back == 7.0
    assert isinstance(back, float)


def test_a_flag_is_not_a_number():
    """In Python ``True`` is an ``int``; in flang a flag and a number differ."""

    assert to_flang(True).tag == rt.TAG_FLAG
    assert to_flang(1).tag == rt.TAG_NUMBER


def test_a_string_is_not_a_sequence_of_strings():
    assert to_flang("ab").tag == rt.TAG_STRING


def test_a_value_with_no_flang_shape_is_refused():
    with pytest.raises(TypeError, match="no flang value for object"):
        to_flang(object())


def test_a_variant_arrives_with_its_name_and_fields():
    got = from_flang(rt.variant("Lasted", {"seconds": rt.number(0.5)}))
    assert got == Variant("Lasted", {"seconds": 0.5})


def test_an_unknown_tag_is_refused():
    """Unreachable from the brain — every tag it produces is handled — so the
    branch is reached the only way it can be, by making the value by hand."""

    with pytest.raises(TypeError, match="unknown flang tag"):
        from_flang(rt.Value(99))


# ── what the brain says about one line and one event ──────────────────────────


@pytest.mark.parametrize(("line", "kind"), [
    ('{"p":"in"}', "Candidate"),
    ('   {"p":"in"}  ', "Candidate"),
    ("[ 1.0] booting", "Malformed"),
    ("}{ torn line", "Malformed"),
    ("", "Blank"),
    ("  \t ", "Blank"),
])
def test_the_brain_sorts_a_line(line, kind):
    assert BRAIN.line_kind(line) == kind


@pytest.mark.parametrize(("phase", "kind"), [
    ("in", "Entered"), ("out", "Returned"), ("exec", "Ignored"),
    (None, "Ignored"), (7, "Ignored"),
])
def test_the_brain_sorts_an_event(phase, kind):
    assert BRAIN.event_kind(phase) == kind


def test_an_exit_with_no_entry_keeps_its_own_name():
    call = BRAIN.completed_call(
        3, BRAIN.no_entry(),
        BRAIN.exit({"id": "b2", "fn": "Mod.boom", "x": "ValueError: nope"}))
    assert call == {"index": 3, "started": "", "call_id": "b2", "name": "Mod.boom",
                    "args": "", "kwargs": "", "outcome_kind": "raised",
                    "outcome": "ValueError: nope", "duration": None, "cpu": None,
                    "thread": ""}


def test_an_exit_pairs_with_the_entry_that_repeats_its_id():
    entry = BRAIN.entry({"id": "a1", "fn": "add"})
    assert BRAIN.same_call(entry, BRAIN.exit({"id": "a1", "fn": "add"}))
    assert not BRAIN.same_call(entry, BRAIN.exit({"id": "b2", "fn": "add"}))


# ── the index the reader uses, against the rule it copies ─────────────────────


def _line(**fields):
    return json.dumps(fields) + "\n"


TRACES = [
    # everything paired
    _line(p="in", t="1", id="a1", fn="add", a="1, 2", k="")
    + _line(p="out", id="a1", fn="add", r="3", d=0.1),
    # one entry never came back
    _line(p="in", t="1", id="a1", fn="add") + _line(p="out", id="a1", fn="add", r="3")
    + _line(p="in", t="2", id="c3", fn="hang", ci=2, th="4242.7"),
    # an exit whose entry the capture never wrote, and a repeated id
    _line(p="out", id="lost", fn="orphan", x="Boom: nope")
    + _line(p="in", t="3", id="dup", fn="twice")
    + _line(p="in", t="4", id="dup", fn="twice")
    + _line(p="out", id="dup", fn="twice", r="ok"),
    # nothing but noise
    "[ 1.0] booting\n}{ torn\n\n",
]


@pytest.mark.parametrize("text", TRACES)
def test_the_reader_index_agrees_with_the_brain(text):
    """``load`` answers "in flight?" with a hash set; the brain answers it by
    scanning the completed ids. Same question, and the answers have to match."""

    loaded = load(text)
    completed = [call.call_id for call in loaded.calls]
    brain = Brain()
    by_rule = []
    for line in text.splitlines():
        if brain.line_kind(line) != "Candidate":
            continue
        event = json.loads(line)
        if brain.event_kind(event.get("p")) != "Entered":
            continue
        entry = brain.entry(event)
        if brain.is_in_flight(entry, completed):
            by_rule.append(brain.flight_view(entry))
    assert loaded.in_flight == by_rule


# ── the limits, against the sink that enforces them ───────────────────────────


def test_the_brain_and_the_sink_agree_on_the_limits():
    assert BRAIN.value_characters_limit == sink._repr.maxstring
    assert BRAIN.value_characters_limit == sink._repr.maxother
    assert BRAIN.list_items_limit == sink._repr.maxlist
    assert BRAIN.list_items_limit == sink._repr.maxdict
    assert BRAIN.list_items_limit == sink._repr.maxtuple
    assert BRAIN.record_bytes_limit == sink.MAX_RECORD_BYTES
    assert BRAIN.shrink_budget == sink.MAX_RECORD_BYTES - 64


@pytest.mark.parametrize("value", ["x" * 5000, "short", "", "\n" * 300])
def test_the_sink_never_renders_a_string_the_brain_would_still_cut(value):
    """The per-value limit is one rule with two enforcers; a string that came out
    of the sink is already short enough that the brain leaves it alone."""

    rendered = sink._repr.repr(value)
    assert len(rendered) <= BRAIN.value_characters_limit
    assert BRAIN.shorten_text(rendered, BRAIN.value_characters_limit) == rendered


@pytest.mark.parametrize("value", [["item"] * 500, tuple(range(400)),
                                   list(range(9)), {"a": 1}])
def test_the_sink_keeps_a_container_to_the_item_limit(value):
    """What the item limit promises is items, not characters."""

    rendered = sink._repr.repr(value)
    assert rendered.count(", ") <= BRAIN.list_items_limit


def test_a_container_of_long_values_still_outgrows_the_value_limit():
    """The one thing neither limit promises, written down rather than assumed.

    Ten items each of two hundred characters is two thousand characters, and
    nothing in the per-value rules says otherwise: what actually bounds a
    written record is the ceiling and the halving, and that is why they exist.
    """

    rendered = sink._repr.repr({"k": "v" * 300})
    assert len(rendered) > BRAIN.value_characters_limit
    assert BRAIN.shorten_text(rendered, BRAIN.value_characters_limit) != rendered


@pytest.mark.parametrize(("text", "limit", "want"), [
    ("abcdefghij", 7, "ab...ij"),
    ("abcdefghij", 8, "ab...hij"),
    ("abcdefghij", 3, "..."),
    ("abcde", 5, "abcde"),
    ("", 5, ""),
])
def test_a_rendered_value_is_cut_in_the_middle(text, limit, want):
    assert BRAIN.shorten_text(text, limit) == want


@pytest.mark.parametrize(("pieces", "limit", "want"), [
    (["1", "2"], 10, ["1", "2"]),
    (["1", "2", "3"], 2, ["1", "2", "..."]),
    ([], 2, []),
])
def test_a_rendered_list_keeps_its_first_items(pieces, limit, want):
    assert BRAIN.shorten_items(pieces, limit) == want


# ── the record bounding, against the sink that copies it ──────────────────────

#: The brain names a field; the sink calls it by its key in the record.
_FIELD_KEY = {"ArgsField": "a", "KwargsField": "k",
              "ProducedField": "r", "RaisedField": "x"}


def _bounded_by_the_brain(record):
    """``runtime._bounded`` with every decision taken by the brain instead."""

    line = sink._encode(record)
    if BRAIN.record_fits(len(line.encode("utf-8"))):
        return line
    record = dict(record)
    trimmed = set()

    def four():
        return (record.get("a", ""), record.get("k", ""),
                record.get("r", ""), record.get("x", ""))

    while BRAIN.anything_to_shrink(*four()):
        key = _FIELD_KEY[BRAIN.longest_field(*four())]
        record[key] = BRAIN.halved(record.get(key, ""))
        trimmed.add(key)
        if BRAIN.within_shrink_budget(len(sink._encode(record).encode("utf-8"))):
            break
    for key in trimmed:
        record[key] += "…"
    return sink._encode(record)


@pytest.mark.parametrize("record", [
    {"p": "in", "t": "1", "id": "a1", "fn": "f", "a": "x" * 9000, "k": ""},
    {"p": "in", "t": "1", "id": "a1", "fn": "f", "a": "x" * 30, "k": "y" * 30},
    {"p": "in", "t": "1", "id": "a1", "fn": "f",
     "a": ", ".join("z" * 200 for _ in range(30)), "k": "k" * 3000},
    {"p": "out", "id": "a1", "fn": "f", "r": "r" * 8000, "d": 0.5},
    {"p": "out", "id": "a1", "fn": "f", "x": "x" * 5000, "d": 0.5},
])
def test_the_sink_bounds_a_record_the_way_the_brain_says(record):
    assert sink._bounded(record) == _bounded_by_the_brain(record)


def test_a_tie_between_two_fields_goes_to_the_earlier_one():
    assert BRAIN.longest_field("aa", "kk", "", "") == "ArgsField"
    assert BRAIN.longest_field("", "kk", "rr", "") == "KwargsField"
    assert BRAIN.longest_field("", "", "rr", "xx") == "ProducedField"
    assert BRAIN.longest_field("", "", "", "xx") == "RaisedField"


def test_halving_ends_at_nothing():
    assert BRAIN.halved("abcd") == "ab"
    assert BRAIN.halved("a") == ""
    assert not BRAIN.anything_to_shrink("", "", "", "")


def test_the_ceiling_is_inclusive():
    assert BRAIN.record_fits(BRAIN.record_bytes_limit)
    assert not BRAIN.record_fits(BRAIN.record_bytes_limit + 1)
    assert BRAIN.within_shrink_budget(BRAIN.shrink_budget)
    assert not BRAIN.within_shrink_budget(BRAIN.shrink_budget + 1)


# ── the stub the type checker reads ───────────────────────────────────────────


def test_the_printed_runtime_still_has_everything_the_stub_declares():
    """The runtime stub is written by hand and the runtime is printed by the
    compiler. An upgrade that renames one of these names has to fail here, not
    be silently believed by ``mypy --strict``."""

    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / STUB).read_text(encoding="utf-8")
    declared = (set(re.findall(r"^def (\w+)\(", text, re.M))
                | set(re.findall(r"^class (\w+)", text, re.M))
                | set(re.findall(r"^(\w+): int$", text, re.M)))
    assert declared, "the stub declares nothing — the pattern above stopped matching"
    missing = sorted(name for name in declared if not hasattr(rt, name))
    assert not missing, f"the printed runtime no longer has: {missing}"


# ── the stamp that ties the print to its source ───────────────────────────────


def _guard():
    """``scripts/emit_brain.py`` as a module. Not on the import path: it is a
    script, and importing it from a test is the only reason it would need to be
    a package."""

    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "emit_brain.py"
    spec = importlib.util.spec_from_file_location("emit_brain", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_print_says_which_source_it_was_made_from():
    """The digest beside the print, against the source as it stands.

    ``--check`` also prints again and compares byte for byte, but only where the
    pinned compiler is installed — which is nowhere in CI. This half needs no
    compiler, so it is the half that always answers."""

    assert _guard().stale_stamp() == []


def test_a_source_the_print_was_not_made_from_is_refused(tmp_path):
    """The negative control, kept as a test rather than as a memory.

    A ``note`` never reaches the print: the compiler drops it, the printed bytes
    do not move, and comparing prints therefore says the tree is current when
    the source has changed. That was true here and went unnoticed. The digest is
    what notices, and this is the case that proves it still can."""

    guard = _guard()
    moved = tmp_path / guard.SOURCE.name
    moved.write_bytes(guard.SOURCE.read_bytes()
                      + b'\nnote "a term that never reaches the print"\n')
    findings = guard.stale_stamp(moved)
    assert findings, "a changed source was called current"
    assert guard.SOURCE.name in findings[0]
    assert guard.STAMP.name in findings[0]
