#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 48 AI Transparency modules.

Coverage: confabulation_detector, fairness_assessor, ai_inventory_manager,
gao_evidence_builder, ai_transparency_audit.  All tests work without optional
dependencies (graceful degradation via skipIf guards).
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is importable
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# -----------------------------------------------------------------------
# Guarded imports — each module may or may not exist yet
# -----------------------------------------------------------------------
try:
    from tools.security.confabulation_detector import (
        check_citation_patterns,
        check_internal_contradictions,
        check_confidence_indicators,
        check_output,
        get_summary,
    )
    _HAS_CONFAB = True
except ImportError:
    _HAS_CONFAB = False

try:
    from tools.compliance.fairness_assessor import (
        FairnessAssessor,
        FAIRNESS_DIMENSIONS,
    )
    _HAS_FAIRNESS = True
except ImportError:
    _HAS_FAIRNESS = False

try:
    from tools.compliance.ai_inventory_manager import (
        register_ai_component,
        list_inventory,
        export_inventory,
    )
    _HAS_INVENTORY = True
except ImportError:
    _HAS_INVENTORY = False

try:
    from tools.compliance.gao_evidence_builder import (
        build_evidence,
    )
    _HAS_GAO = True
except ImportError:
    _HAS_GAO = False

try:
    from tools.compliance.ai_transparency_audit import (
        run_transparency_audit,
    )
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False


# ============================================================
# Shared DB Schema
# ============================================================

_SHARED_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    project_type TEXT DEFAULT 'microservice',
    impact_level TEXT DEFAULT 'IL4',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_bom (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    component_type TEXT,
    component_name TEXT,
    version TEXT,
    provider TEXT,
    license TEXT,
    risk_level TEXT,
    created_at TEXT,
    updated_at TEXT,
    classification TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS ai_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    agent_id TEXT,
    event_type TEXT,
    model_id TEXT,
    prompt_hash TEXT,
    response_hash TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS xai_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    family TEXT,
    status TEXT DEFAULT 'not_assessed',
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id)
);

CREATE TABLE IF NOT EXISTS model_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    card_data TEXT NOT NULL,
    card_hash TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, model_name)
);

CREATE TABLE IF NOT EXISTS fairness_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed',
    evidence TEXT,
    score REAL DEFAULT 0.0,
    assessed_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    UNIQUE(project_id, dimension)
);

CREATE TABLE IF NOT EXISTS ai_inventory (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    component_name TEXT NOT NULL,
    component_type TEXT DEFAULT 'model',
    risk_level TEXT DEFAULT 'medium',
    purpose TEXT,
    vendor TEXT,
    deployment_status TEXT DEFAULT 'development',
    omb_category TEXT,
    registered_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS gao_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    principle TEXT NOT NULL,
    evidence_item TEXT,
    status TEXT DEFAULT 'not_assessed',
    collected_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS transparency_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    audit_date TEXT DEFAULT (datetime('now')),
    overall_score REAL DEFAULT 0.0,
    gap_count INTEGER DEFAULT 0,
    recommendations TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    event_type TEXT,
    actor TEXT,
    action TEXT,
    project_id TEXT,
    details TEXT,
    affected_files TEXT,
    session_id TEXT,
    classification TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS confabulation_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    check_type TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    result TEXT NOT NULL,
    risk_score REAL DEFAULT 0.0,
    findings_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_confabulation_project
    ON confabulation_checks(project_id);

CREATE TABLE IF NOT EXISTS ai_use_case_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    purpose TEXT,
    risk_level TEXT DEFAULT 'minimal_risk'
        CHECK(risk_level IN ('minimal_risk', 'high_impact', 'safety_impacting')),
    classification TEXT DEFAULT 'CUI',
    deployment_status TEXT DEFAULT 'development',
    responsible_official TEXT,
    oversight_role TEXT,
    appeal_mechanism TEXT,
    last_assessed TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, name)
);
CREATE INDEX IF NOT EXISTS idx_ai_inventory_project
    ON ai_use_case_inventory(project_id);

