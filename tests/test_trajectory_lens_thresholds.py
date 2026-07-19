# CUI // SP-CTI
"""Tests for config-driven anomaly detection thresholds in TrajectoryLens.

Validates that all hardcoded threshold values have been replaced with
config-driven module constants loaded from oracle_trajectory_config.yaml,
and that the scoring logic respects those values.
"""
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.oracle.lens_trajectory as _mod
from tools.oracle.lens_trajectory import (
    CC_THRESHOLD,
    MAINTAINABILITY_FLOOR,
    MIN_SNAPSHOTS,
    FORECAST_HORIZON_DAYS,
    HOTSPOT_MIN_FILES,
    HOTSPOT_CRITICAL_FALLBACK,
    DAYS_URGENT,
    DAYS_WARNING,
    VELOCITY_FALLBACK_SLOPE,
    VELOCITY_FALLBACK_RECENT_AVG,
    CC_BREACH_CONF_MAX,
    CC_BREACH_CONF_BASE,
    CC_BREACH_CONF_SCALE,
    MAINT_DECLINE_CONF_MAX,
    MAINT_DECLINE_CONF_BASE,
    MAINT_DECLINE_CONF_SCALE,
    HOTSPOT_CONF_MAX,
    HOTSPOT_CONF_BASE,
    HOTSPOT_CONF_PER_FILE,
    VELOCITY_CONF_FIXED,
    _load_trajectory_config,
    _linear_regression,
    _days_to_threshold,
    TrajectoryLens,
)


# ---------------------------------------------------------------------------
# _load_trajectory_config — reads YAML correctly
# ---------------------------------------------------------------------------

def test_load_config_reads_yaml(tmp_path):
    cfg_file = tmp_path / "oracle_trajectory_config.yaml"
    cfg_file.write_text(textwrap.dedent("""\
        trajectory_forecast:
          fallbacks:
            cc_threshold: 20
            maintainability_floor: 0.4
          urgency:
            days_critical: 14
    """), encoding="utf-8")
    with patch.object(_mod, "_TRAJECTORY_CONFIG_PATH", cfg_file):
        result = _load_trajectory_config()
    assert result["fallbacks"]["cc_threshold"] == 20
    assert result["fallbacks"]["maintainability_floor"] == 0.4
    assert result["urgency"]["days_critical"] == 14


def test_load_config_returns_empty_on_missing_file(tmp_path):
    absent = tmp_path / "no_such_file.yaml"
    with patch.object(_mod, "_TRAJECTORY_CONFIG_PATH", absent):
        result = _load_trajectory_config()
    assert result == {}


def test_load_config_returns_empty_on_invalid_yaml(tmp_path):
    bad = tmp_path / "oracle_trajectory_config.yaml"
    bad.write_text("not: valid: yaml: [[[", encoding="utf-8")
    with patch.object(_mod, "_TRAJECTORY_CONFIG_PATH", bad):
        result = _load_trajectory_config()
    assert result == {}


# ---------------------------------------------------------------------------
# Module constants — verify they are the expected defaults
# ---------------------------------------------------------------------------

def test_default_cc_threshold():
    assert CC_THRESHOLD == 15

def test_default_maintainability_floor():
    assert MAINTAINABILITY_FLOOR == pytest.approx(0.5)

def test_default_min_snapshots():
    assert MIN_SNAPSHOTS == 3

def test_default_forecast_horizon():
    assert FORECAST_HORIZON_DAYS == 365

def test_default_hotspot_min_files():
    assert HOTSPOT_MIN_FILES == 2

def test_default_hotspot_critical_cutoff():
    assert HOTSPOT_CRITICAL_FALLBACK == 4

def test_default_days_urgent():
    assert DAYS_URGENT == 30

def test_default_days_warning():
    assert DAYS_WARNING == 90

def test_default_velocity_slope():
    assert VELOCITY_FALLBACK_SLOPE == pytest.approx(1.0)

def test_default_velocity_recent_avg():
    assert VELOCITY_FALLBACK_RECENT_AVG == pytest.approx(5.0)

def test_default_confidence_values():
    assert CC_BREACH_CONF_MAX == pytest.approx(0.92)
    assert CC_BREACH_CONF_BASE == pytest.approx(0.55)
    assert CC_BREACH_CONF_SCALE == pytest.approx(0.40)
    assert MAINT_DECLINE_CONF_MAX == pytest.approx(0.90)
    assert MAINT_DECLINE_CONF_BASE == pytest.approx(0.55)
    assert MAINT_DECLINE_CONF_SCALE == pytest.approx(0.38)
    assert HOTSPOT_CONF_MAX == pytest.approx(0.88)
    assert HOTSPOT_CONF_BASE == pytest.approx(0.55)
    assert HOTSPOT_CONF_PER_FILE == pytest.approx(0.06)
    assert VELOCITY_CONF_FIXED == pytest.approx(0.70)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def test_linear_regression_perfect_slope():
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [1.0, 3.0, 5.0, 7.0]
    slope, intercept = _linear_regression(xs, ys)
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(1.0)

