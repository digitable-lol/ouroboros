"""Tests for the locate-then-splice primitives."""

from __future__ import annotations

import pytest

from ouroboros.languages.base import (
    Edit,
    apply_edits,
    leading_whitespace,
    line_start_offsets,
)


def test_insertion():
    assert apply_edits("abcdef", [Edit(3, 3, "XY")]) == "abcXYdef"


def test_replacement():
    assert apply_edits("abcdef", [Edit(1, 3, "Z")]) == "aZdef"


def test_multiple_edits_apply_right_to_left():
    src = "0123456789"
    edits = [Edit(0, 0, "A"), Edit(5, 5, "B"), Edit(10, 10, "C")]
    assert apply_edits(src, edits) == "A01234B56789C"


def test_adjacent_insertions_keep_list_order():
    # two insertions at the same offset preserve their original order
    assert apply_edits("XY", [Edit(1, 1, "a"), Edit(1, 1, "b")]) == "XabY"


def test_overlapping_edits_raise():
    with pytest.raises(ValueError, match="overlapping"):
        apply_edits("abcdef", [Edit(1, 4, "X"), Edit(2, 5, "Y")])


def test_invalid_range_rejected():
    with pytest.raises(ValueError):
        Edit(5, 2, "x")


def test_line_start_offsets():
    src = "ab\ncd\n\nef"
    offs = line_start_offsets(src)
    # line 1 -> 0 ("ab"), line 2 -> 3 ("cd"), line 3 -> 6 (""), line 4 -> 7 ("ef")
    assert offs[1] == 0
    assert offs[2] == 3
    assert offs[3] == 6
    assert offs[4] == 7
    assert src[offs[4]:] == "ef"


def test_leading_whitespace():
    src = "def f():\n        return 1\n"
    line2 = line_start_offsets(src)[2]
    assert leading_whitespace(src, line2) == "        "
    assert leading_whitespace(src, 0) == ""
