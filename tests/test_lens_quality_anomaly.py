# CUI // SP-CTI
"""Tests for QualityLens adaptive anomaly detection.

Verifies that hardcoded thresholds have been replaced with statistically-derived,
config-driven adaptive thresholds and that prediction data includes observability keys.
"""
import json
import pytest
from unittest.mock import patch


# ── Helper-function unit tests ────────────────────────────────────────────────

def test_compute_stats_basic():
    from tools.oracle.lenses.lens_quality import _compute_stats
    stats = _compute_stats([10.0, 20.0, 30.0])
    assert stats["mean"] == pytest.approx(20.0)
    assert stats["n"] == 3.0
    assert stats["std"] > 0


def test_compute_stats_single_value():
    from tools.oracle.lenses.lens_quality import _compute_stats
    stats = _compute_stats([42.0])
    assert stats["mean"] == 42.0
    assert stats["std"] == 0.0


def test_compute_stats_empty():
    from tools.oracle.lenses.lens_quality import _compute_stats
    stats = _compute_stats([])
    assert stats["mean"] == 0.0
    assert stats["std"] == 0.0
    assert stats["n"] == 0


def test_zscore_basic():
    from tools.oracle.lenses.lens_quality import _zscore
    stats = {"mean": 10.0, "std": 2.0}
    assert _zscore(14.0, stats) == pytest.approx(2.0)
    assert _zscore(6.0, stats) == pytest.approx(-2.0)


def test_zscore_no_variance_returns_zero():
    from tools.oracle.lenses.lens_quality import _zscore
    stats = {"mean": 5.0, "std": 0.0}
    assert _zscore(100.0, stats) == 0.0


def test_load_quality_config_returns_all_defaults_when_no_file(tmp_path):
    from tools.oracle.lenses.lens_quality import _load_quality_config, _QUALITY_CONFIG_DEFAULTS
    with patch("tools.oracle.lenses.lens_quality.ICDEV_ROOT", tmp_path):
        cfg = _load_quality_config()
    for key, val in _QUALITY_CONFIG_DEFAULTS.items():
        assert cfg[key] == val, f"Default mismatch for {key}"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_lens():
    from tools.oracle.lenses.lens_quality import QualityLens
    return QualityLens()


def _uqs_analysis(scores: list) -> dict:
    history = [
        {"uqs_score": s, "computed_at": f"2024-01-{i+1:02d}T00:00:00"}
        for i, s in enumerate(scores)
    ]
    return {
        "uqs_history": history,
        "gate_history": [],
        "quality_snapshots": [],
        "recent_trends": [],
    }


def _gate_analysis(gate_results: list) -> dict:
    return {
        "uqs_history": [],
        "gate_history": gate_results,
        "quality_snapshots": [],
        "recent_trends": [],
    }


def _findings_analysis(findings_per_snapshot: list) -> dict:
    snapshots = [
        {"total_findings": n, "snapshot_at": f"2024-01-{i+1:02d}"}
        for i, n in enumerate(findings_per_snapshot)
    ]
    return {
        "uqs_history": [],
        "gate_history": [],
        "quality_snapshots": snapshots,
        "recent_trends": [],
    }


# ── UQS trend detection ───────────────────────────────────────────────────────

