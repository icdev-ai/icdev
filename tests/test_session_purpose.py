# [TEMPLATE: CUI // SP-CTI]
"""Tests for session purpose declaration (D-ORCH-5)."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Mock audit_log_event before importing modules to avoid writing to real DB
_audit_mock_sp = patch("tools.agent.session_purpose.audit_log_event", lambda **kw: None)
_audit_mock_sp.start()
_audit_mock_mb = patch("tools.agent.mailbox.audit_log_event", lambda **kw: None)
_audit_mock_mb.start()

from tools.agent.session_purpose import (
    abandon,
    complete,
    declare,
    get_active,
    get_prompt_injection,
    history,
    _ensure_table,
)


class TestSessionPurpose(unittest.TestCase):
    """Tests for session purpose declaration."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        _ensure_table(self.db_path)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_declare_purpose(self):
        """Test declaring a session purpose."""
        result = declare(
            purpose="Implement user authentication module",
            project_id="proj-123",
            db_path=self.db_path,
        )
        self.assertIn("id", result)
        self.assertEqual(result["purpose"], "Implement user authentication module")
        self.assertEqual(result["project_id"], "proj-123")
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["scope"], "session")
        self.assertTrue(result["id"].startswith("purpose-"))

    def test_declare_purpose_with_scope(self):
        """Test declaring purpose with workflow scope."""
        result = declare(
            purpose="Run security scan",
            scope="workflow",
            db_path=self.db_path,
        )
        self.assertEqual(result["scope"], "workflow")

    def test_declare_purpose_hash(self):
        """Test that purpose hash is computed."""
        result = declare(
            purpose="Test purpose",
            db_path=self.db_path,
        )
        self.assertIn("purpose_hash", result)
        self.assertEqual(len(result["purpose_hash"]), 16)

    def test_get_active_purpose(self):
        """Test retrieving active purpose."""
        declare(purpose="Active purpose", project_id="proj-1", db_path=self.db_path)
        active = get_active(project_id="proj-1", db_path=self.db_path)
        self.assertIsNotNone(active)
        self.assertEqual(active["purpose"], "Active purpose")
        self.assertEqual(active["status"], "active")

    def test_get_active_no_purpose(self):
        """Test no active purpose returns None."""
        active = get_active(db_path=self.db_path)
        self.assertIsNone(active)

    def test_get_active_latest(self):
        """Test that most recent active purpose is returned."""
        declare(purpose="First", db_path=self.db_path)
        declare(purpose="Second", db_path=self.db_path)
        active = get_active(db_path=self.db_path)
        self.assertEqual(active["purpose"], "Second")

    def test_complete_purpose(self):
        """Test completing a purpose."""
        result = declare(purpose="Task to complete", db_path=self.db_path)
        success = complete(result["id"], db_path=self.db_path)
        self.assertTrue(success)
        # Should no longer be active
        active = get_active(db_path=self.db_path)
        self.assertIsNone(active)

    def test_complete_nonexistent(self):
        """Test completing nonexistent purpose returns False."""
        success = complete("purpose-nonexistent", db_path=self.db_path)
        self.assertFalse(success)

    def test_abandon_purpose(self):
        """Test abandoning a purpose."""
        result = declare(purpose="Abandoned task", db_path=self.db_path)
        success = abandon(result["id"], db_path=self.db_path)
        self.assertTrue(success)
        active = get_active(db_path=self.db_path)
        self.assertIsNone(active)

    def test_prompt_injection_with_purpose(self):
        """Test prompt injection string generation."""
        declare(
            purpose="Build compliance dashboard",
            project_id="proj-1",
            db_path=self.db_path,
        )
        injection = get_prompt_injection(project_id="proj-1", db_path=self.db_path)
        self.assertIn("Build compliance dashboard", injection)
        self.assertIn("Session Purpose", injection)
        self.assertIn("NIST AU-3", injection)

    def test_prompt_injection_no_purpose(self):
        """Test prompt injection returns empty when no purpose."""
        injection = get_prompt_injection(db_path=self.db_path)
        self.assertEqual(injection, "")

    def test_history(self):
        """Test purpose history."""
        declare(purpose="First task", db_path=self.db_path)
        declare(purpose="Second task", db_path=self.db_path)
        h = history(db_path=self.db_path)
        self.assertEqual(len(h), 2)

    def test_history_with_project_filter(self):
        """Test history filtered by project."""
        declare(purpose="Proj A", project_id="proj-a", db_path=self.db_path)
        declare(purpose="Proj B", project_id="proj-b", db_path=self.db_path)
        h = history(project_id="proj-a", db_path=self.db_path)
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["purpose"], "Proj A")

    def test_history_limit(self):
        """Test history limit."""
        for i in range(5):
            declare(purpose=f"Task {i}", db_path=self.db_path)
        h = history(limit=3, db_path=self.db_path)
        self.assertEqual(len(h), 3)

    def test_metadata_storage(self):
        """Test metadata is stored as JSON."""
        result = declare(
            purpose="With metadata",
            metadata={"workflow": "icdev_sdlc", "issue": 42},
            db_path=self.db_path,
        )
        active = get_active(db_path=self.db_path)
        meta = json.loads(active["metadata"])
        self.assertEqual(meta["workflow"], "icdev_sdlc")
        self.assertEqual(meta["issue"], 42)

    def test_scope_validation(self):
        """Test scope check constraint."""
        conn = sqlite3.connect(self.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO session_purposes (id, purpose, purpose_hash, scope, created_at) VALUES (?, ?, ?, ?, ?)",
                ("test", "test", "hash", "invalid_scope", "2026-01-01"),
            )
        conn.close()

    def test_status_validation(self):
        """Test status check constraint."""
        conn = sqlite3.connect(self.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO session_purposes (id, purpose, purpose_hash, status, created_at) VALUES (?, ?, ?, ?, ?)",
                ("test", "test", "hash", "invalid_status", "2026-01-01"),
            )
        conn.close()

    def test_declared_by_default(self):
        """Test default declared_by is 'user'."""
        result = declare(purpose="Default user", db_path=self.db_path)
        self.assertEqual(result["declared_by"], "user")


