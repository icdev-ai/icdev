# CUI // SP-CTI
"""Tests for the anomaly_detection paradigm of the Prometheus Metric Collector.

Covers ``tools/monitor/metric_collector.py`` after baseline-relative anomaly
detection was added alongside the hardcoded SLA thresholds:

    * ``_median`` — median helper
    * ``_load_metric_anomaly_cfg`` — defaults + override merge + graceful
      degradation when the file/PyYAML/key is missing
    * ``_detect_value_anomaly`` — zscore (default) and mad (robust) methods,
      direction gating, and the too-little-history / degenerate-spread edges
    * ``detect_metric_anomalies`` — end-to-end scoring against in-memory history
"""

import textwrap

from tools.monitor import metric_collector as mc


# ---------------------------------------------------------------------------
# _median
# ---------------------------------------------------------------------------
def test_median_odd():
    assert mc._median([3, 1, 2]) == 2.0


def test_median_even():
    assert mc._median([1, 2, 3, 4]) == 2.5


def test_median_empty():
    assert mc._median([]) == 0.0


# ---------------------------------------------------------------------------
# _load_metric_anomaly_cfg
# ---------------------------------------------------------------------------
def test_load_cfg_defaults_when_missing_file(tmp_path):
    cfg = mc._load_metric_anomaly_cfg(config_path=tmp_path / "nope.yaml")
    assert cfg == mc._DEFAULT_METRIC_ANOMALY_CFG


