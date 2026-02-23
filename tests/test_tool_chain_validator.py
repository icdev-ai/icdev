#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for ToolChainValidator (Phase 45, Gap 2, D258)."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.security.tool_chain_validator import ToolChainValidator


@pytest.fixture
def chain_db(tmp_path):
    """Create a temporary DB with tool_chain_events table."""
    db_path = tmp_path / "test_chain.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE tool_chain_events (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            tool_sequence_json TEXT NOT NULL,
            rule_matched TEXT,
            severity TEXT DEFAULT 'info',
            action TEXT DEFAULT 'allow',
            context_json TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def default_config():
    """Default tool chain config matching owasp_agentic_config.yaml."""
    return {
        "enabled": True,
        "window_size": 10,
        "rules": [
            {
                "id": "TC-001",
                "name": "secrets_then_external",
                "description": "Reading secrets followed by external communication",
                "sequence_pattern": [
                    "*secret*|*credential*|*key_vault*|*password*",
                    "*send*|*post*|*external*|*gateway*|*webhook*",
                ],
                "max_gap": 3,
                "severity": "critical",
                "action": "block",
            },
            {
                "id": "TC-002",
                "name": "read_data_then_exfil",
                "description": "DB read followed by export or upload",
                "sequence_pattern": [
                    "*query*|*read*|*select*|*fetch*",
                    "*upload*|*export*|*transfer*|*email*",
                ],
                "max_gap": 5,
                "severity": "high",
                "action": "flag",
            },
            {
                "id": "TC-003",
                "name": "privilege_escalation_chain",
                "description": "User/role creation followed by deployment",
                "sequence_pattern": [
                    "*create_user*|*add_role*|*grant*|*elevate*",
                    "*deploy*|*terraform*|*k8s*|*kubectl*",
                ],
                "max_gap": 3,
                "severity": "high",
                "action": "flag",
            },
            {
                "id": "TC-004",
                "name": "rapid_tool_burst",
                "description": "More than 20 tool calls in 60 seconds",
                "burst_threshold": 20,
                "burst_window_seconds": 60,
                "severity": "medium",
                "action": "warn",
            },
        ],
    }


