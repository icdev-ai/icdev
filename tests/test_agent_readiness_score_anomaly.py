# CUI // SP-CTI
"""Tests for _base.py score-anomaly detection: load_score_anomaly_config,
detect_score_anomalies, and integration with checker.run_readiness_check."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# load_score_anomaly_config
# ---------------------------------------------------------------------------

class TestLoadScoreAnomalyConfig:
    def _get_mod(self):
        import tools.ai_augmentation.agent_readiness.pillars._base as mod
        return mod

    def _clear_cache(self, mod):
        mod._load_agent_readiness_config.cache_clear()

    def test_returns_defaults_when_file_absent(self, tmp_path, monkeypatch):
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        self._clear_cache(mod)
        cfg = mod.load_score_anomaly_config()
        assert cfg["min_passing_pct"] == pytest.approx(0.3)
        assert cfg["ai_analysis_enabled"] is False
        assert cfg["ai_model"] == "claude-haiku-4-5-20251001"
        assert cfg["ai_max_tokens"] == 256
        self._clear_cache(mod)

    def test_reads_min_passing_pct_from_yaml(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text(
            "score_anomaly:\n  min_passing_pct: 0.5\n",
            encoding="utf-8",
        )
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg_file)
        self._clear_cache(mod)
        cfg = mod.load_score_anomaly_config()
        assert cfg["min_passing_pct"] == pytest.approx(0.5)
        self._clear_cache(mod)

    def test_reads_ai_model_from_yaml(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text(
            "score_anomaly:\n  ai_model: claude-haiku-4-5-20251001\n  ai_analysis_enabled: true\n",
            encoding="utf-8",
        )
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg_file)
        self._clear_cache(mod)
        cfg = mod.load_score_anomaly_config()
        assert cfg["ai_model"] == "claude-haiku-4-5-20251001"
        assert cfg["ai_analysis_enabled"] is True
        self._clear_cache(mod)

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text(":\tbad yaml [", encoding="utf-8")
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg_file)
        self._clear_cache(mod)
        cfg = mod.load_score_anomaly_config()
        assert cfg["min_passing_pct"] == pytest.approx(0.3)
        self._clear_cache(mod)

    def test_missing_score_anomaly_key_uses_defaults(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text("pillar_weights:\n  testing: 1.2\n", encoding="utf-8")
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg_file)
        self._clear_cache(mod)
        cfg = mod.load_score_anomaly_config()
        assert cfg["min_passing_pct"] == pytest.approx(0.3)
        self._clear_cache(mod)


# ---------------------------------------------------------------------------
# detect_score_anomalies
# ---------------------------------------------------------------------------

class TestDetectScoreAnomalies:
    def _detect(self, pillar_scores, criterion_results=None):
        from tools.ai_augmentation.agent_readiness.pillars._base import detect_score_anomalies
        return detect_score_anomalies(pillar_scores, criterion_results)

    def test_returns_empty_for_empty_input(self):
        assert self._detect({}) == []

    def test_skips_pillars_with_zero_total(self):
        scores = {"p1": {"passed": 0, "total": 0, "percentage": 0.0}}
        assert self._detect(scores) == []

    def test_non_anomalous_pillar_is_not_anomalous(self):
        scores = {"testing": {"passed": 3, "total": 4, "percentage": 0.75}}
        reports = self._detect(scores)
        assert len(reports) == 1
        assert reports[0].is_anomalous is False
        assert reports[0].pillar_id == "testing"

    def test_low_score_pillar_is_anomalous(self):
        scores = {"security": {"passed": 1, "total": 5, "percentage": 0.2}}
        reports = self._detect(scores)
        assert len(reports) == 1
        r = reports[0]
        assert r.is_anomalous is True
        assert r.pillar_id == "security"
        assert "anomalously low" in r.reason

    def test_score_exactly_at_threshold_is_not_anomalous(self):
        # Default threshold is 0.3; 0.3 exactly should NOT be flagged.
        scores = {"docs": {"passed": 3, "total": 10, "percentage": 0.3}}
        reports = self._detect(scores)
        assert reports[0].is_anomalous is False

    def test_mixed_pillars(self):
        scores = {
            "good": {"passed": 4, "total": 5, "percentage": 0.8},
            "bad":  {"passed": 1, "total": 5, "percentage": 0.2},
        }
        reports = self._detect(scores)
        anomalous = [r for r in reports if r.is_anomalous]
        normal = [r for r in reports if not r.is_anomalous]
        assert len(anomalous) == 1 and anomalous[0].pillar_id == "bad"
        assert len(normal) == 1 and normal[0].pillar_id == "good"

    def test_reason_contains_pillar_id(self):
        scores = {"nist-controls": {"passed": 0, "total": 4, "percentage": 0.0}}
        r = self._detect(scores)[0]
        assert "nist-controls" in r.reason

    def test_ai_reasoning_empty_when_disabled(self):
        scores = {"security": {"passed": 0, "total": 3, "percentage": 0.0}}
        criterion_results = {
            "security": [{"criterion_id": "x", "passed": False, "message": "No scanner", "skipped": False}]
        }
        reports = self._detect(scores, criterion_results)
        assert reports[0].ai_reasoning == ""

    def test_threshold_stored_in_report(self):
        scores = {"testing": {"passed": 1, "total": 5, "percentage": 0.2}}
        r = self._detect(scores)[0]
        assert r.threshold == pytest.approx(0.3)

    def test_score_pct_stored_in_report(self):
        scores = {"docs": {"passed": 2, "total": 5, "percentage": 0.4}}
        r = self._detect(scores)[0]
        assert r.score_pct == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# checker.run_readiness_check — score_anomalies key in output
# ---------------------------------------------------------------------------

class TestRunReadinessCheckAnomalies:
    """Use fresh Pillar instances — never mutate the module-level singleton objects."""

    def _make_pass_pillar(self, pid: str, n: int = 2):
        from tools.ai_augmentation.agent_readiness.pillars._base import (
            CriterionResult, Criterion, Pillar,
        )
        return Pillar(
            id=pid, name=pid, description="test",
            criteria=[
                Criterion(f"{pid}-c{i}", f"C{i}", "d", pid, 1,
                           lambda r, _i=i: CriterionResult(f"c{_i}", True, "ok"))
                for i in range(n)
            ],
        )

    def test_score_anomalies_key_present(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.checker as mod
        pillars = [self._make_pass_pillar("p1"), self._make_pass_pillar("p2")]
        monkeypatch.setattr(mod, "_ALL_PILLARS", pillars)
        monkeypatch.setattr(mod, "_load_pillar_weights", lambda: {"p1": 1.0, "p2": 1.0})
        result = mod.run_readiness_check(tmp_path)
        assert "score_anomalies" in result
        assert isinstance(result["score_anomalies"], list)

    def test_no_anomalies_when_all_pillars_pass_fully(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.checker as mod
        pillars = [self._make_pass_pillar("p1"), self._make_pass_pillar("p2")]
        monkeypatch.setattr(mod, "_ALL_PILLARS", pillars)
        monkeypatch.setattr(mod, "_load_pillar_weights", lambda: {"p1": 1.0, "p2": 1.0})
        result = mod.run_readiness_check(tmp_path)
        assert result["score_anomalies"] == []

    def test_anomaly_reported_for_fully_failing_pillar(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.checker as mod
        from tools.ai_augmentation.agent_readiness.pillars._base import (
            CriterionResult, Criterion, Pillar,
        )

        fail_pillar = Pillar(
            id="fail-test",
            name="Fail",
            description="All-failing test pillar",
            criteria=[
                Criterion("f1", "F1", "d", "fail-test", 1,
                           lambda r: CriterionResult("f1", False, "nothing found")),
                Criterion("f2", "F2", "d", "fail-test", 1,
                           lambda r: CriterionResult("f2", False, "also missing")),
                Criterion("f3", "F3", "d", "fail-test", 1,
                           lambda r: CriterionResult("f3", False, "still missing")),
                Criterion("f4", "F4", "d", "fail-test", 1,
                           lambda r: CriterionResult("f4", False, "not there")),
            ],
        )
        monkeypatch.setattr(mod, "_ALL_PILLARS", [fail_pillar])
        monkeypatch.setattr(mod, "_load_pillar_weights", lambda: {"fail-test": 1.0})

        result = mod.run_readiness_check(tmp_path)
        anomalies = result["score_anomalies"]
        assert len(anomalies) == 1
        a = anomalies[0]
        assert a["pillar_id"] == "fail-test"
        assert a["is_anomalous"] is True
        assert a["score_pct"] == pytest.approx(0.0)
