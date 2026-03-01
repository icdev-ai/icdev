#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for ATLAS Critique Phase — adversarial plan review (Phase 61, Feature 3).

Coverage: session creation, parallel critic dispatch (mocked), finding
classification by severity, consensus computation (GO/NOGO/CONDITIONAL),
revision loop, DB storage, finding types, config loading, CLI commands,
audit trail, edge cases.

25 tests total.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.agent.atlas_critique import (
    AtlasCritique,
    CritiqueSession,
    Finding,
    FINDING_TYPES,
    SEVERITY_LEVELS,
    SESSION_STATUSES,
    CONSENSUS_VALUES,
    _default_config,
    _try_parse_json,
    ensure_tables,
    load_config,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database with critique tables."""
    db_path = tmp_path / "test_critique.db"
    ensure_tables(db_path)
    return db_path


@pytest.fixture
def critique(tmp_db):
    """Create an AtlasCritique instance with a temp DB."""
    return AtlasCritique(db_path=tmp_db)


@pytest.fixture
def critique_disabled(tmp_db, tmp_path):
    """Create an AtlasCritique with critique disabled."""
    config_path = tmp_path / "disabled.yaml"
    config_path.write_text(
        "atlas_critique:\n  enabled: false\n  max_rounds: 3\n  critics: []\n",
        encoding="utf-8",
    )
    return AtlasCritique(db_path=tmp_db, config_path=config_path)


SAMPLE_PLAN = """
## Implementation Plan
1. Create REST API with Flask
2. Add user authentication via JWT
3. Store data in SQLite
4. Deploy to Kubernetes
"""


def _mock_findings(agent, types, severities):
    """Generate mock findings for a given agent."""
    findings = []
    for ft, sev in zip(types, severities):
        findings.append({
            "finding_type": ft,
            "severity": sev,
            "title": f"Mock {sev} {ft}",
            "description": f"Description for {ft} from {agent}",
            "evidence": "mock evidence",
            "suggested_fix": "fix suggestion",
            "nist_controls": ["AC-2", "AU-6"],
        })
    return findings


# ============================================================
# Test: Import & Constants
# ============================================================

class TestImportAndConstants:
    def test_import(self):
        """AtlasCritique can be imported."""
        assert AtlasCritique is not None

    def test_finding_types_defined(self):
        """All 8 finding types are defined."""
        assert len(FINDING_TYPES) == 8
        assert "security_vulnerability" in FINDING_TYPES
        assert "compliance_gap" in FINDING_TYPES
        assert "architecture_flaw" in FINDING_TYPES
        assert "performance_risk" in FINDING_TYPES
        assert "maintainability_concern" in FINDING_TYPES
        assert "testing_gap" in FINDING_TYPES
        assert "deployment_risk" in FINDING_TYPES
        assert "data_handling_issue" in FINDING_TYPES

    def test_severity_levels(self):
        """4 severity levels defined in correct order."""
        assert SEVERITY_LEVELS == ("critical", "high", "medium", "low")

    def test_session_statuses(self):
        """All session statuses defined."""
        assert "in_progress" in SESSION_STATUSES
        assert "go" in SESSION_STATUSES
        assert "nogo" in SESSION_STATUSES
        assert "conditional" in SESSION_STATUSES
        assert "revised" in SESSION_STATUSES
        assert "failed" in SESSION_STATUSES

    def test_consensus_values(self):
        """Consensus values match expected set."""
        assert CONSENSUS_VALUES == ("go", "nogo", "conditional")


# ============================================================
# Test: Configuration
# ============================================================

class TestConfiguration:
    def test_default_config_structure(self):
        """Default config has required keys."""
        cfg = _default_config()
        assert cfg["enabled"] is True
        assert cfg["max_rounds"] == 3
        assert len(cfg["critics"]) == 3
        assert "consensus_rules" in cfg
        assert "revision_prompt" in cfg

    def test_default_config_critics(self):
        """Default config has security, compliance, knowledge agents."""
        cfg = _default_config()
        agents = [c["agent"] for c in cfg["critics"]]
        assert "security-agent" in agents
        assert "compliance-agent" in agents
        assert "knowledge-agent" in agents

    def test_load_config_missing_file(self, tmp_path):
        """Loading config from missing file returns defaults."""
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg["enabled"] is True
        assert len(cfg["critics"]) == 3

    def test_load_config_from_yaml(self, tmp_path):
        """Loading config from valid YAML file."""
        yaml_path = tmp_path / "test_config.yaml"
        yaml_path.write_text(
            "atlas_critique:\n  enabled: true\n  max_rounds: 5\n"
            "  critics:\n"
            "    - agent: test-agent\n"
            "      role: tester\n"
            "      focus: [testing_gap]\n",
            encoding="utf-8",
        )
        cfg = load_config(yaml_path)
        assert cfg["max_rounds"] == 5
        assert len(cfg["critics"]) == 1
        assert cfg["critics"][0]["agent"] == "test-agent"


# ============================================================
# Test: Database Tables
# ============================================================

class TestDatabase:
    def test_tables_created(self, tmp_db):
        """Ensure critique tables exist after ensure_tables."""
        conn = sqlite3.connect(str(tmp_db))
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        assert "atlas_critique_sessions" in tables
        assert "atlas_critique_findings" in tables

    def test_tables_idempotent(self, tmp_db):
        """Running ensure_tables twice does not raise."""
        ensure_tables(tmp_db)
        ensure_tables(tmp_db)

    def test_session_insert(self, tmp_db):
        """Can insert a session row."""
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            """INSERT INTO atlas_critique_sessions
               (id, project_id, phase_input_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("sess-001", "proj-123", "abc123", "in_progress", "2026-01-01T00:00:00"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM atlas_critique_sessions WHERE id = ?",
            ("sess-001",),
        ).fetchone()
        conn.close()
        assert row is not None

    def test_finding_insert(self, tmp_db):
        """Can insert a finding row with valid types."""
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            """INSERT INTO atlas_critique_sessions
               (id, project_id, phase_input_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("sess-002", "proj-123", "abc123", "in_progress", "2026-01-01T00:00:00"),
        )
        conn.execute(
            """INSERT INTO atlas_critique_findings
               (id, session_id, critic_agent, round_number,
                finding_type, severity, title, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("find-001", "sess-002", "security-agent", 1,
             "security_vulnerability", "critical",
             "SQL Injection", "Unsanitized input",
             "2026-01-01T00:00:00"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM atlas_critique_findings WHERE id = ?",
            ("find-001",),
        ).fetchone()
        conn.close()
        assert row is not None

    def test_finding_invalid_type_rejected(self, tmp_db):
        """Inserting a finding with invalid finding_type raises IntegrityError."""
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            """INSERT INTO atlas_critique_sessions
               (id, project_id, phase_input_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("sess-003", "proj-123", "abc123", "in_progress", "2026-01-01T00:00:00"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO atlas_critique_findings
                   (id, session_id, critic_agent, round_number,
                    finding_type, severity, title, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("find-bad", "sess-003", "agent", 1,
                 "invalid_type", "critical",
                 "Bad", "Bad",
                 "2026-01-01T00:00:00"),
            )
        conn.close()

    def test_finding_invalid_severity_rejected(self, tmp_db):
        """Inserting a finding with invalid severity raises IntegrityError."""
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            """INSERT INTO atlas_critique_sessions
               (id, project_id, phase_input_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("sess-004", "proj-123", "abc123", "in_progress", "2026-01-01T00:00:00"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO atlas_critique_findings
                   (id, session_id, critic_agent, round_number,
                    finding_type, severity, title, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("find-bad2", "sess-004", "agent", 1,
                 "security_vulnerability", "urgent",
                 "Bad", "Bad",
                 "2026-01-01T00:00:00"),
            )
        conn.close()


# ============================================================
# Test: Consensus Computation
# ============================================================

class TestConsensus:
    def test_go_no_findings(self, critique):
        """Zero findings yields GO."""
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        assert critique._compute_consensus(counts) == "go"

    def test_go_only_medium_low(self, critique):
        """Only medium/low findings yields GO."""
        counts = {"critical": 0, "high": 0, "medium": 5, "low": 10}
        assert critique._compute_consensus(counts) == "go"

    def test_conditional_high_no_critical(self, critique):
        """High findings with no critical yields CONDITIONAL."""
        counts = {"critical": 0, "high": 3, "medium": 2, "low": 1}
        assert critique._compute_consensus(counts) == "conditional"

    def test_nogo_critical(self, critique):
        """Any critical finding yields NOGO."""
        counts = {"critical": 1, "high": 0, "medium": 0, "low": 0}
        assert critique._compute_consensus(counts) == "nogo"

    def test_nogo_critical_and_high(self, critique):
        """Critical + high findings yields NOGO."""
        counts = {"critical": 2, "high": 5, "medium": 3, "low": 1}
        assert critique._compute_consensus(counts) == "nogo"


# ============================================================
# Test: Severity Counting
# ============================================================

class TestSeverityCounting:
    def test_count_empty(self):
        """Empty findings list returns all zeros."""
        counts = AtlasCritique._count_severities([])
        assert counts == {"critical": 0, "high": 0, "medium": 0, "low": 0}

    def test_count_mixed(self):
        """Mixed severity findings counted correctly."""
        findings = [
            {"severity": "critical"},
            {"severity": "critical"},
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
            {"severity": "low"},
        ]
        counts = AtlasCritique._count_severities(findings)
        assert counts == {"critical": 2, "high": 1, "medium": 1, "low": 2}


# ============================================================
# Test: Run Critique (mocked agents)
# ============================================================

class TestRunCritique:
    @patch.object(AtlasCritique, "_dispatch_critics")
    @patch.object(AtlasCritique, "_get_llm_router", return_value=None)
    def test_go_with_no_findings(self, mock_router, mock_dispatch, critique):
        """Critique with no findings returns GO."""
        mock_dispatch.return_value = [
            {"agent": "security-agent", "role": "security_reviewer", "findings": []},
            {"agent": "compliance-agent", "role": "compliance_reviewer", "findings": []},
            {"agent": "knowledge-agent", "role": "patterns_reviewer", "findings": []},
        ]
        result = critique.run_critique("proj-123", SAMPLE_PLAN)
        assert result["status"] == "go"
        assert result["consensus"] == "go"
        assert result["total_findings"] == 0
        assert result["critical_count"] == 0

    @patch.object(AtlasCritique, "_dispatch_critics")
    @patch.object(AtlasCritique, "_get_llm_router", return_value=None)
    def test_nogo_with_critical_finding(self, mock_router, mock_dispatch, critique):
        """Critical finding triggers NOGO."""
        mock_dispatch.return_value = [
            {"agent": "security-agent", "role": "security_reviewer",
             "findings": _mock_findings(
                 "security-agent",
                 ["security_vulnerability"],
                 ["critical"],
             )},
            {"agent": "compliance-agent", "role": "compliance_reviewer", "findings": []},
            {"agent": "knowledge-agent", "role": "patterns_reviewer", "findings": []},
        ]
        result = critique.run_critique("proj-123", SAMPLE_PLAN)
        assert result["status"] == "nogo"
        assert result["consensus"] == "nogo"
        assert result["critical_count"] == 1

    @patch.object(AtlasCritique, "_dispatch_critics")
    @patch.object(AtlasCritique, "_request_revision")
    @patch.object(AtlasCritique, "_get_llm_router", return_value=None)
    def test_conditional_triggers_revision(self, mock_router, mock_revision, mock_dispatch, critique):
        """High findings trigger CONDITIONAL and revision loop."""
        # Round 1: high findings
        # Round 2: no findings (after revision)
        call_count = [0]

        def dispatch_side_effect(plan):
            call_count[0] += 1
            if call_count[0] == 1:
                return [
                    {"agent": "security-agent", "role": "security_reviewer",
                     "findings": _mock_findings(
                         "security-agent",
                         ["security_vulnerability"],
                         ["high"],
                     )},
                    {"agent": "compliance-agent", "role": "compliance_reviewer", "findings": []},
                    {"agent": "knowledge-agent", "role": "patterns_reviewer", "findings": []},
                ]
            else:
                return [
                    {"agent": "security-agent", "role": "security_reviewer", "findings": []},
                    {"agent": "compliance-agent", "role": "compliance_reviewer", "findings": []},
                    {"agent": "knowledge-agent", "role": "patterns_reviewer", "findings": []},
                ]

        mock_dispatch.side_effect = dispatch_side_effect
        mock_revision.return_value = {
            "revised_plan": "Revised plan text",
            "summary": "Addressed 1 high finding",
        }

        result = critique.run_critique("proj-123", SAMPLE_PLAN)
        assert result["status"] == "go"
        assert result["consensus"] == "go"
        assert result["rounds_completed"] == 2
        assert result["high_count"] == 1
        mock_revision.assert_called_once()

    @patch.object(AtlasCritique, "_dispatch_critics")
    @patch.object(AtlasCritique, "_request_revision")
    @patch.object(AtlasCritique, "_get_llm_router", return_value=None)
    def test_max_rounds_exhaustion(self, mock_router, mock_revision, mock_dispatch, critique):
        """Max rounds exhaustion results in CONDITIONAL status."""
        # Every round has high findings
        mock_dispatch.return_value = [
            {"agent": "security-agent", "role": "security_reviewer",
             "findings": _mock_findings(
                 "security-agent",
                 ["security_vulnerability"],
                 ["high"],
             )},
            {"agent": "compliance-agent", "role": "compliance_reviewer", "findings": []},
            {"agent": "knowledge-agent", "role": "patterns_reviewer", "findings": []},
        ]
        mock_revision.return_value = {
            "revised_plan": "Still has issues",
            "summary": "Tried to fix",
        }

        result = critique.run_critique("proj-123", SAMPLE_PLAN, max_rounds=2)
        assert result["status"] == "conditional"
        assert result["consensus"] == "conditional"
        assert result["rounds_completed"] == 2

    def test_disabled_critique_skipped(self, critique_disabled):
        """Critique returns skipped when disabled in config."""
        result = critique_disabled.run_critique("proj-123", SAMPLE_PLAN)
        assert result["status"] == "skipped"
        assert "enabled is false" in result["reason"]


# ============================================================
# Test: Session Status & History
# ============================================================

class TestSessionStatusAndHistory:
    @patch.object(AtlasCritique, "_dispatch_critics")
    @patch.object(AtlasCritique, "_get_llm_router", return_value=None)
    def test_get_session_status(self, mock_router, mock_dispatch, critique):
        """Can retrieve session status by ID."""
        mock_dispatch.return_value = [
            {"agent": "security-agent", "role": "security_reviewer", "findings": []},
            {"agent": "compliance-agent", "role": "compliance_reviewer", "findings": []},
            {"agent": "knowledge-agent", "role": "patterns_reviewer", "findings": []},
        ]
        result = critique.run_critique("proj-123", SAMPLE_PLAN)
        session_id = result["session_id"]

        status = critique.get_session_status(session_id)
        assert status["id"] == session_id
        assert status["status"] == "go"
        assert "findings" in status

    def test_get_session_status_not_found(self, critique):
        """Missing session returns error."""
        status = critique.get_session_status("nonexistent-id")
        assert "error" in status

    @patch.object(AtlasCritique, "_dispatch_critics")
    @patch.object(AtlasCritique, "_get_llm_router", return_value=None)
    def test_get_history(self, mock_router, mock_dispatch, critique):
        """Can retrieve project critique history."""
        mock_dispatch.return_value = [
            {"agent": "security-agent", "role": "security_reviewer", "findings": []},
            {"agent": "compliance-agent", "role": "compliance_reviewer", "findings": []},
            {"agent": "knowledge-agent", "role": "patterns_reviewer", "findings": []},
        ]
        # Run two critique sessions
        critique.run_critique("proj-123", SAMPLE_PLAN)
        critique.run_critique("proj-123", "Another plan text")

        history = critique.get_history("proj-123")
        assert history["project_id"] == "proj-123"
        assert history["total_sessions"] == 2
        assert len(history["sessions"]) == 2

    def test_get_history_empty(self, critique):
        """Empty history for unknown project."""
        history = critique.get_history("nonexistent-proj")
        assert history["total_sessions"] == 0
        assert len(history["sessions"]) == 0


# ============================================================
# Test: JSON Parsing
# ============================================================

class TestJsonParsing:
    def test_parse_direct_json(self):
        """Parse direct JSON array."""
        text = '[{"finding_type": "security_vulnerability", "severity": "high", "title": "XSS"}]'
        result = _try_parse_json(text)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_parse_json_code_block(self):
        """Parse JSON from markdown code block."""
        text = '```json\n[{"key": "value"}]\n```'
        result = _try_parse_json(text)
        assert isinstance(result, list)

    def test_parse_json_embedded(self):
        """Parse JSON embedded in text."""
        text = 'Here are findings: [{"finding_type": "testing_gap"}] end.'
        result = _try_parse_json(text)
        assert isinstance(result, list)

    def test_parse_invalid_json(self):
        """Invalid JSON returns None."""
        result = _try_parse_json("This is not JSON at all")
        assert result is None


# ============================================================
# Test: Findings Storage (DB persistence)
# ============================================================

class TestFindingsStorage:
    @patch.object(AtlasCritique, "_dispatch_critics")
    @patch.object(AtlasCritique, "_get_llm_router", return_value=None)
    def test_findings_persisted_in_db(self, mock_router, mock_dispatch, critique, tmp_db):
        """Findings are stored in the database (GO case, no revision loop)."""
        mock_dispatch.return_value = [
            {"agent": "security-agent", "role": "security_reviewer",
             "findings": _mock_findings(
                 "security-agent",
                 ["security_vulnerability", "deployment_risk"],
                 ["medium", "medium"],
             )},
            {"agent": "compliance-agent", "role": "compliance_reviewer",
             "findings": _mock_findings(
                 "compliance-agent",
                 ["compliance_gap"],
                 ["low"],
             )},
            {"agent": "knowledge-agent", "role": "patterns_reviewer", "findings": []},
        ]

        result = critique.run_critique("proj-123", SAMPLE_PLAN)
        session_id = result["session_id"]
        assert result["status"] == "go"  # No high/critical => GO, single round

        # Check DB directly
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        findings = conn.execute(
            "SELECT * FROM atlas_critique_findings WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        conn.close()

        assert len(findings) == 3
        agents = {f["critic_agent"] for f in findings}
        assert "security-agent" in agents
        assert "compliance-agent" in agents