def test_linear_regression_single_point():
    slope, intercept = _linear_regression([0.0], [5.0])
    assert slope == pytest.approx(0.0)
    assert intercept == pytest.approx(5.0)

def test_days_to_threshold_rising():
    # CC at 10, slope +1/week, threshold 15 → 5 weeks = 35 days
    days = _days_to_threshold(10.0, 1.0, 15.0)
    assert days == pytest.approx(35.0)

def test_days_to_threshold_already_above():
    # Current already above threshold in the declining direction
    days = _days_to_threshold(20.0, 1.0, 15.0)
    assert days is None

def test_days_to_threshold_zero_slope():
    assert _days_to_threshold(5.0, 0.0, 10.0) is None


# ---------------------------------------------------------------------------
# score() — CC breach uses config constants, not literals
# ---------------------------------------------------------------------------

def _make_file_trends(fp: str, cc_vals: list[float], maint_vals: list[float]) -> dict:
    return {
        fp: [
            {"week": f"2024-{i+1:02d}", "cc": cc, "cognitive": 1.0, "maintainability": m}
            for i, (cc, m) in enumerate(zip(cc_vals, maint_vals))
        ]
    }


def _run_score(cc_threshold=None, maint_floor=None, days_urgent=None, days_warning=None,
               forecast_horizon=None, hotspot_min=None, hotspot_critical=None,
               vel_slope=None, vel_avg=None, min_snaps=None,
               conf_cc_max=None, conf_cc_base=None, conf_cc_scale=None,
               conf_m_max=None, conf_m_base=None, conf_m_scale=None,
               conf_h_max=None, conf_h_base=None, conf_h_per=None,
               conf_vel=None,
               file_trends=None, dir_activity=None, dependents=None):
    overrides = {}
    if cc_threshold is not None:
        overrides["CC_THRESHOLD"] = cc_threshold
    if maint_floor is not None:
        overrides["MAINTAINABILITY_FLOOR"] = maint_floor
    if days_urgent is not None:
        overrides["DAYS_URGENT"] = days_urgent
    if days_warning is not None:
        overrides["DAYS_WARNING"] = days_warning
    if forecast_horizon is not None:
        overrides["FORECAST_HORIZON_DAYS"] = forecast_horizon
    if hotspot_min is not None:
        overrides["HOTSPOT_MIN_FILES"] = hotspot_min
    if hotspot_critical is not None:
        overrides["HOTSPOT_CRITICAL_FALLBACK"] = hotspot_critical
    if vel_slope is not None:
        overrides["VELOCITY_FALLBACK_SLOPE"] = vel_slope
    if vel_avg is not None:
        overrides["VELOCITY_FALLBACK_RECENT_AVG"] = vel_avg
    if min_snaps is not None:
        overrides["MIN_SNAPSHOTS"] = min_snaps
    if conf_cc_max is not None:
        overrides["CC_BREACH_CONF_MAX"] = conf_cc_max
    if conf_cc_base is not None:
        overrides["CC_BREACH_CONF_BASE"] = conf_cc_base
    if conf_cc_scale is not None:
        overrides["CC_BREACH_CONF_SCALE"] = conf_cc_scale
    if conf_m_max is not None:
        overrides["MAINT_DECLINE_CONF_MAX"] = conf_m_max
    if conf_m_base is not None:
        overrides["MAINT_DECLINE_CONF_BASE"] = conf_m_base
    if conf_m_scale is not None:
        overrides["MAINT_DECLINE_CONF_SCALE"] = conf_m_scale
    if conf_h_max is not None:
        overrides["HOTSPOT_CONF_MAX"] = conf_h_max
    if conf_h_base is not None:
        overrides["HOTSPOT_CONF_BASE"] = conf_h_base
    if conf_h_per is not None:
        overrides["HOTSPOT_CONF_PER_FILE"] = conf_h_per
    if conf_vel is not None:
        overrides["VELOCITY_CONF_FIXED"] = conf_vel

    lens = TrajectoryLens()
    analysis = {
        "file_trends": file_trends or {},
        "dir_activity": dir_activity or {},
        "dependents": dependents or {},
    }
    # These fixtures are deliberately tiny (a handful of files in one directory),
    # so score() always finds the fleet too sparse for percentile adaptation and
    # takes the LLM calibration branch. Unmocked, that reaches a live
    # LLMRouter.invoke — every test here would hit the network and hang.
    # Returning {} is the documented "LLM unavailable" contract, which makes
    # score() fall through to the module-level constants — precisely the
    # behaviour these tests assert on.
    with patch.object(_mod, "_llm_calibrate_thresholds", return_value={}):
        if overrides:
            with patch.multiple(_mod, **overrides):
                return lens.score(analysis)
        return lens.score(analysis)


