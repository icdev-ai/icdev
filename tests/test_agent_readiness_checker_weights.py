# CUI // SP-CTI
"""Tests for checker.py anomaly-detection weight system (_load_pillar_weights)."""
from __future__ import annotations


import pytest


# ---------------------------------------------------------------------------
# _load_pillar_weights — config loading and fallback behaviour
# ---------------------------------------------------------------------------

class TestLoadPillarWeights:
    def _get_mod(self):
        import tools.aiify.agent_readiness.checker as mod
        return mod

    def test_returns_defaults_when_file_absent(self, tmp_path, monkeypatch):
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_pillar_weights.cache_clear()
        result = mod._load_pillar_weights()
        assert result["testing"] == pytest.approx(1.2)
        assert result["security"] == pytest.approx(1.2)
        assert result["il-classification"] == pytest.approx(1.5)
        assert result["nist-controls"] == pytest.approx(1.5)
        mod._load_pillar_weights.cache_clear()

    def test_returns_yaml_values_when_config_present(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillar_weights:\n"
            "  testing: 2.0\n"
            "  security: 0.5\n"
            "  il-classification: 3.0\n",
            encoding="utf-8",
        )
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_weights.cache_clear()
        result = mod._load_pillar_weights()
        assert result["testing"] == pytest.approx(2.0)
        assert result["security"] == pytest.approx(0.5)
        assert result["il-classification"] == pytest.approx(3.0)
        # unspecified pillars fall back to defaults
        assert result["documentation"] == pytest.approx(1.0)
        mod._load_pillar_weights.cache_clear()

    def test_partial_config_merges_with_defaults(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillar_weights:\n  code-quality: 2.5\n",
            encoding="utf-8",
        )
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_weights.cache_clear()
        result = mod._load_pillar_weights()
        assert result["code-quality"] == pytest.approx(2.5)
        assert result["testing"] == pytest.approx(1.2)   # default preserved
        mod._load_pillar_weights.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_weights.cache_clear()
        result = mod._load_pillar_weights()
        assert result["testing"] == pytest.approx(1.2)
        mod._load_pillar_weights.cache_clear()

    def test_empty_pillar_weights_section_uses_defaults(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text("pillar_weights: {}\n", encoding="utf-8")
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_weights.cache_clear()
        result = mod._load_pillar_weights()
        assert result["security"] == pytest.approx(1.2)
        mod._load_pillar_weights.cache_clear()

    def test_anomalously_low_weight_is_clamped_to_min(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text("pillar_weights:\n  testing: 0.0\n", encoding="utf-8")
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_weights.cache_clear()
        result = mod._load_pillar_weights()
        assert result["testing"] == pytest.approx(mod._MIN_WEIGHT)
        mod._load_pillar_weights.cache_clear()

    def test_configurable_min_weight_from_yaml(self, tmp_path, monkeypatch):
        """min_weight in pillar_weights overrides the hardcoded _MIN_WEIGHT fallback."""
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillar_weights:\n"
            "  testing: 0.0\n"
            "  min_weight: 0.3\n",
            encoding="utf-8",
        )
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_weights.cache_clear()
        result = mod._load_pillar_weights()
        # weight 0.0 must be clamped to the configured 0.3, not the hardcoded 0.1
        assert result["testing"] == pytest.approx(0.3)
        # min_weight must not be stored as a pillar entry
        assert "min_weight" not in result
        mod._load_pillar_weights.cache_clear()

    def test_min_weight_in_config_does_not_affect_weights_above_floor(self, tmp_path, monkeypatch):
        """Weights already above min_weight must not be modified."""
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillar_weights:\n"
            "  testing: 1.5\n"
            "  min_weight: 0.3\n",
            encoding="utf-8",
        )
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_pillar_weights.cache_clear()
        result = mod._load_pillar_weights()
        assert result["testing"] == pytest.approx(1.5)
        mod._load_pillar_weights.cache_clear()

    def test_all_default_pillar_ids_present(self, tmp_path, monkeypatch):
        mod = self._get_mod()
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "absent.yaml")
        mod._load_pillar_weights.cache_clear()
        result = mod._load_pillar_weights()
        expected_ids = {
            "code-quality", "documentation", "testing", "structure",
            "dependencies", "configuration", "security",
            "il-classification", "nist-controls", "stig-compliance", "append-only-audit",
        }
        assert expected_ids.issubset(result.keys())
        mod._load_pillar_weights.cache_clear()


