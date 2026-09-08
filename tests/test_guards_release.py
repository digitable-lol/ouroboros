"""The guards that ask the outside world, driven with the outside world faked.

These are the branches nothing had ever executed: GitHub answering 429, a site
serving an HTML error page where JSON was expected, `git` not installed, a
toolchain absent, a compiler of the wrong version. Every one of them is reached
here by handing the guard a fake `urlopen` or a fake `subprocess.run`, so the
refusal a person would read has been read at least once.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Self

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_packaging_names as pkg_mod
import check_pages_live as live_mod
import check_release_published as rel_mod

# --------------------------------------------------------------------------- #
# a fake for urllib
# --------------------------------------------------------------------------- #

class _Response:
    """What `urllib.request.urlopen` hands back, as much of it as is used."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _serve(monkeypatch, pages: dict[str, object]) -> list[str]:
    """Answer every request from `pages`; record the addresses that were asked.

    A value may be bytes (a 200 answer), an int (a status code) or an exception
    to raise. A missing address answers 404, which is what a site that lost a
    page does.
    """

    asked: list[str] = []

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        url = request.full_url if hasattr(request, "full_url") else str(request)
        asked.append(url)
        answer = pages.get(url, 404)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            raise urllib.error.HTTPError(url, answer, "no", {}, None)  # type: ignore[arg-type]
        assert isinstance(answer, bytes)
        return _Response(answer)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return asked


# --------------------------------------------------------------------------- #
# scripts/check_packaging_names.py — one rule per breakage
# --------------------------------------------------------------------------- #

SCRIPTS = {"ouroboros", "ouroboros-mcp"}

SOUND_FORMULA = (
    '  url "https://github.com/x/y/archive/refs/tags/v0.5.0.tar.gz"\n'
    '  bin.install_symlink Dir[libexec/"bin/ouroboros*"]\n'
    '  test do\n'
    '    system bin/"ouroboros", "languages"\n'
    '  end\n'
    '  def caveats\n'
    '    #{bin}/ouroboros-mcp\n'
    '    ouroboros mcp\n'
    '    "command": "ouroboros-mcp"\n'
    '  end\n'
)


def test_the_formula_may_not_call_a_command_the_package_does_not_install():
    body = SOUND_FORMULA + '\n  assert_path_exists bin/"ouroboros-mcp-router"\n'
    problems = pkg_mod.rule_commands_installed(body, SCRIPTS)
    assert "ouroboros-mcp-router" in problems[0]


def test_a_pattern_that_finds_nothing_is_a_pattern_out_of_date():
    problems = pkg_mod.rule_commands_installed("nothing here", SCRIPTS)
    assert len(problems) == 2                      # one per pattern
    assert all("pattern is out of date" in p for p in problems)


def test_a_sound_formula_names_only_installed_commands():
    assert pkg_mod.rule_commands_installed(SOUND_FORMULA, SCRIPTS) == []


def test_a_command_that_reaches_no_symlink_pattern_is_named():
    problems = pkg_mod.rule_commands_exposed(SOUND_FORMULA, {"ouroboros", "other-tool"})
    assert "other-tool" in problems[0]
    assert pkg_mod.rule_commands_exposed(SOUND_FORMULA, SCRIPTS) == []


def test_a_formula_with_no_symlink_line_exposes_nothing():
    problems = pkg_mod.rule_commands_exposed('url "x"', SCRIPTS)
    assert "no bin.install_symlink" in problems[0]


def test_a_subcommand_the_package_does_not_have_is_named():
    assert pkg_mod.rule_subcommands(SOUND_FORMULA, {"languages", "mcp"}) == []
    problems = pkg_mod.rule_subcommands(SOUND_FORMULA, {"mcp"})
    assert "'languages'" in problems[0]
    assert "calls no subcommand at all" in pkg_mod.rule_subcommands("", {"mcp"})[0]


