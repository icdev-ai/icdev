# CUI // SP-CTI
"""Tests for icdev.tools.kanban.pipeline._commit_subject.

_commit_subject(summary) returns the mojibake-repaired first non-empty line of a
commit summary, truncated to 100 chars (99 + ellipsis) when longer, plus a
"  (+N more)" suffix when extra non-empty lines exist; None for empty input.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from icdev.tools.kanban.pipeline import _commit_subject  # noqa: E402


# ── Empty / falsy input → None ────────────────────────────────────────────────

def test_none_returns_none():
    assert _commit_subject(None) is None


def test_empty_string_returns_none():
    assert _commit_subject("") is None


def test_whitespace_only_returns_none():
    # strip() then splitlines() yields no non-empty lines.
    assert _commit_subject("   \n  \t \n ") is None


# ── Single-line subjects ──────────────────────────────────────────────────────

def test_single_line_passthrough():
    assert _commit_subject("feat: add widget") == "feat: add widget"


def test_first_line_is_stripped():
    assert _commit_subject("  feat: trimmed  ") == "feat: trimmed"


def test_single_line_no_extra_suffix():
    # No trailing "(+N more)" when there is exactly one non-empty line.
    assert "(+" not in _commit_subject("fix: one line")


# ── Multi-line → "(+N more)" suffix ───────────────────────────────────────────

def test_two_lines_reports_one_more():
    assert _commit_subject("feat: first\nfeat: second") == "feat: first  (+1 more)"


def test_three_lines_reports_two_more():
    result = _commit_subject("a: one\nb: two\nc: three")
    assert result == "a: one  (+2 more)"


def test_blank_interior_lines_are_not_counted():
    # Blank lines are filtered before the extra-count is computed.
    result = _commit_subject("first commit\n\n\nsecond commit")
    assert result == "first commit  (+1 more)"


def test_leading_blank_lines_do_not_become_the_subject():
    assert _commit_subject("\n\nreal subject\ntail") == "real subject  (+1 more)"


# ── Truncation at 100 chars ───────────────────────────────────────────────────

def test_exactly_100_chars_not_truncated():
    subj = "x" * 100
    assert _commit_subject(subj) == subj


def test_101_chars_truncated_to_99_plus_ellipsis():
    result = _commit_subject("y" * 101)
    assert result == "y" * 99 + "…"
    assert len(result) == 100


def test_truncation_then_extra_suffix():
    # Truncation happens on the first line, then the "(+N more)" suffix is added.
    result = _commit_subject("z" * 150 + "\nsecond\nthird")
    assert result == "z" * 99 + "…" + "  (+2 more)"


# ── Mojibake repair ───────────────────────────────────────────────────────────

def test_mojibake_first_line_is_repaired():
    original = "feat: refactor — cleanup"  # contains an em-dash
    mojibake = original.encode("utf-8").decode("cp1252")
    assert mojibake != original  # sanity: it is actually corrupted
    assert _commit_subject(mojibake) == original


def test_clean_unicode_passes_through_unchanged():
    # No mojibake markers → returned verbatim (em-dash preserved).
    clean = "feat: proper — dash"
    assert _commit_subject(clean) == clean


def test_non_string_summary_is_coerced():
    # str(summary) is applied before splitlines(); a truthy non-str is stringified.
    assert _commit_subject(12345) == "12345"
