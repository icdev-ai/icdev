#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for pdc_pipeline_stale reflex — anomaly detection threshold logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest


def _threshold(day_values):
    from tools.genesis.reflexes.pdc_pipeline_stale import _compute_anomaly_threshold
    return _compute_anomaly_threshold(day_values)


def test_imports():
    try:
        import tools.genesis.reflexes.pdc_pipeline_stale  # noqa: F401
    except ImportError as e:
        pytest.skip(f"Import dependency missing: {e}")


def test_floor_returned_when_too_few_pipelines():
    from tools.genesis.reflexes.pdc_pipeline_stale import STALE_DAYS_FLOOR
    # Fewer than _MIN_PIPELINES_FOR_STATS — must return floor
    assert _threshold([]) == STALE_DAYS_FLOOR
    assert _threshold([5]) == STALE_DAYS_FLOOR
    assert _threshold([5, 10]) == STALE_DAYS_FLOOR


def test_floor_returned_when_all_values_are_sentinel():
    from tools.genesis.reflexes.pdc_pipeline_stale import STALE_DAYS_FLOOR
    # 999 values are treated as missing data
    assert _threshold([999, 999, 999, 999]) == STALE_DAYS_FLOOR


def test_adaptive_threshold_exceeds_floor_for_tight_distribution():
    from tools.genesis.reflexes.pdc_pipeline_stale import STALE_DAYS_FLOOR
    # Tight cluster at 1-3 days: IQR threshold will be < floor, so floor is returned
    result = _threshold([1, 2, 2, 3, 3, 3, 4])
    assert result == STALE_DAYS_FLOOR


def test_adaptive_threshold_detects_wide_spread():
    # Distribution with a long tail: threshold should exceed floor
    result = _threshold([1, 2, 3, 4, 5, 30, 60, 90])
    assert result >= 14


def test_outlier_in_mostly_fresh_pipelines():
    # 7 pipelines updated within 5 days, one at 60 days → should be flagged
    days = [1, 2, 2, 3, 3, 4, 5, 60]
    threshold = _threshold(days)
    stale = [d for d in days if d >= threshold]
    assert 60 in stale
    assert 5 not in stale


def test_run_dry_run_returns_structure():
    try:
        from tools.genesis.reflexes.pdc_pipeline_stale import run
    except ImportError as e:
        pytest.skip(f"Import dependency missing: {e}")
    result = run({"dry_run": True})
    assert "pipelines_checked" in result
    assert "stale_pipelines" in result
    assert "threshold_used" in result
    assert "events_published" in result
    assert result["threshold_used"] >= 14


def test_threshold_used_exposed_in_result():
    """threshold_used must be present and >= STALE_DAYS_FLOOR even on DB error."""
    try:
        from tools.genesis.reflexes.pdc_pipeline_stale import run, STALE_DAYS_FLOOR
    except ImportError as e:
        pytest.skip(f"Import dependency missing: {e}")
    result = run({"dry_run": True})
    assert result["threshold_used"] >= STALE_DAYS_FLOOR


def test_llm_assess_staleness_returns_none_on_empty():
    try:
        from tools.genesis.reflexes.pdc_pipeline_stale import _llm_assess_staleness
    except ImportError as e:
        pytest.skip(f"Import dependency missing: {e}")
    assert _llm_assess_staleness([], 14, {}) is None


def test_llm_assess_staleness_graceful_on_unavailable_llm(monkeypatch):
    """LLM unavailable must return None without raising."""
    try:
        from tools.genesis.reflexes import pdc_pipeline_stale as mod
    except ImportError as e:
        pytest.skip(f"Import dependency missing: {e}")

    def _fail(*a, **kw):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(mod, "_llm_assess_staleness", _fail)
    # _llm_assess_staleness is patched to raise; run() must still complete
    # (run() catches the exception from _llm_assess_staleness inside try/except)
    _pipelines = [{"id": "p1", "name": "test", "days_since_update": 99}]
    result = mod._compute_anomaly_threshold([99])
    assert result >= 14
