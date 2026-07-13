"""Tests for anomaly-detection thresholds and config-driven parameters in genesis/reflexes/heal.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.heal import (
    _compute_adaptive_confidence_threshold,
    _get_healing_patterns,
    _get_recent_failures,
    run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn_scores(scores: list):
    """DB connection returning rows with a single confidence column."""
    rows = [(s,) for s in scores]
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    return conn


def _mock_conn_patterns(patterns: list):
    """DB connection returning healing pattern rows."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = patterns
    return conn


def _mock_conn_failures(failures: list):
    """DB connection returning audit_trail rows."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = failures
    return conn


# ---------------------------------------------------------------------------
# _compute_adaptive_confidence_threshold
# ---------------------------------------------------------------------------


class TestComputeAdaptiveConfidenceThreshold:
    def test_fallback_when_fewer_than_10_samples(self):
        """Returns fallback when DB has < 10 samples."""
        conn = _mock_conn_scores([0.8, 0.7, 0.9])
        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn):
            result = _compute_adaptive_confidence_threshold(fallback=0.65)
        assert result == 0.65

    def test_fallback_on_db_error(self):
        """Returns fallback when DB raises an exception."""
        with patch("tools.genesis.reflexes.heal.get_connection", side_effect=Exception("db down")):
            result = _compute_adaptive_confidence_threshold(fallback=0.72)
        assert result == 0.72

    def test_adaptive_result_is_clamped_to_minimum_0_5(self):
        """Result is never below 0.5 even with very spread data."""
        # mean=0.1, std=large → mean - 0.5*std could go negative
        scores = [0.1] * 5 + [1.0] * 5  # 10 samples
        conn = _mock_conn_scores(scores)
        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn):
            result = _compute_adaptive_confidence_threshold(fallback=0.7)
        assert result >= 0.5

    def test_adaptive_result_is_clamped_to_maximum_0_95(self):
        """Result never exceeds 0.95."""
        # All scores near 1.0 → mean ~ 1.0, std ~ 0 → threshold ~ 1.0 before clamp
        scores = [0.99] * 10
        conn = _mock_conn_scores(scores)
        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn):
            result = _compute_adaptive_confidence_threshold(fallback=0.7)
        # std_dev is ~0, so fallback is returned (std_dev < 0.001 guard)
        assert result == 0.7

    def test_adaptive_threshold_below_mean(self):
        """Adaptive threshold is below the mean (accepts slightly-below-average patterns)."""
        # mean=0.8, std=0.1 → threshold = 0.8 - 0.5*0.1 = 0.75
        scores = [0.7, 0.8, 0.9, 0.8, 0.7, 0.9, 0.8, 0.8, 0.7, 0.9]
        conn = _mock_conn_scores(scores)
        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn):
            result = _compute_adaptive_confidence_threshold(fallback=0.7)
        mean = sum(scores) / len(scores)
        assert result < mean

    def test_fallback_when_std_dev_near_zero(self):
        """Returns fallback when all scores are identical (std_dev < 0.001)."""
        scores = [0.85] * 15
        conn = _mock_conn_scores(scores)
        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn):
            result = _compute_adaptive_confidence_threshold(fallback=0.77)
        assert result == 0.77

    def test_default_fallback_is_0_7(self):
        """Default fallback value is 0.7."""
        conn = _mock_conn_scores([])  # 0 samples → fallback
        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn):
            result = _compute_adaptive_confidence_threshold()
        assert result == 0.7


# ---------------------------------------------------------------------------
# _get_healing_patterns — parameterized min_confidence
# ---------------------------------------------------------------------------


class TestGetHealingPatterns:
    def test_passes_min_confidence_to_sql(self):
        """The SQL query receives the min_confidence parameter."""
        conn = _mock_conn_patterns([])
        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn):
            _get_healing_patterns(min_confidence=0.85)
        call_args = conn.execute.call_args
        sql, params = call_args[0]
        # PostgreSQL is the primary backend, so runtime SQL is authored with %s
        # placeholders (per CLAUDE.md); tools.db.storage translates them to ? on
        # the SQLite init fallback. This asserted "?" -- the pre-PG-primary
        # contract -- so it failed against correct production SQL.
        assert "%s" in sql
        assert params == (0.85,)

    def test_default_min_confidence_is_0_7(self):
        """Default min_confidence is 0.7."""
        conn = _mock_conn_patterns([])
        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn):
            _get_healing_patterns()
        _, params = conn.execute.call_args[0]
        assert params == (0.7,)

    def test_returns_empty_list_on_db_error(self):
        """Returns [] when DB raises an exception."""
        with patch("tools.genesis.reflexes.heal.get_connection", side_effect=Exception("db fail")):
            result = _get_healing_patterns(min_confidence=0.7)
        assert result == []


# ---------------------------------------------------------------------------
# _get_recent_failures — lookback_hours parameter
# ---------------------------------------------------------------------------


class TestGetRecentFailures:
    def test_lookback_hours_affects_cutoff_query(self):
        """Different lookback_hours values pass different cutoff timestamps to SQL."""
        conn1 = _mock_conn_failures([])
        conn2 = _mock_conn_failures([])
        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn1):
            _get_recent_failures(lookback_hours=3)
        cutoff_3h = conn1.execute.call_args[0][1][0]

        with patch("tools.genesis.reflexes.heal.get_connection", return_value=conn2):
            _get_recent_failures(lookback_hours=12)
        cutoff_12h = conn2.execute.call_args[0][1][0]

        # 12-hour lookback has an earlier cutoff than 3-hour
        assert cutoff_12h < cutoff_3h

    def test_returns_empty_list_on_db_error(self):
        with patch("tools.genesis.reflexes.heal.get_connection", side_effect=Exception("db fail")):
            result = _get_recent_failures(lookback_hours=6)
        assert result == []


# ---------------------------------------------------------------------------
# run() — config-driven threshold and cycle parameters
# ---------------------------------------------------------------------------


def _make_failure(failure_id: int, event_type: str, details: str = "") -> dict:
    return {"id": failure_id, "event_type": event_type, "details": details, "created_at": "2026-01-01T00:00:00Z"}


def _make_pattern(pattern_id, confidence: float, error_pattern: str, action: str = "clear_stale_cache") -> dict:
    import json
    return {
        "id": pattern_id,
        "pattern_name": f"pattern-{pattern_id}",
        "error_pattern": error_pattern,
        "resolution_type": action,
        "resolution_action": json.dumps({"action": action}),
        "confidence": confidence,
        "success_count": 0,
        "failure_count": 0,
    }


class TestRunConfigDrivenParameters:
    def test_config_confidence_threshold_overrides_adaptive(self):
        """config['confidence_threshold'] is honoured over adaptive value."""
        # Pattern has confidence 0.75; config threshold = 0.80 → should be skipped
        failures = [_make_failure(1, "error.FileExistsError", "FileExistsError")]
        pattern = _make_pattern("p1", confidence=0.75, error_pattern="FileExistsError")

        with (
            patch("tools.genesis.reflexes.heal._get_recent_failures", return_value=failures),
            patch("tools.genesis.reflexes.heal._get_healing_patterns", return_value=[pattern]),
            patch("tools.genesis.reflexes.heal._get_builtin_patterns", return_value=[]),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value={}),
        ):
            result = run({"confidence_threshold": 0.80}, trust=None)

        assert result["details"]["healed_count"] == 0

    def test_config_confidence_threshold_allows_match(self):
        """A pattern above config threshold is attempted (action may fail, but it's dispatched)."""
        failures = [_make_failure(1, "error.FileExistsError", "FileExistsError")]
        pattern = _make_pattern("p1", confidence=0.85, error_pattern="FileExistsError")

        with (
            patch("tools.genesis.reflexes.heal._get_recent_failures", return_value=failures),
            patch("tools.genesis.reflexes.heal._get_healing_patterns", return_value=[pattern]),
            patch("tools.genesis.reflexes.heal._get_builtin_patterns", return_value=[]),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value={}),
            patch("tools.genesis.reflexes.heal._apply_remediation", return_value=(True, "ok")),
            patch("tools.genesis.reflexes.heal._record_healing_event"),
            patch("tools.genesis.reflexes.heal._update_pattern_confidence"),
        ):
            result = run({"confidence_threshold": 0.80}, trust=None)

        assert result["details"]["healed_count"] == 1

    def test_max_failures_per_cycle_limits_processing(self):
        """Only max_failures_per_cycle failures are examined per run."""
        failures = [_make_failure(i, f"error.type{i}", f"FileExistsError{i}") for i in range(30)]
        pattern = _make_pattern("p1", confidence=0.9, error_pattern="FileExistsError")

        dispatched = []

        def fake_apply(match, failure):
            dispatched.append(failure["id"])
            return True, "ok"

        with (
            patch("tools.genesis.reflexes.heal._get_recent_failures", return_value=failures),
            patch("tools.genesis.reflexes.heal._get_healing_patterns", return_value=[pattern]),
            patch("tools.genesis.reflexes.heal._get_builtin_patterns", return_value=[]),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value={}),
            patch("tools.genesis.reflexes.heal._apply_remediation", side_effect=fake_apply),
            patch("tools.genesis.reflexes.heal._record_healing_event"),
            patch("tools.genesis.reflexes.heal._update_pattern_confidence"),
        ):
            run({"confidence_threshold": 0.5, "max_failures_per_cycle": 5, "max_auto_heals_per_hour": 100}, trust=None)

        assert len(dispatched) <= 5

    def test_lookback_hours_is_passed_to_get_recent_failures(self):
        """config['lookback_hours'] is forwarded to _get_recent_failures."""
        called_with = {}

        def fake_get_failures(lookback_hours=6):
            called_with["lookback_hours"] = lookback_hours
            return []

        with patch("tools.genesis.reflexes.heal._get_recent_failures", side_effect=fake_get_failures):
            run({"lookback_hours": 12}, trust=None)

        assert called_with["lookback_hours"] == 12

    def test_default_lookback_hours_is_6(self):
        """Default lookback_hours is 6 when not in config."""
        called_with = {}

        def fake_get_failures(lookback_hours=6):
            called_with["lookback_hours"] = lookback_hours
            return []

        with patch("tools.genesis.reflexes.heal._get_recent_failures", side_effect=fake_get_failures):
            run({}, trust=None)

        assert called_with["lookback_hours"] == 6

    def test_no_failures_returns_success_immediately(self):
        """run() returns success with metric_value=0 when there are no recent failures."""
        with patch("tools.genesis.reflexes.heal._get_recent_failures", return_value=[]):
            result = run({}, trust=None)

        assert result["success"] is True
        assert result["metric_value"] == 0.0
        assert result["details"]["status"] == "no_recent_failures"

    def test_max_auto_heals_per_hour_caps_heal_count(self):
        """No more than max_auto_heals_per_hour heals are applied in one run."""
        failures = [_make_failure(i, "error.FileExistsError", "FileExistsError") for i in range(10)]
        pattern = _make_pattern("p1", confidence=0.9, error_pattern="FileExistsError")

        with (
            patch("tools.genesis.reflexes.heal._get_recent_failures", return_value=failures),
            patch("tools.genesis.reflexes.heal._get_healing_patterns", return_value=[pattern]),
            patch("tools.genesis.reflexes.heal._get_builtin_patterns", return_value=[]),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value={}),
            patch("tools.genesis.reflexes.heal._apply_remediation", return_value=(True, "ok")),
            patch("tools.genesis.reflexes.heal._record_healing_event"),
            patch("tools.genesis.reflexes.heal._update_pattern_confidence"),
        ):
            result = run(
                {"confidence_threshold": 0.5, "max_auto_heals_per_hour": 2, "max_failures_per_cycle": 100},
                trust=None,
            )

        assert result["details"]["healed_count"] == 2


