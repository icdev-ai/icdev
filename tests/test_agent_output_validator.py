#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for AgentOutputValidator (Phase 45, Gap 3, D259)."""

import json
import sqlite3
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.security.agent_output_validator import AgentOutputValidator


@pytest.fixture
def output_db(tmp_path):
    """Create a temporary DB with agent_output_violations table."""
    db_path = tmp_path / "test_output.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE agent_output_violations (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            agent_id TEXT NOT NULL,
            tool_name TEXT,
            violation_type TEXT NOT NULL,
            severity TEXT DEFAULT 'medium',
            details_json TEXT,
            output_hash TEXT,
            action_taken TEXT DEFAULT 'logged',
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def default_config():
    """Default output validation config."""
    return {
        "enabled": True,
        "max_output_size_bytes": 1048576,
        "classification_patterns": [
            {
                "pattern": "(?i)(SECRET|TOP SECRET|TS//SCI|NOFORN)",
                "severity": "critical",
                "action": "block",
                "description": "Classification marking above CUI in output",
            },
            {
                "pattern": "(?i)\\b(\\d{3}-\\d{2}-\\d{4})\\b",
                "severity": "high",
                "action": "flag",
                "description": "Potential SSN pattern in output",
            },
            {
                "pattern": "(?i)(password|api_key|secret_key|private_key)\\s*[=:]\\s*['\"][^'\"]{8,}",
                "severity": "critical",
                "action": "block",
                "description": "Credential/secret value in agent output",
            },
            {
                "pattern": "(?i)(BEGIN RSA PRIVATE KEY|BEGIN EC PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY)",
                "severity": "critical",
                "action": "block",
                "description": "Private key material in agent output",
            },
            {
                "pattern": "(?i)\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b",
                "severity": "medium",
                "action": "warn",
                "description": "Email address in agent output",
            },
        ],
    }


class TestAgentOutputValidator:
    """Tests for AgentOutputValidator."""

    def test_clean_output_passes(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("Hello, this is a normal response.", agent_id="test")
        assert result["passed"] is True
        assert result["action"] == "allow"
        assert result["violation_count"] == 0

    def test_secret_classification_blocked(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("This document is SECRET//NOFORN", agent_id="test")
        assert result["passed"] is False
        assert result["action"] == "block"

    def test_top_secret_blocked(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("TOP SECRET material here", agent_id="test")
        assert result["passed"] is False
        assert result["action"] == "block"

    def test_ts_sci_blocked(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("This is TS//SCI information", agent_id="test")
        assert result["passed"] is False

    def test_ssn_detected(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("User SSN: 123-45-6789", agent_id="test")
        assert result["passed"] is False
        assert result["action"] == "flag"
        ssn_violations = [v for v in result["violations"] if "SSN" in v.get("description", "")]
        assert len(ssn_violations) > 0

    def test_credential_in_output_blocked(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("password = 'supersecretpassword123'", agent_id="test")
        assert result["passed"] is False
        assert result["action"] == "block"

    def test_api_key_in_output_blocked(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("api_key: 'sk-abcdefghijklmnopqrst'", agent_id="test")
        assert result["passed"] is False
        assert result["action"] == "block"

    def test_private_key_blocked(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("-----BEGIN RSA PRIVATE KEY-----\nMIIE...", agent_id="test")
        assert result["passed"] is False
        assert result["action"] == "block"

    def test_email_warned(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("Contact admin@example.com for help", agent_id="test")
        assert result["passed"] is False
        assert result["action"] == "warn"

    def test_oversized_output_flagged(self, output_db):
        config = {"max_output_size_bytes": 100, "classification_patterns": []}
        v = AgentOutputValidator(db_path=output_db, config=config)
        result = v.validate_output("x" * 200, agent_id="test")
        assert result["passed"] is False
        size_violations = [v for v in result["violations"] if v["type"] == "oversized_response"]
        assert len(size_violations) == 1

    def test_output_hash_computed(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output("test content", agent_id="test")
        assert "output_hash" in result
        assert len(result["output_hash"]) == 64  # SHA-256

    def test_violations_logged_to_db(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        v.validate_output("This is SECRET data", agent_id="test-agent", project_id="proj-1")

        conn = sqlite3.connect(str(output_db))
        row = conn.execute("SELECT COUNT(*) FROM agent_output_violations").fetchone()
        conn.close()
        assert row[0] > 0

    def test_gate_pass_no_violations(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.evaluate_gate()
        assert result["passed"] is True

    def test_gate_fail_critical_violations(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        v.validate_output("This is TOP SECRET info", agent_id="agent-1", project_id="proj-1")

        result = v.evaluate_gate(project_id="proj-1")
        assert result["passed"] is False
        assert result["critical_violations"] > 0

    def test_check_classification_above_cui(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        findings = v.check_classification("This is SECRET data")
        assert len(findings) > 0
        assert findings[0]["type"] == "classification_leak"

    def test_check_classification_cui_ok(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        findings = v.check_classification("CUI // SP-CTI — this is fine")
        assert len(findings) == 0

    def test_check_sensitive_data_ssn(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        findings = v.check_sensitive_data("SSN: 123-45-6789")
        assert len(findings) > 0

    def test_check_sensitive_data_aws_key(self, output_db, default_config):
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        findings = v.check_sensitive_data("AKIAIOSFODNN7EXAMPLE")
        assert len(findings) > 0

    def test_multiple_violations_worst_action_wins(self, output_db, default_config):
        """When multiple violations exist, worst action wins."""
        v = AgentOutputValidator(db_path=output_db, config=default_config)
        result = v.validate_output(
            "Contact admin@example.com, password='secret12345678', this is SECRET",
            agent_id="test",
        )
        assert result["action"] == "block"  # block > warn

    def test_empty_config_passes_everything(self, output_db):
        v = AgentOutputValidator(db_path=output_db, config={"classification_patterns": []})
        result = v.validate_output("This is SECRET data", agent_id="test")
        assert result["passed"] is True

    def test_missing_db_gate_warning(self, tmp_path, default_config):
        v = AgentOutputValidator(db_path=tmp_path / "nonexistent.db", config=default_config)
        result = v.evaluate_gate()
        assert result["passed"] is True
        assert len(result["warnings"]) > 0