class TestUQSTrendDetection:
    def test_clear_decline_is_flagged(self):
        """A large UQS drop triggers a declining-trend prediction."""
        lens = _make_lens()
        # recent avg ≈ 61, older avg ≈ 80 → delta ≈ 19; well above adaptive warn threshold
        analysis = _uqs_analysis([60.0, 61.0, 62.0, 80.0, 81.0])
        predictions = lens.score(analysis)
        assert any(p.title == "UQS Declining Trend" for p in predictions)

    def test_stable_scores_not_flagged(self):
        """Negligible variation does not produce a declining-trend prediction."""
        lens = _make_lens()
        analysis = _uqs_analysis([75.0, 75.5, 76.0, 76.0, 76.5])
        predictions = lens.score(analysis)
        assert not any(p.title == "UQS Declining Trend" for p in predictions)

    def test_clear_improvement_is_flagged(self):
        """A large UQS improvement triggers an improving-trend prediction."""
        lens = _make_lens()
        # 7 old values at 70 warm up the EWMA; delta_rise (≈10.4) > warn_delta (≈9.6)
        analysis = _uqs_analysis([90.0, 91.0, 92.0, 70.0, 70.0, 70.0, 70.0, 70.0, 70.0, 70.0])
        predictions = lens.score(analysis)
        assert any(p.title == "UQS Improving Trend" for p in predictions)

    def test_decline_severity_critical_for_extreme_drop(self):
        """A very large UQS drop (multiple σ) produces 'critical' severity."""
        lens = _make_lens()
        # delta_decline=20 > crit_delta≈19.6 (2σ of spread 9.8) → critical
        analysis = _uqs_analysis([60.0, 60.0, 60.0, 80.0, 80.0])
        predictions = lens.score(analysis)
        preds = [p for p in predictions if p.title == "UQS Declining Trend"]
        assert len(preds) == 1
        assert preds[0].severity == "critical"

    def test_decline_severity_warning_for_moderate_drop(self):
        """A moderate UQS drop (between warn and crit σ) produces 'warning' severity."""
        lens = _make_lens()
        # high variance history → crit_delta≈18.5; delta_decline=17.5 < 18.5 → warning
        # scores: mean≈82, std≈9.3 → warn≈9.3, crit≈18.5
        analysis = _uqs_analysis([75.0, 70.0, 80.0, 90.0, 95.0])
        predictions = lens.score(analysis)
        preds = [p for p in predictions if p.title == "UQS Declining Trend"]
        assert len(preds) == 1
        assert preds[0].severity == "warning"

    def test_dynamic_warn_delta_present_in_prediction_data(self):
        """Prediction data exposes dynamic_warn_delta for threshold observability."""
        lens = _make_lens()
        analysis = _uqs_analysis([60.0, 61.0, 62.0, 80.0, 81.0])
        predictions = lens.score(analysis)
        preds = [p for p in predictions if p.title == "UQS Declining Trend"]
        assert len(preds) == 1
        assert "dynamic_warn_delta" in preds[0].data
        assert preds[0].data["dynamic_warn_delta"] > 0

    def test_fewer_than_three_uqs_samples_produces_no_prediction(self):
        """With < 3 history entries, no UQS trend prediction is emitted."""
        lens = _make_lens()
        analysis = _uqs_analysis([60.0, 80.0])
        predictions = lens.score(analysis)
        uqs_preds = [
            p for p in predictions
            if p.title in ("UQS Declining Trend", "UQS Improving Trend")
        ]
        assert uqs_preds == []


# ── Gate failure detection ────────────────────────────────────────────────────

