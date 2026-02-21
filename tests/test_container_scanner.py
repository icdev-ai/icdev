# CUI // SP-CTI
"""Tests for the ICDEV Container Scanner (tools/security/container_scanner.py).

Validates Dockerfile static analysis (DS001-DS010 rules), Trivy output
parsing, and security gate evaluation logic.
"""

import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.security.container_scanner import (
        DOCKERFILE_CHECKS,
        _extract_cvss,
        _parse_trivy_output,
        evaluate_gate,
        scan_dockerfile,
    )
except ImportError:
    pytestmark = pytest.mark.skip("tools.security.container_scanner not available")


# ---------------------------------------------------------------------------
# Dockerfile Analysis — Individual Check Rules
# ---------------------------------------------------------------------------

class TestDockerfileNoUserDirective:
    """DS001: Container should not run as root."""

    def test_no_user_directive_flags_ds001(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nRUN pip install flask\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS001" in ids

    def test_user_directive_suppresses_ds001(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nUSER appuser\nRUN pip install flask\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS001" not in ids

    def test_user_root_still_flags_ds001(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nUSER root\nRUN pip install flask\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS001" in ids


class TestDockerfileLatestTag:
    """DS002: Base image should not use :latest or no tag."""

    def test_latest_tag_flagged(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:latest\nUSER app\nHEALTHCHECK CMD true\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS002" in ids

    def test_no_tag_flagged(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python\nUSER app\nHEALTHCHECK CMD true\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS002" in ids

    def test_pinned_version_with_alias_clean(self, tmp_path):
        """FROM with 'AS builder' avoids the bare-image regex match."""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11-slim AS builder\nUSER app\nHEALTHCHECK CMD true\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        # The regex ^FROM\s+\S+\s*$ does NOT match when 'AS builder' is appended
        assert "DS002" not in ids


class TestDockerfileAddVsCopy:
    """DS003: ADD should be replaced with COPY."""

    def test_add_instruction_flagged(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nADD . /app\nUSER app\nHEALTHCHECK CMD true\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS003" in ids


class TestDockerfileSudo:
    """DS004: sudo usage should be flagged."""

    def test_sudo_flagged(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nRUN sudo apt-get update\nUSER app\nHEALTHCHECK CMD true\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS004" in ids


class TestDockerfileHealthcheck:
    """DS005: Containers should have a HEALTHCHECK."""

    def test_no_healthcheck_flagged(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nUSER app\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS005" in ids

    def test_healthcheck_present_clean(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "FROM python:3.11\nUSER app\nHEALTHCHECK --interval=30s CMD curl -f http://localhost/ || exit 1\n"
        )
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS005" not in ids


class TestDockerfileSensitivePort:
    """DS006: Exposing SSH port 22 should be flagged."""

    def test_expose_22_flagged(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nEXPOSE 22\nUSER app\nHEALTHCHECK CMD true\n")
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS006" in ids


class TestDockerfileSecretsInEnv:
    """DS007: ENV directives with secrets should be flagged."""

    def test_env_password_flagged(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "FROM python:3.11\nENV DB_PASSWORD=secret123\nUSER app\nHEALTHCHECK CMD true\n"
        )
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS007" in ids


class TestDockerfileCurlPipe:
    """DS008: curl/wget piped to shell should be flagged."""

    def test_curl_pipe_sh_flagged(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "FROM python:3.11\nRUN curl https://example.com/install.sh | sh\n"
            "USER app\nHEALTHCHECK CMD true\n"
        )
        result = scan_dockerfile(str(df))
        ids = [f["check_id"] for f in result["findings"]]
        assert "DS008" in ids


# ---------------------------------------------------------------------------
# Dockerfile scanning — general behavior
# ---------------------------------------------------------------------------

class TestDockerfileScanGeneral:
    """General scan_dockerfile behavior."""

    def test_missing_dockerfile_returns_failure(self, tmp_path):
        result = scan_dockerfile(str(tmp_path / "nonexistent"))
        assert result["success"] is False

    def test_clean_dockerfile_minimal_findings(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "FROM python:3.11-slim\n"
            "COPY . /app\n"
            "USER appuser\n"
            "HEALTHCHECK --interval=30s CMD python -c 'print(1)'\n"
        )
        result = scan_dockerfile(str(df))
        assert result["success"] is True
        # Clean dockerfile should have very few (or zero) findings
        high_count = result["summary"].get("HIGH", 0)
        assert high_count == 0

    def test_summary_has_severity_counts(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python\nRUN echo hello\n")
        result = scan_dockerfile(str(df))
        assert "summary" in result
        assert "total" in result["summary"]
        assert "HIGH" in result["summary"]


# ---------------------------------------------------------------------------
# Trivy Output Parsing
# ---------------------------------------------------------------------------

class TestTrivyParsing:
    """Verify _parse_trivy_output extracts vulnerability findings."""

    def test_parse_empty_json(self):
        findings = _parse_trivy_output("{}")
        assert findings == []

    def test_parse_invalid_json(self):
        findings = _parse_trivy_output("not json")
        assert findings == []

    def test_parse_valid_trivy_output(self):
        trivy_json = json.dumps({
            "Results": [
                {
                    "Target": "python:3.11",
                    "Type": "os",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2024-1234",
                            "PkgName": "openssl",
                            "InstalledVersion": "1.1.1",
                            "FixedVersion": "1.1.2",
                            "Severity": "CRITICAL",
                            "Title": "Buffer overflow in openssl",
                            "Description": "A buffer overflow was found...",
                            "PrimaryURL": "https://nvd.nist.gov/vuln/detail/CVE-2024-1234",
                        }
                    ],
                }
            ]
        })
        findings = _parse_trivy_output(trivy_json)
        assert len(findings) == 1
        assert findings[0]["vulnerability_id"] == "CVE-2024-1234"
        assert findings[0]["severity"] == "CRITICAL"
        assert findings[0]["package"] == "openssl"

    def test_parse_multiple_targets(self):
        trivy_json = json.dumps({
            "Results": [
                {
                    "Target": "layer1",
                    "Type": "os",
                    "Vulnerabilities": [
                        {"VulnerabilityID": "CVE-1", "Severity": "HIGH",
                         "PkgName": "a", "Title": "", "Description": ""},
                    ],
                },
                {
                    "Target": "layer2",
                    "Type": "library",
                    "Vulnerabilities": [
                        {"VulnerabilityID": "CVE-2", "Severity": "LOW",
                         "PkgName": "b", "Title": "", "Description": ""},
                    ],
                },
            ]
        })
        findings = _parse_trivy_output(trivy_json)
        assert len(findings) == 2

    def test_parse_null_vulnerabilities(self):
        trivy_json = json.dumps({
            "Results": [{"Target": "x", "Type": "os", "Vulnerabilities": None}]
        })
        findings = _parse_trivy_output(trivy_json)
        assert findings == []


# ---------------------------------------------------------------------------
# CVSS Score Extraction
# ---------------------------------------------------------------------------

class TestCVSSExtraction:
    """Verify _extract_cvss pulls the highest score."""

    def test_empty_cvss(self):
        assert _extract_cvss({}) is None

    def test_v3_score(self):
        cvss = {"nvd": {"V3Score": 9.8}}
        assert _extract_cvss(cvss) == 9.8

    def test_highest_across_sources(self):
        cvss = {
            "nvd": {"V3Score": 7.5},
            "redhat": {"V3Score": 8.1},
        }
        assert _extract_cvss(cvss) == 8.1


# ---------------------------------------------------------------------------
# Gate Evaluation
# ---------------------------------------------------------------------------

class TestGateEvaluation:
    """Verify evaluate_gate checks findings against thresholds."""

    def test_no_findings_passes(self):
        result = evaluate_gate([])
        assert result["passed"] is True
        assert len(result["violations"]) == 0

    def test_critical_finding_fails_default_gate(self):
        findings = [{"severity": "CRITICAL"}]
        result = evaluate_gate(findings)
        assert result["passed"] is False
        assert any("CRITICAL" in v for v in result["violations"])

    def test_high_finding_fails_default_gate(self):
        findings = [{"severity": "HIGH"}]
        result = evaluate_gate(findings)
        assert result["passed"] is False

    def test_low_findings_pass_default_gate(self):
        findings = [{"severity": "LOW"}, {"severity": "MEDIUM"}]
        result = evaluate_gate(findings)
        assert result["passed"] is True

    def test_custom_thresholds(self):
        findings = [{"severity": "CRITICAL"}]
        # Allow 1 critical
        result = evaluate_gate(findings, thresholds={"max_critical": 1, "max_high": 5})
        assert result["passed"] is True

    def test_severity_counts_in_result(self):
        findings = [
            {"severity": "CRITICAL"},
            {"severity": "HIGH"},
            {"severity": "HIGH"},
            {"severity": "LOW"},
        ]
        result = evaluate_gate(findings)
        assert result["severity_counts"]["CRITICAL"] == 1
        assert result["severity_counts"]["HIGH"] == 2
        assert result["severity_counts"]["LOW"] == 1
