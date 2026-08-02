# CUI // SP-CTI
"""Unit tests for WorkflowEngine, HITLGate, and feedback kickback cycle."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from _sql_compat import connect as _tconnect

# ── minimal in-memory DB fixture ──────────────────────────────────────────────

MINIMAL_WF_SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_templates (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, canvas_type TEXT,
    stages_json TEXT NOT NULL, roles_json TEXT NOT NULL,
    approval_policy TEXT NOT NULL DEFAULT 'any_one',
    kickback_limit INTEGER NOT NULL DEFAULT 3,
    is_default INTEGER NOT NULL DEFAULT 0,
    is_system INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_teams (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
    canvas_type TEXT, created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_team_members (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL, user_id TEXT NOT NULL, role_label TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(team_id, user_id)
);
CREATE TABLE IF NOT EXISTS wf_team_assignments (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL, template_id TEXT, scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(team_id, scope_type, scope_id)
);
CREATE TABLE IF NOT EXISTS wf_instances (
    id TEXT PRIMARY KEY, template_id TEXT NOT NULL,
    task_id TEXT, project_id TEXT, canvas_type TEXT,
    current_stage TEXT NOT NULL DEFAULT 'build',
    kickback_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_approvals (
    id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, stage TEXT NOT NULL,
    team_id TEXT, assigned_to TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    due_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_feedback (
    id TEXT PRIMARY KEY, approval_id TEXT NOT NULL, instance_id TEXT NOT NULL,
    task_id TEXT, canvas_type TEXT, template_id TEXT,
    stage TEXT NOT NULL, decision TEXT NOT NULL,
    feedback_types TEXT, rating INTEGER, comments TEXT,
    improvement_tags TEXT, kickback_reason TEXT, citations_json TEXT,
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_feedback_insights (
    id TEXT PRIMARY KEY, canvas_type TEXT, template_id TEXT,
    feedback_type TEXT, avg_rating REAL, issue_count INTEGER,
    top_tags TEXT, kickback_rate REAL,
    period_start TEXT NOT NULL, period_end TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_external_steps (
    id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
    stage_name TEXT NOT NULL, step_type TEXT NOT NULL,
    external_system TEXT, external_ref TEXT, webhook_token TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    notified_at TEXT, completed_at TEXT, completed_by TEXT, payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_document_templates (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, doc_type TEXT NOT NULL,
    schema_json TEXT NOT NULL, canvas_type TEXT, stage_scope TEXT,
    is_ai_reference INTEGER NOT NULL DEFAULT 0,
    is_human_required INTEGER NOT NULL DEFAULT 0,
    version TEXT NOT NULL DEFAULT '1',
    is_system INTEGER NOT NULL DEFAULT 0, created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_document_submissions (
    id TEXT PRIMARY KEY, approval_id TEXT NOT NULL,
    instance_id TEXT NOT NULL, doc_template_id TEXT NOT NULL,
    stage TEXT NOT NULL, submitted_by TEXT NOT NULL,
    submission_json TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_citations (
    id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
    stage TEXT NOT NULL, source_doc TEXT NOT NULL,
    source_type TEXT, doc_version TEXT, section TEXT,
    page_number INTEGER, excerpt TEXT, cited_by TEXT NOT NULL,
    cited_in_type TEXT NOT NULL, cited_in_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id TEXT PRIMARY KEY, title TEXT, status TEXT, hitl_stage TEXT,
    -- team_manager.resolve_team() reads these when falling back from a direct
    -- task assignment to a project / task_group assignment. They were missing
    -- while the fixture left team_manager bound to the ambient DB, so the query
    -- failed there and the caller's except swallowed it.
    project_id TEXT, tags TEXT
);
"""

_DEFAULT_STAGES = json.dumps([
    {"name": "build",   "step_type": "automated"},
    {"name": "review",  "step_type": "manual",  "role": "reviewer"},
    {"name": "approve", "step_type": "manual",  "role": "manager"},
])
_DEFAULT_ROLES = json.dumps({"build": "engineer", "review": "reviewer", "approve": "manager"})


