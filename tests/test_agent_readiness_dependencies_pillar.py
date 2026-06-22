# CUI // SP-CTI
"""Tests for the dependencies pillar's anomaly-detection threshold system."""
from __future__ import annotations

import pathlib
import textwrap
import time


from tools.ai_augmentation.agent_readiness.pillars.dependencies import (
    PILLAR,
    _check_lock_file,
    _check_lock_file_freshness,
    _check_pinned_versions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: pathlib.Path, rel: str, content: str) -> pathlib.Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _load_thresholds — config loading and fallback behaviour
# ---------------------------------------------------------------------------

class TestLoadThresholds:
    def test_returns_yaml_values_when_config_present(self, tmp_path, monkeypatch):
        cfg = tmp_path / "args" / "agent_readiness_config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "pillars:\n"
            "  dependencies:\n"
            "    lock_file_freshness:\n"
            "      max_age_months: 3\n"
            "    pinned_versions:\n"
            "      min_pinned_ratio: 0.9\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["max_age_months"] == 3
        assert result["min_pinned_ratio"] == 0.9
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["max_age_months"] == 6
        assert result["min_pinned_ratio"] == 0.8
        mod._load_thresholds.cache_clear()

    def test_partial_config_merges_with_defaults(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  dependencies:\n    lock_file_freshness:\n      max_age_months: 12\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["max_age_months"] == 12
        assert result["min_pinned_ratio"] == 0.8   # default
        mod._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["max_age_months"] == 6
        assert result["min_pinned_ratio"] == 0.8
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_lock_file — presence check
# ---------------------------------------------------------------------------

class TestCheckLockFile:
    def test_passes_with_poetry_lock(self, tmp_path):
        _write(tmp_path, "poetry.lock", "# generated\n")
        result = _check_lock_file(tmp_path)
        assert result.passed
        assert "poetry.lock" in result.message

    def test_passes_with_requirements_txt(self, tmp_path):
        _write(tmp_path, "requirements.txt", "flask==3.0.0\n")
        result = _check_lock_file(tmp_path)
        assert result.passed

    def test_fails_with_no_lock_file(self, tmp_path):
        result = _check_lock_file(tmp_path)
        assert not result.passed
        assert "No dependency lock file" in result.message


# ---------------------------------------------------------------------------
# _check_lock_file_freshness — anomaly detection for freshness window
# ---------------------------------------------------------------------------

class TestCheckLockFileFreshness:
    def _patch_thresholds(self, monkeypatch, **overrides):
        defaults = {"max_age_months": 6, "min_pinned_ratio": 0.8}
        defaults.update(overrides)
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)

    def test_passes_when_lock_file_is_fresh(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, max_age_months=6)
        _write(tmp_path, "poetry.lock", "# fresh\n")
        # mtime defaults to now; no need to adjust
        result = _check_lock_file_freshness(tmp_path)
        assert result.passed
        assert "day(s) ago" in result.message

    def test_reports_anomaly_when_lock_file_is_stale(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, max_age_months=6)
        lock = _write(tmp_path, "poetry.lock", "# stale\n")
        # Set mtime to 8 months ago
        old_mtime = time.time() - (8 * 30 * 24 * 60 * 60)
        import os
        os.utime(str(lock), (old_mtime, old_mtime))
        result = _check_lock_file_freshness(tmp_path)
        assert not result.passed
        assert "max 6" in result.message

    def test_custom_threshold_respected(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, max_age_months=3)
        lock = _write(tmp_path, "yarn.lock", "# lockfile\n")
        # Set mtime to 4 months ago — stale under 3-month threshold
        old_mtime = time.time() - (4 * 30 * 24 * 60 * 60)
        import os
        os.utime(str(lock), (old_mtime, old_mtime))
        result = _check_lock_file_freshness(tmp_path)
        assert not result.passed
        assert "max 3" in result.message

    def test_skips_when_no_lock_file_found(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_lock_file_freshness(tmp_path)
        assert result.skipped


# ---------------------------------------------------------------------------
# _check_pinned_versions — anomaly detection for pin ratio
# ---------------------------------------------------------------------------

class TestCheckPinnedVersions:
    def _patch_thresholds(self, monkeypatch, **overrides):
        defaults = {"max_age_months": 6, "min_pinned_ratio": 0.8}
        defaults.update(overrides)
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)

    def test_passes_when_ratio_meets_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_pinned_ratio=0.8)
        # 4/4 = 100% >= 80% threshold
        _write(tmp_path, "requirements.txt",
               "flask==3.0.0\nrequests==2.31.0\nsqlalchemy==2.0.0\npytest==8.0.0\n")
        result = _check_pinned_versions(tmp_path)
        assert result.passed
        assert "4/4" in result.message

    def test_reports_anomaly_when_ratio_below_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_pinned_ratio=0.8)
        _write(tmp_path, "requirements.txt", "flask\nrequests\nsqlalchemy==2.0.0\n")
        result = _check_pinned_versions(tmp_path)
        assert not result.passed
        assert "1/3" in result.message
        assert "80%" in result.message

    def test_custom_ratio_threshold_respected(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_pinned_ratio=1.0)
        _write(tmp_path, "requirements.txt", "flask==3.0.0\nrequests\n")
        result = _check_pinned_versions(tmp_path)
        assert not result.passed
        assert "100%" in result.message

    def test_skips_on_empty_requirements_txt(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, "requirements.txt", "# only comments\n")
        result = _check_pinned_versions(tmp_path)
        assert result.skipped

    def test_passes_via_lock_file_proxy(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, "poetry.lock", "# generated\n")
        result = _check_pinned_versions(tmp_path)
        assert result.passed
        assert "Lock file" in result.message

    def test_fails_with_no_evidence(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_pinned_versions(tmp_path)
        assert not result.passed
        assert "No pinned version evidence" in result.message


# ---------------------------------------------------------------------------
# PILLAR integration — run all criteria
# ---------------------------------------------------------------------------

class TestDependenciesPillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {"lock-file", "lock-file-freshness", "pinned-versions", "sbom-present"}

    def test_score_all_pass(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "max_age_months": 6, "min_pinned_ratio": 0.5,
        })
        _write(tmp_path, "poetry.lock", "# lock\n")
        _write(tmp_path, "requirements.txt", "flask==3.0.0\n")
        _write(tmp_path, "sbom.json", '{"bomFormat":"CycloneDX"}\n')
        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] == score["total"]
