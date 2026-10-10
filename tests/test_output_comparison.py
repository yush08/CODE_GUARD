"""Output comparison semantics used by the judge."""

from __future__ import annotations

from app.services.judge_service import normalize_output, outputs_match


def test_crlf_is_normalised() -> None:
    assert normalize_output("5\r\n") == "5"
    assert outputs_match("5\r\n", "5\n")


def test_trailing_spaces_per_line_are_ignored() -> None:
    assert normalize_output("5   \n10\t\n") == "5\n10"


def test_trailing_blank_lines_are_ignored() -> None:
    assert outputs_match("5\n\n\n", "5")
    assert outputs_match("5\n\n", "5\n")


def test_inner_whitespace_is_significant() -> None:
    assert not outputs_match("1 2", "12")
    assert not outputs_match("1 2", "1  2")


def test_wrong_values_do_not_match() -> None:
    assert not outputs_match("4", "5")
    assert not outputs_match("", "5")


def test_exact_match_after_normalisation() -> None:
    assert outputs_match("1 2 3\r\n", "1 2 3\n")