class TestAsyncResultInjection(unittest.TestCase):
    """Tests for async result injection in agent mailbox (D-ORCH-7)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_mailbox (
                id TEXT PRIMARY KEY,
                from_agent_id TEXT NOT NULL,
                to_agent_id TEXT NOT NULL,
                message_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                priority INTEGER DEFAULT 5,
                in_reply_to TEXT,
                hmac_signature TEXT NOT NULL,
                read_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_send_async_result(self):
        """Test sending an async result."""
        from tools.agent.mailbox import send_async_result
        msg_id = send_async_result(
            from_agent_id="builder-agent",
            to_agent_id="orchestrator-agent",
            subject="Code generation complete",
            result_body='{"files_generated": 3}',
            db_path=self.db_path,
        )
        self.assertIsNotNone(msg_id)

    def test_async_result_high_priority(self):
        """Test async result gets priority 9 for injection."""
        from tools.agent.mailbox import send_async_result, receive, PRIORITY_INJECT_NEXT_TURN
        send_async_result(
            from_agent_id="security-agent",
            to_agent_id="orchestrator-agent",
            subject="SAST scan complete",
            result_body="no critical findings",
            db_path=self.db_path,
        )
        msgs = receive("orchestrator-agent", db_path=self.db_path)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["priority"], PRIORITY_INJECT_NEXT_TURN)
        self.assertEqual(msgs[0]["message_type"], "async_result")

    def test_collect_pending_injections(self):
        """Test collecting pending injections."""
        from tools.agent.mailbox import send_async_result, collect_pending_injections
        send_async_result(
            from_agent_id="builder-agent",
            to_agent_id="orchestrator-agent",
            subject="Build done",
            result_body="success",
            db_path=self.db_path,
        )
        send_async_result(
            from_agent_id="compliance-agent",
            to_agent_id="orchestrator-agent",
            subject="SSP generated",
            result_body="ssp.json",
            db_path=self.db_path,
        )
        results = collect_pending_injections("orchestrator-agent", db_path=self.db_path)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["from_agent"], "builder-agent")

    def test_collect_marks_read(self):
        """Test that collection marks messages as read."""
        from tools.agent.mailbox import send_async_result, collect_pending_injections
        send_async_result(
            from_agent_id="builder-agent",
            to_agent_id="orchestrator-agent",
            subject="Done",
            result_body="ok",
            db_path=self.db_path,
        )
        # First collection
        results = collect_pending_injections("orchestrator-agent", db_path=self.db_path)
        self.assertEqual(len(results), 1)
        # Second collection should be empty
        results2 = collect_pending_injections("orchestrator-agent", db_path=self.db_path)
        self.assertEqual(len(results2), 0)

    def test_collect_empty(self):
        """Test collection with no pending messages."""
        from tools.agent.mailbox import collect_pending_injections
        results = collect_pending_injections("orchestrator-agent", db_path=self.db_path)
        self.assertEqual(len(results), 0)

    def test_no_inject_normal_messages(self):
        """Test that normal messages are not collected as injections."""
        from tools.agent.mailbox import send, collect_pending_injections
        send(
            from_agent_id="builder-agent",
            to_agent_id="orchestrator-agent",
            message_type="notification",
            subject="FYI",
            body="info only",
            priority=5,
            db_path=self.db_path,
        )
        results = collect_pending_injections("orchestrator-agent", db_path=self.db_path)
        self.assertEqual(len(results), 0)


class TestTieredFileAccess(unittest.TestCase):
    """Tests for tiered file access control (D-ORCH-8)."""

    def test_matches_tier_env_file(self):
        """Test .env file matching."""
        from importlib.machinery import SourceFileLoader
        hook_path = str(Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "pre_tool_use.py")
        loader = SourceFileLoader("hook", hook_path)
        hook = loader.load_module()

        # Test basic pattern matching
        result = hook._matches_tier(".env", [".env", ".env.*"])
        self.assertTrue(result)

    def test_matches_tier_pem_file(self):
        """Test .pem file matching."""
        from importlib.machinery import SourceFileLoader
        hook_path = str(Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "pre_tool_use.py")
        loader = SourceFileLoader("hook", hook_path)
        hook = loader.load_module()

        result = hook._matches_tier("certs/server.pem", ["**/*.pem"])
        self.assertTrue(result)

    def test_matches_tier_no_match(self):
        """Test non-matching file."""
        from importlib.machinery import SourceFileLoader
        hook_path = str(Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "pre_tool_use.py")
        loader = SourceFileLoader("hook", hook_path)
        hook = loader.load_module()

        result = hook._matches_tier("src/main.py", [".env", "**/*.pem"])
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
