#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for Functional Requirement 1 — Environment Setup Artifacts Are Present.

Validates the five checks defined in functional_validation_plan.md:
1. .env.example exists with mandatory variables
2. requirements.txt exists with core dependencies
3. health_check.py exists and runs without unhandled exceptions
4. health_check --json produces parseable output with success/status field
5. health_check.py uses cross-platform guards (pathlib, utf-8, tempfile)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEALTH_CHECK_SCRIPT = PROJECT_ROOT / "tools" / "testing" / "health_check.py"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
REQUIREMENTS_TXT = PROJECT_ROOT / "requirements.txt"


class TestEnvExample:
    """Requirement 1 Step 1 — File Existence Check."""

    def test_env_example_exists(self):
        assert ENV_EXAMPLE.exists(), ".env.example must exist in project root"

    @pytest.mark.parametrize(
        "var_name",
        [
            "ICDEV_STORAGE_BACKEND",
            "OLLAMA_BASE_URL",
            "ICDEV_LLM_PROVIDER",
            "ICDEV_AUTO_COMMIT",
        ],
    )
    def test_env_example_contains_mandatory_variable(self, var_name):
        content = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert var_name in content, (
            f".env.example must define mandatory variable {var_name}"
        )


class TestRequirementsTxt:
    """Requirement 1 Step 2 — Dependency Manifest Check."""

    def test_requirements_txt_exists(self):
        assert REQUIREMENTS_TXT.exists(), "requirements.txt must exist in project root"

    @pytest.mark.parametrize("pkg", ["flask", "pytest", "pyyaml", "jinja2"])
    def test_requirements_txt_lists_core_dependency(self, pkg):
        content = REQUIREMENTS_TXT.read_text(encoding="utf-8").lower()
        assert pkg in content, f"requirements.txt must list core dependency {pkg}"


class TestHealthCheckExecution:
    """Requirement 1 Step 3 & 4 — Health Check Tool Execution and Schema."""

    def test_health_check_script_exists(self):
        assert HEALTH_CHECK_SCRIPT.exists(), (
            "tools/testing/health_check.py must exist"
        )

    def test_health_check_executes_without_unhandled_exception(self):
        result = subprocess.run(
            [sys.executable, str(HEALTH_CHECK_SCRIPT), "--json"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode in (0, 1), (
            "health_check.py must exit cleanly (0 or 1), not crash"
        )
        assert "Traceback" not in result.stderr, (
            f"Unhandled exception in health_check.py stderr: {result.stderr}"
        )

    def test_health_check_json_status(self):
        result = subprocess.run(
            [sys.executable, str(HEALTH_CHECK_SCRIPT), "--json"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert result.stdout, "health_check --json must emit output"
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            pytest.fail(f"health_check --json output is not valid JSON: {exc}")

        # Accept either the actual code field 'success' or the planned field 'status'
        if "success" in payload:
            assert isinstance(payload["success"], bool)
        elif "status" in payload:
            assert payload["status"] == "ok"
        else:
            pytest.fail("JSON output missing both 'success' and 'status' fields")


class TestHealthCheckCrossPlatformGuards:
    """Requirement 1 Step 5 — Cross-Platform Guard Check."""

    def test_pathlib_used(self):
        source = HEALTH_CHECK_SCRIPT.read_text(encoding="utf-8")
        assert "pathlib" in source, "health_check.py must import pathlib"
        assert "Path(" in source, "health_check.py must use pathlib.Path"

    def test_utf8_encoding_used(self):
        source = HEALTH_CHECK_SCRIPT.read_text(encoding="utf-8")
        assert "encoding='utf-8'" in source or 'encoding="utf-8"' in source, (
            "health_check.py must use encoding='utf-8' for file I/O"
        )

    def test_no_hardcoded_unix_paths(self):
        source = HEALTH_CHECK_SCRIPT.read_text(encoding="utf-8")
        bad_patterns = ["/tmp/", "/var/", "/usr/", "/opt/", "/home/"]
        found = [p for p in bad_patterns if p in source]
        assert not found, (
            f"health_check.py contains hard-coded Unix paths: {found}"
        )

    def test_tempfile_gettempdir_used(self):
        source = HEALTH_CHECK_SCRIPT.read_text(encoding="utf-8")
        assert "tempfile.gettempdir()" in source, (
            "health_check.py must use tempfile.gettempdir() for temporary paths"
        )