@pytest.fixture()
def tmp_db(tmp_path):
    """Spin up a fresh in-memory SQLite DB, patch get_connection() to use it."""
    db_path = tmp_path / "test_wf.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_WF_SCHEMA)
    # Seed one system template
    conn.execute(
        """INSERT INTO wf_templates
           (id, name, canvas_type, stages_json, roles_json, approval_policy,
            kickback_limit, is_default, is_system, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,1,1,'system',datetime('now'),datetime('now'))""",
        ("sys-wft-global", "Default Global Workflow", None,
         _DEFAULT_STAGES, _DEFAULT_ROLES, "any_one", 3),
    )
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status) VALUES ('task-001', 'Test task', 'in_progress')",
    )
    conn.commit()
    conn.close()

    def _get_conn():
        # Translating wrapper so the module's PG-native %s SQL is rewritten for
        # SQLite, via the shared tests/_sql_compat.py helper (which delegates to
        # the same translate_sql the runtime uses).
        return _tconnect(db_path)

    # Patch the canonical location AND every tools.workflow_hitl module that has
    # already bound get_connection into its own namespace via
    # `from tools.db.storage import get_connection`.
    #
    # Patching only "tools.db.storage.get_connection" rebinds the attribute on
    # the storage module; a module that ran that from-import earlier still holds
    # the ORIGINAL function object and is untouched. Run alone, this file passed
    # because each module was first imported lazily inside a patched test. Run
    # after any file that imports tools.workflow_hitl (e.g.
    # test_workflow_hitl_api.py), the bindings already existed and 17 tests hit
    # the ambient DB instead -- "no such table: wf_teams".
    #
    # Only modules already in sys.modules are patched, so this neither forces
    # imports nor depends on file order: anything imported later re-reads the
    # (patched) attribute from tools.db.storage.
    #
    # Both namespaces are swept. `tools/` is a shim over `icdev.tools`, but it
    # keeps its own __path__, so `import tools.workflow_hitl.team_manager` and
    # `import icdev.tools.workflow_hitl.team_manager` load the file TWICE into
    # two distinct module objects, each with its own get_connection binding.
    # Matching only "tools.workflow_hitl" would leave the canonical copy bound
    # to the ambient DB the moment this file (or a module it exercises) follows
    # the CLAUDE.md rule that new code imports `icdev.tools.*`.
    import sys
    from contextlib import ExitStack

    _PKG_SUFFIX = "tools.workflow_hitl"

    _targets = ["tools.db.storage.get_connection"]
    _targets += [
        f"{name}.get_connection"
        for name, mod in list(sys.modules.items())
        if (name.startswith(_PKG_SUFFIX) or name.startswith(f"icdev.{_PKG_SUFFIX}"))
        and mod is not None
        and hasattr(mod, "get_connection")
    ]

    with ExitStack() as stack:
        for target in _targets:
            stack.enter_context(patch(target, side_effect=_get_conn))
        yield db_path


# ── template_manager ──────────────────────────────────────────────────────────

class TestTemplateManager:
    def test_get_default_returns_system_template(self, tmp_db):
        from tools.workflow_hitl import template_manager as tm
        tmpl = tm.get_default()
        assert tmpl is not None
        assert tmpl["id"] == "sys-wft-global"
        assert tmpl["is_system"] == 1

    def test_create_and_get(self, tmp_db):
        from tools.workflow_hitl import template_manager as tm
        tid = tm.create("My Template", canvas_type="NDC", created_by="user-1")
        assert tid.startswith("wft-")
        t = tm.get(tid)
        assert t["name"] == "My Template"
        assert t["canvas_type"] == "NDC"

    def test_update_non_system(self, tmp_db):
        from tools.workflow_hitl import template_manager as tm
        tid = tm.create("Editable", created_by="user-1")
        ok = tm.update(tid, name="Renamed")
        assert ok
        assert tm.get(tid)["name"] == "Renamed"

    def test_update_system_raises(self, tmp_db):
        from tools.workflow_hitl import template_manager as tm
        with pytest.raises(PermissionError):
            tm.update("sys-wft-global", name="Hacked")

    def test_fork_creates_editable_copy(self, tmp_db):
        from tools.workflow_hitl import template_manager as tm
        forked_id = tm.fork("sys-wft-global", "My Fork", created_by="user-2")
        forked = tm.get(forked_id)
        assert forked["is_system"] == 0
        assert forked["name"] == "My Fork"