def test_cc_breach_flagged_below_threshold():
    # CC rising from 5 → 14 across 3 snapshots; threshold default 15
    trends = _make_file_trends("foo.py", [5.0, 9.5, 14.0], [0.8, 0.8, 0.8])
    preds = _run_score(file_trends=trends)
    cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
    assert len(cc_preds) == 1
    assert cc_preds[0].data["threshold"] == CC_THRESHOLD


def test_cc_breach_not_flagged_beyond_horizon():
    # Very slow slope — breach happens far beyond the forecast horizon
    # slope ~0.1/week, threshold 15, current 5.2 → breach in ~686 days; horizon = 100
    slow_trends = {"baz.py": [
        {"week": "2024-01", "cc": 5.0, "cognitive": 1.0, "maintainability": 0.8},
        {"week": "2024-02", "cc": 5.1, "cognitive": 1.0, "maintainability": 0.8},
        {"week": "2024-03", "cc": 5.2, "cognitive": 1.0, "maintainability": 0.8},
    ]}
    preds = _run_score(forecast_horizon=100, file_trends=slow_trends)
    cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
    assert len(cc_preds) == 0


def test_cc_breach_not_flagged_when_already_above_threshold():
    # CC already above threshold — slope > 0 but no breach to forecast
    trends = _make_file_trends("foo.py", [18.0, 19.0, 20.0], [0.8, 0.8, 0.8])
    preds = _run_score(file_trends=trends)
    cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
    assert len(cc_preds) == 0


def test_maint_decline_flagged():
    trends = _make_file_trends("bar.py", [5.0, 5.0, 5.0], [0.9, 0.75, 0.6])
    preds = _run_score(file_trends=trends)
    m_preds = [p for p in preds if p.data.get("metric") == "maintainability_score"]
    assert len(m_preds) == 1


def test_maint_decline_not_flagged_when_already_below_floor():
    # If floor is raised above current value, the file already violated it — no future breach to forecast
    # current_maint=0.6; floor=0.65 → 0.6 > 0.65 is False → condition skipped
    trends = _make_file_trends("bar.py", [5.0, 5.0, 5.0], [0.9, 0.75, 0.6])
    preds = _run_score(maint_floor=0.65, file_trends=trends)
    m_preds = [p for p in preds if p.data.get("metric") == "maintainability_score"]
    assert len(m_preds) == 0


def test_severity_critical_uses_days_urgent():
    # With DAYS_URGENT=200 (much larger), even a 100-day breach is "critical"
    trends = _make_file_trends("baz.py", [5.0, 9.5, 14.0], [0.8, 0.8, 0.8])
    preds = _run_score(days_urgent=200, file_trends=trends)
    cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
    if cc_preds:
        assert cc_preds[0].severity == "critical"


def test_severity_info_when_days_urgent_very_low():
    # With DAYS_URGENT=1, a breach in ~10 days won't be "critical" unless very imminent
    trends = _make_file_trends("baz.py", [5.0, 9.5, 14.0], [0.8, 0.8, 0.8])
    preds = _run_score(days_urgent=1, days_warning=3, file_trends=trends)
    cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
    if cc_preds:
        assert cc_preds[0].severity in {"warning", "info"}


def test_min_snapshots_skips_short_series():
    # Only 2 snapshots — should be skipped when MIN_SNAPSHOTS=3
    trends = {"f.py": [
        {"week": "2024-01", "cc": 5.0, "cognitive": 1.0, "maintainability": 0.8},
        {"week": "2024-02", "cc": 10.0, "cognitive": 1.0, "maintainability": 0.7},
    ]}
    preds = _run_score(min_snaps=3, file_trends=trends)
    assert preds == []


def test_min_snapshots_includes_exact_match():
    # Exactly MIN_SNAPSHOTS=2 should be included
    trends = {"f.py": [
        {"week": "2024-01", "cc": 5.0, "cognitive": 1.0, "maintainability": 0.8},
        {"week": "2024-02", "cc": 12.0, "cognitive": 1.0, "maintainability": 0.75},
    ]}
    preds = _run_score(min_snaps=2, file_trends=trends)
    cc_preds = [p for p in preds if p.data.get("metric") == "cyclomatic_complexity"]
    assert len(cc_preds) >= 1


# ---------------------------------------------------------------------------
# score() — hotspot detection respects HOTSPOT_MIN_FILES and HOTSPOT_CRITICAL_FALLBACK
# ---------------------------------------------------------------------------

