# CUI // SP-CTI
"""Tests for TrajectoryLens adaptive anomaly detection.

Verifies that hardcoded thresholds have been replaced with fleet-derived,
config-driven adaptive thresholds, and that the prediction pipeline correctly
falls back through: adaptive → llm_calibrated → fallback.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import tools.oracle.lens_trajectory as lt
from tools.oracle.lens_trajectory import (
    TrajectoryLens,
    _compute_adaptive_thresholds,
    _compute_velocity_thresholds,
    _llm_calibrate_thresholds,
)


# ── _compute_adaptive_thresholds ─────────────────────────────────────────────

def _make_file_trends(n_files: int, cc: float, maint: float) -> dict:
    """Build a minimal file_trends dict with n identical latest snapshots."""
    return {
        f"file_{i}.py": [{"week": "2024-01", "cc": cc, "maintainability": maint}]
        for i in range(n_files)
    }


class TestComputeAdaptiveThresholds:
    def test_sparse_fleet_returns_fallback_constants(self):
        trends = _make_file_trends(lt.ADAPTIVE_MIN_FILES - 1, cc=5.0, maint=0.8)
        result = _compute_adaptive_thresholds(trends)
        assert result["cc_threshold"] == pytest.approx(lt.CC_THRESHOLD)
        assert result["maintainability_floor"] == pytest.approx(lt.MAINTAINABILITY_FLOOR)
        assert result["adaptive"] is False

    def test_sufficient_fleet_enables_adaptive(self):
        trends = _make_file_trends(lt.ADAPTIVE_MIN_FILES, cc=10.0, maint=0.6)
        result = _compute_adaptive_thresholds(trends)
        assert result["adaptive"] is True

    def test_adaptive_cc_threshold_is_percentile(self):
        # All files have cc=10; 90th-percentile of a uniform distribution = 10
        trends = _make_file_trends(lt.ADAPTIVE_MIN_FILES, cc=10.0, maint=0.6)
        result = _compute_adaptive_thresholds(trends)
        assert result["cc_threshold"] == pytest.approx(10.0)

    def test_adaptive_maint_floor_is_low_percentile(self):
        # All files have maint=0.6; 10th-percentile of a uniform distribution = 0.6
        trends = _make_file_trends(lt.ADAPTIVE_MIN_FILES, cc=5.0, maint=0.6)
        result = _compute_adaptive_thresholds(trends)
        assert result["maintainability_floor"] == pytest.approx(0.6)

    def test_zero_cc_snapshots_excluded(self):
        # cc=0 entries are ignored; only positive values contribute
        trends = _make_file_trends(lt.ADAPTIVE_MIN_FILES, cc=0.0, maint=0.5)
        result = _compute_adaptive_thresholds(trends)
        # No positive cc values → cc stays at fallback, adaptive=False for cc side
        assert result["cc_threshold"] == pytest.approx(lt.CC_THRESHOLD)

    def test_empty_trends_returns_fallbacks(self):
        result = _compute_adaptive_thresholds({})
        assert result["cc_threshold"] == pytest.approx(lt.CC_THRESHOLD)
        assert result["maintainability_floor"] == pytest.approx(lt.MAINTAINABILITY_FLOOR)
        assert result["adaptive"] is False

    def test_most_recent_snapshot_used(self):
        # File has two snapshots; only the latest (higher cc) should count
        trends = {
            f"file_{i}.py": [
                {"week": "2024-01", "cc": 5.0, "maintainability": 0.9},
                {"week": "2024-02", "cc": 20.0, "maintainability": 0.3},
            ]
            for i in range(lt.ADAPTIVE_MIN_FILES)
        }
        result = _compute_adaptive_thresholds(trends)
        assert result["adaptive"] is True
        assert result["cc_threshold"] == pytest.approx(20.0)


# ── _compute_velocity_thresholds ─────────────────────────────────────────────

def _make_dir_activity(n_dirs: int, slope_val: float = 2.0) -> dict:
    """Build dir_activity with consistent rising weekly counts."""
    activity = {}
    for i in range(n_dirs):
        # 4 weeks of rising activity with given slope
        activity[f"dir_{i}"] = {
            "2024-01": int(slope_val * 1),
            "2024-02": int(slope_val * 2),
            "2024-03": int(slope_val * 3),
            "2024-04": int(slope_val * 4),
        }
    return activity


class TestComputeVelocityThresholds:
    def test_sparse_dirs_returns_fallback(self):
        activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES - 1)
        result = _compute_velocity_thresholds(activity)
        assert result["slope_cutoff"] == pytest.approx(lt.VELOCITY_FALLBACK_SLOPE)
        assert result["recent_avg_cutoff"] == pytest.approx(lt.VELOCITY_FALLBACK_RECENT_AVG)
        assert result["adaptive"] is False

    def test_sufficient_dirs_enables_adaptive(self):
        activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        result = _compute_velocity_thresholds(activity)
        assert result["adaptive"] is True

    def test_adaptive_slope_is_positive(self):
        activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        result = _compute_velocity_thresholds(activity)
        assert result["slope_cutoff"] > 0

    def test_dirs_below_min_snapshots_excluded(self):
        # dirs with fewer than MIN_SNAPSHOTS weeks are skipped
        activity = {
            f"dir_{i}": {"2024-01": 5}  # only 1 week
            for i in range(lt.ADAPTIVE_MIN_FILES + 5)
        }
        result = _compute_velocity_thresholds(activity)
        assert result["adaptive"] is False

    def test_empty_activity_returns_fallback(self):
        result = _compute_velocity_thresholds({})
        assert result["slope_cutoff"] == pytest.approx(lt.VELOCITY_FALLBACK_SLOPE)
        assert result["adaptive"] is False


# ── _llm_calibrate_thresholds ─────────────────────────────────────────────────

class TestLLMCalibrateThresholds:
    def _minimal_trends(self) -> dict:
        return _make_file_trends(5, cc=8.0, maint=0.7)

    def _minimal_activity(self) -> dict:
        return _make_dir_activity(3)

    def test_returns_empty_on_import_error(self):
        # LLMRouter is imported inside the function; simulate unavailability via router constructor
        with patch("tools.llm.router.LLMRouter", side_effect=ImportError("no llm")):
            result = _llm_calibrate_thresholds(self._minimal_trends(), self._minimal_activity())
        assert result == {}

    def test_returns_empty_on_llm_exception(self):
        with patch("tools.llm.router.LLMRouter", side_effect=Exception("boom")):
            result = _llm_calibrate_thresholds(self._minimal_trends(), self._minimal_activity())
        assert result == {}

    def test_valid_llm_response_accepted(self):
        payload = {
            "cc_threshold": 12.0,
            "maintainability_floor": 0.45,
            "slope_cutoff": 1.5,
            "recent_avg_cutoff": 3.0,
        }
        mock_response = MagicMock()
        mock_response.content = json.dumps(payload)
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_response

        with (
            patch("tools.llm.router.LLMRouter", return_value=mock_router),
            patch("tools.llm.provider.LLMRequest"),
        ):
            result = _llm_calibrate_thresholds(self._minimal_trends(), self._minimal_activity())

        assert result.get("cc_threshold") == pytest.approx(12.0)
        assert result.get("maintainability_floor") == pytest.approx(0.45)
        assert result.get("slope_cutoff") == pytest.approx(1.5)
        assert result.get("recent_avg_cutoff") == pytest.approx(3.0)

    def test_llm_values_below_validation_floor_rejected(self):
        payload = {
            "cc_threshold": 0.0,        # below LLM_VAL_CC_THRESHOLD_MIN (1.0)
            "slope_cutoff": 0.001,      # below LLM_VAL_SLOPE_CUTOFF_MIN (0.01)
            "recent_avg_cutoff": 0.005, # below LLM_VAL_RECENT_AVG_CUTOFF_MIN (0.01)
        }
        mock_response = MagicMock()
        mock_response.content = json.dumps(payload)
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_response

        with (
            patch("tools.llm.router.LLMRouter", return_value=mock_router),
            patch("tools.llm.provider.LLMRequest"),
        ):
            result = _llm_calibrate_thresholds(self._minimal_trends(), self._minimal_activity())

        assert "cc_threshold" not in result
        assert "slope_cutoff" not in result
        assert "recent_avg_cutoff" not in result

    def test_llm_markdown_fence_stripped(self):
        payload = {"cc_threshold": 10.0, "maintainability_floor": 0.4, "slope_cutoff": 1.0, "recent_avg_cutoff": 2.0}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        mock_response = MagicMock()
        mock_response.content = fenced
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_response

        with (
            patch("tools.llm.router.LLMRouter", return_value=mock_router),
            patch("tools.llm.provider.LLMRequest"),
        ):
            result = _llm_calibrate_thresholds(self._minimal_trends(), self._minimal_activity())

        assert result.get("cc_threshold") == pytest.approx(10.0)

    def test_llm_unparseable_json_returns_empty(self):
        mock_response = MagicMock()
        mock_response.content = "not json at all"
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_response

        with (
            patch("tools.llm.router.LLMRouter", return_value=mock_router),
            patch("tools.llm.provider.LLMRequest"),
        ):
            result = _llm_calibrate_thresholds(self._minimal_trends(), self._minimal_activity())

        assert result == {}


# ── TrajectoryLens.score() — anomaly detection pipeline ──────────────────────

def _build_rising_cc_trends(n_files: int = 15) -> dict:
    """Fleet with rising CC for n_files; 3 high-CC outliers push adaptive threshold to 20.

    Low-CC rising files (current ~4.5) are well below the 90th-percentile threshold
    (~20.0) so they generate CC-breach predictions.
    """
    trends = {}
    # Outliers set the 90th-percentile CC threshold high (~20)
    for i in range(3):
        trends[f"tools/outlier_{i}.py"] = [
            {"week": f"2024-{w:02d}", "cc": 20.0, "maintainability": 0.5}
            for w in range(1, 6)
        ]
    # Rising files: current CC ~4.5, well below the 20.0 threshold
    for i in range(n_files):
        trends[f"tools/module_{i}.py"] = [
            {"week": f"2024-{w:02d}", "cc": 2.0 + w * 0.5, "maintainability": 0.8}
            for w in range(1, 6)
        ]
    return trends


def _build_declining_maint_trends(n_files: int = 15) -> dict:
    """Fleet with declining maint for n_files; 3 low-maint outliers set the floor to ~0.1.

    Declining files (current ~0.65) are well above the 10th-percentile floor (~0.1)
    so they generate maintainability-decline predictions.
    """
    trends = {}
    # Outliers set the 10th-percentile maint floor low (~0.1)
    for i in range(3):
        trends[f"tools/low_maint_{i}.py"] = [
            {"week": f"2024-{w:02d}", "cc": 5.0, "maintainability": 0.1}
            for w in range(1, 6)
        ]
    # Declining files: current maint ~0.65, well above the 0.1 floor
    for i in range(n_files):
        trends[f"tools/module_{i}.py"] = [
            {"week": f"2024-{w:02d}", "cc": 5.0, "maintainability": 0.9 - w * 0.05}
            for w in range(1, 6)
        ]
    return trends


_NO_LLM = patch("tools.oracle.lens_trajectory._llm_calibrate_thresholds", return_value={})


class TestTrajectoryLensScoreAdaptive:
    """score() uses adaptive thresholds when fleet is large enough."""

    def _no_db_no_git_analysis(self, file_trends: dict, dir_activity: dict | None = None) -> dict:
        return {
            "file_trends": file_trends,
            "dir_activity": dir_activity or {},
            "dependents": {fp: 0 for fp in file_trends},
        }

    def test_adaptive_threshold_source_in_prediction_data(self):
        lens = TrajectoryLens()
        # Supply enough dirs so velocity is also adaptive → no LLM path triggered
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = self._no_db_no_git_analysis(_build_rising_cc_trends(), dir_activity)
        preds = lens.score(analysis)
        cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
        if cc_preds:
            assert cc_preds[0].data["threshold_source"] == "adaptive"

    def test_fallback_threshold_source_when_sparse(self):
        lens = TrajectoryLens()
        tiny_trends = {
            "a.py": [{"week": f"2024-{w:02d}", "cc": 2.0 + w, "maintainability": 0.8} for w in range(1, 5)],
            "b.py": [{"week": f"2024-{w:02d}", "cc": 2.0 + w, "maintainability": 0.8} for w in range(1, 5)],
        }
        analysis = self._no_db_no_git_analysis(tiny_trends)
        with _NO_LLM:
            preds = lens.score(analysis)
        # Only check predictions that carry threshold_source (CC/maint predictions, not hotspots)
        for p in preds:
            src = p.data.get("threshold_source")
            if src is not None:
                assert src in {"fallback", "llm_calibrated"}

    def test_cc_breach_prediction_emitted(self):
        lens = TrajectoryLens()
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = self._no_db_no_git_analysis(_build_rising_cc_trends(), dir_activity)
        preds = lens.score(analysis)
        cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
        assert len(cc_preds) > 0

    def test_cc_breach_prediction_has_required_keys(self):
        lens = TrajectoryLens()
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = self._no_db_no_git_analysis(_build_rising_cc_trends(), dir_activity)
        preds = lens.score(analysis)
        cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
        if cc_preds:
            data = cc_preds[0].data
            for key in ("current_value", "slope_per_week", "threshold", "threshold_source", "days_to_threshold"):
                assert key in data, f"missing key: {key}"

    def test_maintainability_decline_prediction_emitted(self):
        lens = TrajectoryLens()
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = self._no_db_no_git_analysis(_build_declining_maint_trends(), dir_activity)
        preds = lens.score(analysis)
        maint_preds = [p for p in preds if p.data.get("metric") == "maintainability_score"]
        assert len(maint_preds) > 0

    def test_prediction_severity_bounds(self):
        lens = TrajectoryLens()
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = self._no_db_no_git_analysis(_build_rising_cc_trends(), dir_activity)
        preds = lens.score(analysis)
        for p in preds:
            assert p.severity in {"critical", "warning", "info"}

    def test_prediction_confidence_in_range(self):
        lens = TrajectoryLens()
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = self._no_db_no_git_analysis(_build_rising_cc_trends(), dir_activity)
        preds = lens.score(analysis)
        for p in preds:
            assert 0.0 <= p.confidence <= 1.0

    def test_sorted_critical_first(self):
        lens = TrajectoryLens()
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = self._no_db_no_git_analysis(_build_rising_cc_trends(), dir_activity)
        preds = lens.score(analysis)
        sev_order = {"critical": 0, "warning": 1, "info": 2}
        orders = [sev_order[p.severity] for p in preds]
        assert orders == sorted(orders)

    def test_files_below_min_snapshots_skipped(self):
        lens = TrajectoryLens()
        trends = {
            f"file_{i}.py": [
                {"week": "2024-01", "cc": 3.0, "maintainability": 0.8},
                {"week": "2024-02", "cc": 5.0, "maintainability": 0.7},
            ]
            for i in range(20)
        }
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = {
            "file_trends": trends,
            "dir_activity": dir_activity,
            "dependents": {f"file_{i}.py": 0 for i in range(20)},
        }
        preds = lens.score(analysis)
        cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
        assert len(cc_preds) == 0


class TestTrajectoryLensHotspot:
    """Hotspot detection aggregates multiple rising-CC files per directory."""

    def _hotspot_trends(self, n_files: int = 5) -> dict:
        trends = {}
        for i in range(n_files):
            trends[f"tools/hotdir/module_{i}.py"] = [
                {"week": f"2024-{w:02d}", "cc": w * 2.0, "maintainability": 0.8}
                for w in range(1, 5)
            ]
        return trends

    def test_hotspot_prediction_emitted(self):
        lens = TrajectoryLens()
        trends = self._hotspot_trends(lt.HOTSPOT_MIN_FILES + 1)
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = {
            "file_trends": trends,
            "dir_activity": dir_activity,
            "dependents": {fp: 0 for fp in trends},
        }
        with _NO_LLM:
            preds = lens.score(analysis)
        hotspot_preds = [p for p in preds if "trending_file_count" in p.data]
        assert len(hotspot_preds) > 0

    def test_hotspot_prediction_directory_key(self):
        lens = TrajectoryLens()
        trends = self._hotspot_trends(lt.HOTSPOT_MIN_FILES + 1)
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = {
            "file_trends": trends,
            "dir_activity": dir_activity,
            "dependents": {fp: 0 for fp in trends},
        }
        with _NO_LLM:
            preds = lens.score(analysis)
        hotspot_preds = [p for p in preds if "trending_file_count" in p.data]
        if hotspot_preds:
            assert "directory" in hotspot_preds[0].data
            assert hotspot_preds[0].data["trending_file_count"] >= lt.HOTSPOT_MIN_FILES

    def test_single_file_dir_no_hotspot(self):
        lens = TrajectoryLens()
        trends = {
            "tools/solo/module.py": [
                {"week": f"2024-{w:02d}", "cc": w * 2.0, "maintainability": 0.8}
                for w in range(1, 5)
            ]
        }
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = {
            "file_trends": trends,
            "dir_activity": dir_activity,
            "dependents": {"tools/solo/module.py": 0},
        }
        with _NO_LLM:
            preds = lens.score(analysis)
        hotspot_preds = [p for p in preds if "trending_file_count" in p.data]
        assert len(hotspot_preds) == 0


class TestTrajectoryLensHighVelocity:
    """High commit-velocity directories are flagged when above fleet cutoffs."""

    def _velocity_analysis(self, slope_per_week: float = 3.0, n_dirs: int = 1) -> dict:
        # Always include enough dirs in the fleet for velocity to be adaptive
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        # Add the target directory with the specified slope
        dir_activity["tools/target_dir"] = {
            f"2024-{w:02d}": int(slope_per_week * w) for w in range(1, 5)
        }
        return {"file_trends": {}, "dir_activity": dir_activity, "dependents": {}}

    def test_high_velocity_prediction_emitted_above_fallback(self):
        lens = TrajectoryLens()
        analysis = self._velocity_analysis(slope_per_week=100.0)
        with _NO_LLM:
            preds = lens.score(analysis)
        vel_preds = [p for p in preds if "commit_slope_per_week" in p.data]
        assert len(vel_preds) > 0

    def test_low_velocity_no_prediction(self):
        lens = TrajectoryLens()
        analysis = {"file_trends": {}, "dir_activity": {}, "dependents": {}}
        with _NO_LLM:
            preds = lens.score(analysis)
        vel_preds = [p for p in preds if "commit_slope_per_week" in p.data]
        assert len(vel_preds) == 0

    def test_velocity_prediction_confidence_fixed(self):
        lens = TrajectoryLens()
        analysis = self._velocity_analysis(slope_per_week=100.0)
        with _NO_LLM:
            preds = lens.score(analysis)
        vel_preds = [p for p in preds if "commit_slope_per_week" in p.data]
        if vel_preds:
            assert vel_preds[0].confidence == pytest.approx(lt.VELOCITY_CONF_FIXED)


# ── propose() recommendations ─────────────────────────────────────────────────

class TestTrajectoryLensPropose:
    def _score_with_rising_cc(self) -> list:
        lens = TrajectoryLens()
        trends = _build_rising_cc_trends()
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = {
            "file_trends": trends,
            "dir_activity": dir_activity,
            "dependents": {fp: 0 for fp in trends},
        }
        preds = lens.score(analysis)
        return lens.propose(preds)

    def test_cc_breach_has_recommendations(self):
        preds = self._score_with_rising_cc()
        cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
        if cc_preds:
            assert len(cc_preds[0].recommendations) > 0

    def test_maint_decline_has_recommendations(self):
        lens = TrajectoryLens()
        trends = _build_declining_maint_trends()
        dir_activity = _make_dir_activity(lt.ADAPTIVE_MIN_FILES)
        analysis = {
            "file_trends": trends,
            "dir_activity": dir_activity,
            "dependents": {fp: 0 for fp in trends},
        }
        preds = lens.score(analysis)
        preds = lens.propose(preds)
        maint_preds = [p for p in preds if p.data.get("metric") == "maintainability_score"]
        if maint_preds:
            assert len(maint_preds[0].recommendations) > 0
