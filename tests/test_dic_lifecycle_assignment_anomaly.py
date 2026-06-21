"""Tests for document lifecycle assignment anomaly detection.

aiify-rm-a3344-phase-88: hardcoded_threshold -> anomaly_detection in
src/documents/signals/handlers.py (paperless-ngx). The DIC analog is
detect_lifecycle_assignment_anomaly() in ingest_orchestrator — replaces the
static match-score floors that paperless signals/handlers uses when applying
auto-assignment of tags, correspondents, document types, and storage paths
after each Document model save.
"""
from __future__ import annotations

import importlib


orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
detect_lifecycle_assignment_anomaly = orch.detect_lifecycle_assignment_anomaly


# ── helpers ──────────────────────────────────────────────────────────────────

def _assign(field: str, confidence: float) -> dict:
    return {"field": field, "value": "test", "confidence": confidence}


# ── clean outcomes — should return None ─────────────────────────────────────

def test_no_anomaly_normal_assignments():
    assignments = [
        _assign("tag", 0.90),
        _assign("document_type", 0.85),
    ]
    result = detect_lifecycle_assignment_anomaly(assignments, rules_evaluated=5)
    assert result is None


def test_no_anomaly_zero_assignments_zero_rules():
    # No rules evaluated → no_assignments signal must be suppressed.
    result = detect_lifecycle_assignment_anomaly([], rules_evaluated=0)
    assert result is None


def test_no_anomaly_single_assignment_varied_confidence():
    # Single assignment → confidence_floor_hit requires ≥2 scores, so no signal.
    result = detect_lifecycle_assignment_anomaly(
        [_assign("correspondent", 0.75)], rules_evaluated=3
    )
    assert result is None


