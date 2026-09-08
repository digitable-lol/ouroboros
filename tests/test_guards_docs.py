"""The guards that hold the documentation together, driven by their own seams.

Why this file exists. Until now `scripts/` was outside both the linter and the
measurement, and the branches these guards keep for somebody else's failure — a
site that answers with something other than JSON, a missing build configuration,
a ledger line for a file that is gone — had been written and never once executed.
A refusal branch that no run has ever taken is a refusal nobody has read.

Every test here hands a guard its evidence as an argument, or repoints the
guard's module-level `ROOT`/`CONFIG` at a tree made for the test. Nothing here
touches the real tree, and nothing reaches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_doc_anchors as anchors_mod
import check_doc_language as lang_mod
import check_doc_links as links_mod
import check_no_mixed_script as mixed_mod

# --------------------------------------------------------------------------- #
# scripts/check_no_mixed_script.py
# --------------------------------------------------------------------------- #

#: A Russian word with one Latin letter inside it — the layout slip the guard
#: exists to find. Written once, here, so that the tests below quote a name
#: rather than repeat a word the guard would rightly complain about.
SLIP = "хор" + "o" + "шо"          # смешанные-алфавиты: нарочно
CLEAN = "хорошо"


def test_a_word_of_two_alphabets_is_named_with_its_line(tmp_path, capsys):
    """The whole point: the slipped word reads exactly like the ordinary one."""

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.ru.md").write_text(f"# А\n\nвсё {SLIP} тут\n",
                                               encoding="utf-8")
    assert mixed_mod.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert SLIP in out
    assert "docs/a.ru.md:3" in out


def test_a_clean_tree_says_so(tmp_path, capsys):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.ru.md").write_text(f"# А\n\nвсё {CLEAN}\n",
                                               encoding="utf-8")
    assert mixed_mod.main(tmp_path) == 0
    assert "No words mix alphabets" in capsys.readouterr().out


def test_the_opt_out_marker_silences_one_line(tmp_path):
    """Prose about this very problem has to be able to spell an example out."""

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.ru.md").write_text(
        f"# А\n\nнапример {SLIP} ({mixed_mod.OPT_OUT})\n", encoding="utf-8")
    assert mixed_mod.main(tmp_path) == 0


def test_an_escape_sequence_does_not_glue_a_letter_onto_a_russian_word(tmp_path):
    """A newline written before a Russian word must not read as `n` joined to it."""

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.ru.md").write_text(f'x = "\\n{CLEAN}"\n', encoding="utf-8")
    assert mixed_mod.main(tmp_path) == 0


def test_vendored_and_recorded_trees_are_left_alone(tmp_path):
    """A word in node_modules or in a recorded benchmark run is not ours to fix."""

    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "a.md").write_text(SLIP + "\n", encoding="utf-8")
    (tmp_path / "bench" / "runs").mkdir(parents=True)
    (tmp_path / "bench" / "runs" / "b.md").write_text(SLIP + "\n", encoding="utf-8")
    (tmp_path / "keep.md").write_text("ok\n", encoding="utf-8")

    found = [p.name for p in mixed_mod.targets(tmp_path)]
    assert found == ["keep.md"]


def test_the_readme_of_the_russian_edition_is_in_scope():
    """It was in neither list before, and it is the longest Russian page there is."""

    root = Path(mixed_mod.__file__).resolve().parent.parent
    names = {p.relative_to(root).as_posix() for p in mixed_mod.targets(root)}
    assert "README.ru.md" in names
    assert "skill/SKILL.md" in names


# --------------------------------------------------------------------------- #
# scripts/check_doc_language.py
# --------------------------------------------------------------------------- #

EXISTS = {"a.md", "a.ru.md", "solo.ru.md"}


@pytest.mark.parametrize(("rel", "text", "expect"), [
    ("a.md", "**English** · [Русский](a.ru.md)\n\n# A\n\nПлохо.\n", "Cyrillic in a file"),
    ("a.md", "# A\n\nPlain.\n", "the switch line"),
    ("a.md", "**English** · [Русский](other.ru.md)\n\n# A\n", "the switch points at"),
    ("solo.ru.md", "[English](solo.md) · **Русский**\n\n# С\n", "no English one"),
    ("a.ru.md", "# А\n\nРусская страница.\n", "is missing"),
    ("a.ru.md", "[English](other.md) · **Русский**\n\n# А\n", "the switch points at"),
])
def test_a_broken_page_is_refused(rel, text, expect):
    problems = lang_mod.page_problems(rel, text, EXISTS)
    assert problems, f"{rel} passed and should not have"
    assert any(expect in p for p in problems)


@pytest.mark.parametrize(("rel", "text"), [
    ("a.md", "**English** · [Русский](a.ru.md)\n\n# A\n\nPlain English page.\n"),
    ("a.ru.md", "[English](a.md) · **Русский**\n\n# А\n\nРусская страница.\n"),
    ("plain.md", "# Plain\n\nNo counterpart, no switch line needed.\n"),
])
def test_a_sound_page_is_silent(rel, text):
    assert lang_mod.page_problems(rel, text, EXISTS) == []


def test_the_names_on_disk_are_allowed_in_an_english_page():
    """`черновик` is a directory a person has; an inflected form is prose."""

    assert lang_mod.page_problems(
        "a.md", "**English** · [Русский](a.ru.md)\n\nA directory named черновик.\n",
        EXISTS) == []
    assert lang_mod.page_problems(
        "a.md", "**English** · [Русский](a.ru.md)\n\nIt lives in черновике.\n",
        EXISTS) != []


def test_the_ledger_must_shrink(monkeypatch):
    """A pending page that is gone, and one that no longer holds any Russian."""

    monkeypatch.setattr(lang_mod, "PENDING", {"gone.md": "why", "done.md": "why"})
    problems = lang_mod.ledger_problems({"done.md": "All English now.\n"})
    assert any("no such file" in p for p in problems)
    assert any("no Russian left" in p for p in problems)

    monkeypatch.setattr(lang_mod, "PENDING", {"still.md": "why"})
    assert lang_mod.ledger_problems({"still.md": "Ещё не переведено.\n"}) == []


def test_the_flang_description_is_read_for_its_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(lang_mod, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    state = {"languages": 8, "mcp_tools": 17, "version": "0.5.0"}
    (tmp_path / "docs" / "state.json").write_text(json.dumps(state), encoding="utf-8")

    spec = tmp_path / "docs" / "ouroboros.flang"
    spec.write_text(
        'тотальная функция «Языков» ожидается 8\n'
        'тотальная функция «Средств MCP» ожидается 17\n'
        'тотальная функция «Версия» ожидается "0.5.0"\n',
        encoding="utf-8")
    assert lang_mod.flang_numbers() == []

    spec.write_text(
        "тотальная функция «Языков» ожидается 9\n"
        "тотальная функция «Средств MCP» нет примера\n",
        encoding="utf-8")
    problems = lang_mod.flang_numbers()
    assert any("promises '9'" in p for p in problems)
    assert any("no example" in p for p in problems)
    assert any("no function «Версия»" in p for p in problems)


def test_the_flang_numbers_say_when_there_is_nothing_to_read(tmp_path, monkeypatch):
    monkeypatch.setattr(lang_mod, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    assert "the Russian description in flang" in lang_mod.flang_numbers()[0]
    (tmp_path / "docs" / "ouroboros.flang").write_text("x\n", encoding="utf-8")
    assert "state.json" in lang_mod.flang_numbers()[0]


def test_the_self_test_passes_and_counts_the_live_controls(capsys):
    assert lang_mod.selftest() == 0
    assert "10 cases, all agreed" in capsys.readouterr().out


def test_the_self_test_counts_only_the_cases_it_ran(tmp_path, monkeypatch, capsys):
    """With no README.md the two live controls do not run — and are not counted."""

    monkeypatch.setattr(lang_mod, "ROOT", tmp_path)
    assert lang_mod.selftest() == 0
    assert "8 cases, all agreed" in capsys.readouterr().out


def test_the_self_test_refuses_when_a_rule_stops_working(monkeypatch, capsys):
    monkeypatch.setattr(lang_mod, "page_problems", lambda *_a, **_k: [])
    assert lang_mod.selftest() == 1
    out = capsys.readouterr().out
    assert "✗" in out
    assert "disagreed with what was expected" in out


# --------------------------------------------------------------------------- #
# scripts/check_doc_anchors.py
# --------------------------------------------------------------------------- #

def test_an_anchor_is_the_heading_lowercased_and_hyphenated():
    taken: set[str] = set()
    assert anchors_mod.anchor_for("What `wrap_file` does", taken) == "what-wrap_file-does"
    taken.add("a")
    assert anchors_mod.anchor_for("**A**", taken) == "a-1"
    taken.add("a-1")
    assert anchors_mod.anchor_for("[A](x.md)", taken) == "a-2"


def test_the_anchors_of_a_page_come_from_its_headings(tmp_path):
    page = tmp_path / "p.md"
    page.write_text("# One\n\ntext\n\n## Two words\n", encoding="utf-8")
    assert anchors_mod.anchors(page) == {"one", "two-words"}


def test_a_header_opened_and_never_closed_is_refused(tmp_path):
    page = tmp_path / "p.md"
    page.write_text("---\ntitle: A\n", encoding="utf-8")
    assert "opened but never closed" in anchors_mod.front_matter_problems(page)[0]


def test_a_page_without_a_header_has_nothing_to_check(tmp_path):
    page = tmp_path / "p.md"
    page.write_text("# A\n", encoding="utf-8")
    assert anchors_mod.front_matter_problems(page) == []


def test_an_unquoted_colon_in_a_header_is_refused(tmp_path):
    page = tmp_path / "p.md"
    page.write_text("---\ntitle: A: and more\n---\n\n# A\n", encoding="utf-8")
    problems = anchors_mod.front_matter_problems(page)
    assert "will not parse this page header" in problems[0]


@pytest.mark.parametrize("value", ['"A: quoted"', "[a, b]", "{a: 1}", "|", ">", ""])
def test_a_value_yaml_can_read_is_left_alone(value):
    text = f"key: {value}\n"
    assert anchors_mod.unquoted_colon_problems(
        Path("x"), text, first_lineno=1, what="build configuration") == []


def test_a_missing_build_configuration_is_a_problem_not_a_silence(tmp_path, monkeypatch):
    """Returning [] here is how a check stops checking; the site cannot build."""

    monkeypatch.setattr(anchors_mod, "CONFIG", tmp_path / "_config.yml")
    assert "missing" in anchors_mod.config_problems()[0]


def test_the_build_configuration_is_read_by_the_same_rule(tmp_path, monkeypatch):
    cfg = tmp_path / "_config.yml"
    cfg.write_text("description: A tool: for logging\n", encoding="utf-8")
    monkeypatch.setattr(anchors_mod, "CONFIG", cfg)
    assert "build configuration" in anchors_mod.config_problems()[0]

    cfg.write_text('description: "A tool: for logging"\n', encoding="utf-8")
    assert anchors_mod.config_problems() == []


def test_a_link_to_a_missing_file_and_to_a_missing_heading(tmp_path, monkeypatch):
    monkeypatch.setattr(anchors_mod, "ROOT", tmp_path)
    (tmp_path / "there.md").write_text("# Here\n", encoding="utf-8")
    page = tmp_path / "p.md"
    page.write_text(
        "# P\n\n[gone](missing.md) [wrong](there.md#nope) [right](there.md#here)\n"
        "[same page](#p) [outside](https://example.invalid/x)\n",
        encoding="utf-8")

    bad, checked = anchors_mod.link_problems(page, {})
    assert checked == 4                       # the http link is not counted
    assert any("no such file: missing.md" in b for b in bad)
    assert any("has no heading with anchor #nope" in b for b in bad)
    assert len(bad) == 2


def test_the_anchor_check_refuses_and_says_how_many_links_it_read(tmp_path,
                                                                 monkeypatch, capsys):
    monkeypatch.setattr(anchors_mod, "ROOT", tmp_path)
    monkeypatch.setattr(anchors_mod, "CONFIG", tmp_path / "_config.yml")
    (tmp_path / "_config.yml").write_text("title: A\n", encoding="utf-8")
    monkeypatch.setattr(anchors_mod, "GLOBS", ("*.md",))
    (tmp_path / "p.md").write_text("# P\n\n[gone](missing.md)\n", encoding="utf-8")

    assert anchors_mod.main() == 1
    err = capsys.readouterr().err
    assert "no such file: missing.md" in err
    assert "Links checked: 1" in err


def test_the_anchor_check_passes_on_a_sound_tree(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(anchors_mod, "ROOT", tmp_path)
    monkeypatch.setattr(anchors_mod, "CONFIG", tmp_path / "_config.yml")
    (tmp_path / "_config.yml").write_text("title: A\n", encoding="utf-8")
    monkeypatch.setattr(anchors_mod, "GLOBS", ("*.md",))
    (tmp_path / "p.md").write_text("# P\n\n[self](#p)\n", encoding="utf-8")

    assert anchors_mod.main() == 0
    assert "1 links checked" in capsys.readouterr().out


def test_the_pages_of_the_tree_are_listed_once(tmp_path, monkeypatch):
    """Two globs can name the same page; it must not be checked twice."""

    monkeypatch.setattr(anchors_mod, "ROOT", tmp_path)
    monkeypatch.setattr(anchors_mod, "GLOBS", ("*.md", "*.md"))
    (tmp_path / "p.md").write_text("# P\n", encoding="utf-8")
    assert len(anchors_mod.pages()) == 1


# --------------------------------------------------------------------------- #
# scripts/check_doc_links.py
# --------------------------------------------------------------------------- #

def _tree_with_anchor(tmp_path: Path, line: str = "MARKER = 1\n") -> Path:
    (tmp_path / "ouroboros").mkdir()
    (tmp_path / "ouroboros" / "m.py").write_text("x = 0\n" + line, encoding="utf-8")
    return tmp_path


def test_an_anchor_that_points_at_no_file_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr(links_mod, "ANCHORS", [("ouroboros/gone.py", "x")])
    _found, problems = links_mod.anchor_lines(tmp_path)
    assert "points at a file that does not exist" in problems[0]


def test_an_anchor_must_match_exactly_one_line(tmp_path, monkeypatch):
    _tree_with_anchor(tmp_path, "MARKER = 1\nMARKER = 2\n")
    monkeypatch.setattr(links_mod, "ANCHORS", [("ouroboros/m.py", "MARKER")])
    _found, problems = links_mod.anchor_lines(tmp_path)
    assert "occurs 2 times" in problems[0]


def test_an_anchor_is_found_by_its_text_not_by_its_number(tmp_path, monkeypatch):
    _tree_with_anchor(tmp_path)
    monkeypatch.setattr(links_mod, "ANCHORS", [("ouroboros/m.py", "MARKER")])
    found, problems = links_mod.anchor_lines(tmp_path)
    assert problems == []
    assert found == {"ouroboros/m.py": {2: "MARKER"}}


def test_a_citation_of_a_file_with_no_anchor_declared():
    problems, checked = links_mod.citation_problems(
        "docs/a.md", "see ouroboros/other.py:12\n", {"ouroboros/m.py": {2: "x"}})
    assert checked == 1
    assert "no anchor is declared for ouroboros/other.py" in problems[0]


def test_a_citation_of_a_line_that_moved_prints_the_new_numbers():
    problems, checked = links_mod.citation_problems(
        "docs/a.md", "see ouroboros/m.py:99\n", {"ouroboros/m.py": {2: "MARKER"}})
    assert checked == 1
    assert "Anchors in this file: 2 ('MARKER')" in problems[0]


def test_a_citation_that_lands_on_an_anchor_is_silent():
    problems, checked = links_mod.citation_problems(
        "docs/a.md", "see ouroboros/m.py:2\n", {"ouroboros/m.py": {2: "MARKER"}})
    assert (problems, checked) == ([], 1)


def test_a_pasted_traceback_carries_a_line_number_too():
    anchors = {"ouroboros/runtime.py": {7: "result = fn(*args, **kwargs)"}}
    text = 'File ".../ouroboros_runtime.py", line 144, in wrapper\n'
    problems, checked = links_mod.traceback_problems("docs/a.md", text, anchors)
    assert checked == 1
    assert "the pasted traceback says ouroboros_runtime.py line 144" in problems[0]

    text = 'File ".../ouroboros_runtime.py", line 7, in wrapper\n'
    assert links_mod.traceback_problems("docs/a.md", text, anchors) == ([], 1)


def test_the_number_in_the_text_and_in_the_link_must_agree():
    line = ("[`ouroboros/m.py:2`](https://github.com/x/y/blob/main/ouroboros/m.py#L9)\n")
    problems = links_mod.text_and_link_agree("docs/a.md", line)
    assert "the number in the text and the number in the link disagree" in problems[0]

    line = ("[`ouroboros/m.py:2`](https://github.com/x/y/blob/main/ouroboros/m.py#L2)\n")
    assert links_mod.text_and_link_agree("docs/a.md", line) == []


def test_vendored_pages_are_not_read(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "a.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("x\n", encoding="utf-8")
    assert [p.name for p in links_mod.pages(tmp_path)] == ["b.md"]


def test_a_directory_matching_a_glob_is_not_a_page(tmp_path, monkeypatch):
    monkeypatch.setattr(anchors_mod, "ROOT", tmp_path)
    monkeypatch.setattr(anchors_mod, "GLOBS", ("*.md",))
    (tmp_path / "adir.md").mkdir()
    assert anchors_mod.pages() == []


def test_a_link_to_a_file_that_is_not_markdown_needs_no_heading(tmp_path, monkeypatch):
    monkeypatch.setattr(anchors_mod, "ROOT", tmp_path)
    (tmp_path / "x.png").write_bytes(b"\x89PNG")
    page = tmp_path / "p.md"
    page.write_text("# P\n\n[img](x.png#frag)\n", encoding="utf-8")
    assert anchors_mod.link_problems(page, {}) == ([], 1)


def test_one_page_is_parsed_once_however_many_links_point_at_it(tmp_path, monkeypatch):
    monkeypatch.setattr(anchors_mod, "ROOT", tmp_path)
    (tmp_path / "t.md").write_text("# Here\n", encoding="utf-8")
    page = tmp_path / "p.md"
    page.write_text("# P\n\n[a](t.md#here) [b](t.md#here)\n", encoding="utf-8")
    cache: dict[Path, set[str]] = {}
    assert anchors_mod.link_problems(page, cache) == ([], 2)
    assert list(cache) == [(tmp_path / "t.md").resolve()]


# --------------------------------------------------------------------------- #
# the two walks over the tree
# --------------------------------------------------------------------------- #

def _language_tree(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "state.json").write_text(
        json.dumps({"languages": 8, "mcp_tools": 17, "version": "0.5.0"}),
        encoding="utf-8")
    (root / "docs" / "ouroboros.flang").write_text(
        'тотальная функция «Языков» ожидается 8\n'
        'тотальная функция «Средств MCP» ожидается 17\n'
        'тотальная функция «Версия» ожидается "0.5.0"\n',
        encoding="utf-8")


def test_the_language_walk_passes_and_counts_the_pairs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lang_mod, "ROOT", tmp_path)
    monkeypatch.setattr(lang_mod, "ROOTS", (".", "docs"))
    monkeypatch.setattr(lang_mod, "PENDING", {})
    _language_tree(tmp_path)
    (tmp_path / "a.md").write_text(
        "**English** · [Русский](a.ru.md)\n\n# A\n", encoding="utf-8")
    (tmp_path / "a.ru.md").write_text(
        "[English](a.md) · **Русский**\n\n# А\n", encoding="utf-8")

    assert lang_mod.main() == 0
    out = capsys.readouterr().out
    assert "of them pairs: 1" in out
    assert "PENDING" not in out


def test_the_language_walk_refuses_and_names_the_page(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lang_mod, "ROOT", tmp_path)
    monkeypatch.setattr(lang_mod, "ROOTS", (".",))
    monkeypatch.setattr(lang_mod, "PENDING", {})
    _language_tree(tmp_path)
    (tmp_path / "a.md").write_text("# A\n\nРусский абзац.\n", encoding="utf-8")

    assert lang_mod.main() == 1
    assert "Cyrillic in a file without the suffix" in capsys.readouterr().out


def test_the_language_walk_names_the_ledger_and_skips_its_pages(tmp_path, monkeypatch,
                                                                capsys):
    monkeypatch.setattr(lang_mod, "ROOT", tmp_path)
    monkeypatch.setattr(lang_mod, "ROOTS", (".",))
    monkeypatch.setattr(lang_mod, "PENDING", {"todo.md": "not translated yet"})
    _language_tree(tmp_path)
    (tmp_path / "todo.md").write_text("# Т\n\nПока по-русски.\n", encoding="utf-8")

    assert lang_mod.main() == 0
    assert "still holds 1 pages" in capsys.readouterr().out


def test_the_language_walk_leaves_vendored_trees_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(lang_mod, "ROOT", tmp_path)
    monkeypatch.setattr(lang_mod, "ROOTS", (".", "docs", "nosuchdir"))
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "v.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "d.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "r.md").write_text("x\n", encoding="utf-8")

    names = {p.name for p in lang_mod.pages()}
    assert names == {"d.md", "r.md"}


def test_the_link_check_refuses_and_prints_the_new_numbers(tmp_path, monkeypatch,
                                                           capsys):
    monkeypatch.setattr(links_mod, "ANCHORS", [("ouroboros/m.py", "MARKER")])
    _tree_with_anchor(tmp_path)
    (tmp_path / "d.md").write_text("see ouroboros/m.py:99\n", encoding="utf-8")
    assert links_mod.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "Anchors in this file: 2 ('MARKER')" in out
    assert "Links checked: 1" in out


def test_the_link_check_passes_on_a_sound_tree(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(links_mod, "ANCHORS", [("ouroboros/m.py", "MARKER")])
    _tree_with_anchor(tmp_path)
    (tmp_path / "d.md").write_text("see ouroboros/m.py:2\n", encoding="utf-8")
    assert links_mod.main(tmp_path) == 0
    assert "1 checked, all land on anchors" in capsys.readouterr().out


def test_the_same_problem_found_twice_is_reported_once(tmp_path, monkeypatch, capsys):
    """A number in the prose and the same number in the link beside it."""

    monkeypatch.setattr(links_mod, "ANCHORS", [("ouroboros/m.py", "MARKER")])
    _tree_with_anchor(tmp_path)
    (tmp_path / "d.md").write_text(
        "[`ouroboros/m.py:99`](https://github.com/x/y/blob/main/ouroboros/m.py#L99)\n",
        encoding="utf-8")
    assert links_mod.main(tmp_path) == 1
    assert capsys.readouterr().out.count("cites ouroboros/m.py:99") == 1


def test_a_header_line_that_is_not_a_field_is_passed_over():
    """A blank line or a list item inside the header is not a `key: value`."""

    text = "title: A\n\n  - item\n"
    assert anchors_mod.unquoted_colon_problems(
        Path("x"), text, first_lineno=1, what="page header") == []


def test_a_generated_tree_is_skipped_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(lang_mod, "ROOT", tmp_path)
    monkeypatch.setattr(lang_mod, "ROOTS", ("docs",))
    (tmp_path / "docs" / "runs").mkdir(parents=True)
    (tmp_path / "docs" / "runs" / "r.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "docs" / "keep.md").write_text("x\n", encoding="utf-8")
    assert [p.name for p in lang_mod.pages()] == ["keep.md"]


def test_the_self_test_prints_what_a_failing_case_complained_about(monkeypatch, capsys):
    """When a case that should stay silent refuses, the refusal is shown."""

    monkeypatch.setattr(lang_mod, "page_problems",
                        lambda *_a, **_k: ["invented complaint"])
    assert lang_mod.selftest() == 1
    out = capsys.readouterr().out
    assert "invented complaint" in out
    assert "the real README.md does not pass the check" in out


def test_the_language_guard_runs_its_self_test_from_the_command_line(monkeypatch,
                                                                    capsys):
    monkeypatch.setattr(sys, "argv", ["check_doc_language.py", "--self-test"])
    assert lang_mod.main() == 0
    assert "all agreed" in capsys.readouterr().out
