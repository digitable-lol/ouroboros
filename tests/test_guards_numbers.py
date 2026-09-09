"""The guards that keep printed numbers equal to measured ones.

`state_numbers.py` compares the marks in the pages with `docs/state.json` and
with the coverage report the test run has just written; `schema_facts.py` does
the same for the record-field table; `emit_brain.py` compares the committed print
of the flang brain with a fresh one. Between them they hold every number in the
documentation that a person did not type.

The tests below drive them against trees of their own, and against a compiler
that is absent, of the wrong version, or angry — the states a machine other than
this one is in.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import emit_brain as emit_mod
import schema_facts as facts_mod
import state_numbers as state_mod

# --------------------------------------------------------------------------- #
# scripts/schema_facts.py
# --------------------------------------------------------------------------- #

WORDS = {"head": "| language | a | k |", "empty": "empty",
         "unavailable": "not measured on this machine", "unavailable_short": "not measured"}


def test_a_language_with_no_toolchain_is_named_as_not_measured():
    table = facts_mod.render({"python": {"unavailable": "no python"}}, WORDS)
    assert "| Python | not measured on this machine | not measured |" in table


def test_a_language_absent_from_the_measurement_reads_the_same():
    table = facts_mod.render({}, WORDS)
    assert table.count("not measured on this machine") == len(facts_mod.TITLES)


def test_an_empty_field_is_the_word_empty_not_an_empty_cell():
    table = facts_mod.render({"python": {"a": "2, 3", "k": ""}}, WORDS)
    assert "| Python | `2, 3` | empty |" in table


@pytest.fixture
def facts_tree(tmp_path, monkeypatch):
    page = tmp_path / "languages.md"
    facts = tmp_path / "schema-facts.json"
    monkeypatch.setattr(facts_mod, "ROOT", tmp_path)
    monkeypatch.setattr(facts_mod, "FACTS", facts)
    monkeypatch.setattr(facts_mod, "PAGES", {page: WORDS})
    return page, facts


def test_the_table_check_needs_a_measurement_to_compare_against(facts_tree, capsys):
    page, _facts = facts_tree
    page.write_text("<!--schema-facts--><!--/schema-facts-->\n", encoding="utf-8")
    assert facts_mod.main() == 1
    assert "run with --measure" in capsys.readouterr().out


def test_a_page_that_lost_its_marks_is_refused(facts_tree, capsys):
    page, facts = facts_tree
    facts.write_text("{}", encoding="utf-8")
    page.write_text("no marks here\n", encoding="utf-8")
    assert facts_mod.main() == 1
    assert "has no <!--schema-facts--> marks" in capsys.readouterr().out


def test_a_table_that_drifted_from_the_run_is_shown_both_ways(facts_tree, capsys):
    page, facts = facts_tree
    facts.write_text(json.dumps({"python": {"a": "2, 3", "k": ""}}), encoding="utf-8")
    page.write_text("<!--schema-facts-->\n| stale |\n<!--/schema-facts-->\n",
                    encoding="utf-8")
    assert facts_mod.main() == 1
    out = capsys.readouterr().out
    assert "in the page:" in out
    assert "measured by the run:" in out


def test_a_table_that_matches_the_run_is_silent(facts_tree, capsys):
    page, facts = facts_tree
    facts.write_text(json.dumps({"python": {"a": "2, 3", "k": ""}}), encoding="utf-8")
    page.write_text("<!--schema-facts-->x<!--/schema-facts-->\n", encoding="utf-8")
    facts_mod.apply(json.loads(facts.read_text(encoding="utf-8")))
    assert facts_mod.main() == 0
    assert "matches the measured run" in capsys.readouterr().out


def test_writing_the_table_into_a_page_without_marks_is_refused(facts_tree):
    page, _facts = facts_tree
    page.write_text("no marks\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no <!--schema-facts--> marks"):
        facts_mod.apply({})


def test_writing_the_same_table_twice_changes_nothing(facts_tree):
    page, _facts = facts_tree
    page.write_text("<!--schema-facts-->x<!--/schema-facts-->\n", encoding="utf-8")
    assert facts_mod.apply({}) is True
    assert facts_mod.apply({}) is False


def test_measuring_records_a_language_whose_toolchain_is_absent(monkeypatch, capsys):
    """The branch that fires on a machine without a compiler for that language."""

    def skip_go(lang: str) -> None:
        if lang == "go":
            raise RuntimeError("go is not installed")

    # `vars(fake).update`, not `fake.x = ...`: a module object takes any
    # attribute at run time, and this is the shape a type checker can read.
    fake = types.ModuleType("test_schema_parity")
    vars(fake).update({
        "_LANGS": ["python", "go"],
        "_ADD": {"python": "add", "go": "add"},
        "_records": lambda _lang, _root: [
            {"p": "in", "fn": "add", "a": "2, 3", "k": ""}],
        "_skip_unless_available": skip_go,
    })
    monkeypatch.setitem(sys.modules, "test_schema_parity", fake)

    out = facts_mod.measure()
    assert out["go"] == {"unavailable": "go is not installed"}
    assert out["python"] == {"a": "2, 3", "k": ""}
    assert "go: skipped" in capsys.readouterr().out


def test_measuring_writes_the_facts_and_the_pages(facts_tree, monkeypatch, capsys):
    page, facts = facts_tree
    page.write_text("<!--schema-facts-->x<!--/schema-facts-->\n", encoding="utf-8")
    monkeypatch.setattr(facts_mod, "measure", lambda: {"python": {"a": "2", "k": ""}})
    monkeypatch.setattr(sys, "argv", ["schema_facts.py", "--measure"])
    assert facts_mod.main() == 0
    assert "pages updated" in capsys.readouterr().out
    assert json.loads(facts.read_text(encoding="utf-8")) == {"python": {"a": "2", "k": ""}}

    monkeypatch.setattr(sys, "argv", ["schema_facts.py", "--measure"])
    assert facts_mod.main() == 0
    assert "pages already matched" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# scripts/state_numbers.py
# --------------------------------------------------------------------------- #

MEASURED = {"version": "0.5.0", "tests": 1072, "mcp_tools": 17,
            "uncovered_units": 0, "total_units": 3848,
            "coverage_percent": 100, "coverage_exact": 100.0}


@pytest.fixture
def numbers_tree(tmp_path, monkeypatch):
    """A tree with one page carrying marks, and a recorded measurement."""

    monkeypatch.setattr(state_mod, "ROOT", tmp_path)
    monkeypatch.setattr(state_mod, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(state_mod, "PAGES", ["README.md"])
    (tmp_path / "pyproject.toml").write_text('version = "0.5.0"\n', encoding="utf-8")
    (tmp_path / "mcp-tools.json").write_text('{"declared_tool_count": 17}',
                                             encoding="utf-8")
    monkeypatch.setattr(state_mod, "tool_count", lambda: 17)
    monkeypatch.setattr(state_mod, "collected_tests", lambda: 1072)
    (tmp_path / "README.md").write_text(
        "tests: <!--state:tests-->1072<!--/state-->\n", encoding="utf-8")
    (tmp_path / "state.json").write_text(json.dumps(MEASURED), encoding="utf-8")
    return tmp_path


def test_the_numbers_agree_with_the_measurement(numbers_tree, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["state_numbers.py"])
    assert state_mod.main() == 0
    assert "agree with the measurement" in capsys.readouterr().out


def test_a_mark_that_drifted_from_the_measurement_is_named(numbers_tree, monkeypatch,
                                                           capsys):
    (numbers_tree / "README.md").write_text(
        "tests: <!--state:tests-->999<!--/state-->\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["state_numbers.py"])
    assert state_mod.main() == 1
    out = capsys.readouterr().out
    assert "mark tests says '999'" in out
    assert "--measure" in out


def test_a_mark_naming_a_number_nobody_measures_is_named(numbers_tree):
    (numbers_tree / "README.md").write_text(
        "<!--state:invented-->1<!--/state-->\n", encoding="utf-8")
    problems = state_mod.check(MEASURED)
    assert "no such number is measured" in problems[0]


def test_the_cheap_numbers_are_recounted_rather_than_trusted(numbers_tree, monkeypatch):
    monkeypatch.setattr(state_mod, "collected_tests", lambda: 1100)
    monkeypatch.setattr(state_mod, "tool_count", lambda: 18)
    (numbers_tree / "pyproject.toml").write_text('version = "0.6.0"\n', encoding="utf-8")
    problems = state_mod.check(MEASURED)
    assert any("there are 1100 tests now" in p for p in problems)
    assert any("pyproject.toml says version 0.6.0" in p for p in problems)
    assert any("the reference lists 18 tools" in p for p in problems)


def test_a_coverage_report_beside_the_tree_is_compared_too(numbers_tree):
    """The hole this closes: coverage used to be taken by hand, once in a while."""

    (numbers_tree / ".coverage.json").write_text(json.dumps({"totals": {
        "missing_lines": 3, "missing_branches": 1,
        "num_statements": 3000, "num_branches": 848,
        "percent_covered": 99.8958,
    }}), encoding="utf-8")
    problems = state_mod.coverage_drift(MEASURED)
    assert any("uncovered_units: the run just measured 4" in p for p in problems)
    assert any("coverage_percent: the run just measured 99" in p for p in problems)


def test_no_coverage_report_claims_nothing(numbers_tree):
    assert state_mod.coverage_drift(MEASURED) == []


def test_a_coverage_report_that_agrees_is_silent(numbers_tree):
    (numbers_tree / ".coverage.json").write_text(json.dumps({"totals": {
        "missing_lines": 0, "missing_branches": 0,
        "num_statements": 3000, "num_branches": 848,
        "percent_covered": 100.0,
    }}), encoding="utf-8")
    assert state_mod.coverage_drift(MEASURED) == []


def test_with_no_recorded_measurement_there_is_nothing_to_compare(numbers_tree,
                                                                  monkeypatch, capsys):
    (numbers_tree / "state.json").unlink()
    monkeypatch.setattr(sys, "argv", ["state_numbers.py"])
    assert state_mod.main() == 1
    assert "run with --measure" in capsys.readouterr().out


def test_the_version_is_read_from_pyproject_and_nowhere_else(numbers_tree):
    assert state_mod.version() == "0.5.0"
    (numbers_tree / "pyproject.toml").write_text("no version here\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no version line"):
        state_mod.version()


def test_the_tool_count_comes_from_the_reference_taken_live(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "mcp-tools.json").write_text('{"declared_tool_count": 17}',
                                                      encoding="utf-8")
    assert state_mod.tool_count() == 17


def test_the_marks_are_rewritten_and_the_changed_pages_named(numbers_tree):
    changed = state_mod.apply_marks({**MEASURED, "tests": 1100})
    assert changed == ["README.md"]
    assert "1100" in (numbers_tree / "README.md").read_text(encoding="utf-8")
    assert state_mod.apply_marks({**MEASURED, "tests": 1100}) == []


def test_rewriting_a_mark_nobody_measures_names_the_page(numbers_tree):
    (numbers_tree / "README.md").write_text(
        "<!--state:invented-->1<!--/state-->\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"README\.md: mark 'invented'"):
        state_mod.apply_marks(MEASURED)


def test_counting_the_tests_reads_either_wording_pytest_uses(monkeypatch):
    def answer(text: str):
        return lambda *_a, **_k: subprocess.CompletedProcess([], 0, text, "")

    monkeypatch.setattr(subprocess, "run", answer("1072 tests collected in 3s\n"))
    assert state_mod.collected_tests() == 1072

    monkeypatch.setattr(subprocess, "run", answer("tests/a.py: 5\ntests/b.py: 7\n"))
    assert state_mod.collected_tests() == 12

    monkeypatch.setattr(subprocess, "run", answer("ERROR: collection failed\n"))
    with pytest.raises(SystemExit, match="could not parse the test collection"):
        state_mod.collected_tests()


def test_the_languages_are_counted_by_asking_the_command(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_k: subprocess.CompletedProcess(
                            [], 0, '{"languages": ["python", "go"]}', ""))
    assert state_mod.languages() == 2


def test_measuring_refuses_to_record_numbers_from_a_failing_run(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_k: subprocess.CompletedProcess([], 1, "boom", ""))
    with pytest.raises(SystemExit, match="the tests did not pass"):
        state_mod.measure()


def test_measuring_refuses_when_it_cannot_find_the_test_total(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "quiet", ""))
    with pytest.raises(SystemExit, match="could not parse the test total"):
        state_mod.measure()


def test_measuring_rounds_the_coverage_down(numbers_tree, monkeypatch, capsys):
    """99.51 % is not "100 %": a number rounded up promises what is not there."""

    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_k: subprocess.CompletedProcess(
                            [], 0, "1072 passed in 400s\n", ""))
    (numbers_tree / ".coverage.json").write_text(json.dumps({"totals": {
        "missing_lines": 2, "missing_branches": 3,
        "num_statements": 900, "num_branches": 100,
        "percent_covered": 99.51,
    }}), encoding="utf-8")
    monkeypatch.setattr(state_mod, "languages", lambda: 8)

    measured = state_mod.measure()
    assert measured["coverage_percent"] == 99
    assert measured["coverage_exact"] == 99.51
    assert measured["uncovered_units"] == 5
    assert measured["total_units"] == 1000


def test_the_measure_run_writes_the_state_file_and_the_pages(numbers_tree, monkeypatch,
                                                             capsys):
    monkeypatch.setattr(state_mod, "measure", lambda: {**MEASURED, "tests": 1100})
    monkeypatch.setattr(sys, "argv", ["state_numbers.py", "--measure"])
    assert state_mod.main() == 0
    out = capsys.readouterr().out
    assert "pages updated: README.md" in out
    assert "1100" in (numbers_tree / "README.md").read_text(encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["state_numbers.py", "--measure"])
    assert state_mod.main() == 0
    assert "nothing to change" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# scripts/emit_brain.py — the compiler is absent, of the wrong version, or angry
# --------------------------------------------------------------------------- #

PRINTED_MODULE = (
    "import flang_runtime as rt\n"
    "def new_context():\n    pass\n"
    "def entry():\n    pass\n"
    "def call(ctx, name, args):\n    pass\n"
    "def line_kind(ctx, line):\n    pass\n"
    "def _private(ctx):\n    pass\n"
)


def _compiler(monkeypatch, out_dir: Path, *, returncode: int = 0,
              module: str = PRINTED_MODULE) -> None:
    """A `flang emit` that writes what the real one writes, without a compiler."""

    def fake_run(argv, **_kwargs):
        if returncode == 0:
            target = Path(argv[argv.index("--out") + 1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "trace_brain.py").write_text(module, encoding="utf-8")
            (target / "flang_runtime.py").write_text("# runtime\n", encoding="utf-8")
            (target / "Makefile").write_text("all:\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, returncode, "out\n", "err\n")

    monkeypatch.setattr(emit_mod, "flang_binary", lambda: "/usr/bin/flang")
    monkeypatch.setattr(subprocess, "run", fake_run)


def test_without_the_compiler_the_print_is_not_compared_and_says_so(monkeypatch, capsys):
    """Code 2 is "not compared", which the gate must not read as "all is well"."""

    monkeypatch.setattr(emit_mod, "flang_binary", lambda: None)
    monkeypatch.setattr(sys, "argv", ["emit_brain.py", "--check"])
    assert emit_mod.main() == 2
    assert "flang is not installed" in capsys.readouterr().out


def test_a_compiler_of_another_version_does_not_compare_either(monkeypatch, capsys):
    """The tap moved ahead of the pinned version the print was made by."""

    monkeypatch.setattr(emit_mod, "flang_binary", lambda: "/usr/bin/flang")
    monkeypatch.setattr(emit_mod, "flang_version", lambda _b: "0.7.3")
    monkeypatch.setattr(sys, "argv", ["emit_brain.py", "--check"])
    assert emit_mod.main() == 2
    out = capsys.readouterr().out
    assert "this is flang 0.7.3" in out
    assert emit_mod.EXPECTED_VERSION in out


def test_the_version_is_read_off_the_compilers_own_answer(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "flang 0.7.14\n", ""))
    assert emit_mod.flang_version("/usr/bin/flang") == "0.7.14"


def test_printing_without_a_compiler_stops_at_once(monkeypatch, tmp_path):
    monkeypatch.setattr(emit_mod, "flang_binary", lambda: None)
    with pytest.raises(SystemExit, match="npm i -g"):
        emit_mod.print_brain(tmp_path)


def test_a_compiler_that_refuses_shows_its_own_output(monkeypatch, tmp_path, capsys):
    _compiler(monkeypatch, tmp_path, returncode=3)
    with pytest.raises(SystemExit, match="flang emit failed with code 3"):
        emit_mod.print_brain(tmp_path)
    captured = capsys.readouterr()
    assert captured.out == "out\n"
    assert captured.err == "err\n"


def test_a_backend_that_changed_its_runtime_import_is_refused(monkeypatch, tmp_path):
    """The one rewrite this script makes; if the text it looks for is gone, so is it."""

    _compiler(monkeypatch, tmp_path, module="import something_else as rt\n")
    with pytest.raises(SystemExit, match="no longer contains"):
        emit_mod.print_brain(tmp_path)


def test_the_print_is_rewritten_and_stripped_of_what_is_not_kept(monkeypatch, tmp_path):
    _compiler(monkeypatch, tmp_path)
    emit_mod.print_brain(tmp_path)

    module = (tmp_path / "trace_brain.py").read_text(encoding="utf-8")
    assert emit_mod.IMPORT_TO in module
    assert not module.startswith(emit_mod.IMPORT_FROM)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(emit_mod.PRINTED)


def test_the_stub_is_derived_from_the_print_not_written_by_hand(tmp_path):
    module = tmp_path / "m.py"
    module.write_text(PRINTED_MODULE, encoding="utf-8")
    stub = emit_mod.stub_for(module)
    assert "def new_context() -> Ctx: ..." in stub
    assert "def entry() -> object: ..." in stub
    assert "def call(ctx: Ctx, name: str, args: list[Value]) -> Value: ..." in stub
    assert "def line_kind(ctx: Ctx, line: Value) -> Value: ..." in stub
    assert "_private" not in stub


def test_a_print_that_matches_the_tree_is_current(monkeypatch, tmp_path, capsys):
    _compiler(monkeypatch, tmp_path)
    monkeypatch.setattr(emit_mod, "flang_version", lambda _b: emit_mod.EXPECTED_VERSION)
    monkeypatch.setattr(emit_mod, "TARGET", tmp_path / "committed")
    monkeypatch.setattr(emit_mod, "differences", lambda _out: [])
    monkeypatch.setattr(sys, "argv", ["emit_brain.py", "--check"])
    assert emit_mod.main() == 0
    assert "is current" in capsys.readouterr().out


def test_a_print_that_fell_behind_names_the_files(monkeypatch, tmp_path, capsys):
    _compiler(monkeypatch, tmp_path)
    monkeypatch.setattr(emit_mod, "flang_version", lambda _b: emit_mod.EXPECTED_VERSION)
    monkeypatch.setattr(emit_mod, "differences",
                        lambda _out: ["trace_brain.py: committed print differs"])
    monkeypatch.setattr(sys, "argv", ["emit_brain.py", "--check"])
    assert emit_mod.main() == 1
    out = capsys.readouterr().out
    assert "committed print differs" in out
    assert "run: uv run python scripts/emit_brain.py" in out


def test_the_difference_is_told_from_a_file_that_was_never_committed(tmp_path,
                                                                    monkeypatch):
    committed = tmp_path / "committed"
    committed.mkdir()
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    for name in emit_mod.PRINTED:
        (fresh / name).write_text("new\n", encoding="utf-8")
    (committed / emit_mod.PRINTED[0]).write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(emit_mod, "TARGET", committed)

    bad = emit_mod.differences(fresh)
    assert "committed print differs from a fresh one" in bad[0]
    assert "not committed at all" in bad[1]


def test_printing_for_real_installs_the_files_and_says_how_many(monkeypatch, tmp_path,
                                                                capsys):
    _compiler(monkeypatch, tmp_path / "out")
    target = tmp_path / "committed"
    target.mkdir()
    monkeypatch.setattr(emit_mod, "TARGET", target)
    monkeypatch.setattr(sys, "argv", ["emit_brain.py"])
    assert emit_mod.main() == 0
    out = capsys.readouterr().out
    assert f"printed {len(emit_mod.PRINTED)} files" in out
    assert sorted(p.name for p in target.iterdir()) == sorted(emit_mod.PRINTED)


def test_the_compiler_is_looked_for_on_the_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert emit_mod.flang_binary() == "/usr/bin/flang"


def test_a_print_equal_to_the_committed_one_yields_no_differences(tmp_path,
                                                                 monkeypatch):
    committed = tmp_path / "committed"
    committed.mkdir()
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    for name in emit_mod.PRINTED:
        (fresh / name).write_text("same\n", encoding="utf-8")
        (committed / name).write_text("same\n", encoding="utf-8")
    monkeypatch.setattr(emit_mod, "TARGET", committed)
    assert emit_mod.differences(fresh) == []


def test_the_stamp_names_the_source_its_digest_and_the_compiler(tmp_path):
    source = tmp_path / "trace_brain.flang"
    source.write_text("правило\n", encoding="utf-8")
    stamp = emit_mod.stamp_for(source)
    assert stamp.startswith("source trace_brain.flang\n")
    assert hashlib.sha256(source.read_bytes()).hexdigest() in stamp
    assert f"compiler flang {emit_mod.EXPECTED_VERSION}" in stamp


def test_a_print_with_no_stamp_says_nothing_holds_it_to_a_source(tmp_path,
                                                                 monkeypatch):
    monkeypatch.setattr(emit_mod, "STAMP", tmp_path / "printed-from.txt")
    assert "not committed at all" in emit_mod.stale_stamp()[0]


def test_a_source_edited_after_the_print_is_named(tmp_path, monkeypatch):
    """The finding this stamp exists for: the print is of some earlier text."""

    source = tmp_path / "trace_brain.flang"
    source.write_text("правило\n", encoding="utf-8")
    stamp = tmp_path / "printed-from.txt"
    stamp.write_text(emit_mod.stamp_for(source), encoding="utf-8")
    monkeypatch.setattr(emit_mod, "STAMP", stamp)
    assert emit_mod.stale_stamp(source) == []

    source.write_text("правило\nещё одно\n", encoding="utf-8")
    assert "changed since it was printed" in emit_mod.stale_stamp(source)[0]


def test_a_print_made_by_another_compiler_is_a_different_finding(tmp_path,
                                                                 monkeypatch):
    source = tmp_path / "trace_brain.flang"
    source.write_text("правило\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    stamp = tmp_path / "printed-from.txt"
    stamp.write_text(f"source {source.name}\nsha256 {digest}\ncompiler flang 0.7.3\n",
                     encoding="utf-8")
    monkeypatch.setattr(emit_mod, "STAMP", stamp)
    problems = emit_mod.stale_stamp(source)
    assert "made by flang 0.7.3" in problems[0]

    stamp.write_text(f"source {source.name}\nsha256 {digest}\n", encoding="utf-8")
    assert "a compiler this file does not name" in emit_mod.stale_stamp(source)[0]


def test_the_stamp_check_checks_itself(capsys):
    assert emit_mod.self_test() == 0
    assert "refuses a source it did not print from" in capsys.readouterr().out


def test_the_stamp_self_test_refuses_when_the_check_stops_refusing(monkeypatch,
                                                                   capsys):
    monkeypatch.setattr(emit_mod, "stale_stamp", lambda source=None: [])
    assert emit_mod.self_test() == 1
    out = capsys.readouterr().out
    assert "was called current" in out
    assert "does not refuse what it must refuse" in out


def test_the_stamp_self_test_refuses_when_the_tree_is_called_stale(monkeypatch,
                                                                   capsys):
    monkeypatch.setattr(emit_mod, "stale_stamp", lambda source=None: ["always"])
    assert emit_mod.self_test() == 1
    assert "the tree's own source was called stale" in capsys.readouterr().out


def test_the_stamp_self_test_runs_from_the_command_line(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emit_brain.py", "--self-test"])
    assert emit_mod.main() == 0
    assert "refuses a source it did not print from" in capsys.readouterr().out


def test_a_stale_stamp_stops_the_check_before_the_compiler(monkeypatch, capsys):
    """A missing compiler must not leave the digest question unasked."""

    monkeypatch.setattr(emit_mod, "stale_stamp", lambda source=None: ["it moved"])
    monkeypatch.setattr(emit_mod, "flang_binary", lambda: None)
    monkeypatch.setattr(sys, "argv", ["emit_brain.py", "--check"])
    assert emit_mod.main() == 1
    out = capsys.readouterr().out
    assert "it moved" in out
    assert "run: uv run python scripts/emit_brain.py" in out


def test_printing_for_real_writes_the_stamp_too(monkeypatch, tmp_path, capsys):
    _compiler(monkeypatch, tmp_path / "out")
    target = tmp_path / "committed"
    target.mkdir()
    source = tmp_path / "trace_brain.flang"
    source.write_text("правило\n", encoding="utf-8")
    monkeypatch.setattr(emit_mod, "TARGET", target)
    monkeypatch.setattr(emit_mod, "SOURCE", source)
    monkeypatch.setattr(emit_mod, "STAMP", tmp_path / "printed-from.txt")
    monkeypatch.setattr(emit_mod, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["emit_brain.py"])
    assert emit_mod.main() == 0
    out = capsys.readouterr().out
    assert "stamped printed-from.txt" in out
    assert (tmp_path / "printed-from.txt").read_text(encoding="utf-8") == \
        emit_mod.stamp_for(source)