def _hotspot_trends(n_files: int, slope_per_week: float = 2.0) -> dict:
    """Build n_files all rising in the same directory."""
    trends = {}
    for i in range(n_files):
        fp = f"src/mymod/file{i}.py"
        base = 5.0
        trends[fp] = [
            {"week": f"2024-{j+1:02d}", "cc": base + slope_per_week * j,
             "cognitive": 1.0, "maintainability": 0.8}
            for j in range(3)
        ]
    return trends


def test_hotspot_not_detected_below_min_files():
    preds = _run_score(hotspot_min=3, file_trends=_hotspot_trends(2))
    hotspot = [p for p in preds if "trending_file_count" in p.data]
    assert hotspot == []


def test_hotspot_detected_at_min_files():
    preds = _run_score(hotspot_min=2, file_trends=_hotspot_trends(2))
    hotspot = [p for p in preds if "trending_file_count" in p.data]
    assert len(hotspot) == 1


def test_hotspot_critical_uses_cutoff_constant():
    # 5 files trending up; with HOTSPOT_CRITICAL_FALLBACK=3 → critical
    preds = _run_score(hotspot_critical=3, file_trends=_hotspot_trends(5))
    hotspot = [p for p in preds if "trending_file_count" in p.data]
    assert hotspot
    assert hotspot[0].severity == "critical"


def test_hotspot_warning_when_below_cutoff():
    # 3 files trending; with HOTSPOT_CRITICAL_FALLBACK=10 → warning
    preds = _run_score(hotspot_critical=10, file_trends=_hotspot_trends(3))
    hotspot = [p for p in preds if "trending_file_count" in p.data]
    assert hotspot
    assert hotspot[0].severity == "warning"


def test_hotspot_confidence_uses_per_file_constant():
    # 4 trending files: confidence = min(HOTSPOT_CONF_MAX, HOTSPOT_CONF_BASE + 4 * HOTSPOT_CONF_PER_FILE)
    preds = _run_score(
        hotspot_min=2, hotspot_critical=100,
        conf_h_max=0.88, conf_h_base=0.55, conf_h_per=0.10,
        file_trends=_hotspot_trends(4),
    )
    hotspot = [p for p in preds if "trending_file_count" in p.data]
    assert hotspot
    expected = min(0.88, 0.55 + 4 * 0.10)
    assert hotspot[0].confidence == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# score() — velocity detection respects VELOCITY_FALLBACK_SLOPE / _RECENT_AVG
# ---------------------------------------------------------------------------

def _dir_activity_with_slope(slope: float, recent_avg: float, weeks: int = 5) -> dict:
    """Build a directory with the given recent average commits."""
    counts = {}
    for i in range(weeks):
        counts[f"2024-{i+1:02d}"] = int(recent_avg + slope * (i - weeks + 1))
    return {"tools/mydir": counts}


def test_velocity_flagged_above_thresholds():
    dir_act = {"tools/mydir": {"2024-01": 6, "2024-02": 8, "2024-03": 10}}
    preds = _run_score(vel_slope=1.0, vel_avg=5.0, dir_activity=dir_act)
    vel_preds = [p for p in preds if "commit_slope_per_week" in p.data]
    assert len(vel_preds) == 1
    assert preds[-1].confidence == pytest.approx(VELOCITY_CONF_FIXED)


def test_velocity_not_flagged_below_slope_threshold():
    # slope ~0.5 < threshold 1.0 → no flag
    dir_act = {"tools/mydir": {"2024-01": 6, "2024-02": 6, "2024-03": 7}}
    preds = _run_score(vel_slope=2.0, vel_avg=5.0, dir_activity=dir_act)
    vel_preds = [p for p in preds if "commit_slope_per_week" in p.data]
    assert vel_preds == []


def test_velocity_not_flagged_below_avg_threshold():
    # High slope but low recent average
    dir_act = {"tools/mydir": {"2024-01": 1, "2024-02": 2, "2024-03": 3}}
    preds = _run_score(vel_slope=1.0, vel_avg=10.0, dir_activity=dir_act)
    vel_preds = [p for p in preds if "commit_slope_per_week" in p.data]
    assert vel_preds == []


def test_velocity_skipped_below_min_snapshots():
    dir_act = {"tools/mydir": {"2024-01": 10, "2024-02": 12}}
    preds = _run_score(min_snaps=3, vel_slope=0.5, vel_avg=5.0, dir_activity=dir_act)
    vel_preds = [p for p in preds if "commit_slope_per_week" in p.data]
    assert vel_preds == []


def test_velocity_confidence_uses_velocity_conf_fixed():
    dir_act = {"tools/mydir": {"2024-01": 6, "2024-02": 8, "2024-03": 10}}
    preds = _run_score(vel_slope=1.0, vel_avg=5.0, conf_vel=0.42, dir_activity=dir_act)
    vel_preds = [p for p in preds if "commit_slope_per_week" in p.data]
    assert vel_preds
    assert vel_preds[0].confidence == pytest.approx(0.42)
