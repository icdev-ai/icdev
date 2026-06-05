# CUI // SP-CTI
"""Tests for the configurable time-bucket window of the Log Analyzer.

Covers ``tools/monitor/log_analyzer.py`` after the hardcoded 5-minute
frequency-anomaly bucketing window (the time-partition granularity) was
extracted to ``anomaly_detection.frequency.bucket_window_minutes`` in
``args/monitoring_config.yaml`` (AI-ify opportunity 5969,
hardcoded_threshold -> anomaly_detection):

    * ``_floor_to_window`` — anchors buckets at midnight, preserves the legacy
      5-minute alignment, supports arbitrary windows, clamps invalid input.
    * ``_load_anomaly_cfg`` — loads / validates the new key, degrading to the
      legacy 5-minute window on a missing / non-int / non-positive override.
    * ``analyze_logs`` — still returns the standard keys with a custom window.
"""

from datetime import datetime, timezone

from tools.monitor import log_analyzer as la


def _dt(hour: int, minute: int, second: int = 0):
    return datetime(2026, 6, 5, hour, minute, second, 123456, tzinfo=timezone.utc)


def _legacy_5min(dt: datetime) -> datetime:
    """The original inline formula, kept here as the equivalence oracle."""
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# _floor_to_window — legacy 5-minute equivalence
# ---------------------------------------------------------------------------
def test_floor_default_5_matches_legacy_formula():
    # Sweep every minute of an hour: the default window must be byte-identical
    # to the formula it replaced so existing buckets are unchanged.
    for minute in range(60):
        dt = _dt(12, minute, 37)
        assert la._floor_to_window(dt, 5) == _legacy_5min(dt)


def test_floor_zeroes_seconds_and_microseconds():
    out = la._floor_to_window(_dt(8, 13, 59), 5)
    assert out.second == 0 and out.microsecond == 0
    assert (out.hour, out.minute) == (8, 10)


# ---------------------------------------------------------------------------
# _floor_to_window — other divisor windows
# ---------------------------------------------------------------------------
def test_floor_window_15():
    assert la._floor_to_window(_dt(9, 44), 15) == datetime(2026, 6, 5, 9, 30, tzinfo=timezone.utc)
    assert la._floor_to_window(_dt(9, 45), 15) == datetime(2026, 6, 5, 9, 45, tzinfo=timezone.utc)


def test_floor_window_30():
    assert la._floor_to_window(_dt(9, 29), 30) == datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)
    assert la._floor_to_window(_dt(9, 31), 30) == datetime(2026, 6, 5, 9, 30, tzinfo=timezone.utc)


def test_floor_window_60_floors_to_hour():
    assert la._floor_to_window(_dt(14, 59), 60) == datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc)