class TestToolChainValidator:
    """Tests for ToolChainValidator."""

    def test_no_violations_on_safe_calls(self, chain_db, default_config):
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        violations = v.record_tool_call("agent-1", "sess-1", "lint")
        assert violations == []

    def test_secrets_then_external_detected(self, chain_db, default_config):
        """TC-001: reading secret followed by external send."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-1", "sess-1", "read_secret_config")
        violations = v.record_tool_call("agent-1", "sess-1", "send_external_webhook")
        assert len(violations) == 1
        assert violations[0]["rule_id"] == "TC-001"
        assert violations[0]["severity"] == "critical"
        assert violations[0]["action"] == "block"

    def test_credential_then_post(self, chain_db, default_config):
        """TC-001: credential access then post."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-1", "sess-1", "get_credential_store")
        violations = v.record_tool_call("agent-1", "sess-1", "post_to_gateway")
        assert len(violations) == 1
        assert violations[0]["rule_id"] == "TC-001"

    def test_max_gap_respected(self, chain_db, default_config):
        """If gap exceeds max_gap, no violation."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-1", "sess-1", "read_secret")
        # Insert 4 safe calls (max_gap=3 means we look up to 3 positions ahead)
        for i in range(4):
            v.record_tool_call("agent-1", "sess-1", f"safe_call_{i}")
        violations = v.record_tool_call("agent-1", "sess-1", "send_external")
        # The gap between secret read and send is now > max_gap
        tc001 = [v for v in violations if v.get("rule_id") == "TC-001"]
        assert len(tc001) == 0

    def test_read_then_upload_detected(self, chain_db, default_config):
        """TC-002: DB read followed by upload."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-2", "sess-2", "query_database")
        violations = v.record_tool_call("agent-2", "sess-2", "upload_to_s3")
        assert len(violations) == 1
        assert violations[0]["rule_id"] == "TC-002"
        assert violations[0]["severity"] == "high"

    def test_select_then_export(self, chain_db, default_config):
        """TC-002: select followed by export."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-2", "sess-2", "select_all_records")
        violations = v.record_tool_call("agent-2", "sess-2", "export_csv_report")
        assert len(violations) == 1
        assert violations[0]["rule_id"] == "TC-002"

    def test_privilege_escalation_detected(self, chain_db, default_config):
        """TC-003: user creation then deploy."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-3", "sess-3", "create_user_admin")
        violations = v.record_tool_call("agent-3", "sess-3", "deploy_to_production")
        assert len(violations) == 1
        assert violations[0]["rule_id"] == "TC-003"

    def test_grant_then_terraform(self, chain_db, default_config):
        """TC-003: granting role then terraform apply."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-3", "sess-3", "grant_admin_role")
        violations = v.record_tool_call("agent-3", "sess-3", "terraform_apply")
        assert len(violations) == 1
        assert violations[0]["rule_id"] == "TC-003"

    def test_separate_sessions_isolated(self, chain_db, default_config):
        """Different sessions don't cross-contaminate."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-1", "sess-A", "read_secret")
        violations = v.record_tool_call("agent-1", "sess-B", "send_external")
        assert violations == []

    def test_separate_agents_isolated(self, chain_db, default_config):
        """Different agents don't cross-contaminate."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-A", "sess-1", "read_secret")
        violations = v.record_tool_call("agent-B", "sess-1", "send_external")
        assert violations == []

    def test_window_trimming(self, chain_db, default_config):
        """Window should not exceed configured size."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        for i in range(20):
            v.record_tool_call("agent-1", "sess-1", f"tool_{i}")
        window = v.check_session("agent-1", "sess-1")
        assert len(window) == 10  # window_size=10

    def test_get_rules(self, chain_db, default_config):
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        rules = v.get_rules()
        assert len(rules) == 4
        assert rules[0]["id"] == "TC-001"

    def test_pattern_matching_case_insensitive(self, chain_db, default_config):
        """Pattern matching should be case-insensitive."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-1", "sess-1", "READ_SECRET_CONFIG")
        violations = v.record_tool_call("agent-1", "sess-1", "SEND_EXTERNAL_API")
        assert len(violations) == 1

    def test_violation_logged_to_db(self, chain_db, default_config):
        """Violations should be logged to tool_chain_events table."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-1", "sess-1", "read_secret")
        v.record_tool_call("agent-1", "sess-1", "send_external")

        conn = sqlite3.connect(str(chain_db))
        row = conn.execute("SELECT COUNT(*) FROM tool_chain_events").fetchone()
        conn.close()
        assert row[0] > 0

    def test_gate_pass_no_violations(self, chain_db, default_config):
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        result = v.evaluate_gate()
        assert result["passed"] is True
        assert result["critical_violations"] == 0

    def test_gate_fail_critical_violation(self, chain_db, default_config):
        """Gate should fail with critical violations."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-1", "sess-1", "read_secret", project_id="proj-1")
        v.record_tool_call("agent-1", "sess-1", "send_external", project_id="proj-1")

        result = v.evaluate_gate(project_id="proj-1")
        assert result["passed"] is False
        assert result["critical_violations"] > 0

    def test_violation_structure(self, chain_db, default_config):
        """Violation dict should have required fields."""
        v = ToolChainValidator(db_path=chain_db, config=default_config)
        v.record_tool_call("agent-1", "sess-1", "read_secret")
        violations = v.record_tool_call("agent-1", "sess-1", "send_external")
        assert len(violations) == 1
        viol = violations[0]
        assert "rule_id" in viol
        assert "rule_name" in viol
        assert "severity" in viol
        assert "action" in viol
        assert "agent_id" in viol
        assert "session_id" in viol

    def test_empty_config_no_rules(self, chain_db):
        v = ToolChainValidator(db_path=chain_db, config={"rules": []})
        violations = v.record_tool_call("agent-1", "sess-1", "anything")
        assert violations == []

    def test_missing_db_gate_returns_warning(self, tmp_path, default_config):
        v = ToolChainValidator(db_path=tmp_path / "nonexistent.db", config=default_config)
        result = v.evaluate_gate()
        assert result["passed"] is True
        assert len(result["warnings"]) > 0

    def test_matches_pattern_group(self, chain_db, default_config):
        """Test the static pattern matcher."""
        assert ToolChainValidator._matches_pattern_group("read_secret_key", "*secret*|*credential*")
        assert ToolChainValidator._matches_pattern_group("get_credential", "*secret*|*credential*")
        assert not ToolChainValidator._matches_pattern_group("lint_code", "*secret*|*credential*")
