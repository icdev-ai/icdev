# CUI // SP-CTI
"""Tests for agent_readiness.checker — weight loading and anomaly detection."""
from __future__ import annotations

import tempfile


from tools.ai_augmentation.agent_readiness.checker import (
    _DEFAULT_PILLAR_WEIGHTS,
    _detect_anomalies,
    _get_pillar_weights,
    run_readiness_check,
)


# ---------------------------------------------------------------------------
# _detect_anomalies
# ---------------------------------------------------------------------------

def _make_scores(percentages: dict[str, float]) -> dict[str, dict]:
    return {pid: {"percentage": pct, "passed": 0, "total": 0} for pid, pct in percentages.items()}


def test_detect_anomalies_flags_low_outlier():
    scores = _make_scores({
        "code-quality": 0.9,
        "documentation": 0.85,
        "testing": 0.88,
        "structure": 0.0,   # obviously anomalous
        "dependencies": 0.9,
    })
    flags = _detect_anomalies(scores, threshold=-1.5, min_samples=3)
    assert flags["structure"] is True
    assert flags["code-quality"] is False


def test_detect_anomalies_all_equal_returns_false():
    scores = _make_scores({"a": 0.5, "b": 0.5, "c": 0.5})
    flags = _detect_anomalies(scores, threshold=-1.5, min_samples=3)
    assert all(not v for v in flags.values())


def test_detect_anomalies_too_few_samples_returns_false():
    scores = _make_scores({"a": 1.0, "b": 0.0})
    flags = _detect_anomalies(scores, threshold=-1.5, min_samples=3)
    assert all(not v for v in flags.values())


def test_detect_anomalies_threshold_respected():
    # Only 'low' is anomalous at -1.5 but not at -3.0
    scores = _make_scores({"high": 1.0, "mid": 0.5, "low": 0.0, "x": 0.8, "y": 0.7})
    strict_flags = _detect_anomalies(scores, threshold=-3.0, min_samples=3)
    lenient_flags = _detect_anomalies(scores, threshold=-0.5, min_samples=3)
    # At a very strict threshold nothing should be flagged
    assert all(not v for v in strict_flags.values())
    # At a lenient threshold the low scorer is flagged
    assert lenient_flags["low"] is True


# ---------------------------------------------------------------------------
# _get_pillar_weights — fallback to defaults when config missing / malformed
# ---------------------------------------------------------------------------

def test_get_pillar_weights_returns_all_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.ai_augmentation.agent_readiness.checker._REPO_ROOT",
        tmp_path,  # no aac_config.yaml here
    )
    weights = _get_pillar_weights()
    assert weights == _DEFAULT_PILLAR_WEIGHTS


def test_get_pillar_weights_reads_from_config(tmp_path, monkeypatch):
    import yaml

    (tmp_path / "args").mkdir()
    config = {
        "agent_readiness": {
            "pillar_weights": {"testing": 2.0, "security": 2.5},
        }
    }
    (tmp_path / "args" / "aac_config.yaml").write_text(
        yaml.dump(config), encoding="utf-8"
    )
    monkeypatch.setattr(
        "tools.ai_augmentation.agent_readiness.checker._REPO_ROOT",
        tmp_path,
    )
    weights = _get_pillar_weights()
    assert weights["testing"] == 2.0
    assert weights["security"] == 2.5


# ---------------------------------------------------------------------------
# run_readiness_check — result shape includes anomaly_flags
# ---------------------------------------------------------------------------

def test_run_readiness_check_returns_anomaly_flags():
    with tempfile.TemporaryDirectory() as td:
        result = run_readiness_check(td)
    assert "anomaly_flags" in result
    assert isinstance(result["anomaly_flags"], dict)
    # Every pillar that has a score entry must also have a flag entry
    for pid in result["pillar_scores"]:
        assert pid in result["anomaly_flags"]
        assert isinstance(result["anomaly_flags"][pid], bool)


def test_run_readiness_check_overall_score_range():
    with tempfile.TemporaryDirectory() as td:
        result = run_readiness_check(td)
    assert 0.0 <= result["overall_readiness_score"] <= 1.0
