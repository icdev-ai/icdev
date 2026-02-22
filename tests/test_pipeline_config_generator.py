#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/ci/pipeline_config_generator.py."""

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.ci.pipeline_config_generator import (
    CHECK_REGISTRY,
    generate_pipeline,
    _build_gate_evaluation_script,
    _generate_github_workflow,
    _generate_gitlab_ci,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _write_yaml(tmp_dir: str, content: dict) -> str:
    path = Path(tmp_dir) / "icdev.yaml"
    try:
        import yaml
        path.write_text(yaml.dump(content), encoding="utf-8")
    except ImportError:
        path.write_text(json.dumps(content), encoding="utf-8")
    return str(path)


MINIMAL_MANIFEST = {
    "version": 1,
    "project": {"name": "test-app", "type": "api", "language": "python"},
    "impact_level": "IL4",
}


# ── Test generate_pipeline ──────────────────────────────────────────────

class TestGeneratePipeline:
    def test_github_minimal(self, tmp_path):
        _write_yaml(str(tmp_path), MINIMAL_MANIFEST)
        result = generate_pipeline(directory=str(tmp_path), platform="github")

        assert result["platform"] == "github"
        assert result["errors"] == []
        assert "icdev.yml" in result["output_path"]
        assert "ICDEV Compliance Pipeline" in result["content"]
        assert "actions/checkout@v4" in result["content"]

    def test_gitlab_minimal(self, tmp_path):
        _write_yaml(str(tmp_path), MINIMAL_MANIFEST)
        result = generate_pipeline(directory=str(tmp_path), platform="gitlab")

        assert result["platform"] == "gitlab"
        assert result["errors"] == []
        assert ".gitlab-ci.yml" in result["output_path"]
        assert "stages:" in result["content"]
        assert "icdev-setup" in result["content"]

    def test_checks_from_on_pr(self, tmp_path):
        manifest = {
            **MINIMAL_MANIFEST,
            "pipeline": {"on_pr": ["sast", "unit_tests"]},
        }
        _write_yaml(str(tmp_path), manifest)
        result = generate_pipeline(directory=str(tmp_path), platform="github")

        content = result["content"]
        assert "SAST Scan" in content
        assert "Unit Tests" in content
        # Should NOT contain checks that aren't in on_pr
        assert "Secret Detection" not in content
        assert result["checks_enabled"]["on_pr"] == ["sast", "unit_tests"]

    def test_gate_thresholds(self, tmp_path):
        manifest = {
            **MINIMAL_MANIFEST,
            "pipeline": {
                "on_pr": ["stig_check"],
                "gates": {"stig_max_cat1": 0, "stig_max_cat2": 5},
            },
        }
        _write_yaml(str(tmp_path), manifest)
        result = generate_pipeline(directory=str(tmp_path), platform="github")

        content = result["content"]
        assert "Gate Evaluation" in content
        assert "cat1" in content.lower() or "CAT1" in content

    def test_schedule_jobs(self, tmp_path):
        manifest = {
            **MINIMAL_MANIFEST,
            "pipeline": {
                "on_pr": ["sast"],
                "on_schedule": ["cve_triage"],
            },
        }
        _write_yaml(str(tmp_path), manifest)
        result = generate_pipeline(directory=str(tmp_path), platform="github")

        content = result["content"]
        assert "schedule" in content
        assert "cron" in content
        assert "CVE Triage" in content

    def test_platform_auto_detection(self, tmp_path):
        _write_yaml(str(tmp_path), MINIMAL_MANIFEST)
        with mock.patch("tools.ci.pipeline_config_generator.detect_vcs_platform", return_value="github"):
            result = generate_pipeline(directory=str(tmp_path), platform="auto")
        assert result["platform"] == "github"

    def test_write_flag_creates_file(self, tmp_path):
        _write_yaml(str(tmp_path), MINIMAL_MANIFEST)
        result = generate_pipeline(directory=str(tmp_path), platform="github", write=True)

        assert result["written"] is True
        assert Path(result["output_path"]).exists()

    def test_dry_run_does_not_write(self, tmp_path):
        _write_yaml(str(tmp_path), MINIMAL_MANIFEST)
        result = generate_pipeline(directory=str(tmp_path), platform="github", write=True, dry_run=True)

        assert result["written"] is False
        assert not Path(result["output_path"]).exists()
        assert len(result["content"]) > 0

    def test_missing_manifest_error(self, tmp_path):
        result = generate_pipeline(directory=str(tmp_path), platform="github")
        assert len(result["errors"]) > 0

    def test_merge_checks_in_output(self, tmp_path):
        manifest = {
            **MINIMAL_MANIFEST,
            "pipeline": {
                "on_pr": ["sast"],
                "on_merge": ["ssp_generate", "sbom_generate"],
            },
        }
        _write_yaml(str(tmp_path), manifest)
        result = generate_pipeline(directory=str(tmp_path), platform="gitlab")

        content = result["content"]
        assert "ssp_generate" in content
        assert "sbom_generate" in content
        assert "main" in content  # Only on main branch


# ── Test gate script ────────────────────────────────────────────────────

class TestGateScript:
    def test_empty_gates(self):
        script = _build_gate_evaluation_script({}, "github")
        assert script == ""

    def test_stig_gate(self):
        script = _build_gate_evaluation_script({"stig_max_cat1": 0}, "github")
        assert "cat1" in script.lower() or "CAT1" in script
        assert "BLOCKED" in script

    def test_custom_thresholds(self):
        gates = {"stig_max_cat1": 0, "stig_max_cat2": 3, "max_critical_vulns": 0}
        script = _build_gate_evaluation_script(gates, "github")
        assert "cat2" in script.lower() or "CAT2" in script
        assert "critical" in script.lower()


# ── Test check registry ─────────────────────────────────────────────────

class TestCheckRegistry:
    def test_all_checks_have_required_fields(self):
        for name, check in CHECK_REGISTRY.items():
            assert "name" in check, f"Check {name} missing 'name'"
            assert "command" in check, f"Check {name} missing 'command'"
            assert "stage" in check, f"Check {name} missing 'stage'"