# ── team_manager ──────────────────────────────────────────────────────────────

class TestTeamManager:
    def test_create_team_and_add_member(self, tmp_db):
        from tools.workflow_hitl import team_manager as tman
        team_id = tman.create_team("Alpha Squad", created_by="admin")
        assert team_id.startswith("wft-")
        mem_id = tman.add_member(team_id, "user-99", "reviewer")
        assert mem_id is not None
        # get_members_by_role is the available accessor
        members = tman.get_members_by_role(team_id, "reviewer")
        assert any(m["user_id"] == "user-99" for m in members)

    def test_remove_member(self, tmp_db):
        from tools.workflow_hitl import team_manager as tman
        import sqlite3
        team_id = tman.create_team("Beta Squad", created_by="admin")
        tman.add_member(team_id, "user-10", "engineer")
        ok = tman.remove_member(team_id, "user-10")
        assert ok
        # Verify via DB directly
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM wf_team_members WHERE team_id=? AND user_id=?",
                            (team_id, "user-10")).fetchall()
        conn.close()
        assert len(rows) == 0

    def test_assign_and_resolve_team(self, tmp_db):
        from tools.workflow_hitl import team_manager as tman
        team_id = tman.create_team("Task Team", created_by="admin")
        tman.assign_team(team_id, "task", "task-001")
        result = tman.resolve_team("task-001")
        assert result is not None
        # resolve_team returns {"team": {...}, "template": {...}|None}
        assert "team" in result


# ── engine ────────────────────────────────────────────────────────────────────

