# CUI // SP-CTI
"""Unit tests for tools/ai_augmentation/agent_readiness/checker.py.

Tests cover:
  - Config loading (YAML present / absent / malformed)
  - Pillar weight merging
  - Anomaly detection rules (critical pillar, outlier, low overall)
  - Anomaly detection disabled
  - run_readiness_check integration (mocked pillars)
"""
from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, patch


from tools.ai_augmentation.agent_readiness.checker import (
    _DEFAULT_ANOMALY_CONFIG,
    _DEFAULT_PILLAR_WEIGHTS,
    _detect_score_anomalies,
    _get_anomaly_config,
    _get_pillar_weights,
    _load_readiness_config,
    run_readiness_check,
)
from tools.ai_augmentation.agent_readiness.pillars._base import CriterionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pillar_scores(overrides: dict[str, float] | None = None) -> dict[str, dict]:
    """Build a synthetic pillar_scores dict with all 11 pillars at 100% by default."""
    ids = list(_DEFAULT_PILLAR_WEIGHTS.keys())
    scores = {pid: {"passed": 10, "total": 10, "percentage": 1.0} for pid in ids}
    for pid, pct in (overrides or {}).items():
        passed = int(pct * 10)
        scores[pid] = {"passed": passed, "total": 10, "percentage": pct}
    return scores


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestLoadReadinessConfig:
    def test_returns_empty_dict_when_no_file(self, tmp_path):
        missing = tmp_path / "nonexistent.yaml"
        result = _load_readiness_config(missing)
        assert result == {}

    def test_loads_yaml_file(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            textwrap.dedent("""\
                version: "1.0"
                pillar_weights:
                  testing: 2.0
                anomaly_detection:
                  enabled: false
            """),
            encoding="utf-8",
        )
        result = _load_readiness_config(cfg_file)
        assert result["pillar_weights"]["testing"] == 2.0
        assert result["anomaly_detection"]["enabled"] is False

    def test_returns_empty_dict_on_malformed_yaml(self, tmp_path):
        cfg_file = tmp_path / "bad.yaml"
        cfg_file.write_text("key: [unclosed", encoding="utf-8")
        result = _load_readiness_config(cfg_file)
        assert result == {}


# ---------------------------------------------------------------------------
# Weight merging
# ---------------------------------------------------------------------------


class TestGetPillarWeights:
    def test_uses_defaults_when_no_config(self):
        weights = _get_pillar_weights({})
        assert weights == _DEFAULT_PILLAR_WEIGHTS

    def test_yaml_values_override_defaults(self):
        weights = _get_pillar_weights({"pillar_weights": {"testing": 3.0}})
        assert weights["testing"] == 3.0
        assert weights["security"] == _DEFAULT_PILLAR_WEIGHTS["security"]

    def test_yaml_can_add_new_pillar(self):
        weights = _get_pillar_weights({"pillar_weights": {"custom-pillar": 0.5}})
        assert weights["custom-pillar"] == 0.5


# ---------------------------------------------------------------------------
# Anomaly config merging
# ---------------------------------------------------------------------------


class TestGetAnomalyConfig:
    def test_uses_defaults_when_no_config(self):
        cfg = _get_anomaly_config({})
        assert cfg == _DEFAULT_ANOMALY_CONFIG

    def test_yaml_overrides_single_field(self):
        cfg = _get_anomaly_config({"anomaly_detection": {"critical_pillar_min_score": 0.75}})
        assert cfg["critical_pillar_min_score"] == 0.75
        assert cfg["min_overall_readiness"] == _DEFAULT_ANOMALY_CONFIG["min_overall_readiness"]


# ---------------------------------------------------------------------------
# Anomaly detection — rule 1: critical pillar below minimum
# ---------------------------------------------------------------------------