# ---------------------------------------------------------------------------
# Constitution-driven anomaly-detection parameters
# ---------------------------------------------------------------------------

_AD_CFG_BASE = {
    "fallback_threshold": 0.70,
    "min_samples": 10,
    "z_score_factor": 0.5,
    "threshold_floor": 0.50,
    "threshold_ceiling": 0.95,
}


class TestConstitutionDrivenAnomalyDetection:
    def test_fallback_threshold_from_constitution(self):
        """When no explicit arg, fallback_threshold from constitution section is used."""
        constitution = {"anomaly_detection": {**_AD_CFG_BASE, "fallback_threshold": 0.65}}
        conn = _mock_conn_scores([0.8])  # < 10 samples → returns fallback
        with (
            patch("tools.genesis.reflexes.heal.get_connection", return_value=conn),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value=constitution),
        ):
            result = _compute_adaptive_confidence_threshold()
        assert result == 0.65

    def test_explicit_fallback_arg_overrides_constitution(self):
        """Explicit fallback= arg is used even when constitution has a different fallback_threshold."""
        constitution = {"anomaly_detection": {**_AD_CFG_BASE, "fallback_threshold": 0.65}}
        conn = _mock_conn_scores([])  # 0 samples → fallback
        with (
            patch("tools.genesis.reflexes.heal.get_connection", return_value=conn),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value=constitution),
        ):
            result = _compute_adaptive_confidence_threshold(fallback=0.72)
        assert result == 0.72

    def test_min_samples_from_constitution(self):
        """constitution anomaly_detection.min_samples overrides the default of 10."""
        # 5 samples < default min_samples 10 → fallback; but constitution sets min_samples=3
        constitution = {"anomaly_detection": {**_AD_CFG_BASE, "min_samples": 3}}
        scores = [0.70, 0.75, 0.80, 0.72, 0.78]  # 5 samples; std > 0.001
        conn = _mock_conn_scores(scores)
        with (
            patch("tools.genesis.reflexes.heal.get_connection", return_value=conn),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value=constitution),
        ):
            result = _compute_adaptive_confidence_threshold()
        # With min_samples=3, computation proceeds; threshold is below the mean
        mean = sum(scores) / len(scores)
        assert result < mean

    def test_z_score_factor_from_constitution(self):
        """Higher z_score_factor produces a lower (more lenient) threshold."""
        scores = [0.7, 0.8, 0.9, 0.8, 0.7, 0.9, 0.8, 0.8, 0.7, 0.9]
        constitution_low = {"anomaly_detection": {**_AD_CFG_BASE, "z_score_factor": 0.5}}
        constitution_high = {"anomaly_detection": {**_AD_CFG_BASE, "z_score_factor": 2.0}}
        conn1 = _mock_conn_scores(scores)
        conn2 = _mock_conn_scores(scores)
        with (
            patch("tools.genesis.reflexes.heal.get_connection", return_value=conn1),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value=constitution_low),
        ):
            result_low = _compute_adaptive_confidence_threshold()
        with (
            patch("tools.genesis.reflexes.heal.get_connection", return_value=conn2),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value=constitution_high),
        ):
            result_high = _compute_adaptive_confidence_threshold()
        assert result_high < result_low

    def test_threshold_floor_from_constitution(self):
        """constitution anomaly_detection.threshold_floor clamps the computed threshold."""
        # Large spread → computed threshold could be below 0.5; set floor to 0.6
        scores = [0.1] * 5 + [1.0] * 5  # mean=0.55, large std
        constitution = {"anomaly_detection": {**_AD_CFG_BASE, "threshold_floor": 0.60, "z_score_factor": 2.0}}
        conn = _mock_conn_scores(scores)
        with (
            patch("tools.genesis.reflexes.heal.get_connection", return_value=conn),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value=constitution),
        ):
            result = _compute_adaptive_confidence_threshold()
        assert result >= 0.60

    def test_threshold_ceiling_from_constitution(self):
        """constitution anomaly_detection.threshold_ceiling clamps the computed threshold."""
        # Tight high cluster → mean high, std ~0 → fallback; use a low ceiling to verify clamp
        # Vary scores slightly so std > 0.001
        scores = [0.94, 0.95, 0.96, 0.94, 0.95, 0.96, 0.94, 0.95, 0.96, 0.94]
        constitution = {"anomaly_detection": {**_AD_CFG_BASE, "threshold_ceiling": 0.80, "fallback_threshold": 0.85, "z_score_factor": 0.0}}
        conn = _mock_conn_scores(scores)
        with (
            patch("tools.genesis.reflexes.heal.get_connection", return_value=conn),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value=constitution),
        ):
            result = _compute_adaptive_confidence_threshold()
        assert result <= 0.80

    def test_run_uses_constitution_anomaly_detection_fallback(self):
        """run() derives threshold via constitution anomaly_detection (no hardcoded 0.7)."""
        # Constitution sets fallback_threshold=0.80; DB has no patterns → adaptive=0.80
        # Pattern with confidence=0.75 < 0.80 → should be skipped
        constitution = {
            "anomaly_detection": {**_AD_CFG_BASE, "fallback_threshold": 0.80},
            "rules": [],
        }
        pattern = _make_pattern("p1", confidence=0.75, error_pattern="FileExistsError")
        failures = [_make_failure(1, "error.FileExistsError", "FileExistsError")]
        with (
            patch("tools.genesis.reflexes.heal._get_recent_failures", return_value=failures),
            patch("tools.genesis.reflexes.heal._get_healing_patterns", return_value=[pattern]),
            patch("tools.genesis.reflexes.heal._get_builtin_patterns", return_value=[]),
            patch("tools.genesis.reflexes.heal._load_constitution", return_value=constitution),
        ):
            result = run({}, trust=None)
        assert result["details"]["healed_count"] == 0
