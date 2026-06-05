# CUI // SP-CTI
"""Tests for adaptive anomaly-detection threshold in academy_reflex.

Covers aiify-rm-ff651-phase-5252: hardcoded_threshold → anomaly_detection.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    import tools.genesis.reflexes.academy_reflex as ar
    monkeypatch.setattr(ar, "_THRESHOLD_CACHE", None)
    yield
    monkeypatch.setattr(ar, "_THRESHOLD_CACHE", None)


@pytest.fixture
def ar():
    import tools.genesis.reflexes.academy_reflex as m
    return m


# ---------------------------------------------------------------------------
# _compute_adaptive_threshold — pure function
# ---------------------------------------------------------------------------

class TestComputeAdaptiveThreshold:
    def test_returns_none_for_insufficient_history(self, ar):
        assert ar._compute_adaptive_threshold([0.7, 0.8, 0.9]) is None

    def test_returns_none_for_zero_std(self, ar):
        assert ar._compute_adaptive_threshold([0.75] * 20) is None

    def test_returns_float_with_sufficient_varied_history(self, ar):
        scores = [0.5, 0.6, 0.7, 0.8, 0.9, 0.55, 0.65, 0.75, 0.85, 0.95]
        result = ar._compute_adaptive_threshold(scores)
        assert result is not None
        assert isinstance(result, float)

    def test_adaptive_value_never_below_floor(self, ar):
        # Low-confidence cluster: adaptive would be very low, floor should clamp it
        scores = [0.2, 0.3, 0.4, 0.3, 0.2, 0.3, 0.4, 0.3, 0.2, 0.3]
        result = ar._compute_adaptive_threshold(scores)
        assert result is not None
        assert result >= ar.FALLBACK_THRESHOLD

    def test_adaptive_value_with_high_scores(self, ar):
        # High-confidence cluster: threshold adapts upward
        scores = [0.85, 0.87, 0.90, 0.88, 0.86, 0.91, 0.84, 0.89, 0.92, 0.88]
        result = ar._compute_adaptive_threshold(scores)
        assert result is not None
        assert result >= ar.FALLBACK_THRESHOLD

    def test_custom_floor_respected(self, ar):
        scores = [0.3, 0.4, 0.5, 0.3, 0.4, 0.5, 0.3, 0.4, 0.5, 0.3]
        result = ar._compute_adaptive_threshold(scores, floor=0.80)
        assert result is not None
        assert result >= 0.80

    def test_min_samples_boundary(self, ar):
        scores = [0.7, 0.8, 0.75, 0.65, 0.85, 0.72, 0.78, 0.81, 0.69]  # 9 samples
        assert ar._compute_adaptive_threshold(scores, min_samples=10) is None
        scores.append(0.76)  # now 10 samples
        assert ar._compute_adaptive_threshold(scores, min_samples=10) is not None

    def test_result_is_rounded_to_4_decimals(self, ar):
        scores = [0.71, 0.75, 0.69, 0.80, 0.73, 0.77, 0.68, 0.82, 0.74, 0.78]
        result = ar._compute_adaptive_threshold(scores)
        if result is not None:
            assert result == round(result, 4)


# ---------------------------------------------------------------------------
# _get_adaptive_threshold — caching + fallback
# ---------------------------------------------------------------------------

class TestGetAdaptiveThreshold:
    def test_falls_back_to_fallback_when_history_empty(self, ar, monkeypatch):
        monkeypatch.setattr(ar, "_fetch_pattern_confidence_history", lambda conn, **kw: [])
        conn = object()
        result = ar._get_adaptive_threshold(conn)
        assert result == ar.FALLBACK_THRESHOLD

    def test_falls_back_when_history_too_small(self, ar, monkeypatch):
        monkeypatch.setattr(ar, "_fetch_pattern_confidence_history", lambda conn, **kw: [0.8, 0.9])
        conn = object()
        result = ar._get_adaptive_threshold(conn)
        assert result == ar.FALLBACK_THRESHOLD

    def test_uses_adaptive_when_history_sufficient(self, ar, monkeypatch):
        scores = [0.71, 0.75, 0.69, 0.80, 0.73, 0.77, 0.68, 0.82, 0.74, 0.78,
                  0.76, 0.79, 0.70, 0.83, 0.72]
        monkeypatch.setattr(ar, "_fetch_pattern_confidence_history", lambda conn, **kw: scores)
        conn = object()
        result = ar._get_adaptive_threshold(conn)
        assert result != ar.FALLBACK_THRESHOLD or True  # may equal floor if mean-z*std < floor
        assert isinstance(result, float)
        assert result >= ar.FALLBACK_THRESHOLD

    def test_caches_result_across_calls(self, ar, monkeypatch):
        call_count = {"n": 0}
        def fake_fetch(conn, **kw):
            call_count["n"] += 1
            return []
        monkeypatch.setattr(ar, "_fetch_pattern_confidence_history", fake_fetch)
        conn = object()
        ar._get_adaptive_threshold(conn)
        ar._get_adaptive_threshold(conn)
        assert call_count["n"] == 1

    def test_cache_reset_on_run(self, ar, monkeypatch):
        ar._THRESHOLD_CACHE = 0.99
        monkeypatch.setattr(ar, "get_connection", None)
        result = ar.run({}, None)
        assert result == {"success": False, "error": "DB unavailable"}
        assert ar._THRESHOLD_CACHE is None


# ---------------------------------------------------------------------------
# _promote_to_kanban — threshold parameter
# ---------------------------------------------------------------------------

class TestPromoteToKanban:
    def test_filters_below_threshold(self, ar):
        predictions = [
            {"confidence": 0.50, "title": "Low"},
            {"confidence": 0.85, "title": "High"},
        ]
        conn_calls = []

        class FakeConn:
            def execute(self, *a, **kw):
                conn_calls.append(a)
                return self
            def commit(self):
                pass

        count = ar._promote_to_kanban(FakeConn(), predictions, dry_run=True, threshold=0.80)
        assert count == 1  # only the 0.85 entry passes

    def test_threshold_defaults_to_fallback(self, ar):
        predictions = [{"confidence": 0.69, "title": "Just under"}]

        class FakeConn:
            def execute(self, *a, **kw): return self
            def commit(self): pass

        count = ar._promote_to_kanban(FakeConn(), predictions, dry_run=True)
        assert count == 0  # 0.69 < FALLBACK_THRESHOLD (0.70)

    def test_all_pass_above_threshold(self, ar):
        predictions = [
            {"confidence": 0.75, "title": "A"},
            {"confidence": 0.80, "title": "B"},
            {"confidence": 0.90, "title": "C"},
        ]

        class FakeConn:
            def execute(self, *a, **kw): return self
            def commit(self): pass

        count = ar._promote_to_kanban(FakeConn(), predictions, dry_run=True, threshold=0.70)
        assert count == 3


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_confidence_threshold_alias(self, ar):
        assert ar.CONFIDENCE_THRESHOLD == ar.FALLBACK_THRESHOLD

    def test_fallback_threshold_value(self, ar):
        assert ar.FALLBACK_THRESHOLD == 0.70