class TestAnomalyDetectionCriticalPillar:
    def test_no_findings_when_all_healthy(self):
        scores = _make_pillar_scores()
        findings = _detect_score_anomalies(scores, 1.0, _DEFAULT_ANOMALY_CONFIG)
        assert findings == []

    def test_critical_finding_when_security_below_min(self):
        scores = _make_pillar_scores({"security": 0.3})
        findings = _detect_score_anomalies(scores, 0.9, _DEFAULT_ANOMALY_CONFIG)
        critical = [f for f in findings if f["pillar"] == "security" and f["severity"] == "critical"]
        assert len(critical) == 1
        assert "30.0%" in critical[0]["reason"]

    def test_critical_finding_for_il_classification(self):
        scores = _make_pillar_scores({"il-classification": 0.0})
        findings = _detect_score_anomalies(scores, 0.9, _DEFAULT_ANOMALY_CONFIG)
        # Rule 1 must produce at least one critical finding (rule 2 may also produce a warning)
        critical = [f for f in findings if f["pillar"] == "il-classification" and f["severity"] == "critical"]
        assert len(critical) >= 1

    def test_no_finding_when_critical_pillar_exactly_at_min(self):
        scores = _make_pillar_scores({"security": 0.50})
        findings = _detect_score_anomalies(scores, 0.9, _DEFAULT_ANOMALY_CONFIG)
        # score == threshold: rule 1 must NOT produce a critical finding (< not <=)
        security_critical = [f for f in findings if f["pillar"] == "security" and f["severity"] == "critical"]
        assert security_critical == []

    def test_non_critical_pillar_below_min_is_not_flagged_as_critical(self):
        scores = _make_pillar_scores({"documentation": 0.1})
        findings = _detect_score_anomalies(scores, 0.9, _DEFAULT_ANOMALY_CONFIG)
        doc_critical = [f for f in findings if f["pillar"] == "documentation" and f["severity"] == "critical"]
        assert doc_critical == []


# ---------------------------------------------------------------------------
# Anomaly detection — rule 2: outlier detection
# ---------------------------------------------------------------------------


class TestAnomalyDetectionOutlier:
    def test_outlier_pillar_flagged_as_warning(self):
        # Most pillars at 1.0, one at 0.0 — extreme outlier
        scores = _make_pillar_scores({"documentation": 0.0})
        cfg = {**_DEFAULT_ANOMALY_CONFIG, "outlier_std_dev_threshold": 1.5}
        findings = _detect_score_anomalies(scores, 0.9, cfg)
        outlier = [f for f in findings if f["pillar"] == "documentation" and f["severity"] == "warning"]
        assert len(outlier) >= 1

    def test_no_outlier_when_all_scores_equal(self):
        scores = _make_pillar_scores()  # all 1.0
        findings = _detect_score_anomalies(scores, 1.0, _DEFAULT_ANOMALY_CONFIG)
        warnings = [f for f in findings if f["severity"] == "warning"]
        assert warnings == []

    def test_skips_outlier_detection_below_min_pillars(self):
        # Only 2 pillars — below min_pillars_for_stats=3
        scores = {
            "security": {"passed": 0, "total": 10, "percentage": 0.0},
            "testing":  {"passed": 10, "total": 10, "percentage": 1.0},
        }
        cfg = {**_DEFAULT_ANOMALY_CONFIG, "min_pillars_for_stats": 3}
        findings = _detect_score_anomalies(scores, 0.5, cfg)
        # No outlier findings since we have fewer than min_pillars_for_stats
        warnings = [f for f in findings if f["severity"] == "warning"]
        assert warnings == []


# ---------------------------------------------------------------------------
# Anomaly detection — rule 3: overall score below floor
# ---------------------------------------------------------------------------


class TestAnomalyDetectionOverallScore:
    def test_critical_finding_when_overall_below_min(self):
        scores = _make_pillar_scores()
        findings = _detect_score_anomalies(scores, 0.20, _DEFAULT_ANOMALY_CONFIG)
        overall_findings = [f for f in findings if f["pillar"] is None and f["severity"] == "critical"]
        assert len(overall_findings) == 1
        assert "20.0%" in overall_findings[0]["reason"]

    def test_no_finding_when_overall_at_min(self):
        scores = _make_pillar_scores()
        findings = _detect_score_anomalies(scores, 0.40, _DEFAULT_ANOMALY_CONFIG)
        overall_findings = [f for f in findings if f["pillar"] is None]
        assert overall_findings == []


