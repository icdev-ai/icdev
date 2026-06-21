# CUI // SP-CTI
"""Tests for _base.py global pillar anomaly-detection threshold system."""
from __future__ import annotations

import pathlib

import pytest

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _PILLAR_DEFAULTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pillar(n_criteria: int = 3) -> Pillar:
    def _always_pass(repo: pathlib.Path) -> CriterionResult:
        return CriterionResult("x", True, "ok")

    def _always_fail(repo: pathlib.Path) -> CriterionResult:
        return CriterionResult("x", False, "fail")

    criteria = [
        Criterion(f"c{i}", f"C{i}", "", "test", 1, _always_pass)
        for i in range(n_criteria)
    ]
    return Pillar("test", "Test", "", criteria)


def _make_results(passed: int, failed: int, skipped: int = 0) -> list[CriterionResult]:
    results = []
    for i in range(passed):
        results.append(CriterionResult(f"p{i}", True, "pass"))
    for i in range(failed):
        results.append(CriterionResult(f"f{i}", False, "fail"))
    for i in range(skipped):
        results.append(CriterionResult(f"s{i}", False, "skip", skipped=True))
    return results


# ---------------------------------------------------------------------------
# _load_pillar_thresholds — config loading and fallback behaviour
# ---------------------------------------------------------------------------

class TestLoadPillarThresholds:
    def test_returns_yaml_value_when_config_present(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  global:\n    min_passing_threshold: 0.75\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars._base as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_thresholds.cache_clear()
        result = mod._load_pillar_thresholds()
        assert result["min_passing_threshold"] == pytest.approx(0.75)
        mod._load_pillar_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars._base as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_pillar_thresholds.cache_clear()
        result = mod._load_pillar_thresholds()
        assert result["min_passing_threshold"] == _PILLAR_DEFAULTS["min_passing_threshold"]
        mod._load_pillar_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.ai_augmentation.agent_readiness.pillars._base as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_thresholds.cache_clear()
        result = mod._load_pillar_thresholds()
        assert result["min_passing_threshold"] == _PILLAR_DEFAULTS["min_passing_threshold"]
        mod._load_pillar_thresholds.cache_clear()

    def test_partial_config_uses_default_for_missing_key(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text("pillars:\n  global: {}\n", encoding="utf-8")
        import tools.ai_augmentation.agent_readiness.pillars._base as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_thresholds.cache_clear()
        result = mod._load_pillar_thresholds()
        assert result["min_passing_threshold"] == _PILLAR_DEFAULTS["min_passing_threshold"]
        mod._load_pillar_thresholds.cache_clear()

    def test_no_global_section_uses_default(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text("pillars:\n  documentation:\n    readme:\n      min_content_length: 80\n", encoding="utf-8")
        import tools.ai_augmentation.agent_readiness.pillars._base as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_thresholds.cache_clear()
        result = mod._load_pillar_thresholds()
        assert result["min_passing_threshold"] == _PILLAR_DEFAULTS["min_passing_threshold"]
        mod._load_pillar_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# Pillar.score() — anomalous flag
# ---------------------------------------------------------------------------

class TestPillarScoreAnomalousFlag:
    def _patch_threshold(self, monkeypatch, threshold: float) -> None:
        import tools.ai_augmentation.agent_readiness.pillars._base as mod
        monkeypatch.setattr(mod, "_load_pillar_thresholds", lambda: {"min_passing_threshold": threshold})

    def test_score_includes_anomalous_key(self, monkeypatch):
        self._patch_threshold(monkeypatch, 0.6)
        pillar = _make_pillar()
        score = pillar.score(_make_results(3, 0))
        assert "anomalous" in score

    def test_all_pass_not_anomalous(self, monkeypatch):
        self._patch_threshold(monkeypatch, 0.6)
        pillar = _make_pillar()
        score = pillar.score(_make_results(3, 0))
        assert score["percentage"] == pytest.approx(1.0)
        assert score["anomalous"] is False

    def test_score_below_threshold_is_anomalous(self, monkeypatch):
        self._patch_threshold(monkeypatch, 0.6)
        pillar = _make_pillar()
        # 1/3 ≈ 0.333, below 0.6
        score = pillar.score(_make_results(1, 2))
        assert score["anomalous"] is True

    def test_score_at_threshold_not_anomalous(self, monkeypatch):
        self._patch_threshold(monkeypatch, 0.6)
        pillar = _make_pillar(5)
        # 3/5 = 0.6, exactly at threshold — not anomalous
        score = pillar.score(_make_results(3, 2))
        assert score["anomalous"] is False

    def test_score_just_below_threshold_is_anomalous(self, monkeypatch):
        self._patch_threshold(monkeypatch, 0.6)
        pillar = _make_pillar(10)
        # 5/10 = 0.5, below 0.6
        score = pillar.score(_make_results(5, 5))
        assert score["anomalous"] is True

    def test_zero_total_not_anomalous(self, monkeypatch):
        self._patch_threshold(monkeypatch, 0.6)
        pillar = _make_pillar()
        # All skipped → 0.0 percentage — below threshold but no evaluated criteria
        score = pillar.score(_make_results(0, 0, skipped=3))
        assert score["percentage"] == pytest.approx(0.0)
        assert score["anomalous"] is True  # 0.0 < 0.6

    def test_strict_threshold_flags_more_pillars(self, monkeypatch):
        self._patch_threshold(monkeypatch, 1.0)
        pillar = _make_pillar()
        # 2/3 passes — anomalous at threshold=1.0
        score = pillar.score(_make_results(2, 1))
        assert score["anomalous"] is True

    def test_zero_threshold_never_anomalous(self, monkeypatch):
        self._patch_threshold(monkeypatch, 0.0)
        pillar = _make_pillar()
        score = pillar.score(_make_results(0, 3))
        assert score["anomalous"] is False

    def test_score_dict_retains_existing_keys(self, monkeypatch):
        self._patch_threshold(monkeypatch, 0.6)
        pillar = _make_pillar(4)
        score = pillar.score(_make_results(2, 2))
        assert score["pillar_id"] == "test"
        assert score["passed"] == 2
        assert score["total"] == 4
        assert "percentage" in score
