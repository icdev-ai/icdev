# [TEMPLATE: CUI // SP-CTI]
"""Tests for the ICDEV Compliance Exporter (tools/compliance/compliance_exporter.py).

Validates CSV control matrix export, executive summary Markdown generation,
evidence package export, POAM CSV creation, and export_all orchestration.
"""

import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.compliance.compliance_exporter import (
        _build_recommendations,
        _STATUS_NORM,
        export_all,
        export_control_matrix,
        export_evidence_package,
        export_executive_summary,
        export_poam_csv,
    )
except ImportError:
    pytestmark = pytest.mark.skip("tools.compliance.compliance_exporter not available")


# ---------------------------------------------------------------------------
# Sample assessment data
# ---------------------------------------------------------------------------

def _sample_assessment(
    project_id="proj-test-001",
    include_not_satisfied=True,
    include_partial=False,
):
    """Build a sample assessment data dict for testing."""
    results = [
        {
            "requirement_id": "AC-1",
            "title": "Policy and Procedures",
            "status": "satisfied",
            "implementation_detail": "Access control policy documented",
            "evidence": "SSP Section 13, AC-1 narrative",
            "last_assessed": "2026-01-15T10:00:00",
        },
        {
            "requirement_id": "AC-2",
            "title": "Account Management",
            "status": "satisfied",
            "implementation_detail": "Automated account provisioning via IdP",
            "evidence": "Active Directory audit logs",
            "last_assessed": "2026-01-15T10:00:00",
        },
    ]

    if include_not_satisfied:
        results.append({
            "requirement_id": "SC-7",
            "title": "Boundary Protection",
            "status": "not_satisfied",
            "implementation_detail": "",
            "evidence": "",
            "last_assessed": "2026-01-15T10:00:00",
        })

    if include_partial:
        results.append({
            "requirement_id": "AU-3",
            "title": "Content of Audit Records",
            "status": "partially_satisfied",
            "implementation_detail": "Basic logging enabled",
            "evidence": "CloudWatch logs",
            "last_assessed": "2026-01-15T10:00:00",
        })

    status_counts = {}
    for r in results:
        s = r["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    total = len(results)
    satisfied = status_counts.get("satisfied", 0)
    partial = status_counts.get("partially_satisfied", 0)
    coverage = round(((satisfied + partial * 0.5) / total * 100) if total else 0, 1)

    return {
        "framework_id": "nist",
        "framework_name": "NIST 800-53 Rev 5",
        "project_id": project_id,
        "assessment_date": "2026-01-15T10:00:00",
        "total_requirements": total,
        "status_counts": status_counts,
        "coverage_pct": coverage,
        "gate_status": "non_compliant" if include_not_satisfied else "compliant",
        "results": results,
    }


# ---------------------------------------------------------------------------
# CSV Control Matrix Export
# ---------------------------------------------------------------------------

class TestControlMatrixExport:
    """Verify export_control_matrix generates valid CSV."""

    def test_csv_file_created(self, tmp_path):
        data = _sample_assessment()
        out = str(tmp_path / "matrix.csv")
        result_path = export_control_matrix(data, out)
        assert Path(result_path).exists()

    def test_csv_has_header(self, tmp_path):
        data = _sample_assessment()
        out = str(tmp_path / "matrix.csv")
        export_control_matrix(data, out)
        with open(out, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert "control_id" in header
        assert "status" in header
        assert "evidence" in header

    def test_csv_row_count_matches(self, tmp_path):
        data = _sample_assessment()
        out = str(tmp_path / "matrix.csv")
        export_control_matrix(data, out)
        with open(out, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        # Header + data rows
        assert len(rows) == len(data["results"]) + 1

    def test_csv_control_ids_present(self, tmp_path):
        data = _sample_assessment()
        out = str(tmp_path / "matrix.csv")
        export_control_matrix(data, out)
        content = Path(out).read_text(encoding="utf-8")
        assert "AC-1" in content
        assert "AC-2" in content


# ---------------------------------------------------------------------------
# Executive Summary Export
# ---------------------------------------------------------------------------

class TestExecutiveSummaryExport:
    """Verify export_executive_summary generates Markdown."""

    def test_executive_summary_file_created(self, tmp_path):
        data = _sample_assessment()
        out = str(tmp_path / "summary.md")
        result_path = export_executive_summary(data, out)
        assert Path(result_path).exists()

    def test_executive_summary_contains_project_id(self, tmp_path):
        data = _sample_assessment()
        out = str(tmp_path / "summary.md")
        export_executive_summary(data, out)
        content = Path(out).read_text(encoding="utf-8")
        assert "proj-test-001" in content

    def test_executive_summary_contains_framework(self, tmp_path):
        data = _sample_assessment()
        out = str(tmp_path / "summary.md")
        export_executive_summary(data, out)
        content = Path(out).read_text(encoding="utf-8")
        assert "NIST 800-53" in content

    def test_executive_summary_contains_cui_marking(self, tmp_path):
        data = _sample_assessment()
        out = str(tmp_path / "summary.md")
        export_executive_summary(data, out)
        content = Path(out).read_text(encoding="utf-8")
        assert "CUI" in content


# ---------------------------------------------------------------------------
# Evidence Package Export
# ---------------------------------------------------------------------------

class TestEvidencePackageExport:
    """Verify export_evidence_package groups findings by status."""

    def test_evidence_package_created(self, tmp_path):
        data = _sample_assessment(include_not_satisfied=True, include_partial=True)
        out = str(tmp_path / "evidence.md")
        result_path = export_evidence_package(data, out)
        assert Path(result_path).exists()

    def test_evidence_package_has_status_sections(self, tmp_path):
        data = _sample_assessment(include_not_satisfied=True, include_partial=True)
        out = str(tmp_path / "evidence.md")
        export_evidence_package(data, out)
        content = Path(out).read_text(encoding="utf-8")
        assert "Not Satisfied" in content
        assert "Satisfied" in content

    def test_evidence_package_has_recommendations(self, tmp_path):
        data = _sample_assessment()
        out = str(tmp_path / "evidence.md")
        export_evidence_package(data, out)
        content = Path(out).read_text(encoding="utf-8")
        assert "Recommendations" in content


# ---------------------------------------------------------------------------
# POAM CSV Export
# ---------------------------------------------------------------------------

class TestPOAMExport:
    """Verify export_poam_csv filters to actionable items."""

    def test_poam_csv_created(self, tmp_path):
        data = _sample_assessment(include_not_satisfied=True)
        out = str(tmp_path / "poam.csv")
        result_path = export_poam_csv(data, out)
        assert Path(result_path).exists()

    def test_poam_only_includes_non_compliant(self, tmp_path):
        data = _sample_assessment(include_not_satisfied=True)
        out = str(tmp_path / "poam.csv")
        export_poam_csv(data, out)
        with open(out, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # Only SC-7 (not_satisfied) should be in POAM
        assert len(rows) == 1
        assert "SC-7" in rows[0]["control_id"]

    def test_poam_has_poam_id(self, tmp_path):
        data = _sample_assessment(include_not_satisfied=True)
        out = str(tmp_path / "poam.csv")
        export_poam_csv(data, out)
        with open(out, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert rows[0]["poam_id"].startswith("POAM-")

    def test_poam_has_scheduled_completion(self, tmp_path):
        data = _sample_assessment(include_not_satisfied=True)
        out = str(tmp_path / "poam.csv")
        export_poam_csv(data, out)
        with open(out, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert rows[0]["scheduled_completion"] != ""

    def test_poam_empty_when_all_satisfied(self, tmp_path):
        data = _sample_assessment(include_not_satisfied=False)
        out = str(tmp_path / "poam.csv")
        export_poam_csv(data, out)
        with open(out, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# export_all orchestration
# ---------------------------------------------------------------------------

class TestExportAll:
    """Verify export_all creates all four output files."""

    def test_export_all_returns_four_paths(self, tmp_path):
        data = _sample_assessment()
        result = export_all(data, str(tmp_path), "nist")
        assert "csv" in result
        assert "executive_summary" in result
        assert "evidence_package" in result
        assert "poam" in result

    def test_export_all_files_exist(self, tmp_path):
        data = _sample_assessment()
        result = export_all(data, str(tmp_path), "nist")
        for key, path in result.items():
            assert Path(path).exists(), f"{key} file missing: {path}"


# ---------------------------------------------------------------------------
# Recommendations Builder
# ---------------------------------------------------------------------------

class TestBuildRecommendations:
    """Verify _build_recommendations generates appropriate suggestions."""

    def test_recommendations_for_non_compliant(self):
        data = _sample_assessment(include_not_satisfied=True)
        recs = _build_recommendations(data)
        assert any("Remediate" in r for r in recs)

    def test_recommendations_for_full_compliance(self):
        data = _sample_assessment(include_not_satisfied=False)
        data["coverage_pct"] = 100.0
        data["status_counts"] = {"satisfied": 2}
        recs = _build_recommendations(data)
        assert any("Maintain" in r for r in recs)

    def test_recommendations_for_low_coverage(self):
        data = _sample_assessment()
        data["coverage_pct"] = 40.0
        recs = _build_recommendations(data)
        assert any("below 80%" in r for r in recs)


# ---------------------------------------------------------------------------
# Status Normalization
# ---------------------------------------------------------------------------

class TestStatusNormMapping:
    """Verify _STATUS_NORM maps implementation statuses correctly."""

    def test_implemented_to_satisfied(self):
        assert _STATUS_NORM["implemented"] == "satisfied"

    def test_partially_implemented(self):
        assert _STATUS_NORM["partially_implemented"] == "partially_satisfied"

    def test_not_implemented(self):
        assert _STATUS_NORM["not_implemented"] == "not_satisfied"

    def test_planned_to_not_assessed(self):
        assert _STATUS_NORM["planned"] == "not_assessed"

    def test_alternative_to_risk_accepted(self):
        assert _STATUS_NORM["alternative"] == "risk_accepted"

    def test_not_applicable_passes_through(self):
        assert _STATUS_NORM["not_applicable"] == "not_applicable"
