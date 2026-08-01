# [CUI // SP-CTI]
"""Tests for attack_path_twin predicate evaluation — fail-closed on unknown predicate.

Covers the IQE predicate filter `_safe_eval_predicate`:
  1. Known predicate types still evaluate as before (==, >, `in`, boolean shorthand).
  2. An unrecognized predicate returns False (excludes the path) — fail-closed.
  3. The unknown-predicate case emits a logger.warning naming the predicate.

The evaluator input is constructed directly (a minimal path dict) rather than
standing up the whole attack-path twin.
"""

import logging

from tools.security_canvas.attack_path_twin import _safe_eval_predicate


def _make_path() -> dict:
    """Minimal path dict shaped like a replay_attack_paths() entry."""
    return {
        "risk_level": "critical",
        "total_cost": 3.5,
        "hop_count": 4,
        "has_classification_violation": True,
        "ttp_sequence": ["T1190", "T1055"],
    }


# ── Known predicates still evaluate as before ────────────────────────────────

def test_equality_predicate_match():
    path = _make_path()
    assert _safe_eval_predicate("risk_level == critical", path) is True
    assert _safe_eval_predicate("risk_level == low", path) is False


def test_numeric_comparison_predicate():
    path = _make_path()
    assert _safe_eval_predicate("hop_count > 2", path) is True
    assert _safe_eval_predicate("hop_count > 10", path) is False


def test_in_predicate_against_list_field():
    path = _make_path()
    assert _safe_eval_predicate("'T1190' in ttp_sequence", path) is True
    assert _safe_eval_predicate("'T9999' in ttp_sequence", path) is False


def test_boolean_shorthand_predicate():
    path = _make_path()
    assert _safe_eval_predicate("has_classification_violation", path) is True
    path["has_classification_violation"] = False
    assert _safe_eval_predicate("has_classification_violation", path) is False


def test_empty_predicate_includes_all():
    # Empty/whitespace predicate is an explicit "no filter" — still include.
    path = _make_path()
    assert _safe_eval_predicate("", path) is True
    assert _safe_eval_predicate("   ", path) is True


# ── Unknown predicate fails closed ───────────────────────────────────────────

def test_unknown_predicate_excludes():
    path = _make_path()
    # Not an operator expression and not an allowed boolean field.
    assert _safe_eval_predicate("bogus_field", path) is False
    assert _safe_eval_predicate("not_a_real_predicate", path) is False


def test_unknown_predicate_emits_warning(caplog):
    path = _make_path()
    with caplog.at_level(logging.WARNING, logger="tools.security_canvas.attack_path_twin"):
        result = _safe_eval_predicate("mystery_predicate", path)
    assert result is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a warning to be emitted for an unknown predicate"
    assert any("mystery_predicate" in r.getMessage() for r in warnings), (
        "warning should name the unrecognized predicate"
    )
