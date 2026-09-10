"""The guard that keeps the landing page's claims equal to the measurement."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_landing_claims as guard

MEASURED = """
| who answered | answers | without | with | difference | interval |
|---|---|---|---|---|---|
| `qwen3.5:4b`, 4.7 billion weights | 600 | 44.0% | 78.3% | +34.3 | 19.7 … 47.7 |
| `qwen2.5:14b-instruct`, 14.8 billion | 600 | 61.0% | 84.7% | +23.7 | 12.3 … 35.0 |
| `qwen3:32b`, 32.8 billion | 600 | 66.7% | 90.3% | +23.6 | 10.3 … 36.3 |
| a Claude Opus 5 subagent | 120 | 95.0% | 98.3% | +3.3 | 0.0 … 8.3 |
"""

CLAIMED = """
| who answered | answers | without | with | difference |
|---|---|---|---|---|
| `qwen3.5:4b` | 600 | 44.0% | 78.3% | **+34.3** |
| `qwen2.5:14b-instruct` | 600 | 61.0% | 84.7% | **+23.7** |
| `qwen3:32b` | 600 | 66.7% | 90.3% | **+23.6** |
| a Claude Opus 5 subagent | 120 | 95.0% | 98.3% | +3.3 |
"""


def test_equal_tables_are_silent() -> None:
    assert guard.problems(MEASURED, CLAIMED) == []


def test_an_inflated_claim_is_named() -> None:
    louder = CLAIMED.replace("78.3%", "98.3%")
    (complaint,) = guard.problems(MEASURED, louder)
    assert "qwen3.5:4b" in complaint
    assert "parted ways" in complaint


def test_a_re_taken_measurement_leaves_the_claim_behind() -> None:
    retaken = MEASURED.replace("| 600 | 61.0% | 84.7% |", "| 600 | 61.0% | 71.2% |")
    (complaint,) = guard.problems(retaken, CLAIMED)
    assert "qwen2.5:14b-instruct" in complaint


def test_a_dropped_landing_row_is_named() -> None:
    without = "\n".join(x for x in CLAIMED.splitlines() if "qwen3:32b" not in x)
    (complaint,) = guard.problems(MEASURED, without)
    assert "dropped the row" in complaint


def test_a_dropped_measurement_row_is_named() -> None:
    without = "\n".join(x for x in MEASURED.splitlines() if "qwen3:32b" not in x)
    (complaint,) = guard.problems(without, CLAIMED)
    assert "no longer has a row" in complaint


def test_the_real_tree_agrees(capsys: pytest.CaptureFixture[str]) -> None:
    assert guard.main() == 0
    assert "every number agrees" in capsys.readouterr().out


def test_the_guard_refuses_when_they_disagree(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(guard, "problems", lambda *_a: ["a made-up disagreement"])
    monkeypatch.setattr(sys, "argv", ["check_landing_claims.py"])
    assert guard.main() == 1
    out = capsys.readouterr().out
    assert "a made-up disagreement" in out
    assert "trace-help/run.sh" in out


def test_the_self_test_passes_on_the_real_tree(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["check_landing_claims.py", "--self-test"])
    assert guard.main() == 0
    assert "all agreed" in capsys.readouterr().out


def test_the_self_test_notices_a_blinded_guard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(guard, "problems", lambda *_a: [])
    assert guard.self_test() == 1
    assert "disagreed with what was expected" in capsys.readouterr().out
