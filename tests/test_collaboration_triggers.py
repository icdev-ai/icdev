# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for collaboration trigger wiring in TeamOrchestrator.

Covers:
- _infer_topic: skill_id/description -> authority topic heuristic
- _auto_review: ORANGE-tier reviewer_pattern trigger after subtask completion
- _process_collaboration_mailbox: veto/escalation message routing into workflow
- execute_workflow integration: auto-review blocks cause downstream blocking
"""

import json
import sqlite3
from unittest.mock import patch

import pytest

from tools.agent.team_orchestrator import Subtask, TeamOrchestrator, Workflow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def collab_db(tmp_path):
    """Temporary database with all tables needed for collaboration tests."""
    db_path = tmp_path / "collab.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_workflows (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            project_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_by TEXT DEFAULT 'orchestrator-agent',
            aggregated_result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_subtasks (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            description TEXT DEFAULT '',
            depends_on TEXT DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            input_data TEXT,
            output_data TEXT,
            error_message TEXT DEFAULT '',
            attempt_count INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (workflow_id) REFERENCES agent_workflows(id)
        );

        CREATE TABLE IF NOT EXISTS agent_mailbox (
            id TEXT PRIMARY KEY,
            from_agent_id TEXT NOT NULL,
            to_agent_id TEXT NOT NULL,
            message_type TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT,
            priority INTEGER DEFAULT 5,
            in_reply_to TEXT,
            hmac_signature TEXT NOT NULL,
            read_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_vetoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            authority_agent_id TEXT NOT NULL,
            vetoed_agent_id TEXT NOT NULL,
            task_id TEXT,
            workflow_id TEXT,
            project_id TEXT,
            topic TEXT,
            veto_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            evidence TEXT,
            status TEXT DEFAULT 'active',
            overridden_by TEXT,
            override_justification TEXT,
            override_approval_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            project_id TEXT,
            details TEXT,
            classification TEXT DEFAULT 'CUI',
            session_id TEXT,
            source_ip TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def orchestrator(collab_db):
    """TeamOrchestrator instance pointed at the temporary database."""
    return TeamOrchestrator(max_workers=2, db_path=collab_db)


# ---------------------------------------------------------------------------
# TestInferTopic
# ---------------------------------------------------------------------------


class TestInferTopic:
    """_infer_topic: maps skill_id and description to authority topics."""

    def test_infers_code_generation(self):
        st = Subtask(id="s1", agent_id="a", skill_id="generate_code", description="Build API layer")
        assert TeamOrchestrator._infer_topic(st) == "code_generation"

    def test_infers_infrastructure(self):
        st = Subtask(id="s1", agent_id="a", skill_id="provision", description="Deploy terraform infra")
        assert TeamOrchestrator._infer_topic(st) == "infrastructure_change"

    def test_infers_secret_management(self):
        st = Subtask(id="s1", agent_id="a", skill_id="rotate_secret", description="Renew vault token")
        assert TeamOrchestrator._infer_topic(st) == "secret_management"

    def test_infers_container(self):
        st = Subtask(id="s1", agent_id="a", skill_id="build_image", description="Docker kubernetes setup")
        assert TeamOrchestrator._infer_topic(st) == "container_configuration"

    def test_returns_none_for_unknown(self):
        st = Subtask(id="s1", agent_id="a", skill_id="foo", description="bar baz qux")
        assert TeamOrchestrator._infer_topic(st) is None

    def test_description_fallback(self):
        st = Subtask(id="s1", agent_id="a", skill_id="generic", description="Run a schema migration")
        assert TeamOrchestrator._infer_topic(st) == "schema_change"


# ---------------------------------------------------------------------------
# TestAutoReview
# ---------------------------------------------------------------------------


class TestAutoReview:
    """_auto_review: ORANGE-tier reviewer_pattern trigger."""

    @patch("tools.agent.team_orchestrator._audit_log")
    @patch("tools.agent.collaboration.reviewer_pattern")
    @patch("tools.agent.authority.get_required_reviewers")
    def test_approved_subtask_unchanged(
        self, mock_get_reviewers, mock_reviewer, mock_audit, orchestrator
    ):
        """When reviewer approves, subtask stays completed."""
        mock_get_reviewers.return_value = [
            {"agent_id": "security-agent", "veto_type": "hard"}
        ]
        mock_reviewer.return_value = {"approved": True, "rounds": 1}

        wf = Workflow(id="wf-1", name="Test", project_id="proj-1")
        st = Subtask(
            id="s1",
            agent_id="builder-agent",
            skill_id="generate_code",
            status="completed",
            output_data={"file": "main.py"},
        )
        result = orchestrator._auto_review(st, wf)

        assert result.status == "completed"
        mock_reviewer.assert_called_once()

    @patch("tools.agent.team_orchestrator._audit_log")
    @patch("tools.agent.collaboration.reviewer_pattern")
    @patch("tools.agent.authority.get_required_reviewers")
    def test_rejected_subtask_blocked(
        self, mock_get_reviewers, mock_reviewer, mock_audit, orchestrator
    ):
        """When reviewer rejects, subtask is blocked."""
        mock_get_reviewers.return_value = [
            {"agent_id": "security-agent", "veto_type": "hard"}
        ]
        mock_reviewer.return_value = {
            "approved": False,
            "feedback_history": [{"round": 1, "decision": "reject"}],
        }

        wf = Workflow(id="wf-1", name="Test", project_id="proj-1")
        st = Subtask(
            id="s1",
            agent_id="builder-agent",
            skill_id="generate_code",
            status="completed",
            output_data={"file": "main.py"},
        )
        result = orchestrator._auto_review(st, wf)

        assert result.status == "blocked"
        assert "Auto-review FAILED" in result.error_message
        mock_reviewer.assert_called_once()

    @patch("tools.agent.team_orchestrator._audit_log")
    @patch("tools.agent.authority.get_required_reviewers")
    def test_no_reviewers_skips_review(self, mock_get_reviewers, mock_audit, orchestrator):
        """When no reviewers exist for the topic, subtask stays completed."""
        mock_get_reviewers.return_value = []

        wf = Workflow(id="wf-1", name="Test", project_id="proj-1")
        st = Subtask(
            id="s1",
            agent_id="builder-agent",
            skill_id="generate_code",
            status="completed",
            output_data={"file": "main.py"},
        )
        result = orchestrator._auto_review(st, wf)

        assert result.status == "completed"

    def test_no_topic_skips_review(self, orchestrator):
        """When no topic inferred, subtask stays completed without calling LLM."""
        wf = Workflow(id="wf-1", name="Test", project_id="proj-1")
        st = Subtask(id="s1", agent_id="a", skill_id="foo", status="completed", output_data={})
        result = orchestrator._auto_review(st, wf)
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# TestProcessCollaborationMailbox
# ---------------------------------------------------------------------------


class TestProcessCollaborationMailbox:
    """_process_collaboration_mailbox: veto/escalation message routing."""

    def _send_mailbox_msg(self, db_path, to_agent, msg_type, body, from_agent="security-agent"):
        import uuid
        import hmac
        import hashlib

        mid = str(uuid.uuid4())
        msg_bytes = f"{from_agent}|{to_agent}|subject|{body}".encode("utf-8")
        sig = hmac.new("icdev-default-hmac-key".encode(), msg_bytes, hashlib.sha256).hexdigest()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO agent_mailbox
               (id, from_agent_id, to_agent_id, message_type, subject, body, hmac_signature)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mid, from_agent, to_agent, msg_type, "subject", body, sig),
        )
        conn.commit()
        conn.close()
        return mid

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_veto_message_blocks_subtask(self, mock_audit, orchestrator, collab_db):
        """A veto mailbox message marks the target subtask blocked."""
        wf = Workflow(id="wf-veto-1", name="Veto test", project_id="proj-v")
        wf.subtasks["s1"] = Subtask(id="s1", agent_id="a", skill_id="sk", status="completed")
        wf.subtasks["s2"] = Subtask(id="s2", agent_id="a", skill_id="sk", depends_on=["s1"])
        orchestrator._persist_workflow(wf)

        body = json.dumps({"subtask_id": "s1", "reason": "Security violation"})
        self._send_mailbox_msg(collab_db, "orchestrator-agent", "veto", body)

        orchestrator._process_collaboration_mailbox(wf)

        assert wf.subtasks["s1"].status == "blocked"
        assert "Security violation" in wf.subtasks["s1"].error_message
        assert wf.subtasks["s2"].status == "blocked"

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_escalation_message_blocks_subtask(self, mock_audit, orchestrator, collab_db):
        """An escalation mailbox message marks the target subtask blocked."""
        wf = Workflow(id="wf-esc-1", name="Esc test", project_id="proj-e")
        wf.subtasks["s1"] = Subtask(id="s1", agent_id="a", skill_id="sk", status="completed")
        wf.subtasks["s2"] = Subtask(id="s2", agent_id="a", skill_id="sk", depends_on=["s1"])
        orchestrator._persist_workflow(wf)

        body = json.dumps({"subtask_id": "s1", "reason": "Need human approval"})
        self._send_mailbox_msg(collab_db, "orchestrator-agent", "escalation", body)

        orchestrator._process_collaboration_mailbox(wf)

        assert wf.subtasks["s1"].status == "blocked"
        assert "human review" in wf.subtasks["s1"].error_message
        assert wf.subtasks["s2"].status == "blocked"

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_irrelevant_message_ignored(self, mock_audit, orchestrator, collab_db):
        """Mailbox messages targeting unknown subtasks are ignored."""
        wf = Workflow(id="wf-ign-1", name="Ignore test", project_id="proj-i")
        wf.subtasks["s1"] = Subtask(id="s1", agent_id="a", skill_id="sk", status="completed")
        orchestrator._persist_workflow(wf)

        body = json.dumps({"subtask_id": "nonexistent", "reason": " whatever"})
        self._send_mailbox_msg(collab_db, "orchestrator-agent", "veto", body)

        orchestrator._process_collaboration_mailbox(wf)

        assert wf.subtasks["s1"].status == "completed"

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_messages_marked_read(self, mock_audit, orchestrator, collab_db):
        """Processed mailbox messages are marked as read."""
        wf = Workflow(id="wf-read-1", name="Read test", project_id="proj-r")
        wf.subtasks["s1"] = Subtask(id="s1", agent_id="a", skill_id="sk", status="completed")
        orchestrator._persist_workflow(wf)

        body = json.dumps({"subtask_id": "s1", "reason": "test"})
        mid = self._send_mailbox_msg(collab_db, "orchestrator-agent", "veto", body)

        orchestrator._process_collaboration_mailbox(wf)

        conn = sqlite3.connect(str(collab_db))
        row = conn.execute("SELECT read_at FROM agent_mailbox WHERE id = ?", (mid,)).fetchone()
        conn.close()
        assert row[0] is not None


# ---------------------------------------------------------------------------
# TestExecuteWorkflowIntegration
# ---------------------------------------------------------------------------


class TestExecuteWorkflowIntegration:
    """execute_workflow integration with collaboration triggers."""

    @patch("tools.agent.team_orchestrator._audit_log")
    @patch("tools.agent.collaboration.reviewer_pattern")
    @patch("tools.agent.authority.get_required_reviewers")
    def test_auto_review_blocks_cause_workflow_partial(
        self, mock_get_reviewers, mock_reviewer, mock_audit, orchestrator
    ):
        """When auto-review blocks a subtask, downstream is blocked and workflow is partial."""
        mock_get_reviewers.return_value = [
            {"agent_id": "security-agent", "veto_type": "hard"}
        ]
        mock_reviewer.return_value = {"approved": False, "feedback_history": []}

        wf = Workflow(id="wf-int-1", name="Integration", project_id="proj-i")
        wf.subtasks["s1"] = Subtask(
            id="s1", agent_id="builder-agent", skill_id="generate_code", depends_on=[]
        )
        wf.subtasks["s2"] = Subtask(
            id="s2", agent_id="builder-agent", skill_id="write_tests", depends_on=["s1"]
        )
        orchestrator._persist_workflow(wf)

        def _mock_exec(subtask, context):
            subtask.status = "completed"
            subtask.output_data = {"ok": True}
            subtask.duration_ms = 10
            return subtask

        orchestrator._execute_subtask = _mock_exec
        result = orchestrator.execute_workflow(wf, timeout=30)

        assert result.status == "failed"
        assert result.aggregated_result["summary"]["failed"] == 0
        assert result.aggregated_result["summary"]["blocked"] == 2
        assert wf.subtasks["s2"].status == "blocked"

    @patch("tools.agent.team_orchestrator._audit_log")
    @patch("tools.agent.collaboration.reviewer_pattern")
    @patch("tools.agent.authority.get_required_reviewers")
    def test_auto_review_approved_workflow_completes(
        self, mock_get_reviewers, mock_reviewer, mock_audit, orchestrator
    ):
        """When auto-review approves, workflow completes normally."""
        mock_get_reviewers.return_value = [
            {"agent_id": "security-agent", "veto_type": "hard"}
        ]
        mock_reviewer.return_value = {"approved": True, "feedback_history": []}

        wf = Workflow(id="wf-int-2", name="Integration OK", project_id="proj-i")
        wf.subtasks["s1"] = Subtask(
            id="s1", agent_id="builder-agent", skill_id="generate_code", depends_on=[]
        )
        orchestrator._persist_workflow(wf)

        def _mock_exec(subtask, context):
            subtask.status = "completed"
            subtask.output_data = {"ok": True}
            subtask.duration_ms = 10
            return subtask

        orchestrator._execute_subtask = _mock_exec
        result = orchestrator.execute_workflow(wf, timeout=30)

        assert result.status == "completed"
        assert result.aggregated_result["summary"]["completed"] == 1

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_mailbox_veto_during_execution(self, mock_audit, orchestrator, collab_db):
        """A veto message placed during execution blocks the subtask."""
        wf = Workflow(id="wf-mv-1", name="Mailbox veto", project_id="proj-mv")
        wf.subtasks["s1"] = Subtask(id="s1", agent_id="a", skill_id="sk", depends_on=[])
        wf.subtasks["s2"] = Subtask(id="s2", agent_id="a", skill_id="sk", depends_on=["s1"])
        orchestrator._persist_workflow(wf)

        call_count = {"n": 0}

        def _mock_exec(subtask, context):
            call_count["n"] += 1
            subtask.status = "completed"
            subtask.output_data = {"ok": True}
            subtask.duration_ms = 10
            # Inject a mailbox veto for s1 while it is executing
            if subtask.id == "s1":
                import uuid
                import hmac
                import hashlib
                mid = str(uuid.uuid4())
                body = json.dumps({"subtask_id": "s1", "reason": "Runtime veto"})
                msg_bytes = f"security-agent|orchestrator-agent|subject|{body}".encode("utf-8")
                sig = hmac.new("icdev-default-hmac-key".encode(), msg_bytes, hashlib.sha256).hexdigest()
                conn = sqlite3.connect(str(collab_db))
                conn.execute(
                    """INSERT INTO agent_mailbox
                       (id, from_agent_id, to_agent_id, message_type, subject, body, hmac_signature)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (mid, "security-agent", "orchestrator-agent", "veto", "subject", body, sig),
                )
                conn.commit()
                conn.close()
            return subtask

        orchestrator._execute_subtask = _mock_exec
        result = orchestrator.execute_workflow(wf, timeout=30)

        # s1 is blocked by mailbox veto, s2 is blocked downstream
        assert wf.subtasks["s1"].status == "blocked"
        assert wf.subtasks["s2"].status == "blocked"
        assert result.status in ("failed", "partially_completed")


# [TEMPLATE: CUI // SP-CTI]