class TestGateFailureDetection:
    def test_high_failure_rate_gate_is_flagged(self):
        """A gate with a high failure rate (≥ rate_warn threshold) is flagged."""
        lens = _make_lens()
        # 3/3 = 100% failure rate, well above 0.45 fallback threshold
        results = [
            {"gate_id": "CAT1", "status": "fail", "executed_at": f"2024-01-{i:02d}"}
            for i in range(1, 4)
        ]
        predictions = lens.score(_gate_analysis(results))
        assert any("CAT1" in p.title for p in predictions)

    def test_low_failure_rate_gate_not_flagged(self):
        """A gate with a low failure rate (well below rate_floor=0.35) is not flagged."""
        lens = _make_lens()
        # 2 failures out of 20 runs → rate ≈ 0.10, below default rate_floor=0.35
        results = (
            [{"gate_id": "MINOR", "status": "fail", "executed_at": f"2024-01-{i:02d}"} for i in range(1, 3)]
            + [{"gate_id": "MINOR", "status": "pass", "executed_at": f"2024-02-{i:02d}"} for i in range(1, 19)]
        )
        predictions = lens.score(_gate_analysis(results))
        assert not any("MINOR" in p.title for p in predictions)

    def test_anomalous_gate_flagged_among_peers(self):
        """Gate with anomalously high failure rate vs peers is flagged."""
        lens = _make_lens()
        # gate_A fails 7/8 (87.5%); gate_B fails 1/11 (9%) → gate_A flagged
        results = (
            [{"gate_id": "gate_A", "status": "fail", "executed_at": f"2024-01-{i:02d}"} for i in range(1, 8)]
            + [{"gate_id": "gate_A", "status": "pass", "executed_at": "2024-01-08"}]
            + [{"gate_id": "gate_B", "status": "fail", "executed_at": "2024-01-09"}]
            + [{"gate_id": "gate_B", "status": "pass", "executed_at": f"2024-01-{i:02d}"} for i in range(10, 20)]
        )
        predictions = lens.score(_gate_analysis(results))
        flagged = [p.data.get("gate_id") for p in predictions if p.category == "gate_failure_pattern"]
        assert "gate_A" in flagged

    def test_prediction_data_includes_rate_and_zscore(self):
        """Prediction data exposes failure_rate and rate_zscore for observability."""
        lens = _make_lens()
        results = [
            {"gate_id": "CAT1", "status": "fail", "executed_at": f"2024-01-{i:02d}"}
            for i in range(1, 6)
        ]
        predictions = lens.score(_gate_analysis(results))
        gate_preds = [p for p in predictions if p.category == "gate_failure_pattern"]
        assert len(gate_preds) >= 1
        assert "failure_rate" in gate_preds[0].data
        assert "rate_zscore" in gate_preds[0].data

    def test_severity_warning_for_moderate_failure_rate(self):
        """A gate with a failure rate between rate_floor and rate_crit gets 'warning'."""
        lens = _make_lens()
        # 4 failures / 8 runs = 50% → above 0.45 warn, below 0.75 crit → warning
        results = (
            [{"gate_id": "MED", "status": "fail", "executed_at": f"2024-01-{i:02d}"} for i in range(1, 5)]
            + [{"gate_id": "MED", "status": "pass", "executed_at": f"2024-01-{i+10:02d}"} for i in range(1, 5)]
        )
        predictions = lens.score(_gate_analysis(results))
        preds = [p for p in predictions if p.category == "gate_failure_pattern"]
        assert len(preds) >= 1
        assert preds[0].severity == "warning"

    def test_high_count_gate_is_critical(self):
        """A gate with failure rate ≥ rate_crit (0.75) gets 'critical' severity."""
        lens = _make_lens()
        # 6/6 = 100% → above default rate_crit=0.75 → critical
        results = [
            {"gate_id": "HIGH", "status": "fail", "executed_at": f"2024-01-{i:02d}"}
            for i in range(1, 7)
        ]
        predictions = lens.score(_gate_analysis(results))
        high_preds = [p for p in predictions if p.category == "gate_failure_pattern"]
        assert len(high_preds) >= 1
        assert high_preds[0].severity == "critical"


# ── Findings spike detection ──────────────────────────────────────────────────

