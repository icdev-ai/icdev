# CUI // SP-CTI
"""Tests for the anomaly_detection paradigm of the Log Analyzer.

Covers ``tools/monitor/log_analyzer.py`` after the hardcoded thresholds
(z-score > 2.0; error-rate spike at 0.10) were replaced with config-driven,
optionally-robust detection:

    * ``_load_anomaly_cfg`` — defaults + override merge + graceful degradation
    * ``_detect_frequency_anomalies`` — zscore (legacy) and mad (robust) methods
    * ``analyze_logs`` — error-rate spike honors the configured threshold and
      defaults preserve the legacy behavior.
"""

from datetime import datetime, timezone

from tools.monitor import log_analyzer as la


def _bucket(minute: int):
    """A deterministic UTC bucket datetime keyed by minute."""
    return datetime(2026, 6, 5, 12, minute, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _median
# ---------------------------------------------------------------------------
def test_median_odd():
    assert la._median([3, 1, 2]) == 2.0


def test_median_even():
    assert la._median([1, 2, 3, 4]) == 2.5


def test_median_empty():
    assert la._median([]) == 0.0


# ---------------------------------------------------------------------------
# _load_anomaly_cfg
# ---------------------------------------------------------------------------
def test_load_cfg_defaults_when_file_missing(tmp_path):
    cfg = la._load_anomaly_cfg(config_path=tmp_path / "nope.yaml")
    assert cfg["frequency"]["z_threshold"] == 2.0
    assert cfg["frequency"]["method"] == "zscore"
    assert cfg["error_rate"]["spike_threshold"] == 0.10


def test_load_cfg_merges_overrides(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text(
        "anomaly_detection:\n"
        "  frequency:\n"
        "    method: mad\n"
        "    mad_threshold: 4.0\n"
        "  error_rate:\n"
        "    spike_threshold: 0.25\n",
        encoding="utf-8",
    )
    cfg = la._load_anomaly_cfg(config_path=p)
    assert cfg["frequency"]["method"] == "mad"
    assert cfg["frequency"]["mad_threshold"] == 4.0
    # Unspecified keys keep their defaults.
    assert cfg["frequency"]["z_threshold"] == 2.0
    assert cfg["error_rate"]["spike_threshold"] == 0.25


def test_load_cfg_ignores_null_values(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text("anomaly_detection:\n  frequency:\n    z_threshold: null\n", encoding="utf-8")
    cfg = la._load_anomaly_cfg(config_path=p)
    assert cfg["frequency"]["z_threshold"] == 2.0


def test_load_cfg_absent_section_yields_defaults(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text("sla:\n  availability: 99.9\n", encoding="utf-8")
    cfg = la._load_anomaly_cfg(config_path=p)
    assert cfg["frequency"]["z_threshold"] == 2.0


def test_repo_config_loads_without_error():
    # The shipped args/monitoring_config.yaml must parse and expose the block.
    cfg = la._load_anomaly_cfg()
    assert cfg["frequency"]["method"] in ("zscore", "mad")
    assert isinstance(cfg["error_rate"]["spike_threshold"], (int, float))


# ---------------------------------------------------------------------------
# _detect_frequency_anomalies — zscore (legacy)
# ---------------------------------------------------------------------------
def _default_cfg():
    return {
        "frequency": {"method": "zscore", "z_threshold": 2.0, "mad_threshold": 3.5, "min_buckets": 2},
        "error_rate": {"spike_threshold": 0.10},
    }


def test_zscore_flags_clear_spike():
    # A single outlier's z-score is capped at sqrt(n-1), so 8 buckets are
    # needed for a lone spike to clear z > 2.0 (sqrt(7) ≈ 2.65).
    buckets = {_bucket(m): 1 for m in (0, 5, 10, 15, 20, 25, 30)}
    buckets[_bucket(35)] = 50
    out = la._detect_frequency_anomalies(buckets, _default_cfg())
    assert len(out) == 1
    assert out[0]["count"] == 50
    assert out[0]["method"] == "zscore"
    assert out[0]["z_score"] > 2.0


def test_zscore_no_anomaly_when_flat():
    buckets = {_bucket(0): 5, _bucket(5): 5, _bucket(10): 5}
    assert la._detect_frequency_anomalies(buckets, _default_cfg()) == []


def test_too_few_buckets_returns_empty():
    buckets = {_bucket(0): 100}
    assert la._detect_frequency_anomalies(buckets, _default_cfg()) == []


def test_zscore_threshold_override_changes_result():
    buckets = {_bucket(0): 10, _bucket(5): 10, _bucket(10): 10, _bucket(15): 16}
    strict = _default_cfg()
    strict["frequency"]["z_threshold"] = 2.0
    loose = _default_cfg()
    loose["frequency"]["z_threshold"] = 1.0
    # The 16-count bucket clears z=1.0 but not z=2.0 for this distribution.
    assert la._detect_frequency_anomalies(buckets, strict) == []
    assert len(la._detect_frequency_anomalies(buckets, loose)) == 1


# ---------------------------------------------------------------------------
# _detect_frequency_anomalies — mad (robust)
# ---------------------------------------------------------------------------
def _mad_cfg(threshold=3.5):
    return {
        "frequency": {"method": "mad", "z_threshold": 2.0, "mad_threshold": threshold, "min_buckets": 2},
        "error_rate": {"spike_threshold": 0.10},
    }


def test_mad_flags_spike():
    # MAD > 0 (the bulk has spread), so the smooth modified-z path runs.
    buckets = {_bucket(0): 2, _bucket(5): 3, _bucket(10): 2, _bucket(15): 4, _bucket(20): 40}
    out = la._detect_frequency_anomalies(buckets, _mad_cfg())
    assert len(out) == 1
    assert out[0]["count"] == 40
    assert out[0]["method"] == "mad"
    assert out[0]["mod_z_score"] is not None and out[0]["mod_z_score"] > 3.5


def test_mad_robust_to_masking():
    # Two co-occurring spikes (12 and 30) over a 3-5 baseline. MAD's robust
    # spread (resistant to the 30 outlier) still flags the smaller 12 spike;
    # the quiet 3-5 buckets stay clean.
    buckets = {_bucket(0): 3, _bucket(5): 4, _bucket(10): 3, _bucket(15): 5, _bucket(20): 4, _bucket(25): 12, _bucket(30): 30}
    out = la._detect_frequency_anomalies(buckets, _mad_cfg())
    flagged = {a["count"] for a in out}
    assert flagged == {12, 30}


def test_mad_degenerate_spread_flags_above_median():
    # MAD == 0 (majority identical); only buckets strictly above median flag.
    buckets = {_bucket(0): 5, _bucket(5): 5, _bucket(10): 5, _bucket(15): 9}
    out = la._detect_frequency_anomalies(buckets, _mad_cfg())
    assert len(out) == 1
    assert out[0]["count"] == 9
    assert out[0]["mod_z_score"] is None  # infinite modified z encoded as None


# ---------------------------------------------------------------------------
# analyze_logs wiring — spike threshold + backward compatibility
# ---------------------------------------------------------------------------
def test_analyze_logs_default_spike_threshold():
    # Off-network → empty logs → no spike, threshold key still resolves.
    result = la.analyze_logs(source="elk", query="error", time_range="1h", elk_url="http://127.0.0.1:1")
    assert result["error_rate_is_spike"] is False
    assert result["frequency_anomalies"] == []


def test_analyze_logs_accepts_anomaly_config_override():
    cfg = _mad_cfg()
    result = la.analyze_logs(
        source="elk", query="error", time_range="1h", elk_url="http://127.0.0.1:1", anomaly_config=cfg
    )
    # Empty result set still returns the standard keys without raising.
    assert "frequency_anomalies" in result
    assert result["frequency_anomalies"] == []