def test_the_mcp_configuration_names_a_command_that_exists():
    assert pkg_mod.rule_mcp_command(SOUND_FORMULA, SCRIPTS) == []
    problems = pkg_mod.rule_mcp_command(SOUND_FORMULA, {"ouroboros"})
    assert "ouroboros-mcp" in problems[0]
    assert "no MCP configuration" in pkg_mod.rule_mcp_command("", SCRIPTS)[0]


def test_the_archive_url_carries_the_package_version():
    assert pkg_mod.rule_archive_tag(SOUND_FORMULA, "0.5.0") == []
    assert "pulls version" in pkg_mod.rule_archive_tag(SOUND_FORMULA, "0.6.0")[0]
    assert "no archive URL" in pkg_mod.rule_archive_tag('url "x"', "0.5.0")[0]


def test_the_asdf_plugin_exposes_exactly_the_package_commands():
    listing = "for name in ouroboros ouroboros-mcp; do\n"
    assert pkg_mod.rule_asdf_listing(listing, SCRIPTS) == []
    assert "the plugin exposes" in pkg_mod.rule_asdf_listing(listing, {"ouroboros"})[0]
    assert "no list of names" in pkg_mod.rule_asdf_listing("", SCRIPTS)[0]


def test_the_asdf_shims_must_be_in_place_and_executable(tmp_path):
    for name in ("download", "install", "list-all"):
        real = tmp_path / "packaging" / "asdf" / "bin" / name
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text("#!/bin/sh\n", encoding="utf-8")
        real.chmod(0o755)
        shim = tmp_path / "bin" / name
        shim.parent.mkdir(exist_ok=True)
        shim.write_text(f"exec packaging/asdf/bin/{name}\n", encoding="utf-8")
        shim.chmod(0o755)
    assert pkg_mod.rule_asdf_shims(tmp_path) == []

    (tmp_path / "bin" / "download").chmod(0o644)
    (tmp_path / "bin" / "install").unlink()
    (tmp_path / "bin" / "list-all").write_text("echo nothing\n", encoding="utf-8")
    problems = pkg_mod.rule_asdf_shims(tmp_path)
    assert any("is not executable" in p for p in problems)
    assert any("no such file: bin/install" in p for p in problems)
    assert any("does not hand the work to" in p for p in problems)


def test_the_packaging_check_passes_on_the_real_tree(capsys):
    assert pkg_mod.main() == 0
    assert "agree with the package" in capsys.readouterr().out


def test_the_packaging_check_refuses_when_a_rule_fires(monkeypatch, capsys):
    monkeypatch.setattr(pkg_mod, "rule_archive_tag", lambda *_a: ["invented"])
    assert pkg_mod.main() == 1
    out = capsys.readouterr().out
    assert "parted ways with the package" in out
    assert "invented" in out


# --------------------------------------------------------------------------- #
# scripts/check_pages_live.py — the site answers, and answers wrongly
# --------------------------------------------------------------------------- #

