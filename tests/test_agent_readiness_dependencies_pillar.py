# CUI // SP-CTI
"""Tests for the dependencies pillar's anomaly-detection threshold system."""
from __future__ import annotations

import pathlib
import textwrap
import time


from tools.aiify.agent_readiness.pillars.dependencies import (
    PILLAR,
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
            "      min_pin_ratio: 0.9\n",
            encoding="utf-8",
        )
        import tools.aiify.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["max_age_months"] == 3
        assert result["min_pin_ratio"] == 0.9
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.aiify.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["max_age_months"] == 6
        assert result["min_pin_ratio"] == 0.8
        mod._load_thresholds.cache_clear()

    def test_partial_config_merges_with_defaults(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  dependencies:\n    lock_file_freshness:\n      max_age_months: 12\n",
            encoding="utf-8",
        )
        import tools.aiify.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["max_age_months"] == 12
        assert result["min_pin_ratio"] == 0.8   # default
        mod._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.aiify.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["max_age_months"] == 6
        assert result["min_pin_ratio"] == 0.8
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_lock_file_freshness — anomaly detection for lock file age
# ---------------------------------------------------------------------------

class TestCheckLockFileFreshness:
    def _patch_thresholds(self, monkeypatch, **overrides):
        defaults = {"max_age_months": 6, "min_pin_ratio": 0.8}
        defaults.update(overrides)
        import tools.aiify.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)

    def test_passes_when_lock_file_fresh(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, max_age_months=6)
        p = _write(tmp_path, "poetry.lock", "[metadata]\n")
        # Set mtime to 10 days ago — well within 6 months
        recent = time.time() - (10 * 86400)
        import os
        os.utime(str(p), (recent, recent))
        result = _check_lock_file_freshness(tmp_path)
        assert result.passed
        assert "10 day" in result.message

    def test_reports_anomaly_when_lock_file_stale(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, max_age_months=6)
        p = _write(tmp_path, "poetry.lock", "[metadata]\n")
        # Set mtime to 8 months ago — beyond the 6-month threshold
        stale = time.time() - (8 * 30 * 86400)
        import os
        os.utime(str(p), (stale, stale))
        result = _check_lock_file_freshness(tmp_path)
        assert not result.passed
        assert "anomalously stale" in result.message
        assert "max 6" in result.message

    def test_custom_threshold_tightens_freshness_window(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, max_age_months=2)
        p = _write(tmp_path, "Cargo.lock", "[metadata]\n")
        # 3 months old — passes 6-month default but fails custom 2-month threshold
        mtime = time.time() - (3 * 30 * 86400)
        import os
        os.utime(str(p), (mtime, mtime))
        result = _check_lock_file_freshness(tmp_path)
        assert not result.passed
        assert "max 2" in result.message

    def test_skips_when_no_lock_file_present(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_lock_file_freshness(tmp_path)
        assert result.skipped


# ---------------------------------------------------------------------------
# _check_pinned_versions — anomaly detection for pin ratio
# ---------------------------------------------------------------------------

class TestCheckPinnedVersions:
    def _patch_thresholds(self, monkeypatch, **overrides):
        defaults = {"max_age_months": 6, "min_pin_ratio": 0.8}
        defaults.update(overrides)
        import tools.aiify.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)

    def test_passes_when_ratio_meets_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_pin_ratio=0.8)
        # 4/5 pinned = 80% — exactly meets the threshold
        _write(tmp_path, "requirements.txt",
               "flask==3.0.0\nrequests==2.31.0\nnumpy==1.26.0\nscipy==1.12.0\npytest\n")
        result = _check_pinned_versions(tmp_path)
        assert result.passed
        assert "4/5" in result.message

    def test_reports_anomaly_when_ratio_below_threshold(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_pin_ratio=0.8)
        # Only 1 of 4 pinned = 25% — well below 80%
        _write(tmp_path, "requirements.txt", "flask==3.0.0\nrequests>=2.0\nnumpy\nscipy\n")
        result = _check_pinned_versions(tmp_path)
        assert not result.passed
        assert "anomalously low" in result.message
        assert "80%" in result.message

    def test_custom_threshold_relaxes_pin_requirement(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch, min_pin_ratio=0.5)
        # 3 of 4 pinned = 75% — fails 80% default but passes 50% custom threshold
        _write(tmp_path, "requirements.txt", "flask==3.0.0\nrequests==2.31.0\nnumpy==1.26.0\nscipy\n")
        result = _check_pinned_versions(tmp_path)
        assert result.passed

    def test_skips_when_requirements_txt_empty(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, "requirements.txt", "# just a comment\n")
        result = _check_pinned_versions(tmp_path)
        assert result.skipped

    def test_passes_via_lock_file_proxy_when_no_requirements_txt(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        _write(tmp_path, "poetry.lock", "[metadata]\n")
        result = _check_pinned_versions(tmp_path)
        assert result.passed
        assert "Lock file" in result.message

    def test_fails_when_no_requirements_and_no_lock_file(self, tmp_path, monkeypatch):
        self._patch_thresholds(monkeypatch)
        result = _check_pinned_versions(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# PILLAR integration — run all criteria
# ---------------------------------------------------------------------------

class TestDependenciesPillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {"lock-file", "lock-file-freshness", "pinned-versions", "sbom-present"}

    def test_score_lock_file_and_pinned_pass(self, tmp_path, monkeypatch):
        import tools.aiify.agent_readiness.pillars.dependencies as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "max_age_months": 6,
            "min_pin_ratio": 0.5,
        })
        _write(tmp_path, "requirements.txt", "flask==3.0.0\nrequests==2.31.0\n")
        results = PILLAR.run(tmp_path)
        by_id = {r.criterion_id: r for r in results}
        assert by_id["lock-file"].passed
        assert by_id["pinned-versions"].passed
