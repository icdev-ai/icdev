# CUI // SP-CTI
"""Tests for _base.py: load_pillar_config and Pillar.score configurable precision."""
from __future__ import annotations


import pytest


class TestLoadAgentReadinessConfig:
    def _mod(self):
        import tools.ai_augmentation.agent_readiness.pillars._base as m
        return m

    def test_returns_empty_when_file_absent(self, tmp_path, monkeypatch):
        m = self._mod()
        monkeypatch.setattr(m, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        m._load_agent_readiness_config.cache_clear()
        assert m._load_agent_readiness_config() == {}
        m._load_agent_readiness_config.cache_clear()

    def test_load_pillar_config_returns_sub_dict(self, tmp_path, monkeypatch):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("pillars:\n  my_pillar:\n    threshold: 42\n", encoding="utf-8")
        m = self._mod()
        monkeypatch.setattr(m, "_ARGS_PATH", cfg)
        m._load_agent_readiness_config.cache_clear()
        assert m.load_pillar_config("my_pillar") == {"threshold": 42}
        m._load_agent_readiness_config.cache_clear()

    def test_load_pillar_config_returns_empty_for_unknown_key(self, tmp_path, monkeypatch):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("pillars:\n  other: {}\n", encoding="utf-8")
        m = self._mod()
        monkeypatch.setattr(m, "_ARGS_PATH", cfg)
        m._load_agent_readiness_config.cache_clear()
        assert m.load_pillar_config("nonexistent") == {}
        m._load_agent_readiness_config.cache_clear()

    def test_returns_empty_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        m = self._mod()
        monkeypatch.setattr(m, "_ARGS_PATH", cfg)
        m._load_agent_readiness_config.cache_clear()
        assert m._load_agent_readiness_config() == {}
        m._load_agent_readiness_config.cache_clear()


class TestPillarScorePrecision:
    """Pillar.score() precision is driven by score.precision in YAML (default 4)."""

    def _make_two_of_three_pillar(self):
        from tools.ai_augmentation.agent_readiness.pillars._base import (
            Criterion, CriterionResult, Pillar,
        )
        return Pillar(
            id="test-p", name="Test", description="Test pillar",
            criteria=[
                Criterion("c1", "C1", "d", "test-p", 1, lambda r: CriterionResult("c1", True, "ok")),
                Criterion("c2", "C2", "d", "test-p", 1, lambda r: CriterionResult("c2", False, "fail")),
                Criterion("c3", "C3", "d", "test-p", 1, lambda r: CriterionResult("c3", True, "ok")),
            ],
        )

    def test_default_precision_is_4(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars._base as m
        monkeypatch.setattr(m, "_ARGS_PATH", tmp_path / "absent.yaml")
        m._load_agent_readiness_config.cache_clear()
        pillar = self._make_two_of_three_pillar()
        results = pillar.run(tmp_path)
        score = pillar.score(results)
        assert score["percentage"] == pytest.approx(round(2 / 3, 4))
        m._load_agent_readiness_config.cache_clear()

    def test_configurable_precision_reads_from_yaml(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars._base as m
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("score:\n  precision: 2\n", encoding="utf-8")
        monkeypatch.setattr(m, "_ARGS_PATH", cfg)
        m._load_agent_readiness_config.cache_clear()
        pillar = self._make_two_of_three_pillar()
        results = pillar.run(tmp_path)
        score = pillar.score(results)
        assert score["percentage"] == pytest.approx(round(2 / 3, 2))
        m._load_agent_readiness_config.cache_clear()

    def test_all_pass_returns_1_0(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars._base as m
        from tools.ai_augmentation.agent_readiness.pillars._base import (
            Criterion, CriterionResult, Pillar,
        )
        monkeypatch.setattr(m, "_ARGS_PATH", tmp_path / "absent.yaml")
        m._load_agent_readiness_config.cache_clear()
        pillar = Pillar(
            id="p", name="P", description="P",
            criteria=[Criterion("x", "X", "d", "p", 1, lambda r: CriterionResult("x", True, "ok"))],
        )
        score = pillar.score(pillar.run(tmp_path))
        assert score["percentage"] == pytest.approx(1.0)
        assert score["passed"] == 1
        assert score["total"] == 1
        m._load_agent_readiness_config.cache_clear()

    def test_all_skipped_returns_0_0(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars._base as m
        from tools.ai_augmentation.agent_readiness.pillars._base import (
            Criterion, CriterionResult, Pillar,
        )
        monkeypatch.setattr(m, "_ARGS_PATH", tmp_path / "absent.yaml")
        m._load_agent_readiness_config.cache_clear()
        pillar = Pillar(
            id="p", name="P", description="P",
            criteria=[Criterion("x", "X", "d", "p", 1, lambda r: CriterionResult("x", True, "ok", skipped=True))],
        )
        score = pillar.score(pillar.run(tmp_path))
        assert score["percentage"] == pytest.approx(0.0)
        assert score["passed"] == 0
        assert score["total"] == 0
        m._load_agent_readiness_config.cache_clear()

    def test_score_dict_has_required_keys(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars._base as m
        monkeypatch.setattr(m, "_ARGS_PATH", tmp_path / "absent.yaml")
        m._load_agent_readiness_config.cache_clear()
        pillar = self._make_two_of_three_pillar()
        score = pillar.score(pillar.run(tmp_path))
        assert {"pillar_id", "passed", "total", "percentage"} <= score.keys()
        assert score["pillar_id"] == "test-p"
        m._load_agent_readiness_config.cache_clear()