def test_floor_window_1_is_minute_granularity():
    assert la._floor_to_window(_dt(3, 7, 41), 1) == datetime(2026, 6, 5, 3, 7, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _floor_to_window — windows that cross the hour boundary
# ---------------------------------------------------------------------------
def test_floor_window_over_60_crosses_hour():
    # 120-minute window: 22:30 floors to 22:00; the bucket spans two hours.
    assert la._floor_to_window(_dt(22, 30), 120) == datetime(2026, 6, 5, 22, 0, tzinfo=timezone.utc)
    assert la._floor_to_window(_dt(23, 15), 120) == datetime(2026, 6, 5, 22, 0, tzinfo=timezone.utc)


def test_floor_non_divisor_window_floors_by_minutes_since_midnight():
    # 7-minute window does not divide 60: 01:00 (minute-of-day 60) -> 56 -> 00:56.
    assert la._floor_to_window(_dt(1, 0), 7) == datetime(2026, 6, 5, 0, 56, tzinfo=timezone.utc)
    # Next bucket starts at minute-of-day 63 -> 01:03.
    assert la._floor_to_window(_dt(1, 5), 7) == datetime(2026, 6, 5, 1, 3, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _floor_to_window — invalid window clamps to the legacy 5
# ---------------------------------------------------------------------------
def test_floor_invalid_window_falls_back_to_5():
    dt = _dt(12, 38)
    for bad in (0, -1, -10, None, "5", 2.5):
        assert la._floor_to_window(dt, bad) == _legacy_5min(dt)


# ---------------------------------------------------------------------------
# _load_anomaly_cfg — bucket_window_minutes key
# ---------------------------------------------------------------------------
def test_cfg_default_bucket_window_is_5(tmp_path):
    cfg = la._load_anomaly_cfg(config_path=tmp_path / "missing.yaml")
    assert cfg["frequency"]["bucket_window_minutes"] == 5


def test_cfg_merges_valid_bucket_window(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text(
        "anomaly_detection:\n  frequency:\n    bucket_window_minutes: 15\n",
        encoding="utf-8",
    )
    cfg = la._load_anomaly_cfg(config_path=p)
    assert cfg["frequency"]["bucket_window_minutes"] == 15


def test_cfg_zero_bucket_window_falls_back_to_5(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text(
        "anomaly_detection:\n  frequency:\n    bucket_window_minutes: 0\n",
        encoding="utf-8",
    )
    cfg = la._load_anomaly_cfg(config_path=p)
    assert cfg["frequency"]["bucket_window_minutes"] == 5


def test_cfg_negative_bucket_window_falls_back_to_5(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text(
        "anomaly_detection:\n  frequency:\n    bucket_window_minutes: -3\n",
        encoding="utf-8",
    )
    cfg = la._load_anomaly_cfg(config_path=p)
    assert cfg["frequency"]["bucket_window_minutes"] == 5


def test_cfg_non_numeric_bucket_window_falls_back_to_5(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text(
        "anomaly_detection:\n  frequency:\n    bucket_window_minutes: wide\n",
        encoding="utf-8",
    )
    cfg = la._load_anomaly_cfg(config_path=p)
    assert cfg["frequency"]["bucket_window_minutes"] == 5


def test_cfg_float_bucket_window_truncates_to_int(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text(
        "anomaly_detection:\n  frequency:\n    bucket_window_minutes: 10.9\n",
        encoding="utf-8",
    )
    cfg = la._load_anomaly_cfg(config_path=p)
    assert cfg["frequency"]["bucket_window_minutes"] == 10


def test_cfg_other_frequency_keys_unaffected(tmp_path):
    # Adding the new key must not perturb the previously-extracted thresholds.
    cfg = la._load_anomaly_cfg(config_path=tmp_path / "missing.yaml")
    assert cfg["frequency"]["z_threshold"] == 2.0
    assert cfg["frequency"]["min_buckets"] == 2
    assert cfg["error_rate"]["spike_threshold"] == 0.10


# ---------------------------------------------------------------------------
# Bucketing behavior — window changes how timestamps group
# ---------------------------------------------------------------------------
def test_window_changes_bucket_grouping():
    # Five events one minute apart span one 5-min bucket but two 1-min buckets.
    stamps = [_dt(10, m) for m in (0, 1, 2, 3, 4)]
    five = {la._floor_to_window(s, 5) for s in stamps}
    one = {la._floor_to_window(s, 1) for s in stamps}
    assert len(five) == 1
    assert len(one) == 5


# ---------------------------------------------------------------------------
# analyze_logs wiring — custom window still returns standard keys
# ---------------------------------------------------------------------------
def test_analyze_logs_accepts_custom_bucket_window():
    cfg = dict(la._DEFAULT_ANOMALY_CFG)
    cfg["frequency"] = {**la._DEFAULT_ANOMALY_CFG["frequency"], "bucket_window_minutes": 15}
    # Off-network → empty logs → standard keys present, no raise.
    result = la.analyze_logs(
        source="elk", query="error", time_range="1h", elk_url="http://127.0.0.1:1", anomaly_config=cfg
    )
    assert result["frequency_anomalies"] == []
    assert "error_rate_is_spike" in result