# ---------------------------------------------------------------------------
# Anomaly detection — disabled
# ---------------------------------------------------------------------------


class TestAnomalyDetectionDisabled:
    def test_returns_empty_when_disabled(self):
        scores = _make_pillar_scores({"security": 0.0, "il-classification": 0.0})
        cfg = {**_DEFAULT_ANOMALY_CONFIG, "enabled": False}
        findings = _detect_score_anomalies(scores, 0.1, cfg)
        assert findings == []


# ---------------------------------------------------------------------------
# Integration: run_readiness_check
# ---------------------------------------------------------------------------


class TestRunReadinessCheck:
    def _make_mock_pillar(self, pillar_id: str, passed: int, total: int):
        result = CriterionResult(
            criterion_id=f"{pillar_id}-c1",
            passed=passed > 0,
            message="ok" if passed > 0 else "fail",
        )
        pillar = MagicMock()
        pillar.id = pillar_id
        pillar.run.return_value = [result]
        pillar.score.return_value = {
            "pillar_id": pillar_id,
            "passed": passed,
            "total": total,
            "percentage": round(passed / total, 4) if total > 0 else 0.0,
        }
        return pillar

    def test_result_contains_expected_keys(self, tmp_path):
        all_pillar_ids = list(_DEFAULT_PILLAR_WEIGHTS.keys())
        mock_pillars = [self._make_mock_pillar(pid, 8, 10) for pid in all_pillar_ids]

        with patch("tools.ai_augmentation.agent_readiness.checker._ALL_PILLARS", mock_pillars):
            result = run_readiness_check(tmp_path)

        assert "pillar_scores" in result
        assert "overall_readiness_score" in result
        assert "icdev_checks" in result
        assert "anomalies" in result

    def test_anomalies_empty_when_all_pillars_pass(self, tmp_path):
        all_pillar_ids = list(_DEFAULT_PILLAR_WEIGHTS.keys())
        mock_pillars = [self._make_mock_pillar(pid, 10, 10) for pid in all_pillar_ids]

        with patch("tools.ai_augmentation.agent_readiness.checker._ALL_PILLARS", mock_pillars):
            result = run_readiness_check(tmp_path)

        assert result["anomalies"] == []

    def test_anomalies_populated_when_security_fails(self, tmp_path):
        all_pillar_ids = list(_DEFAULT_PILLAR_WEIGHTS.keys())
        mock_pillars = []
        for pid in all_pillar_ids:
            passed = 0 if pid == "security" else 10
            mock_pillars.append(self._make_mock_pillar(pid, passed, 10))

        with patch("tools.ai_augmentation.agent_readiness.checker._ALL_PILLARS", mock_pillars):
            result = run_readiness_check(tmp_path)

        security_anomalies = [
            a for a in result["anomalies"]
            if a.get("pillar") == "security" and a["severity"] == "critical"
        ]
        assert len(security_anomalies) == 1

    def test_config_path_override_loads_custom_weights(self, tmp_path):
        cfg_file = tmp_path / "custom.yaml"
        cfg_file.write_text(
            "version: '1.0'\npillar_weights:\n  testing: 99.0\n",
            encoding="utf-8",
        )
        all_pillar_ids = list(_DEFAULT_PILLAR_WEIGHTS.keys())
        mock_pillars = [self._make_mock_pillar(pid, 10, 10) for pid in all_pillar_ids]

        with patch("tools.ai_augmentation.agent_readiness.checker._ALL_PILLARS", mock_pillars):
            result = run_readiness_check(tmp_path, config_path=cfg_file)

        # Overall score still valid float in [0, 1]
        assert 0.0 <= result["overall_readiness_score"] <= 1.0
