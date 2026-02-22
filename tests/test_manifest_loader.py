#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/project/manifest_loader.py."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.project.manifest_loader import (
    IL_DEFAULTS,
    _apply_defaults,
    _apply_env_overrides,
    _deep_get,
    _deep_set,
    detect_vcs_platform,
    load_manifest,
    validate_manifest,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _write_yaml(tmp_dir: str, content: dict) -> str:
    """Write a dict as YAML (or JSON fallback) to tmp_dir/icdev.yaml."""
    path = Path(tmp_dir) / "icdev.yaml"
    try:
        import yaml
        path.write_text(yaml.dump(content), encoding="utf-8")
    except ImportError:
        path.write_text(json.dumps(content), encoding="utf-8")
    return str(path)


# ── Test deep helpers ────────────────────────────────────────────────────

class TestDeepHelpers:
    def test_deep_get_nested(self):
        d = {"a": {"b": {"c": 42}}}
        assert _deep_get(d, ("a", "b", "c")) == 42

    def test_deep_get_missing(self):
        assert _deep_get({}, ("a", "b"), "default") == "default"

    def test_deep_set_creates_path(self):
        d = {}
        _deep_set(d, ("a", "b", "c"), 99)
        assert d == {"a": {"b": {"c": 99}}}


# ── Test load_manifest ───────────────────────────────────────────────────

class TestLoadManifest:
    def test_load_minimal_yaml(self, tmp_path):
        content = {
            "version": 1,
            "project": {"name": "my-app", "type": "api", "language": "python"},
            "impact_level": "IL4",
        }
        _write_yaml(str(tmp_path), content)

        result = load_manifest(directory=str(tmp_path))
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["normalized"]["project"]["name"] == "my-app"
        assert result["normalized"]["project"]["id"] == "proj-my-app"

    def test_load_by_file_path(self, tmp_path):
        content = {
            "version": 1,
            "project": {"name": "test", "type": "cli", "language": "go"},
            "impact_level": "IL2",
        }
        path = _write_yaml(str(tmp_path), content)

        result = load_manifest(file_path=path)
        assert result["valid"] is True
        assert result["normalized"]["impact_level"] == "IL2"

    def test_missing_file_returns_error(self, tmp_path):
        result = load_manifest(directory=str(tmp_path))
        assert result["valid"] is False
        assert any("not found" in e for e in result["errors"])

    def test_invalid_yaml_returns_error(self, tmp_path):
        p = tmp_path / "icdev.yaml"
        p.write_text("{invalid yaml::: [[[", encoding="utf-8")
        result = load_manifest(directory=str(tmp_path))
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_non_dict_root_returns_error(self, tmp_path):
        p = tmp_path / "icdev.yaml"
        try:
            import yaml
            p.write_text(yaml.dump(["list", "not", "dict"]), encoding="utf-8")
        except ImportError:
            p.write_text(json.dumps(["list"]), encoding="utf-8")
        result = load_manifest(directory=str(tmp_path))
        assert result["valid"] is False
        assert any("mapping" in e for e in result["errors"])

    def test_version_mismatch_warning(self, tmp_path):
        content = {
            "version": 99,
            "project": {"name": "test", "type": "api"},
            "impact_level": "IL4",
        }
        _write_yaml(str(tmp_path), content)
        result = load_manifest(directory=str(tmp_path))
        assert any("version" in w.lower() for w in result["warnings"])


# ── Test defaults ────────────────────────────────────────────────────────

class TestApplyDefaults:
    def test_il4_defaults(self):
        raw = {"project": {"name": "test"}, "impact_level": "IL4"}
        config = _apply_defaults(raw)

        assert config["classification"]["level"] == "CUI"
        assert config["classification"]["cui_markings"] is True
        assert "fedramp_moderate" in config["compliance"]["frameworks"]
        assert config["profile"]["template"] == "dod_baseline"
        assert config["deployment"]["cloud"] == "aws_govcloud"

    def test_il2_defaults(self):
        raw = {"project": {"name": "test"}, "impact_level": "IL2"}
        config = _apply_defaults(raw)

        assert config["classification"]["level"] == "UNCLASSIFIED"
        assert config["classification"]["cui_markings"] is False
        assert config["compliance"]["frameworks"] == []
        assert config["profile"]["template"] == "startup"
        assert config["deployment"]["cloud"] == "aws"

    def test_il5_defaults(self):
        raw = {"project": {"name": "test"}, "impact_level": "IL5"}
        config = _apply_defaults(raw)

        assert "fedramp_high" in config["compliance"]["frameworks"]
        assert "cmmc_l2" in config["compliance"]["frameworks"]

    def test_il6_defaults(self):
        raw = {"project": {"name": "test"}, "impact_level": "IL6"}
        config = _apply_defaults(raw)

        assert config["classification"]["level"] == "SECRET"
        assert "cmmc_l3" in config["compliance"]["frameworks"]

    def test_auto_generates_project_id(self):
        raw = {"project": {"name": "My Cool App"}, "impact_level": "IL4"}
        config = _apply_defaults(raw)
        assert config["project"]["id"] == "proj-my-cool-app"

    def test_preserves_explicit_project_id(self):
        raw = {"project": {"name": "test", "id": "proj-custom"}, "impact_level": "IL4"}
        config = _apply_defaults(raw)
        assert config["project"]["id"] == "proj-custom"

    def test_default_pipeline_checks(self):
        raw = {"project": {"name": "test"}, "impact_level": "IL4"}
        config = _apply_defaults(raw)
        assert "sast" in config["pipeline"]["on_pr"]
        assert "unit_tests" in config["pipeline"]["on_pr"]
        assert config["pipeline"]["gates"]["stig_max_cat1"] == 0

    def test_user_pipeline_preserved(self):
        raw = {
            "project": {"name": "test"},
            "impact_level": "IL4",
            "pipeline": {"on_pr": ["lint", "unit_tests"]},
        }
        config = _apply_defaults(raw)
        assert config["pipeline"]["on_pr"] == ["lint", "unit_tests"]


