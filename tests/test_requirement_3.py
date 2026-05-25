#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for Functional Requirement 3 — Security Hardening Artifacts Are In Place.

Validates the five checks defined in functional_validation_plan.md:
1. pipeline_security_generator.py exists and passes py_compile
2. args/security_gates.yaml exists and is valid YAML
3. Gates config contains blocking conditions for CAT1 STIG, critical/high vulns, failed tests, missing markings
4. Bandit scan of tools/devsecops/ returns zero findings in the security generator
5. Security gates config references SBOM regeneration and container hardening requirements
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECURITY_GENERATOR = PROJECT_ROOT / "tools" / "devsecops" / "pipeline_security_generator.py"
SECURITY_GATES_CONFIG = PROJECT_ROOT / "args" / "security_gates.yaml"
DEVSECOPS_DIR = PROJECT_ROOT / "tools" / "devsecops"
COMPLIANCE_CONFIG = PROJECT_ROOT / "args" / "compliance_config.yaml"


class TestPipelineSecurityGeneratorExists:
    """Requirement 3 Step 1 — Generator Existence and Syntax."""

    def test_security_generator_exists(self):
        assert SECURITY_GENERATOR.exists(), (
            "tools/devsecops/pipeline_security_generator.py must exist"
        )

    def test_security_generator_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SECURITY_GENERATOR)],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0, (
            f"pipeline_security_generator.py failed py_compile: {result.stderr}"
        )

    def test_security_generator_has_entry_point(self):
        source = SECURITY_GENERATOR.read_text(encoding="utf-8")
        assert "if __name__ == '__main__':" in source or 'argparse' in source, (
            "security generator must have a CLI entry point"
        )


class TestSecurityGatesConfigExists:
    """Requirement 3 Step 2 & 3 — Gates Config Existence and Coverage."""

    def test_security_gates_config_exists(self):
        assert SECURITY_GATES_CONFIG.exists(), "args/security_gates.yaml must exist"

    def test_security_gates_config_is_valid_yaml(self):
        content = SECURITY_GATES_CONFIG.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            pytest.fail(f"args/security_gates.yaml is not valid YAML: {exc}")
        assert data is not None, "args/security_gates.yaml must not be empty"

    def test_security_gates_has_merge_block_conditions(self):
        content = SECURITY_GATES_CONFIG.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert "merge_gates" in data, "args/security_gates.yaml must define merge_gates"
        block_on = data["merge_gates"].get("block_on", [])
        required = [
            "cat1_stig_finding",
            "critical_vulnerability",
            "high_vulnerability",
            "failed_tests",
            "missing_cui_markings_if_required",
        ]
        missing = [r for r in required if r not in block_on]
        assert not missing, (
            f"merge_gates block_on missing required conditions: {missing}"
        )

    def test_security_gates_has_deployment_block_conditions(self):
        content = SECURITY_GATES_CONFIG.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert "deployment_gates" in data, (
            "args/security_gates.yaml must define deployment_gates"
        )
        block_on = data["deployment_gates"].get("block_on", [])
        assert "sbom_not_generated" in block_on, (
            "deployment_gates must block on sbom_not_generated"
        )

    def test_security_gates_references_sbom(self):
        content = SECURITY_GATES_CONFIG.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        flat = yaml.dump(data)
        assert "sbom" in flat.lower(), (
            "security gates config must reference SBOM policy"
        )

    def test_compliance_config_references_container_hardening(self):
        """Container hardening (non-root, read-only rootfs) is enforced via compliance_config."""
        assert COMPLIANCE_CONFIG.exists(), "args/compliance_config.yaml must exist"
        flat = COMPLIANCE_CONFIG.read_text(encoding="utf-8").lower()
        assert "non_root" in flat or "non-root" in flat, (
            "compliance config must reference non-root execution"
        )
        assert "read_only" in flat or "read_only_rootfs" in flat, (
            "compliance config must reference read-only rootfs"
        )


class TestBanditSecurityScan:
    """Requirement 3 Step 4 — Static Security Scan."""

    def test_bandit_finds_zero_findings_in_security_generator(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "bandit",
                "-f",
                "json",
                "-ii",
                "-ll",
                str(SECURITY_GENERATOR),
            ],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        # bandit returns 0 when no issues found, 1 when issues found
        if result.returncode == 0:
            return
        # If there are findings, parse JSON and assert none are in the generator
        try:
            payload = json.loads(result.stdout)
        except Exception:
            pytest.fail(f"bandit output not parseable: {result.stdout}\n{result.stderr}")
        results = payload.get("results", [])
        generator_findings = [
            r for r in results
            if Path(r.get("filename", "")).resolve() == SECURITY_GENERATOR.resolve()
        ]
        assert not generator_findings, (
            f"bandit found findings in pipeline_security_generator.py: {generator_findings}"
        )

    def test_bandit_finds_zero_findings_in_devsecops_directory(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "bandit",
                "-r",
                str(DEVSECOPS_DIR),
                "--severity-level",
                "medium",
                "-f",
                "json",
            ],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        try:
            payload = json.loads(result.stdout)
        except Exception:
            pytest.fail(f"bandit output not parseable: {result.stdout}\n{result.stderr}")
        results = payload.get("results", [])
        # Only count medium+ severity findings
        medium_plus = [r for r in results if r.get("issue_severity", "").lower() in ("medium", "high")]
        assert not medium_plus, (
            f"bandit found medium/high findings in tools/devsecops/: {medium_plus}"
        )