class TestFindingsSpikeDetection:
    def test_large_spike_is_flagged(self):
        """A large findings jump above the dynamic threshold is flagged."""
        lens = _make_lens()
        # current=50 >> upper_warn of ~47 → flagged
        analysis = _findings_analysis([50, 20, 22, 19])
        predictions = lens.score(analysis)
        assert any(p.title == "Findings Growth Spike" for p in predictions)

    def test_small_absolute_count_not_flagged(self):
        """A large ratio with tiny absolute count is not flagged (below min_abs floor)."""
        lens = _make_lens()
        # ratio=2.0 but current=4 < min_abs=5 → not flagged
        analysis = _findings_analysis([4, 2, 2, 2])
        predictions = lens.score(analysis)
        assert not any(p.title == "Findings Growth Spike" for p in predictions)

    def test_modest_growth_within_normal_range_not_flagged(self):
        """Findings within 1σ of historical mean are not flagged."""
        lens = _make_lens()
        # Historical range 8-12 with std≈1.2; current=11 < upper_warn≈11.8 → not flagged
        analysis = _findings_analysis([11, 10, 9, 8, 12, 11, 10, 9])
        predictions = lens.score(analysis)
        assert not any(p.title == "Findings Growth Spike" for p in predictions)

    def test_prediction_data_includes_growth_ratio_and_zscore(self):
        """Prediction data exposes growth_ratio and z_score for observability."""
        lens = _make_lens()
        analysis = _findings_analysis([50, 20, 22, 19])
        predictions = lens.score(analysis)
        spike_preds = [p for p in predictions if p.category == "findings_spike"]
        assert len(spike_preds) == 1
        assert "growth_ratio" in spike_preds[0].data
        assert "z_score" in spike_preds[0].data

    def test_fewer_than_two_snapshots_produces_no_spike(self):
        """With only one snapshot, no findings spike is emitted."""
        lens = _make_lens()
        analysis = _findings_analysis([50])
        predictions = lens.score(analysis)
        assert not any(p.category == "findings_spike" for p in predictions)


# ── UQS window constants ──────────────────────────────────────────────────────

class TestUQSWindowConstants:
    def test_score_window_and_recent_window_exported(self):
        """_UQS_SCORE_WINDOW and _UQS_RECENT_WINDOW must be importable module constants."""
        from tools.oracle.lenses.lens_quality import _UQS_SCORE_WINDOW, _UQS_RECENT_WINDOW
        assert isinstance(_UQS_SCORE_WINDOW, int) and _UQS_SCORE_WINDOW >= 1
        assert isinstance(_UQS_RECENT_WINDOW, int) and _UQS_RECENT_WINDOW >= 1

    def test_recent_window_smaller_than_score_window(self):
        """recent_window must be < score_window so the 'older' slice is non-empty."""
        from tools.oracle.lenses.lens_quality import _UQS_SCORE_WINDOW, _UQS_RECENT_WINDOW
        assert _UQS_RECENT_WINDOW < _UQS_SCORE_WINDOW

    def test_avg_recent_and_avg_older_respect_score_window(self):
        """avg_recent/avg_older in prediction data come only from the windowed slice.

        Uses a moderate tail (not extreme) so it doesn't inflate std enough to suppress
        the prediction, while still being distinct from older_val — verifying that the
        tail is excluded from the avg computation.
        """
        from tools.oracle.lenses.lens_quality import _UQS_SCORE_WINDOW, _UQS_RECENT_WINDOW
        lens = _make_lens()
        recent_val = 60.0
        older_val = 90.0
        # Tail values deliberately distinct from older_val; moderate so std stays manageable.
        tail_val = 50.0
        recent = [recent_val] * _UQS_RECENT_WINDOW
        older = [older_val] * (_UQS_SCORE_WINDOW - _UQS_RECENT_WINDOW)
        tail = [tail_val] * 4
        analysis = _uqs_analysis(recent + older + tail)
        preds = lens.score(analysis)
        regression_preds = [p for p in preds if p.category == "quality_regression"]
        assert len(regression_preds) >= 1, "Expected a declining-trend prediction from windowed data"
        data = regression_preds[0].data
        # avg_recent must equal recent_val (tail not included)
        assert abs(data["avg_recent"] - recent_val) < 0.01, (
            f"avg_recent={data['avg_recent']} should be {recent_val} (window-only)"
        )
        # avg_older must equal older_val, not contaminated by tail_val
        assert abs(data["avg_older"] - older_val) < 0.01, (
            f"avg_older={data['avg_older']} should be {older_val}, not tail-contaminated"
        )

    def test_uqs_guard_uses_min_samples(self):
        """Score() must require at least _MIN_SAMPLES entries; fewer → no UQS predictions."""
        from tools.oracle.lenses.lens_quality import _MIN_SAMPLES
        lens = _make_lens()
        # Supply exactly _MIN_SAMPLES - 1 entries → no UQS prediction
        analysis = _uqs_analysis([60.0] * (_MIN_SAMPLES - 1))
        preds = lens.score(analysis)
        uqs_preds = [p for p in preds if p.title in ("UQS Declining Trend", "UQS Improving Trend")]
        assert uqs_preds == []