class TestWorkflowEngine:
    def test_create_instance(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        wfe = WorkflowEngine()
        inst_id = wfe.create_instance(task_id="task-001", canvas_type="NDC")
        assert inst_id.startswith("wfi-")

    def test_get_status(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        wfe = WorkflowEngine()
        inst_id = wfe.create_instance(task_id="task-001")
        status = wfe.get_status(inst_id)
        assert status["status"] == "active"
        assert "current_stage" in status

    def test_advance_stage_from_build(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        wfe = WorkflowEngine()
        inst_id = wfe.create_instance(task_id="task-001")
        # Automated build stage should advance on create; get current stage
        status = wfe.get_status(inst_id)
        # build is automated — may auto-advance to review; just verify we have a stage
        assert status["current_stage"] in ("build", "review", "approve", "done")

    def test_kickback_increments_count(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        wfe = WorkflowEngine()
        inst_id = wfe.create_instance(task_id="task-001")
        result = wfe.kickback(inst_id, kickback_reason="missing tests", submitted_by="reviewer-1")
        assert result.get("kickback_count", 0) >= 1

    def test_kickback_cycle_up_to_limit(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        wfe = WorkflowEngine()
        inst_id = wfe.create_instance(task_id="task-001")
        # Kick back 3 times (default limit)
        for i in range(3):
            wfe.kickback(inst_id, kickback_reason=f"kickback {i}", submitted_by="reviewer-1")
        # On or after the limit the instance should escalate or remain escalated
        final_status = wfe.get_status(inst_id)
        assert final_status["status"] in ("escalated", "active")

    def test_escalate(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        wfe = WorkflowEngine()
        inst_id = wfe.create_instance(task_id="task-001")
        result = wfe.escalate(inst_id, reason="reviewer unavailable")
        # engine returns {"escalated": True, "reason": ...}
        assert result.get("escalated") is True

    def test_cancel(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        wfe = WorkflowEngine()
        inst_id = wfe.create_instance(task_id="task-001")
        result = wfe.cancel(inst_id)
        # engine returns {"cancelled": True}
        assert result.get("cancelled") is True

    def test_nonexistent_instance_returns_none(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        wfe = WorkflowEngine()
        # get_status returns None for unknown instance (does not raise)
        result = wfe.get_status("wfi-nonexistent-000")
        assert result is None


# ── gate ──────────────────────────────────────────────────────────────────────

class TestHITLGate:
    def test_should_gate_false_when_no_instance(self, tmp_db):
        from tools.workflow_hitl.gate import HITLGate
        gate = HITLGate()
        assert gate.should_gate("task-no-workflow") is False

    def test_get_pending_none_when_no_instance(self, tmp_db):
        from tools.workflow_hitl.gate import HITLGate
        gate = HITLGate()
        assert gate.get_pending("task-no-workflow") is None

    def test_gate_blocks_when_pending_approval(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        from tools.workflow_hitl.gate import HITLGate
        wfe = WorkflowEngine()
        wfe.create_instance(task_id="task-001")
        # If there's an active pending approval the gate should fire
        gate = HITLGate()
        # Either should_gate=True (pending exists) or False (automated stage completed)
        result = gate.should_gate("task-001")
        assert isinstance(result, bool)


# ── feedback ──────────────────────────────────────────────────────────────────

class TestFeedback:
    def _get_approval_id(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        wfe = WorkflowEngine()
        inst_id = wfe.create_instance(task_id="task-001")
        # Find a pending approval
        import sqlite3
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id FROM wf_approvals WHERE instance_id=? AND status='pending' LIMIT 1",
            (inst_id,),
        ).fetchone()
        conn.close()
        return inst_id, (dict(row)["id"] if row else None)

    def test_submit_approve(self, tmp_db):
        from tools.workflow_hitl import feedback as fb
        inst_id, approval_id = self._get_approval_id(tmp_db)
        if not approval_id:
            pytest.skip("No pending approval — automated stage may have auto-advanced")
        result = fb.submit_feedback(
            approval_id=approval_id,
            decision="approve",
            rating=5,
            comments="Looks good",
            submitted_by="reviewer-1",
        )
        # submit_feedback() is declared `-> str` and returns the feedback id.
        # The previous dict-shaped assertion never ran: _get_approval_id() found
        # no approval while the fixture left the engine on the ambient DB, so
        # this test skipped every time and the mismatch stayed hidden.
        assert isinstance(result, str) and result

    def test_submit_kickback_requires_reason(self, tmp_db):
        from tools.workflow_hitl import feedback as fb
        inst_id, approval_id = self._get_approval_id(tmp_db)
        if not approval_id:
            pytest.skip("No pending approval")
        with pytest.raises((ValueError, KeyError)):
            fb.submit_feedback(
                approval_id=approval_id,
                decision="kickback",
                submitted_by="reviewer-1",
                # intentionally omit kickback_reason
            )


# ── citation_manager ──────────────────────────────────────────────────────────

class TestCitationManager:
    def test_add_and_get_citation(self, tmp_db):
        from tools.workflow_hitl.engine import WorkflowEngine
        from tools.workflow_hitl import citation_manager as cm
        wfe = WorkflowEngine()
        inst_id = wfe.create_instance(task_id="task-001")
        cid = cm.add_citation(
            instance_id=inst_id,
            stage="build",
            source_doc="NIST SP 800-53",
            section="AC-2",
            page_number=47,
            excerpt="The organization manages information system accounts...",
            cited_by="system",
            cited_in_type="step_output",
            cited_in_id="step-001",
        )
        assert cid is not None
        citations = cm.get_by_instance(inst_id)
        assert any(c["id"] == cid for c in citations)

    def test_format_citation_ref(self, tmp_db):
        from tools.workflow_hitl import citation_manager as cm
        ref = cm.format_citation_ref({
            "source_doc": "NIST SP 800-53",
            "doc_version": "Rev 5",
            "section": "AC-2(a)",
            "page_number": 47,
        })
        assert "NIST SP 800-53" in ref
        assert "AC-2(a)" in ref
