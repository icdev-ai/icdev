# CUI // SP-CTI
"""Tests for the il_classification pillar's anomaly-detection threshold system."""
from __future__ import annotations

import pathlib
import textwrap


from tools.aiify.agent_readiness.pillars.il_classification import (
    PILLAR,
    _check_cui_file_headers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: pathlib.Path, rel: str, content: str) -> pathlib.Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _patch_thresholds(monkeypatch, **overrides):
    defaults = {
        "sample_size": 30,
        "min_header_ratio": 0.5,
        "warn_header_ratio": 0.3,
        "min_adaptive_sample": 10,
        "adaptive_denominator": 5,
    }
    defaults.update(overrides)
    import tools.aiify.agent_readiness.pillars.il_classification as mod
    monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)


# ---------------------------------------------------------------------------
# _load_thresholds — config loading and fallback behaviour
# ---------------------------------------------------------------------------

class TestLoadThresholds:
    def test_returns_yaml_values_when_config_present(self, tmp_path, monkeypatch):
        cfg = tmp_path / "args" / "agent_readiness_config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "pillars:\n"
            "  il_classification:\n"
            "    cui_file_headers:\n"
            "      sample_size: 50\n"
            "      min_header_ratio: 0.8\n"
            "      warn_header_ratio: 0.6\n",
            encoding="utf-8",
        )
        import tools.aiify.agent_readiness.pillars.il_classification as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["sample_size"] == 50
        assert result["min_header_ratio"] == 0.8
        assert result["warn_header_ratio"] == 0.6
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.aiify.agent_readiness.pillars.il_classification as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["sample_size"] == 30
        assert result["min_header_ratio"] == 0.5
        assert result["warn_header_ratio"] == 0.3
        mod._load_thresholds.cache_clear()

    def test_partial_config_merges_with_defaults(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  il_classification:\n    cui_file_headers:\n      min_header_ratio: 0.9\n",
            encoding="utf-8",
        )
        import tools.aiify.agent_readiness.pillars.il_classification as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["min_header_ratio"] == 0.9
        assert result["sample_size"] == 30        # default
        assert result["warn_header_ratio"] == 0.3  # default
        mod._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.aiify.agent_readiness.pillars.il_classification as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["sample_size"] == 30
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_cui_file_headers — adaptive sample + two-tier threshold
# ---------------------------------------------------------------------------

class TestCheckCuiFileHeaders:
    def test_skips_when_no_python_files(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch)
        result = _check_cui_file_headers(tmp_path)
        assert result.passed
        assert result.skipped

    def test_passes_when_ratio_meets_min_threshold(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, min_header_ratio=0.5)
        _write(tmp_path, "a.py", "# CUI // SP-CTI\nx = 1\n")
        _write(tmp_path, "b.py", "# CUI // SP-CTI\nx = 2\n")
        result = _check_cui_file_headers(tmp_path)
        assert result.passed
        assert "2/2" in result.message

    def test_fails_in_warning_zone_between_warn_and_min(self, tmp_path, monkeypatch):
        # ratio = 1/3 ≈ 0.33 — above warn_header_ratio (0.3) but below min_header_ratio (0.5)
        _patch_thresholds(monkeypatch, min_header_ratio=0.5, warn_header_ratio=0.3)
        _write(tmp_path, "a.py", "# CUI // SP-CTI\nx = 1\n")
        _write(tmp_path, "b.py", "x = 2\n")
        _write(tmp_path, "c.py", "x = 3\n")
        result = _check_cui_file_headers(tmp_path)
        assert not result.passed
        # Should NOT include "anomalously low" in the warning-zone message
        assert "anomalously low" not in result.message.lower()

    def test_fails_with_anomaly_message_below_warn_threshold(self, tmp_path, monkeypatch):
        # ratio = 0/3 = 0.0 — below warn_header_ratio (0.3) → anomaly message
        _patch_thresholds(monkeypatch, min_header_ratio=0.5, warn_header_ratio=0.3)
        _write(tmp_path, "a.py", "x = 1\n")
        _write(tmp_path, "b.py", "x = 2\n")
        _write(tmp_path, "c.py", "x = 3\n")
        result = _check_cui_file_headers(tmp_path)
        assert not result.passed
        assert "anomalously low" in result.message.lower()

    def test_adaptive_sample_respects_configured_cap(self, tmp_path, monkeypatch):
        # With sample_size=5 and 20 files, adaptive sample = min(5, max(10, 20//5+1)) = 5
        _patch_thresholds(monkeypatch, sample_size=5, min_header_ratio=0.5)
        for i in range(20):
            _write(tmp_path, f"f{i:02d}.py", "# CUI // SP-CTI\nx = 1\n")
        result = _check_cui_file_headers(tmp_path)
        assert result.passed

    def test_recognises_controlled_unclassified_header(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, min_header_ratio=0.5)
        _write(tmp_path, "a.py", "# CONTROLLED UNCLASSIFIED\nx = 1\n")
        _write(tmp_path, "b.py", "# CONTROLLED UNCLASSIFIED\nx = 2\n")
        result = _check_cui_file_headers(tmp_path)
        assert result.passed

    def test_fails_when_no_headers_present(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, min_header_ratio=0.5, warn_header_ratio=0.3)
        _write(tmp_path, "a.py", "x = 1\n")
        _write(tmp_path, "b.py", "x = 2\n")
        result = _check_cui_file_headers(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# PILLAR integration — criteria list
# ---------------------------------------------------------------------------

class TestIlClassificationPillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {
            "cui-file-headers",
            "claude-md-classification",
            "il-env-variable",
            "classification-manager-usage",
        }

    def test_pillar_id(self):
        assert PILLAR.id == "il-classification"