# ── LLM anomaly threshold calibration ────────────────────────────────────────

class TestLLMCalibration:
    def test_empty_inputs_returns_empty(self):
        """No data → skip LLM call and return {}."""
        from tools.oracle.lenses.lens_quality import _calibrate_quality_anomaly_thresholds
        assert _calibrate_quality_anomaly_thresholds([], [], {}) == {}

    def test_disabled_returns_empty(self):
        """_LLM_CAL_ENABLED=False short-circuits without calling LLM."""
        from tools.oracle.lenses.lens_quality import _calibrate_quality_anomaly_thresholds
        with patch("tools.oracle.lenses.lens_quality._LLM_CAL_ENABLED", False):
            result = _calibrate_quality_anomaly_thresholds([80.0, 90.0, 70.0], [0.5], [10.0])
        assert result == {}

    def test_llm_failure_returns_empty(self):
        """Any LLM exception → graceful {} fallback, no exception raised."""
        from tools.oracle.lenses.lens_quality import _calibrate_quality_anomaly_thresholds
        with patch("tools.oracle.lenses.lens_quality._LLM_CAL_ENABLED", True), \
             patch("tools.llm.router.LLMRouter", side_effect=Exception("unavailable")):
            result = _calibrate_quality_anomaly_thresholds([80.0, 90.0, 70.0], [0.5], [10.0])
        assert result == {}

    def test_llm_response_parsed_and_validated(self):
        """Valid LLM JSON is parsed; all five threshold keys are returned."""
        from tools.oracle.lenses.lens_quality import _calibrate_quality_anomaly_thresholds
        payload = json.dumps({
            "uqs_warn_delta": 3.0, "uqs_crit_delta": 6.0,
            "gate_rate_warn": 0.4, "gate_rate_crit": 0.7,
            "spike_multiplier": 1.8,
        })
        mock_response = type("R", (), {"content": payload})()
        mock_router = type("Router", (), {"invoke": lambda self, fn, req: mock_response})()
        with patch("tools.oracle.lenses.lens_quality._LLM_CAL_ENABLED", True), \
             patch("tools.llm.router.LLMRouter", return_value=mock_router), \
             patch("tools.llm.provider.LLMRequest"):
            result = _calibrate_quality_anomaly_thresholds([75.0, 80.0, 85.0, 70.0], [0.5, 0.3], [15.0, 10.0])
        assert result.get("uqs_warn_delta") == pytest.approx(3.0)
        assert result.get("uqs_crit_delta") == pytest.approx(6.0)
        assert result.get("gate_rate_warn") == pytest.approx(0.4)
        assert result.get("gate_rate_crit") == pytest.approx(0.7)
        assert result.get("spike_multiplier") == pytest.approx(1.8)

    def test_values_below_floor_are_clamped(self):
        """Values below validation floors are clamped to their minimum, not rejected."""
        from tools.oracle.lenses.lens_quality import (
            _calibrate_quality_anomaly_thresholds,
            _LLM_VAL_UQS_WARN_MIN, _LLM_VAL_UQS_CRIT_MIN,
            _LLM_VAL_RATE_WARN_MIN, _LLM_VAL_RATE_CRIT_MIN,
            _LLM_VAL_SPIKE_MUL_MIN,
        )
        payload = json.dumps({
            "uqs_warn_delta": 0.01, "uqs_crit_delta": 0.02,
            "gate_rate_warn": 0.001, "gate_rate_crit": 0.002,
            "spike_multiplier": 0.1,
        })
        mock_response = type("R", (), {"content": payload})()
        mock_router = type("Router", (), {"invoke": lambda self, fn, req: mock_response})()
        with patch("tools.oracle.lenses.lens_quality._LLM_CAL_ENABLED", True), \
             patch("tools.llm.router.LLMRouter", return_value=mock_router), \
             patch("tools.llm.provider.LLMRequest"):
            result = _calibrate_quality_anomaly_thresholds([75.0, 80.0, 85.0, 70.0], [0.5], [10.0])
        assert result["uqs_warn_delta"] >= _LLM_VAL_UQS_WARN_MIN
        assert result["uqs_crit_delta"] >= _LLM_VAL_UQS_CRIT_MIN
        assert result["gate_rate_warn"] >= _LLM_VAL_RATE_WARN_MIN
        assert result["gate_rate_crit"] >= _LLM_VAL_RATE_CRIT_MIN
        assert result["spike_multiplier"] >= _LLM_VAL_SPIKE_MUL_MIN

    def test_score_uses_calibrated_uqs_warn_delta_to_suppress(self):
        """A very high uqs_warn_delta from LLM suppresses a normally-flagged decline."""
        lens = _make_lens()
        # [60,61,62,80,81] normally triggers UQS Declining Trend
        analysis = _uqs_analysis([60.0, 61.0, 62.0, 80.0, 81.0])
        analysis["llm_calibrated_thresholds"] = {"uqs_warn_delta": 100.0, "uqs_crit_delta": 200.0}
        preds = lens.score(analysis)
        assert not any(p.title == "UQS Declining Trend" for p in preds)

    def test_score_uses_calibrated_uqs_warn_delta_to_surface(self):
        """A very low uqs_warn_delta from LLM surfaces a decline that would otherwise be ignored."""
        lens = _make_lens()
        # Tiny drop: avg_recent≈76, avg_older≈78, delta≈1.67; default warn_delta≈2.0 → not flagged
        analysis = _uqs_analysis([76.0, 76.0, 77.0, 78.0, 78.0])
        analysis["llm_calibrated_thresholds"] = {"uqs_warn_delta": 0.5, "uqs_crit_delta": 2.0}
        preds = lens.score(analysis)
        assert any(p.title == "UQS Declining Trend" for p in preds)

    def test_score_no_calibration_key_uses_defaults(self):
        """score() works normally when llm_calibrated_thresholds is absent from analysis."""
        lens = _make_lens()
        analysis = _uqs_analysis([60.0, 61.0, 62.0, 80.0, 81.0])
        # No llm_calibrated_thresholds key at all
        preds = lens.score(analysis)
        assert any(p.title == "UQS Declining Trend" for p in preds)

    def test_score_calibrated_gate_rate_warn_suppresses(self):
        """A very high gate_rate_warn from LLM suppresses a normally-flagged gate."""
        lens = _make_lens()
        results = [
            {"gate_id": "CAT1", "status": "fail", "executed_at": f"2024-01-{i:02d}"}
            for i in range(1, 4)
        ]
        analysis = _gate_analysis(results)
        analysis["llm_calibrated_thresholds"] = {"gate_rate_warn": 0.99, "gate_rate_crit": 1.0}
        preds = lens.score(analysis)
        assert not any(p.category == "gate_failure_pattern" for p in preds)

    def test_analyze_includes_llm_calibrated_thresholds_key(self):
        """analyze() always returns llm_calibrated_thresholds even when DBs are absent."""
        lens = _make_lens()
        with patch("tools.oracle.lenses.lens_quality.QDC_DB", type("P", (), {"exists": lambda self: False})()),\
             patch("tools.oracle.lenses.lens_quality.GENESIS_QDB", type("P", (), {"exists": lambda self: False})()):
            data = lens.analyze()
        assert "llm_calibrated_thresholds" in data
        assert isinstance(data["llm_calibrated_thresholds"], dict)