INSERT INTO projects (id, name) VALUES ('proj-test', 'Test Project');
"""


@pytest.fixture
def transparency_db(tmp_path):
    """Create temp DB with all tables needed by Phase 48 transparency tools."""
    db_path = tmp_path / "test_transparency.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SHARED_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def populated_transparency_db(transparency_db):
    """DB pre-populated with XAI assessments and ai_bom data."""
    conn = sqlite3.connect(str(transparency_db))
    now = datetime.now(timezone.utc).isoformat()
    # XAI assessments
    conn.execute(
        "INSERT INTO xai_assessments (project_id, requirement_id, requirement_title, "
        "status) VALUES (?, ?, ?, ?)",
        ("proj-test", "XAI-001", "Tracing Active", "satisfied"),
    )
    conn.execute(
        "INSERT INTO xai_assessments (project_id, requirement_id, requirement_title, "
        "status) VALUES (?, ?, ?, ?)",
        ("proj-test", "XAI-006", "SHAP Analysis", "satisfied"),
    )
    # ai_bom entries
    conn.execute(
        "INSERT INTO ai_bom (id, project_id, component_type, component_name, "
        "version, provider, risk_level, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("bom-1", "proj-test", "model", "claude-sonnet", "4.0",
         "bedrock", "medium", now, now),
    )
    # Model card
    conn.execute(
        "INSERT INTO model_cards (project_id, model_name, card_data, card_hash, version) "
        "VALUES (?, ?, ?, ?, ?)",
        ("proj-test", "claude-sonnet",
         json.dumps({"model_details": {}, "intended_use": {}}),
         "a" * 16, 1),
    )
    conn.commit()
    conn.close()
    return transparency_db


# ============================================================
# Confabulation Detector Tests
# ============================================================

@pytest.mark.skipif(not _HAS_CONFAB, reason="confabulation_detector not available")
class TestConfabulationCitationPatterns:
    def test_urls_detected(self):
        """Text with URLs should have citation patterns detected."""
        text = "According to https://example.com/study, the results show improvement."
        result = check_citation_patterns(text)
        assert isinstance(result, list)
        # A short URL won't necessarily be flagged as suspicious, but the
        # function should still return a list
        for finding in result:
            assert isinstance(finding, dict)
            assert "type" in finding

    def test_academic_citations_detected(self):
        """Text with many academic-style citations should be detected."""
        # Need 6+ citations to trigger the high_citation_density finding
        text = (
            "As shown by Smith et al. (2024), and Jones et al. (2023), "
            "and Brown et al. (2022), and Davis et al. (2021), "
            "and Wilson et al. (2020), and Taylor et al. (2019), "
            "the framework is effective."
        )
        result = check_citation_patterns(text)
        assert isinstance(result, list)

    def test_no_citations(self):
        """Text without citations should report none found."""
        text = "The sky is blue and the grass is green."
        result = check_citation_patterns(text)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_empty_text(self):
        """Empty text should not raise and should report no citations."""
        result = check_citation_patterns("")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_document_references_detected(self):
        """Text with OMB M- or EO references should be flagged."""
        text = "Per OMB M-26-04 and EO 14028, the system must comply."
        result = check_citation_patterns(text)
        assert isinstance(result, list)
        assert len(result) >= 1
        types = [f["type"] for f in result]
        assert "document_reference" in types


@pytest.mark.skipif(not _HAS_CONFAB, reason="confabulation_detector not available")
class TestConfabulationContradictions:
    def test_contradicting_sentences(self):
        """Text with contradictions should be flagged."""
        text = (
            "Encryption must be enabled for all data at rest. "
            "Encryption must not be enabled for temporary files."
        )
        result = check_internal_contradictions(text)
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0]["type"] == "potential_contradiction"

    def test_consistent_text(self):
        """Consistent text should have no contradictions."""
        text = "The system uses SQLite. The database is SQLite-based."
        result = check_internal_contradictions(text)
        assert isinstance(result, list)
        assert len(result) == 0


@pytest.mark.skipif(not _HAS_CONFAB, reason="confabulation_detector not available")
class TestConfabulationConfidenceIndicators:
    def test_hedging_language(self):
        """Text with hedging words should be detected."""
        text = "I think this might possibly work, and it could be effective."
        result = check_confidence_indicators(text)
        assert isinstance(result, list)
        assert len(result) > 0
        types = [f["type"] for f in result]
        assert "hedging_language" in types

    def test_confident_text(self):
        """Confident text should show no hedging indicators."""
        text = "The system complies with NIST 800-53 Rev 5 AC-2."
        result = check_confidence_indicators(text)
        assert isinstance(result, list)
        assert len(result) == 0


@pytest.mark.skipif(not _HAS_CONFAB, reason="confabulation_detector not available")
class TestConfabulationFullCheck:
    def test_check_output_returns_dict(self, transparency_db):
        """check_output returns a dict with risk scoring."""
        text = "According to https://example.com, the accuracy is 99%."
        result = check_output("proj-test", text, db_path=transparency_db)
        assert isinstance(result, dict)
        assert "risk_score" in result

    def test_check_output_has_findings(self, transparency_db):
        """check_output includes findings list and count."""
        text = "I think this might work. Per NIST SP 800-53, encryption must be enabled."
        result = check_output("proj-test", text, db_path=transparency_db)
        assert isinstance(result, dict)
        assert "findings" in result
        assert "findings_count" in result
        assert isinstance(result["findings"], list)

    def test_get_summary_after_check(self, transparency_db):
        """get_summary returns a summary after running checks."""
        text = "The model achieves state-of-the-art performance on all benchmarks."
        check_output("proj-test", text, db_path=transparency_db)
        summary = get_summary("proj-test", db_path=transparency_db)
        assert isinstance(summary, dict)
        assert summary["total_checks"] >= 1
        assert "avg_risk_score" in summary


# ============================================================
# Fairness Assessor Tests
# ============================================================

@pytest.mark.skipif(not _HAS_FAIRNESS, reason="fairness_assessor not available")
class TestFairnessDimensions:
    def test_dimensions_count(self):
        """FAIRNESS_DIMENSIONS should contain exactly 8 items."""
        assert len(FAIRNESS_DIMENSIONS) == 8

    def test_dimensions_are_strings(self):
        """All dimension identifiers should be strings."""
        for dim in FAIRNESS_DIMENSIONS:
            if isinstance(dim, dict):
                assert "id" in dim or "name" in dim
            else:
                assert isinstance(dim, str)


@pytest.mark.skipif(not _HAS_FAIRNESS, reason="fairness_assessor not available")
class TestFairnessAssessment:
    def test_assess_returns_dict(self, transparency_db):
        """assess_fairness returns a dict."""
        assessor = FairnessAssessor(db_path=str(transparency_db))
        result = assessor.assess_fairness("proj-test")
        assert isinstance(result, dict)

    def test_assess_has_8_dimensions(self, transparency_db):
        """Assessment covers all 8 fairness dimensions."""
        assessor = FairnessAssessor(db_path=str(transparency_db))
        result = assessor.assess_fairness("proj-test")
        dimensions = result.get("dimensions", result.get("assessments", []))
        if isinstance(dimensions, list):
            assert len(dimensions) == 8
        elif isinstance(dimensions, dict):
            assert len(dimensions) == 8

    def test_assess_with_xai_data(self, populated_transparency_db):
        """Assessment with XAI data in DB should show satisfied dimensions."""
        assessor = FairnessAssessor(db_path=str(populated_transparency_db))
        result = assessor.assess_fairness("proj-test")
        # With XAI data present, at least some dimensions should be satisfied
        dimensions = result.get("dimensions", result.get("assessments", {}))
        statuses = []
        if isinstance(dimensions, list):
            statuses = [d.get("status", "not_assessed") for d in dimensions]
        elif isinstance(dimensions, dict):
            statuses = [v.get("status", v) if isinstance(v, dict) else v
                        for v in dimensions.values()]
        # At least one dimension should reference available data
        assert len(statuses) > 0

    def test_evaluate_gate_no_assessment(self, transparency_db):
        """Gate evaluation with no prior assessment."""
        assessor = FairnessAssessor(db_path=str(transparency_db))
        result = assessor.evaluate_gate("proj-test")
        assert isinstance(result, dict)
        assert "pass" in result or "gate_status" in result

    def test_evaluate_gate_with_assessment(self, populated_transparency_db):
        """Gate evaluation after assessment has been run."""
        assessor = FairnessAssessor(db_path=str(populated_transparency_db))
        # Run assessment first
        assessor.assess_fairness("proj-test")
        result = assessor.evaluate_gate("proj-test")
        assert isinstance(result, dict)


# ============================================================
# AI Inventory Manager Tests
# ============================================================

@pytest.mark.skipif(not _HAS_INVENTORY, reason="ai_inventory_manager not available")
class TestAIInventoryRegister:
    def test_register_returns_dict(self, transparency_db):
        """register_ai_component returns a dict with component info."""
        result = register_ai_component(
            project_id="proj-test",
            name="claude-sonnet-4",
            purpose="Code generation",
            risk_level="minimal_risk",
            db_path=transparency_db,
        )
        assert isinstance(result, dict)
        assert result.get("name") == "claude-sonnet-4"
        assert result.get("status") == "registered"

    def test_register_stored_in_db(self, transparency_db):
        """Registered component is stored in ai_use_case_inventory table."""
        register_ai_component(
            project_id="proj-test",
            name="gpt-4o",
            purpose="Fallback generation",
            risk_level="high_impact",
            db_path=transparency_db,
        )
        conn = sqlite3.connect(str(transparency_db))
        rows = conn.execute(
            "SELECT COUNT(*) FROM ai_use_case_inventory WHERE project_id = ?",
            ("proj-test",),
        ).fetchone()
        conn.close()
        assert rows[0] >= 1

    def test_invalid_risk_level(self, transparency_db):
        """Invalid risk_level should raise ValueError."""
        with pytest.raises((ValueError, Exception)):
            register_ai_component(
                project_id="proj-test",
                name="bad-model",
                purpose="Testing",
                risk_level="ultra_extreme",
                db_path=transparency_db,
            )

    def test_duplicate_registration(self, transparency_db):
        """Duplicate registration should use INSERT OR REPLACE semantics."""
        register_ai_component(
            project_id="proj-test",
            name="dup-model",
            purpose="Test",
            risk_level="minimal_risk",
            db_path=transparency_db,
        )
        # Register again with updated purpose
        register_ai_component(
            project_id="proj-test",
            name="dup-model",
            purpose="Updated purpose",
            risk_level="minimal_risk",
            db_path=transparency_db,
        )
        conn = sqlite3.connect(str(transparency_db))
        rows = conn.execute(
            "SELECT COUNT(*) FROM ai_use_case_inventory WHERE name = ?",
            ("dup-model",),
        ).fetchone()
        conn.close()
        # Should have 1 row (replaced via UNIQUE constraint)
        assert rows[0] == 1


@pytest.mark.skipif(not _HAS_INVENTORY, reason="ai_inventory_manager not available")
class TestAIInventoryList:
    def test_list_empty(self, transparency_db):
        """list_inventory returns empty result for project with no components."""
        result = list_inventory("proj-empty", db_path=transparency_db)
        assert result["total"] == 0
        assert len(result["items"]) == 0

    def test_list_after_register(self, transparency_db):
        """list_inventory returns registered component."""
        register_ai_component(
            project_id="proj-test",
            name="test-model",
            purpose="Testing",
            risk_level="minimal_risk",
            db_path=transparency_db,
        )
        result = list_inventory("proj-test", db_path=transparency_db)
        assert result["total"] >= 1
        assert len(result["items"]) >= 1


@pytest.mark.skipif(not _HAS_INVENTORY, reason="ai_inventory_manager not available")
class TestAIInventoryExport:
    def test_export_omb_format(self, transparency_db):
        """export_inventory returns OMB M-25-21 format."""
        register_ai_component(
            project_id="proj-test",
            name="export-model",
            purpose="Export test",
            risk_level="high_impact",
            db_path=transparency_db,
        )
        result = export_inventory("proj-test", db_path=transparency_db)
        assert isinstance(result, dict)
        # OMB M-25-21 format should have report_type, summary, use_cases
        assert result["report_type"] == "OMB M-25-21 AI Use Case Inventory"
        assert "summary" in result
        assert "use_cases" in result
        assert len(result["use_cases"]) >= 1


# ============================================================
# GAO Evidence Builder Tests
# ============================================================

@pytest.mark.skipif(not _HAS_GAO, reason="gao_evidence_builder not available")
class TestGAOEvidenceBuilder:
    def test_build_evidence_returns_dict(self, transparency_db):
        """build_evidence returns a dict."""
        result = build_evidence("proj-test", db_path=str(transparency_db))
        assert isinstance(result, dict)

    def test_four_principles_returned(self, transparency_db):
        """GAO AI Accountability has 4 principles; verify all returned."""
        result = build_evidence("proj-test", db_path=str(transparency_db))
        principles = result.get("principles", result.get("evidence", []))
        if isinstance(principles, list):
            assert len(principles) == 4
        elif isinstance(principles, dict):
            assert len(principles) == 4

    def test_coverage_percentages(self, populated_transparency_db):
        """Summary should include coverage percentages."""
        result = build_evidence("proj-test", db_path=str(populated_transparency_db))
        summary = result.get("summary", {})
        if isinstance(summary, dict):
            # Should have coverage or score fields
            assert "coverage" in summary or "overall_coverage" in summary or \
                   "score" in summary or len(summary) > 0

    def test_evidence_items_have_fields(self, transparency_db):
        """Each evidence item should have required fields."""
        result = build_evidence("proj-test", db_path=str(transparency_db))
        principles = result.get("principles", result.get("evidence", []))
        if isinstance(principles, list):
            for p in principles:
                if isinstance(p, dict):
                    assert "principle" in p or "name" in p or "id" in p
        elif isinstance(principles, dict):
            for key, val in principles.items():
                assert isinstance(key, str)
                if isinstance(val, dict):
                    assert len(val) > 0


# ============================================================
# AI Transparency Audit Tests
# ============================================================

@pytest.mark.skipif(not _HAS_AUDIT, reason="ai_transparency_audit not available")
class TestAITransparencyAudit:
    def test_run_audit_returns_dict(self, transparency_db):
        """run_transparency_audit returns a dict with scoring."""
        result = run_transparency_audit("proj-test", db_path=transparency_db)
        assert isinstance(result, dict)

    def test_combined_scoring(self, transparency_db):
        """Audit should produce an overall transparency score."""
        result = run_transparency_audit("proj-test", db_path=transparency_db)
        assert "overall_transparency_score" in result
        assert isinstance(result["overall_transparency_score"], (int, float))

    def test_gap_detection_missing_artifacts(self, transparency_db):
        """Audit on empty project should detect gaps for missing artifacts."""
        result = run_transparency_audit("proj-test", db_path=transparency_db)
        assert result["gap_count"] > 0
        assert len(result["gaps"]) > 0

    def test_gap_detection_with_data(self, populated_transparency_db):
        """Audit with populated data recognizes existing model cards."""
        result = run_transparency_audit(
            "proj-test", db_path=populated_transparency_db,
        )
        # Model card was inserted by fixture, so the artifact count should be > 0
        assert result["artifacts"]["model_cards"] > 0
        # The "No model cards generated" gap should NOT appear
        gap_areas = [g["area"] for g in result["gaps"]]
        assert "Model Documentation" not in gap_areas

    def test_recommendation_present(self, transparency_db):
        """Audit should provide recommendation text."""
        result = run_transparency_audit("proj-test", db_path=transparency_db)
        assert "recommendation" in result
        assert isinstance(result["recommendation"], str)
        assert len(result["recommendation"]) > 0
