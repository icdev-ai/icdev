"""Tests for genesis/feedback_collector.py — anomaly detection thresholds (aiify-rm-ff651-phase-5221)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import tools.genesis.feedback_collector as fc
from tools.genesis.feedback_collector import (
    _THRESHOLD_DEFAULTS,
    _read_genesis_anomaly_config,
    compute_anomaly_thresholds,
    prioritize_reflexes,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_feedback_file(directory: Path, date_str: str, summary: dict) -> Path:
    """Write a synthetic feedback file and return its path."""
    f = directory / f"feedback-{date_str}.json"
    f.write_text(
        json.dumps({"collected_at": f"{date_str}T00:00:00Z", "summary": summary}),
        encoding="utf-8",
    )
    return f


# ── _read_genesis_anomaly_config ──────────────────────────────────────────────


def test_read_genesis_anomaly_config_returns_dict():
    cfg = _read_genesis_anomaly_config()
    assert isinstance(cfg, dict)


def test_read_genesis_anomaly_config_missing_yaml_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "BASE_DIR", tmp_path)
    result = _read_genesis_anomaly_config()
    assert result == {}


# ── compute_anomaly_thresholds — fallback (insufficient history) ───────────────


def test_fallback_when_no_history(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "FEEDBACK_DIR", tmp_path / "feedback")
    thresholds = compute_anomaly_thresholds(min_samples=5)
    assert thresholds["runtime_failures"] == _THRESHOLD_DEFAULTS["runtime_failures"]
    assert thresholds["coverage_gaps"] == _THRESHOLD_DEFAULTS["coverage_gaps"]
    assert thresholds["high_demand_signals"] == _THRESHOLD_DEFAULTS["high_demand_signals"]


def test_fallback_when_fewer_than_min_samples(tmp_path, monkeypatch):
    feed_dir = tmp_path / "feedback"
    feed_dir.mkdir()
    monkeypatch.setattr(fc, "FEEDBACK_DIR", feed_dir)

    for i in range(3):
        _make_feedback_file(feed_dir, f"2026-01-0{i+1}", {"runtime_failures": 10, "coverage_gaps": 5, "high_demand_signals": 2, "trace_recommendations": 0})

    thresholds = compute_anomaly_thresholds(min_samples=5)
    # 3 samples < min_samples=5 → fallback
    assert thresholds["runtime_failures"] == _THRESHOLD_DEFAULTS["runtime_failures"]


# ── compute_anomaly_thresholds — adaptive (sufficient history) ─────────────────


def test_adaptive_threshold_above_mean(tmp_path, monkeypatch):
    feed_dir = tmp_path / "feedback"
    feed_dir.mkdir()
    monkeypatch.setattr(fc, "FEEDBACK_DIR", feed_dir)

    # 7 days of uniform data: runtime_failures=4 every day
    for i in range(7):
        _make_feedback_file(
            feed_dir, f"2026-01-{i+1:02d}",
            {"runtime_failures": 4, "coverage_gaps": 8, "high_demand_signals": 2, "trace_recommendations": 0},
        )

    thresholds = compute_anomaly_thresholds(z_score=2.0, min_samples=5)
    # Uniform data → std=0 → threshold == mean == 4.0
    assert thresholds["runtime_failures"] == pytest.approx(4.0)
    assert thresholds["coverage_gaps"] == pytest.approx(8.0)


def test_adaptive_threshold_with_variance(tmp_path, monkeypatch):
    feed_dir = tmp_path / "feedback"
    feed_dir.mkdir()
    monkeypatch.setattr(fc, "FEEDBACK_DIR", feed_dir)
    # Pin z_score=1.0 via config mock so the test is config-independent
    monkeypatch.setattr(fc, "_read_genesis_anomaly_config", lambda: {"z_score": 1.0, "min_samples": 5})

    # failures: 2, 4, 6, 8, 10  → mean=6, std=sqrt(8)≈2.83
    values = [2, 4, 6, 8, 10]
    for i, v in enumerate(values):
        _make_feedback_file(
            feed_dir, f"2026-01-{i+1:02d}",
            {"runtime_failures": v, "coverage_gaps": 5, "high_demand_signals": 1, "trace_recommendations": 0},
        )

    thresholds = compute_anomaly_thresholds(z_score=1.0, min_samples=5)
    expected_mean = sum(values) / len(values)  # 6.0
    expected_std = (sum((v - expected_mean) ** 2 for v in values) / len(values)) ** 0.5  # ~2.83
    assert thresholds["runtime_failures"] == pytest.approx(expected_mean + 1.0 * expected_std, rel=1e-6)


def test_maintainability_score_max_falls_back_to_defaults_when_no_db_data(tmp_path, monkeypatch):
    """Falls back to _THRESHOLD_DEFAULTS when code_quality_metrics returns no rows (< min_samples)."""
    feed_dir = tmp_path / "feedback"
    feed_dir.mkdir()
    monkeypatch.setattr(fc, "FEEDBACK_DIR", feed_dir)
    # _safe_query returns [] for code_quality_metrics (table missing or empty in test env)
    monkeypatch.setattr(fc, "_safe_query", lambda q, p=(): [])
    thresholds = compute_anomaly_thresholds()
    assert thresholds["maintainability_score_max"] == _THRESHOLD_DEFAULTS["maintainability_score_max"]


def test_maintainability_score_max_dynamic_from_db_with_enough_samples(tmp_path, monkeypatch):
    """With >= min_samples DB rows, maintainability_score_max = max(0, mean - z*std)."""
    feed_dir = tmp_path / "feedback"
    feed_dir.mkdir()
    monkeypatch.setattr(fc, "FEEDBACK_DIR", feed_dir)
    monkeypatch.setattr(fc, "_read_genesis_anomaly_config", lambda: {"min_samples": 5, "z_score": 2.0})

    scores = [60.0, 70.0, 65.0, 55.0, 80.0, 75.0]

    def _fake_safe_query(query, params=()):
        if "maintainability_score" in query:
            return [{"maintainability_score": v} for v in scores]
        return []

    monkeypatch.setattr(fc, "_safe_query", _fake_safe_query)

    thresholds = compute_anomaly_thresholds()

    mean = sum(scores) / len(scores)
    variance = sum((v - mean) ** 2 for v in scores) / len(scores)
    expected = max(0.0, mean - 2.0 * (variance ** 0.5))
    assert thresholds["maintainability_score_max"] == pytest.approx(expected, rel=1e-6)
    # Must differ from the static default when dynamic data is available
    assert thresholds["maintainability_score_max"] != _THRESHOLD_DEFAULTS["maintainability_score_max"]


def test_maintainability_score_max_clamped_to_zero(tmp_path, monkeypatch):
    """mean - z*std is clamped to >= 0.0 even with high z_score."""
    feed_dir = tmp_path / "feedback"
    feed_dir.mkdir()
    monkeypatch.setattr(fc, "FEEDBACK_DIR", feed_dir)
    monkeypatch.setattr(fc, "_read_genesis_anomaly_config", lambda: {"min_samples": 5, "z_score": 100.0})
    scores = [5.0, 6.0, 5.5, 4.5, 7.0, 6.5]

    monkeypatch.setattr(
        fc, "_safe_query",
        lambda q, p=(): [{"maintainability_score": v} for v in scores] if "maintainability_score" in q else [],
    )

    thresholds = compute_anomaly_thresholds()
    assert thresholds["maintainability_score_max"] >= 0.0


def test_collect_coverage_gaps_uses_dynamic_threshold_by_default(tmp_path, monkeypatch):
    """collect_coverage_gaps() without args uses the threshold from compute_anomaly_thresholds()."""
    import tools.genesis.feedback_collector as fc_mod

    dynamic_threshold = 38.7
    monkeypatch.setattr(fc_mod, "compute_anomaly_thresholds", lambda: {"maintainability_score_max": dynamic_threshold})

    captured_params: list = []
    monkeypatch.setattr(fc_mod, "_safe_query", lambda q, p=(): captured_params.append(p) or [])

    fc_mod.collect_coverage_gaps()

    assert len(captured_params) == 1
    assert captured_params[0] == (dynamic_threshold,)


def test_collect_coverage_gaps_explicit_max_score_bypasses_compute(tmp_path, monkeypatch):
    """Explicit max_score skips compute_anomaly_thresholds() entirely."""
    import tools.genesis.feedback_collector as fc_mod

    compute_called = []
    monkeypatch.setattr(fc_mod, "compute_anomaly_thresholds", lambda: compute_called.append(1) or {})
    monkeypatch.setattr(fc_mod, "_safe_query", lambda q, p=(): [])

    fc_mod.collect_coverage_gaps(max_score=30.0)

    assert not compute_called, "compute_anomaly_thresholds() must not be called when max_score is explicit"


# ── compute_anomaly_thresholds — config integration ───────────────────────────


def test_config_z_score_overrides_arg(tmp_path, monkeypatch):
    feed_dir = tmp_path / "feedback"
    feed_dir.mkdir()
    monkeypatch.setattr(fc, "FEEDBACK_DIR", feed_dir)

    for i in range(6):
        _make_feedback_file(
            feed_dir, f"2026-01-{i+1:02d}",
            {"runtime_failures": float(i), "coverage_gaps": 5, "high_demand_signals": 1, "trace_recommendations": 0},
        )

    # Patch config to return z_score=0.0 → threshold == mean
    monkeypatch.setattr(fc, "_read_genesis_anomaly_config", lambda: {"z_score": 0.0, "min_samples": 5})
    thresholds = compute_anomaly_thresholds(z_score=999.0, min_samples=5)
    values = [float(i) for i in range(6)]
    expected_mean = sum(values) / len(values)
    assert thresholds["runtime_failures"] == pytest.approx(expected_mean, rel=1e-6)


def test_config_min_samples_overrides_arg(tmp_path, monkeypatch):
    feed_dir = tmp_path / "feedback"
    feed_dir.mkdir()
    monkeypatch.setattr(fc, "FEEDBACK_DIR", feed_dir)

    # Only 3 samples
    for i in range(3):
        _make_feedback_file(
            feed_dir, f"2026-01-{i+1:02d}",
            {"runtime_failures": 10, "coverage_gaps": 5, "high_demand_signals": 2, "trace_recommendations": 0},
        )

    # Config says min_samples=2 → 3 samples is enough → adaptive mode
    monkeypatch.setattr(fc, "_read_genesis_anomaly_config", lambda: {"z_score": 0.0, "min_samples": 2})
    thresholds = compute_anomaly_thresholds()
    # z_score=0 → threshold == mean == 10.0
    assert thresholds["runtime_failures"] == pytest.approx(10.0)


# ── prioritize_reflexes ────────────────────────────────────────────────────────


def test_prioritize_no_data():
    with patch.object(fc, "get_latest", return_value={"error": "No feedback collected yet"}):
        result = prioritize_reflexes()
    assert result["priorities"] == {}
    assert result["reason"] == "no_feedback_data"


def test_prioritize_normal_signals_no_boosts():
    summary = {
        "runtime_failures": 0,
        "coverage_gaps": 0,
        "high_demand_signals": 0,
        "trace_recommendations": 0,
    }
    thresholds = {
        "runtime_failures": 5.0,
        "coverage_gaps": 10.0,
        "high_demand_signals": 3.0,
        "trace_recommendations": 0.0,
        "maintainability_score_max": 50.0,
    }
    with patch.object(fc, "get_latest", return_value={"collected_at": "2026-01-01T00:00:00Z", "summary": summary}):
        with patch.object(fc, "compute_anomaly_thresholds", return_value=thresholds):
            result = prioritize_reflexes()

    assert result["boost_count"] == 0
    for info in result["priorities"].values():
        assert info["priority"] == "normal"


def test_prioritize_anomalous_failures_boosts_heal_and_audit():
    summary = {
        "runtime_failures": 20,  # well above threshold of 5
        "coverage_gaps": 0,
        "high_demand_signals": 0,
        "trace_recommendations": 0,
    }
    thresholds = {
        "runtime_failures": 5.0,
        "coverage_gaps": 10.0,
        "high_demand_signals": 3.0,
        "trace_recommendations": 0.0,
        "maintainability_score_max": 50.0,
    }
    with patch.object(fc, "get_latest", return_value={"collected_at": "2026-01-01T00:00:00Z", "summary": summary}):
        with patch.object(fc, "compute_anomaly_thresholds", return_value=thresholds):
            result = prioritize_reflexes()

    assert result["priorities"]["heal"]["priority"] == "boost"
    assert result["priorities"]["audit"]["priority"] == "boost"
    assert result["boost_count"] == 2


def test_prioritize_trace_recommendations_boosts_learn():
    summary = {
        "runtime_failures": 0,
        "coverage_gaps": 0,
        "high_demand_signals": 0,
        "trace_recommendations": 1,
    }
    thresholds = {
        "runtime_failures": 5.0,
        "coverage_gaps": 10.0,
        "high_demand_signals": 3.0,
        "trace_recommendations": 0.0,
        "maintainability_score_max": 50.0,
    }
    with patch.object(fc, "get_latest", return_value={"collected_at": "2026-01-01T00:00:00Z", "summary": summary}):
        with patch.object(fc, "compute_anomaly_thresholds", return_value=thresholds):
            result = prioritize_reflexes()

    assert result["priorities"]["learn"]["priority"] == "boost"


def test_prioritize_includes_thresholds_in_result():
    summary = {"runtime_failures": 0, "coverage_gaps": 0, "high_demand_signals": 0, "trace_recommendations": 0}
    thresholds = {
        "runtime_failures": 7.5,
        "coverage_gaps": 12.3,
        "high_demand_signals": 4.1,
        "trace_recommendations": 0.0,
        "maintainability_score_max": 50.0,
    }
    with patch.object(fc, "get_latest", return_value={"collected_at": "2026-01-01T00:00:00Z", "summary": summary}):
        with patch.object(fc, "compute_anomaly_thresholds", return_value=thresholds):
            result = prioritize_reflexes()

    assert "thresholds" in result
    assert result["thresholds"]["runtime_failures"] == pytest.approx(7.5)