# ── Test env overrides ───────────────────────────────────────────────────

class TestEnvOverrides:
    def test_impact_level_override(self):
        config = {"impact_level": "IL4"}
        with mock.patch.dict(os.environ, {"ICDEV_IMPACT_LEVEL": "IL5"}):
            result = _apply_env_overrides(config)
        assert result["impact_level"] == "IL5"

    def test_gate_override(self):
        config = {"pipeline": {"gates": {"min_coverage": 80}}}
        with mock.patch.dict(os.environ, {"ICDEV_GATE_MIN_COVERAGE": "95"}):
            result = _apply_env_overrides(config)
        assert result["pipeline"]["gates"]["min_coverage"] == 95

    def test_deployment_cloud_override(self):
        config = {"deployment": {"cloud": "aws"}}
        with mock.patch.dict(os.environ, {"ICDEV_DEPLOYMENT_CLOUD": "aws_govcloud"}):
            result = _apply_env_overrides(config)
        assert result["deployment"]["cloud"] == "aws_govcloud"

    def test_no_env_no_change(self):
        config = {"impact_level": "IL4"}
        result = _apply_env_overrides(config)
        assert result["impact_level"] == "IL4"


# ── Test validation ──────────────────────────────────────────────────────

class TestValidateManifest:
    def test_valid_il4_config(self):
        config = _apply_defaults(
            {"project": {"name": "test"}, "impact_level": "IL4"}
        )
        errors, warnings = validate_manifest(config)
        assert errors == []

    def test_il6_requires_secret(self):
        config = _apply_defaults(
            {"project": {"name": "test"}, "impact_level": "IL6"}
        )
        # Override classification to non-SECRET
        config["classification"]["level"] = "CUI"
        errors, _ = validate_manifest(config)
        assert any("SECRET" in e for e in errors)

    def test_cjis_requires_il4(self):
        config = _apply_defaults(
            {"project": {"name": "test"}, "impact_level": "IL2"}
        )
        config["compliance"]["frameworks"] = ["cjis"]
        errors, _ = validate_manifest(config)
        assert any("cjis" in e for e in errors)

    def test_fedramp_high_requires_govcloud(self):
        config = _apply_defaults(
            {"project": {"name": "test"}, "impact_level": "IL4"}
        )
        config["compliance"]["frameworks"] = ["fedramp_high"]
        config["deployment"]["cloud"] = "aws"
        errors, _ = validate_manifest(config)
        assert any("aws_govcloud" in e for e in errors)

    def test_missing_name_is_error(self):
        config = {"project": {}, "impact_level": "IL4"}
        errors, _ = validate_manifest(config)
        assert any("name" in e for e in errors)

    def test_no_frameworks_il4_warning(self):
        config = _apply_defaults(
            {"project": {"name": "test"}, "impact_level": "IL4"}
        )
        config["compliance"]["frameworks"] = []
        _, warnings = validate_manifest(config)
        assert any("No compliance frameworks" in w for w in warnings)

    def test_cui_disabled_il5_warning(self):
        config = _apply_defaults(
            {"project": {"name": "test"}, "impact_level": "IL5"}
        )
        config["classification"]["cui_markings"] = False
        _, warnings = validate_manifest(config)
        assert any("CUI markings disabled" in w for w in warnings)


# ── Test VCS detection ───────────────────────────────────────────────────

class TestDetectVcsPlatform:
    def test_github_detected(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0, stdout="https://github.com/org/repo.git\n"
            )
            assert detect_vcs_platform(str(tmp_path)) == "github"

    def test_gitlab_detected(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0, stdout="https://gitlab.example.mil/org/repo.git\n"
            )
            assert detect_vcs_platform(str(tmp_path)) == "gitlab"

    def test_no_remote_returns_unknown(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="error")
            assert detect_vcs_platform(str(tmp_path)) == "unknown"

    def test_exception_returns_unknown(self, tmp_path):
        with mock.patch("subprocess.run", side_effect=OSError("no git")):
            assert detect_vcs_platform(str(tmp_path)) == "unknown"