# ---------------------------------------------------------------------------
# run_readiness_check — weight integration
# ---------------------------------------------------------------------------

class TestRunReadinessCheckWeights:
    def test_custom_weight_changes_overall_score(self, tmp_path, monkeypatch):
        """Boosting one pillar's weight must shift the overall score."""
        import tools.aiify.agent_readiness.checker as mod

        # Patch all pillars to return 100% pass rate
        all_pass_weights = {p: 1.0 for p in mod._WEIGHT_DEFAULTS}
        monkeypatch.setattr(mod, "_load_pillar_weights", lambda: all_pass_weights)

        from tools.aiify.agent_readiness.pillars._base import CriterionResult

        def _all_pass_pillar(pillar):
            def run(repo_path):
                return [CriterionResult(c.id, True, "ok") for c in pillar.criteria]
            pillar.run = run
            return pillar

        patched_pillars = [_all_pass_pillar(p) for p in mod._ALL_PILLARS]
        monkeypatch.setattr(mod, "_ALL_PILLARS", patched_pillars)

        result = mod.run_readiness_check(tmp_path)
        assert result["overall_readiness_score"] == pytest.approx(1.0)

    def test_overall_score_reflects_failing_high_weight_pillar(self, tmp_path, monkeypatch):
        """A heavily-weighted failing pillar must reduce the overall score more than a lightly-weighted one."""
        import tools.aiify.agent_readiness.checker as mod
        from tools.aiify.agent_readiness.pillars._base import CriterionResult, Criterion, Pillar

        heavy_fail = Pillar(
            id="heavy", name="Heavy", description="High-weight failing pillar",
            criteria=[Criterion("x", "X", "desc", "heavy", 1, lambda r: CriterionResult("x", False, "fail"))],
        )
        light_fail = Pillar(
            id="light", name="Light", description="Low-weight failing pillar",
            criteria=[Criterion("y", "Y", "desc", "light", 1, lambda r: CriterionResult("y", False, "fail"))],
        )
        monkeypatch.setattr(mod, "_ALL_PILLARS", [heavy_fail, light_fail])
        # heavy pillar fails with weight 3.0; light pillar fails with weight 1.0 — same score (both 0%)
        monkeypatch.setattr(mod, "_load_pillar_weights", lambda: {"heavy": 3.0, "light": 1.0})

        result = mod.run_readiness_check(tmp_path)
        # Both 0% → overall must be 0.0 regardless of weights
        assert result["overall_readiness_score"] == pytest.approx(0.0)

    def test_overall_score_weighted_average(self, tmp_path, monkeypatch):
        """overall = (1.0*w_pass + 0.0*w_fail) / (w_pass + w_fail)."""
        import tools.aiify.agent_readiness.checker as mod
        from tools.aiify.agent_readiness.pillars._base import CriterionResult, Criterion, Pillar

        pass_pillar = Pillar(
            id="pass-p", name="Pass", description="All-pass pillar",
            criteria=[Criterion("a", "A", "d", "pass-p", 1, lambda r: CriterionResult("a", True, "ok"))],
        )
        fail_pillar = Pillar(
            id="fail-p", name="Fail", description="All-fail pillar",
            criteria=[Criterion("b", "B", "d", "fail-p", 1, lambda r: CriterionResult("b", False, "ko"))],
        )
        monkeypatch.setattr(mod, "_ALL_PILLARS", [pass_pillar, fail_pillar])
        monkeypatch.setattr(mod, "_load_pillar_weights", lambda: {"pass-p": 2.0, "fail-p": 1.0})

        result = mod.run_readiness_check(tmp_path)
        # expected = (1.0 * 2.0 + 0.0 * 1.0) / (2.0 + 1.0) = 2/3
        assert result["overall_readiness_score"] == pytest.approx(2 / 3, abs=1e-4)