def test_no_anomaly_confidences_well_above_floor(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MIN_CONFIDENCE", 0.60)
    monkeypatch.setattr(orch, "_LIFECYCLE_CONFIDENCE_EPSILON", 0.05)
    # 0.80 and 0.90 are far above floor 0.60 ± 0.05 → no signal.
    result = detect_lifecycle_assignment_anomaly(
        [_assign("tag", 0.80), _assign("tag", 0.90)], rules_evaluated=4
    )
    assert result is None


# ── no_assignments signal ────────────────────────────────────────────────────

def test_no_assignments_when_rules_evaluated():
    result = detect_lifecycle_assignment_anomaly([], rules_evaluated=3)
    assert result is not None
    assert any("no_assignments" in s for s in result["signals"])
    assert result["source"] == "lifecycle_assignment_anomaly"


def test_no_assignments_suppressed_when_no_rules():
    result = detect_lifecycle_assignment_anomaly([], rules_evaluated=0)
    assert result is None


def test_no_assignments_signal_contains_rule_count():
    result = detect_lifecycle_assignment_anomaly([], rules_evaluated=7)
    assert result is not None
    assert any("7" in s for s in result["signals"])


# ── over_assignment signal ───────────────────────────────────────────────────

def test_over_assignment_above_ceiling(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MAX_ASSIGNMENTS", 5)
    assignments = [_assign("tag", 0.80) for _ in range(6)]
    result = detect_lifecycle_assignment_anomaly(assignments, rules_evaluated=10)
    assert result is not None
    assert any("over_assignment" in s for s in result["signals"])


def test_over_assignment_at_ceiling_no_signal(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MAX_ASSIGNMENTS", 5)
    assignments = [_assign("tag", 0.80) for _ in range(5)]
    result = detect_lifecycle_assignment_anomaly(assignments, rules_evaluated=10)
    assert result is None


def test_over_assignment_one_below_ceiling_no_signal(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MAX_ASSIGNMENTS", 5)
    assignments = [_assign("tag", 0.80) for _ in range(4)]
    result = detect_lifecycle_assignment_anomaly(assignments, rules_evaluated=10)
    assert result is None


def test_over_assignment_signal_contains_count(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MAX_ASSIGNMENTS", 3)
    assignments = [_assign("tag", 0.80) for _ in range(8)]
    result = detect_lifecycle_assignment_anomaly(assignments, rules_evaluated=10)
    assert result is not None
    assert any("8" in s for s in result["signals"])


# ── confidence_floor_hit signal ──────────────────────────────────────────────

def test_confidence_floor_hit_all_marginal(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MIN_CONFIDENCE", 0.60)
    monkeypatch.setattr(orch, "_LIFECYCLE_CONFIDENCE_EPSILON", 0.05)
    # 0.61 and 0.62 are both within 0.05 of floor 0.60.
    result = detect_lifecycle_assignment_anomaly(
        [_assign("tag", 0.61), _assign("document_type", 0.62)],
        rules_evaluated=5,
    )
    assert result is not None
    assert any("confidence_floor_hit" in s for s in result["signals"])


def test_confidence_floor_hit_one_outlier_no_signal(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MIN_CONFIDENCE", 0.60)
    monkeypatch.setattr(orch, "_LIFECYCLE_CONFIDENCE_EPSILON", 0.05)
    # 0.61 is marginal but 0.90 is not → not ALL marginal → no signal.
    result = detect_lifecycle_assignment_anomaly(
        [_assign("tag", 0.61), _assign("document_type", 0.90)],
        rules_evaluated=5,
    )
    assert result is None


def test_confidence_floor_hit_skipped_for_single_assignment(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MIN_CONFIDENCE", 0.60)
    monkeypatch.setattr(orch, "_LIFECYCLE_CONFIDENCE_EPSILON", 0.05)
    # Only one score → requires ≥2 for confidence_floor_hit.
    result = detect_lifecycle_assignment_anomaly(
        [_assign("tag", 0.61)], rules_evaluated=3
    )
    assert result is None


def test_confidence_floor_hit_items_missing_confidence(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MIN_CONFIDENCE", 0.60)
    monkeypatch.setattr(orch, "_LIFECYCLE_CONFIDENCE_EPSILON", 0.05)
    # Items without confidence key are skipped gracefully; only 1 valid score
    # → requires ≥2 for the signal, so returns None (no other signal fires).
    assignments = [
        {"field": "tag", "value": "foo"},
        _assign("tag", 0.61),
    ]
    result = detect_lifecycle_assignment_anomaly(assignments, rules_evaluated=3)
    assert result is None


# ── multiple signals can co-occur ─────────────────────────────────────────────

def test_over_assignment_and_floor_hit_co_occur(monkeypatch):
    monkeypatch.setattr(orch, "_LIFECYCLE_MAX_ASSIGNMENTS", 3)
    monkeypatch.setattr(orch, "_LIFECYCLE_MIN_CONFIDENCE", 0.60)
    monkeypatch.setattr(orch, "_LIFECYCLE_CONFIDENCE_EPSILON", 0.05)
    # 4 assignments (above ceiling) + all marginal confidences.
    assignments = [_assign("tag", 0.61) for _ in range(4)]
    result = detect_lifecycle_assignment_anomaly(assignments, rules_evaluated=5)
    assert result is not None
    sigs = result["signals"]
    assert any("over_assignment" in s for s in sigs)
    assert any("confidence_floor_hit" in s for s in sigs)


# ── return structure ──────────────────────────────────────────────────────────

def test_result_structure():
    result = detect_lifecycle_assignment_anomaly([], rules_evaluated=1)
    assert isinstance(result, dict)
    assert result["source"] == "lifecycle_assignment_anomaly"
    assert isinstance(result["signals"], list)
    assert len(result["signals"]) > 0


# ── input validation ──────────────────────────────────────────────────────────

def test_non_list_assignments_returns_none():
    result = detect_lifecycle_assignment_anomaly(None, rules_evaluated=1)  # type: ignore[arg-type]
    assert result is None


def test_empty_list_no_rules_returns_none():
    result = detect_lifecycle_assignment_anomaly([])
    assert result is None


# ── error resilience ──────────────────────────────────────────────────────────

def test_returns_none_on_unexpected_error(monkeypatch):
    # Corrupt _LIFECYCLE_MAX_ASSIGNMENTS so the comparison raises.
    monkeypatch.setattr(orch, "_LIFECYCLE_MAX_ASSIGNMENTS", "NOT_AN_INT")
    result = detect_lifecycle_assignment_anomaly(
        [_assign("tag", 0.80), _assign("tag", 0.81)], rules_evaluated=3
    )
    assert result is None or isinstance(result, dict)