def test_load_cfg_merges_overrides(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text(
        textwrap.dedent(
            """
            metric_anomaly:
              method: "mad"
              z_threshold: 4.0
              min_samples: 8
            """
        ),
        encoding="utf-8",
    )
    cfg = mc._load_metric_anomaly_cfg(config_path=p)
    assert cfg["method"] == "mad"
    assert cfg["z_threshold"] == 4.0
    assert cfg["min_samples"] == 8
    # Unspecified keys keep their defaults.
    assert cfg["mad_threshold"] == mc._DEFAULT_METRIC_ANOMALY_CFG["mad_threshold"]
    assert cfg["direction"] == "both"


def test_load_cfg_absent_block_degrades(tmp_path):
    p = tmp_path / "monitoring_config.yaml"
    p.write_text("sla:\n  availability: 99.9\n", encoding="utf-8")
    cfg = mc._load_metric_anomaly_cfg(config_path=p)
    assert cfg == mc._DEFAULT_METRIC_ANOMALY_CFG


# ---------------------------------------------------------------------------
# _detect_value_anomaly — guards
# ---------------------------------------------------------------------------
def _cfg(**over):
    c = dict(mc._DEFAULT_METRIC_ANOMALY_CFG)
    c.update(over)
    return c


def test_too_little_history_returns_none():
    # min_samples default is 5; only 3 points provided.
    assert mc._detect_value_anomaly(100.0, [1, 2, 3], _cfg()) is None


def test_history_with_none_values_filtered():
    # Nones are dropped, leaving < min_samples → no scoring.
    assert mc._detect_value_anomaly(100.0, [1, None, 2, None], _cfg(min_samples=3)) is None


# ---------------------------------------------------------------------------
# _detect_value_anomaly — zscore method
# ---------------------------------------------------------------------------
def test_zscore_flags_high_spike():
    history = [10, 11, 9, 10, 10, 11, 9]
    res = mc._detect_value_anomaly(100.0, history, _cfg(method="zscore", z_threshold=3.0))
    assert res is not None
    assert res["direction"] == "high"
    assert res["method"] == "zscore"
    assert res["z_score"] > 3.0
    assert res["samples"] == len(history)


def test_zscore_normal_value_not_flagged():
    history = [10, 11, 9, 10, 10, 11, 9]
    assert mc._detect_value_anomaly(10.0, history, _cfg(method="zscore", z_threshold=3.0)) is None


def test_zscore_flags_low_drop():
    history = [100, 101, 99, 100, 100, 99, 101]
    res = mc._detect_value_anomaly(1.0, history, _cfg(method="zscore", z_threshold=3.0))
    assert res is not None
    assert res["direction"] == "low"
    assert res["z_score"] < -3.0


def test_zscore_degenerate_zero_std_flags_departure():
    history = [50, 50, 50, 50, 50]
    res = mc._detect_value_anomaly(80.0, history, _cfg(method="zscore"))
    assert res is not None
    assert res["direction"] == "high"
    assert res["z_score"] is None  # infinite score reported as None


def test_zscore_degenerate_zero_std_equal_value_not_flagged():
    history = [50, 50, 50, 50, 50]
    assert mc._detect_value_anomaly(50.0, history, _cfg(method="zscore")) is None


# ---------------------------------------------------------------------------
# _detect_value_anomaly — mad method
# ---------------------------------------------------------------------------
def test_mad_flags_spike_robustly():
    # A history containing one prior outlier would inflate std-dev under zscore
    # and mask the new spike; MAD is resistant to it.
    history = [10, 10, 11, 9, 10, 200]  # 200 is a prior outlier
    res = mc._detect_value_anomaly(150.0, history, _cfg(method="mad", mad_threshold=3.5))
    assert res is not None
    assert res["method"] == "mad"
    assert res["direction"] == "high"
    assert "mod_z_score" in res


def test_mad_normal_value_not_flagged():
    history = [10, 11, 9, 10, 10, 11]
    assert mc._detect_value_anomaly(10.0, history, _cfg(method="mad", mad_threshold=3.5)) is None


def test_mad_degenerate_zero_mad_flags_departure():
    history = [7, 7, 7, 7, 7]
    res = mc._detect_value_anomaly(20.0, history, _cfg(method="mad"))
    assert res is not None
    assert res["mod_z_score"] is None
    assert res["direction"] == "high"


# ---------------------------------------------------------------------------
# _detect_value_anomaly — direction gating
# ---------------------------------------------------------------------------
def test_direction_high_ignores_low_drop():
    history = [100, 101, 99, 100, 100, 99]
    assert mc._detect_value_anomaly(1.0, history, _cfg(direction="high", z_threshold=3.0)) is None


def test_direction_low_ignores_high_spike():
    history = [10, 11, 9, 10, 10, 11]
    assert mc._detect_value_anomaly(100.0, history, _cfg(direction="low", z_threshold=3.0)) is None


def test_direction_low_flags_drop():
    history = [100, 101, 99, 100, 100, 99]
    res = mc._detect_value_anomaly(1.0, history, _cfg(direction="low", z_threshold=3.0))
    assert res is not None
    assert res["direction"] == "low"


# ---------------------------------------------------------------------------
# detect_metric_anomalies — end to end with injected history
# ---------------------------------------------------------------------------
def test_detect_metric_anomalies_end_to_end(monkeypatch):
    histories = {
        "error_rate": [0.01, 0.012, 0.009, 0.011, 0.010, 0.013],
        "latency_p95": [0.20, 0.21, 0.19, 0.20, 0.22, 0.20],
    }

    def fake_history(project_id, metric_name, exclude_value=None, limit=200, db_path=None):
        return histories.get(metric_name, [])

    monkeypatch.setattr(mc, "_metric_history", fake_history)

    current = {"error_rate": 0.5, "latency_p95": 0.20, "missing": None}
    result = mc.detect_metric_anomalies("proj-1", current_metrics=current, config_path=None)

    # error_rate spiked; latency stayed normal; None metric skipped.
    assert result["metrics_evaluated"] == 2
    assert result["anomaly_count"] == 1
    names = {a["metric"] for a in result["anomalies"]}
    assert names == {"error_rate"}
    assert result["anomalies"][0]["direction"] == "high"


def test_detect_metric_anomalies_method_override(monkeypatch):
    monkeypatch.setattr(
        mc,
        "_metric_history",
        lambda *a, **k: [10, 10, 11, 9, 10, 200],
    )
    result = mc.detect_metric_anomalies(
        "proj-1", current_metrics={"cpu_usage": 150.0}, config_path=None, method="mad"
    )
    assert result["method"] == "mad"
    assert result["anomaly_count"] == 1
    assert result["anomalies"][0]["method"] == "mad"


def test_detect_metric_anomalies_no_history_no_anomalies(monkeypatch):
    monkeypatch.setattr(mc, "_metric_history", lambda *a, **k: [])
    result = mc.detect_metric_anomalies(
        "proj-1", current_metrics={"cpu_usage": 999.0}, config_path=None
    )
    assert result["anomaly_count"] == 0
    assert result["metrics_evaluated"] == 1
