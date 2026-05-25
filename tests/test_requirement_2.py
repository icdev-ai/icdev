#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for Functional Requirement 2 — CI/CD Pipeline Configuration Is In Place.

Validates the five checks defined in functional_validation_plan.md:
1. pipeline_config_generator.py exists and exposes a callable entry point
2. args/cicd_config.yaml exists and is valid YAML
3. Config defines meaningful CI/CD keys (cicd, connectivity, routing, channels, executor, recovery, conversation)
4. Generator smoke test runs without crashing
5. GitHub workflow file references compatible stage names
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_SCRIPT = PROJECT_ROOT / "tools" / "ci" / "pipeline_config_generator.py"
CICD_CONFIG = PROJECT_ROOT / "args" / "cicd_config.yaml"
GITHUB_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci_cd_pipeline.yml"


class TestPipelineConfigGeneratorExists:
    """Requirement 2 Step 1 — Generator Existence."""

    def test_generator_script_exists(self):
        assert GENERATOR_SCRIPT.exists(), (
            "tools/ci/pipeline_config_generator.py must exist"
        )

    def test_generator_has_entry_point(self):
        source = GENERATOR_SCRIPT.read_text(encoding="utf-8")
        assert "if __name__ == '__main__':" in source or 'argparse' in source, (
            "generator must have a CLI entry point (__main__ block or argparse)"
        )


class TestCicdConfigExists:
    """Requirement 2 Step 2 & 3 — Config File Existence and Content."""

    def test_cicd_config_exists(self):
        assert CICD_CONFIG.exists(), "args/cicd_config.yaml must exist"

    def test_cicd_config_is_valid_yaml(self):
        content = CICD_CONFIG.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            pytest.fail(f"args/cicd_config.yaml is not valid YAML: {exc}")
        assert data is not None, "args/cicd_config.yaml must not be empty"

    def test_cicd_config_has_meaningful_keys(self):
        content = CICD_CONFIG.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert "cicd" in data, "args/cicd_config.yaml must define a 'cicd' top-level key"
        cicd = data["cicd"]
        required_sections = ["connectivity", "routing", "channels", "executor", "recovery", "conversation"]
        missing = [s for s in required_sections if s not in cicd]
        assert not missing, (
            f"args/cicd_config.yaml missing CI/CD sections: {missing}"
        )


class TestGeneratorSmokeTest:
    """Requirement 2 Step 4 — Generator Smoke Test."""

    def test_generator_dry_run_exits_cleanly(self):
        result = subprocess.run(
            [sys.executable, str(GENERATOR_SCRIPT), "--dry-run", "--json"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode in (0, 1), (
            "generator must exit cleanly (0 or 1), not crash"
        )
        assert "Traceback" not in result.stderr, (
            f"Unhandled exception in generator stderr: {result.stderr}"
        )

    def test_generator_json_output_is_parseable(self):
        result = subprocess.run(
            [sys.executable, str(GENERATOR_SCRIPT), "--dry-run", "--json"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert result.stdout, "generator --json must emit output"
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            pytest.fail(f"generator --json output is not valid JSON: {exc}")
        assert isinstance(payload, dict), "generator JSON output must be an object"


class TestGithubWorkflowIntegration:
    """Requirement 2 Step 5 — Upstream Integration."""

    def test_github_workflow_exists(self):
        assert GITHUB_WORKFLOW.exists(), (
            ".github/workflows/ci_cd_pipeline.yml must exist"
        )

    def test_github_workflow_has_jobs(self):
        content = GITHUB_WORKFLOW.read_text(encoding="utf-8")
        assert "jobs:" in content, (
            ".github/workflows/ci_cd_pipeline.yml must define jobs"
        )

    def test_github_workflow_references_stages(self):
        content = GITHUB_WORKFLOW.read_text(encoding="utf-8")
        assert "Interface-Validation-Scan" in content or "deploy" in content, (
            ".github/workflows/ci_cd_pipeline.yml must reference known stage/job names"
        )
