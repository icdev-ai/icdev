#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Behavioral Red Teaming in ATLASRedTeamScanner (Phase 45, Gap 7, D262)."""

import json
import sqlite3
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.security.atlas_red_team import ATLASRedTeamScanner, BEHAVIORAL_TECHNIQUES


@pytest.fixture
def red_team_db(tmp_path):
    """Create temp DB with atlas_red_team_results table."""
    db_path = tmp_path / "test_redteam.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE atlas_red_team_results (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            technique TEXT NOT NULL,
            technique_name TEXT NOT NULL,
            passed INTEGER NOT NULL DEFAULT 0,
            tests_run INTEGER NOT NULL DEFAULT 0,
            tests_passed INTEGER NOT NULL DEFAULT 0,
            findings_json TEXT,
            scanned_at TEXT NOT NULL,
            classification TEXT DEFAULT 'CUI'
        )
    """)
    conn.commit()
    conn.close()
    return db_path


class TestBehavioralRedTeam:
    """Tests for behavioral red teaming (D262)."""

    def test_behavioral_techniques_defined(self):
        """BEHAVIORAL_TECHNIQUES dict should have 6 entries."""
        assert len(BEHAVIORAL_TECHNIQUES) == 6
        for tid in ["BRT-001", "BRT-002", "BRT-003", "BRT-004", "BRT-005", "BRT-006"]:
            assert tid in BEHAVIORAL_TECHNIQUES

    def test_run_all_behavioral_tests(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.run_behavioral_tests()
        assert "techniques_tested" in result
        assert result["techniques_tested"] == 6
        assert "results" in result
        assert len(result["results"]) == 6

    def test_run_single_behavioral_technique(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.run_behavioral_tests(technique="BRT-001")
        assert result["techniques_tested"] == 1
        assert result["results"][0]["technique"] == "BRT-001"

    def test_invalid_behavioral_technique(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.run_behavioral_tests(technique="BRT-999")
        assert "error" in result

    def test_brt001_goal_hijacking_structure(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.test_goal_hijacking()
        assert result["technique"] == "BRT-001"
        assert result["name"] == "Goal Hijacking"
        assert result["tests_run"] >= 3
        assert "passed" in result
        assert "findings" in result

    def test_brt002_authority_escalation_structure(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.test_authority_escalation()
        assert result["technique"] == "BRT-002"
        assert result["tests_run"] >= 3

    def test_brt003_hitl_fatigue_structure(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.test_hitl_fatigue()
        assert result["technique"] == "BRT-003"
        assert result["tests_run"] >= 3

    def test_brt004_multi_agent_collusion_structure(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.test_multi_agent_collusion()
        assert result["technique"] == "BRT-004"
        assert result["tests_run"] >= 3

    def test_brt005_tool_chain_exploitation_structure(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.test_tool_chain_exploitation()
        assert result["technique"] == "BRT-005"
        assert result["tests_run"] >= 3

    def test_brt006_memory_poisoning_via_output_structure(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.test_memory_poisoning_via_output()
        assert result["technique"] == "BRT-006"
        assert result["tests_run"] >= 3

    def test_behavioral_results_stored_in_db(self, red_team_db):
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        scanner.run_behavioral_tests()
        conn = sqlite3.connect(str(red_team_db))
        row = conn.execute("SELECT COUNT(*) FROM atlas_red_team_results").fetchone()
        conn.close()
        assert row[0] == 6

    def test_behavioral_finding_severity(self, red_team_db):
        """Findings should have severity field."""
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.run_behavioral_tests()
        for r in result["results"]:
            for finding in r.get("findings", []):
                assert "severity" in finding
                assert finding["severity"] in ("critical", "high", "medium", "low")

    def test_existing_atlas_tests_still_work(self, red_team_db):
        """Existing ATLAS techniques should still function."""
        scanner = ATLASRedTeamScanner(db_path=red_team_db)
        result = scanner.run_all_tests()
        assert "techniques_tested" in result
        assert result["techniques_tested"] == 6  # Original 6 ATLAS techniques