ROOT_URL = "https://example.invalid/ouroboros"
TREE_STATE = {"tests": 1072, "version": "0.5.0"}


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A tree with one documentation page and a state file, plus the site address."""

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "_config.yml").write_text(
        'url: "https://example.invalid"\nbaseurl: "/ouroboros"\n', encoding="utf-8")
    (tmp_path / "docs" / "index.md").write_text("# Index\n", encoding="utf-8")
    (tmp_path / "docs" / "state.json").write_text(json.dumps(TREE_STATE), encoding="utf-8")
    monkeypatch.setattr(live_mod, "ROOT", tmp_path)
    monkeypatch.setattr(live_mod, "CONFIG", tmp_path / "docs" / "_config.yml")
    monkeypatch.setattr(live_mod, "STATE_FILE", tmp_path / "docs" / "state.json")
    return tmp_path


def _live_pages(state: dict[str, object] | None = None,
                marks: str | None = None) -> dict[str, object]:
    body = marks if marks is not None else "<!--state:tests-->1072<!--/state-->"
    return {
        f"{ROOT_URL}/index.html": b"<html></html>",
        f"{ROOT_URL}/state.json": json.dumps(state or TREE_STATE).encode(),
        f"{ROOT_URL}/docs/index.html": body.encode(),
    }


def test_the_site_address_comes_from_the_build_configuration(site):
    assert live_mod.site_root() == ROOT_URL


def test_a_build_configuration_with_no_url_stops_the_check(site, monkeypatch):
    (site / "docs" / "_config.yml").write_text("title: x\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no url field"):
        live_mod.site_root()


def test_a_site_that_matches_the_tree_is_silent(site, monkeypatch, capsys):
    _serve(monkeypatch, _live_pages())
    assert live_mod.check() == 0
    assert "serves the current tree" in capsys.readouterr().out


def test_a_page_in_the_tree_that_the_site_does_not_serve(site, monkeypatch):
    pages = _live_pages()
    del pages[f"{ROOT_URL}/index.html"]       # docs/index.md is served at the root
    _serve(monkeypatch, pages)
    bad, checked = live_mod.pages_served(ROOT_URL)
    assert checked == 1
    assert "the site answers 404, yet the page is in the tree" in bad[0]


def test_a_site_that_serves_something_that_is_not_json(site, monkeypatch):
    """A 200 with an HTML error page in it — the shape a proxy hands back."""

    _serve(monkeypatch, {f"{ROOT_URL}/state.json": b"<html>oops</html>"})
    bad, checked = live_mod.state_matches(ROOT_URL, TREE_STATE)
    assert checked == 1
    assert "served something that is not JSON" in bad[0]


def test_a_state_file_the_site_answers_with_a_code_for(site, monkeypatch):
    _serve(monkeypatch, {f"{ROOT_URL}/state.json": 500})
    bad, _checked = live_mod.state_matches(ROOT_URL, TREE_STATE)
    assert "state.json: the site answers 500" in bad[0]


def test_a_site_built_from_a_different_tree_is_named_field_by_field(site, monkeypatch):
    _serve(monkeypatch, _live_pages(state={"tests": 999, "version": "0.5.0"}))
    bad, _checked = live_mod.state_matches(ROOT_URL, TREE_STATE)
    assert "on the site 999, in the tree 1072" in bad[0]


def test_a_documentation_page_without_marks_is_not_that_page(site, monkeypatch):
    _serve(monkeypatch, _live_pages(marks="<html>no marks</html>"))
    bad, checked = live_mod.marks_match(ROOT_URL, TREE_STATE)
    assert checked == 2
    assert "nothing to compare the numbers against" in bad[0]


def test_the_marks_on_the_live_page_are_compared_number_by_number(site, monkeypatch):
    _serve(monkeypatch, _live_pages(marks="<!--state:tests-->999<!--/state-->"))
    bad, _checked = live_mod.marks_match(ROOT_URL, TREE_STATE)
    assert "the site shows '999', the tree says '1072'" in bad[0]


def test_the_documentation_index_is_looked_for_at_both_addresses(site, monkeypatch):
    """While Jekyll built the site it was the root; now it is under docs/."""

    pages = _live_pages()
    del pages[f"{ROOT_URL}/docs/index.html"]
    pages[f"{ROOT_URL}/index.html"] = b"<!--state:tests-->1072<!--/state-->"
    _serve(monkeypatch, pages)
    where, _body = live_mod.documentation_index(ROOT_URL)
    assert where == "index.html"


def test_a_site_that_cannot_be_reached_is_code_two_not_code_one(site, monkeypatch,
                                                               capsys):
    """Unreachable is "the check did not happen", which is not "they differ"."""

    _serve(monkeypatch, {f"{ROOT_URL}/index.html": urllib.error.URLError("no route")})
    assert live_mod.check() == 2
    assert "The check did NOT happen" in capsys.readouterr().err


def test_a_network_that_drops_halfway_is_still_code_two(site, monkeypatch, capsys):
    """It used to be an uncaught URLError: a traceback, and the gate red for the
    wrong reason."""

    _serve(monkeypatch, {
        f"{ROOT_URL}/index.html": b"ok",
        f"{ROOT_URL}/docs/index.html": urllib.error.URLError("dropped"),
    })
    assert live_mod.check() == 2
    assert "could not be reached" in capsys.readouterr().err


def test_a_site_that_differs_prints_how_many_requests_it_took(site, monkeypatch,
                                                              capsys):
    _serve(monkeypatch, _live_pages(state={"tests": 999}))
    assert live_mod.check() == 1
    err = capsys.readouterr().err
    assert "parted ways with the tree" in err
    assert "Requests made:" in err


def test_the_site_is_asked_once_when_no_retries_are_allowed(site, monkeypatch):
    asked = _serve(monkeypatch, _live_pages())
    monkeypatch.setattr(sys, "argv", ["check_pages_live.py"])
    assert live_mod.main() == 0
    assert asked


def test_a_failing_site_is_asked_again_while_retries_remain(site, monkeypatch, capsys):
    answers = iter([1, 0])
    monkeypatch.setattr(live_mod, "check", lambda: next(answers))
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(sys, "argv",
                        ["check_pages_live.py", "--retries", "2", "--wait", "0"])
    assert live_mod.main() == 0
    out = capsys.readouterr().out
    assert "attempt 2 of 3" in out
    assert "attempt 3 of 3" not in out       # it caught up, so it stopped asking


def test_a_negative_retry_count_still_asks_once(site, monkeypatch):
    """`--retries -1` used to return a variable that was never assigned."""

    monkeypatch.setattr(live_mod, "check", lambda: 1)
    monkeypatch.setattr(sys, "argv",
                        ["check_pages_live.py", "--retries", "-1"])
    assert live_mod.main() == 1


def test_a_line_of_the_build_configuration_that_is_not_a_field_is_passed_over(site):
    (site / "docs" / "_config.yml").write_text(
        "# a comment\n\nurl: https://example.invalid\n", encoding="utf-8")
    assert live_mod.site_root() == "https://example.invalid"


def test_a_site_that_never_catches_up_returns_the_last_answer(site, monkeypatch,
                                                              capsys):
    monkeypatch.setattr(live_mod, "check", lambda: 1)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(sys, "argv",
                        ["check_pages_live.py", "--retries", "2", "--wait", "0"])
    assert live_mod.main() == 1
    out = capsys.readouterr().out
    assert "attempt 2 of 3" in out
    assert "attempt 3 of 3" in out


# --------------------------------------------------------------------------- #
# scripts/check_release_published.py — the tree, and what is published
# --------------------------------------------------------------------------- #

def test_the_release_rules_check_themselves(capsys):
    """`--self-test` feeds every rule its own breakage and demands a refusal."""

    assert rel_mod.self_test() == 0
    assert "each goes red on its own breakage" in capsys.readouterr().out


def test_the_self_test_notices_a_rule_that_stopped_checking(monkeypatch, capsys):
    monkeypatch.setattr(rel_mod, "ALL_RULES",
                        (("asleep", lambda _e: []), *rel_mod.ALL_RULES[1:]))
    assert rel_mod.self_test() == 1
    assert "did NOT notice it" in capsys.readouterr().err


def test_the_self_test_notices_a_rule_that_complains_about_sound_evidence(monkeypatch,
                                                                         capsys):
    monkeypatch.setattr(rel_mod, "ALL_RULES",
                        (("shouty", lambda _e: ["always"]), *rel_mod.ALL_RULES[1:]))
    assert rel_mod.self_test() == 1
    assert "complains about sound evidence" in capsys.readouterr().err


def test_the_self_test_notices_a_rule_that_judges_what_it_never_asked(monkeypatch,
                                                                     capsys):
    """A rule must stay silent on a field that was never filled in."""

    monkeypatch.setattr(rel_mod, "ONLINE_RULES",
                        (("guessing", lambda _e: ["a verdict with no evidence"]),))
    monkeypatch.setattr(rel_mod, "ALL_RULES",
                        rel_mod.OFFLINE_RULES + rel_mod.ONLINE_RULES)
    assert rel_mod.self_test() == 1
    assert "judges by what it never asked about" in capsys.readouterr().err


def test_the_version_may_not_live_in_two_places():
    sound = rel_mod.sound_evidence()
    assert rel_mod.rule_package_version(sound) == []
    from dataclasses import replace

    none = replace(sound, version_source=("none", ""))
    assert "declares no __version__" in rel_mod.rule_package_version(none)[0]

    stale = replace(sound, version_source=("literal", "0.1.0"))
    assert "parted ways with pyproject.toml" in rel_mod.rule_package_version(stale)[0]

    agreeing = replace(sound, version_source=("literal", sound.version))
    assert "as a literal" in rel_mod.rule_package_version(agreeing)[0]


def test_a_formula_the_pattern_no_longer_matches_stops_the_check():
    with pytest.raises(SystemExit, match="no url declaration"):
        rel_mod.formula_fields("nothing here")
    with pytest.raises(SystemExit, match="no sha256 declaration"):
        rel_mod.formula_fields('  url "https://x/y.tar.gz"\n')
    with pytest.raises(SystemExit, match="carries no version tag"):
        rel_mod.formula_fields('  url "https://x/y.tar.gz"\n  sha256 "' + "a" * 64 + '"\n')


def test_the_formula_fields_are_read_past_the_comments():
    text = ('# url "https://x/archive/refs/tags/v9.9.9.tar.gz"\n'
            '  url "https://x/archive/refs/tags/v0.5.0.tar.gz"\n'
            '  sha256 "' + "b" * 64 + '"\n')
    url, tag, sha = rel_mod.formula_fields(text)
    assert (tag, sha) == ("0.5.0", "b" * 64)
    assert url.endswith("v0.5.0.tar.gz")


def test_github_rate_limiting_is_unreachable_not_a_disagreement(monkeypatch):
    _serve(monkeypatch, {"https://api.github.com/x": 429})
    with pytest.raises(rel_mod.UnreachableError, match="rate-limiting us"):
        rel_mod.fetch("https://api.github.com/x")


def test_any_other_answer_from_github_says_the_code(monkeypatch):
    _serve(monkeypatch, {"https://api.github.com/x": 502})
    with pytest.raises(rel_mod.UnreachableError, match="answered 502"):
        rel_mod.fetch("https://api.github.com/x")


def test_no_network_at_all_is_unreachable(monkeypatch):
    _serve(monkeypatch, {"https://api.github.com/x": urllib.error.URLError("no route")})
    with pytest.raises(rel_mod.UnreachableError, match="no route"):
        rel_mod.fetch("https://api.github.com/x")


def test_a_token_in_the_environment_is_sent_only_to_the_api(monkeypatch):
    seen: list[dict[str, str]] = []

    def fake_urlopen(request, timeout=0):
        seen.append(dict(request.headers))
        return _Response(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    rel_mod.fetch("https://api.github.com/x", accept="application/vnd.github+json")
    rel_mod.fetch("https://example.invalid/y")
    assert "Authorization" in seen[0]
    assert "Authorization" not in seen[1]


def test_a_tap_answer_in_an_unexpected_encoding_is_unreachable(monkeypatch):
    """GitHub answers `"encoding": "none"` for a blob over a megabyte."""

    monkeypatch.setattr(rel_mod, "fetch",
                        lambda *_a, **_k: b'{"encoding": "none", "content": ""}')
    with pytest.raises(rel_mod.UnreachableError, match="unexpected encoding"):
        rel_mod.published_formula()


def test_the_published_formula_comes_back_decoded(monkeypatch):
    import base64

    payload = {"encoding": "base64",
               "content": base64.b64encode(b"formula\n").decode("ascii")}
    monkeypatch.setattr(rel_mod, "fetch", lambda *_a, **_k: json.dumps(payload).encode())
    assert rel_mod.published_formula() == b"formula\n"


def test_git_that_cannot_be_run_makes_the_tags_unreachable(monkeypatch):
    def refuse(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(rel_mod.UnreachableError, match="git ls-remote"):
        rel_mod.remote_tags()


def test_the_tags_come_back_in_version_order(monkeypatch):
    out = ("aaa\trefs/tags/v0.10.0\n"
           "bbb\trefs/tags/v0.2.0\n"
           "ccc\trefs/tags/not-a-version\n")
    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_k: subprocess.CompletedProcess([], 0, out, ""))
    assert rel_mod.remote_tags() == ["0.2.0", "0.10.0"]


def test_the_archive_is_downloaded_and_hashed(monkeypatch):
    import hashlib

    monkeypatch.setattr(rel_mod, "fetch", lambda *_a, **_k: b"archive bytes")
    assert rel_mod.archive_sha("https://x/y") == hashlib.sha256(b"archive bytes").hexdigest()


def test_a_git_less_checkout_leaves_the_history_and_the_modes_empty(monkeypatch):
    def refuse(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", refuse)
    assert rel_mod.sha_history() == {}
    assert rel_mod.git_modes() == {}


def test_the_offline_run_uses_only_what_the_tree_shows(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv",
                        ["check_release_published.py", "--offline"])
    assert rel_mod.main() == 0
    assert "agrees in the tree" in capsys.readouterr().out


def test_an_unreachable_outside_is_code_two_and_names_what_was_missed(monkeypatch,
                                                                     capsys):
    evidence, _ = rel_mod.collect(online=False)
    monkeypatch.setattr(rel_mod, "collect",
                        lambda *, online: (evidence, ["repository tags: no route"]))
    monkeypatch.setattr(sys, "argv", ["check_release_published.py"])
    assert rel_mod.main() == 2
    err = capsys.readouterr().err
    assert "The check did NOT happen" in err
    assert "repository tags: no route" in err


def test_a_disagreement_also_names_what_could_not_be_asked(monkeypatch, capsys):
    from dataclasses import replace

    evidence, _ = rel_mod.collect(online=False)
    broken = replace(evidence, version_source=("literal", "0.0.1"))
    monkeypatch.setattr(rel_mod, "collect",
                        lambda *, online: (broken, ["release archive: no route"]))
    monkeypatch.setattr(sys, "argv", ["check_release_published.py"])
    assert rel_mod.main() == 1
    err = capsys.readouterr().err
    assert "parted ways with what is visible from outside" in err
    assert "And these were not asked either" in err


def test_collecting_online_records_every_failure_it_met(monkeypatch):
    def unreachable(*_a, **_k):
        raise rel_mod.UnreachableError("no route")

    monkeypatch.setattr(rel_mod, "remote_tags", unreachable)
    monkeypatch.setattr(rel_mod, "archive_sha", unreachable)
    monkeypatch.setattr(rel_mod, "published_formula", unreachable)
    _evidence, missed = rel_mod.collect(online=True)
    assert len(missed) == 3
    assert missed[0].startswith("repository tags:")


def test_collecting_online_fills_the_evidence_in(monkeypatch):
    monkeypatch.setattr(rel_mod, "remote_tags", lambda: ["0.5.0"])
    monkeypatch.setattr(rel_mod, "archive_sha", lambda _url: "c" * 64)
    monkeypatch.setattr(rel_mod, "published_formula", lambda: b"copy")
    evidence, missed = rel_mod.collect(online=True)
    assert missed == []
    assert (evidence.tags, evidence.archive_sha, evidence.published) == (
        ["0.5.0"], "c" * 64, b"copy")


def test_a_file_missing_from_git_is_a_file_asdf_will_not_find():
    """On a checkout with no `.git` every mode is absent — say so, do not guess."""

    from dataclasses import replace

    e = replace(rel_mod.sound_evidence(), git_modes={})
    problems = rel_mod.rule_exec_bits(e)
    assert len(problems) == len(rel_mod.ASDF_SCRIPTS)
    assert all("not in git" in p for p in problems)


def test_a_published_formula_with_no_version_tag_says_so():
    from dataclasses import replace

    e = replace(rel_mod.sound_evidence(), published=b'  url "https://x/y.tar.gz"\n')
    assert "no archive URL with a version tag" in rel_mod.rule_tap_not_stale(e)[0]


def test_the_version_source_is_read_out_of_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rel_mod, "ROOT", tmp_path)
    (tmp_path / "ouroboros").mkdir()
    init = tmp_path / "ouroboros" / "__init__.py"

    init.write_text("x = 1\n", encoding="utf-8")
    assert rel_mod.version_source() == ("none", "")

    init.write_text('__version__ = "0.5.0"\n', encoding="utf-8")
    assert rel_mod.version_source() == ("literal", "0.5.0")

    init.write_text(
        'import importlib.metadata\n__version__ = importlib.metadata.version("x")\n',
        encoding="utf-8")
    assert rel_mod.version_source() == ("metadata", "")

    # A `__version__` that is neither a number nor read from the metadata: the
    # rule has nothing to complain about, so it says "metadata" and stays quiet.
    init.write_text('__version__ = "unknown"\n', encoding="utf-8")
    assert rel_mod.version_source() == ("metadata", "")


def test_the_checksum_history_skips_commits_it_cannot_read(monkeypatch):
    """A formula that did not exist in an old commit is not evidence of anything."""

    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str:
        calls.append(args)
        if args[0] == "log":
            return "aaa\nbbb\nccc\n"
        if args[1].startswith("aaa"):
            raise subprocess.CalledProcessError(128, "git")
        if args[1].startswith("bbb"):
            return '  url "https://x/y.tar.gz"\n  sha256 "' + "d" * 64 + '"\n'
        return ('  url "https://x/archive/refs/tags/v0.4.0.tar.gz"\n'
                '  sha256 "' + "e" * 64 + '"\n')

    monkeypatch.setattr(rel_mod, "_git", fake_git)
    assert rel_mod.sha_history() == {"e" * 64: {"0.4.0"}}


def test_the_git_modes_are_read_off_the_index(monkeypatch):
    monkeypatch.setattr(rel_mod, "_git",
                        lambda *_a: "100755 aaa 0\tbin/download\nnot a row\n")
    assert rel_mod.git_modes() == {"bin/download": "100755"}


def test_the_release_check_runs_its_self_test_from_the_command_line(monkeypatch,
                                                                   capsys):
    monkeypatch.setattr(sys, "argv",
                        ["check_release_published.py", "--self-test"])
    assert rel_mod.main() == 0
    assert "The check is checked" in capsys.readouterr().out


def test_a_disagreement_with_nothing_missed_prints_only_the_disagreement(monkeypatch,
                                                                        capsys):
    from dataclasses import replace

    evidence, _ = rel_mod.collect(online=False)
    broken = replace(evidence, version_source=("literal", "0.0.1"))
    monkeypatch.setattr(rel_mod, "collect", lambda *, online: (broken, []))
    monkeypatch.setattr(sys, "argv", ["check_release_published.py"])
    assert rel_mod.main() == 1
    err = capsys.readouterr().err
    assert "And these were not asked either" not in err
