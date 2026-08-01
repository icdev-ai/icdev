# CUI // SP-CTI
"""DWO-DUR-04 — Studio gates and workflow_hitl external steps are one surface.

Before this, an approver had two inboxes: Studio's `awaiting_approval` steps and
workflow_hitl's `wf_external_steps`. Neither knew about the other. Direction (a)
of the spike makes Studio a *producer* of wf_external_steps so workflow_hitl
stays the single reviewer inbox.

See docs/spikes/dwo-00-superplane-workflow-adaptation.md (gap DUR).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from tools.db.storage import get_connection
from tools.studio import gate_bridge
from tools.studio import workflow_runner as wr
from tools.studio.init_db import init_studio_tables
from tools.workflow_hitl import external_steps

# workflow_hitl tables are not in the shared conftest schema — the bridge needs
# wf_templates/wf_instances (FK targets) and wf_external_steps.
_HITL_DDL = [
    """CREATE TABLE IF NOT EXISTS wf_templates (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, canvas_type TEXT,
        stages_json TEXT NOT NULL, roles_json TEXT NOT NULL,
        approval_policy TEXT NOT NULL DEFAULT 'any_one',
        kickback_limit INTEGER NOT NULL DEFAULT 3,
        is_default INTEGER NOT NULL DEFAULT 0, is_system INTEGER NOT NULL DEFAULT 0,
        created_by TEXT, created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS wf_instances (
        id TEXT PRIMARY KEY, template_id TEXT NOT NULL, task_id TEXT,
        project_id TEXT, canvas_type TEXT,
        current_stage TEXT NOT NULL DEFAULT 'build',
        kickback_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS wf_external_steps (
        id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, stage_name TEXT NOT NULL,
        step_type TEXT NOT NULL, external_system TEXT, external_ref TEXT,
        webhook_token TEXT, status TEXT NOT NULL DEFAULT 'pending',
        notified_at TEXT, completed_at TEXT, completed_by TEXT, payload_json TEXT,
        created_at TEXT, updated_at TEXT)""",
]


@pytest.fixture(autouse=True)
def _schema():
    init_studio_tables()
    conn = get_connection()
    try:
        for ddl in _HITL_DDL:
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()
    gate_bridge._bridging.clear()


def _seed_parked_gate() -> tuple[str, str]:
    """A Studio run parked on a human gate. Returns (run_id, step_run_id)."""
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
    step_run_id = f"sr-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflow_runs (run_id, workflow_id, workflow_name, "
            "status, started_at, triggered_by, project_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (run_id, workflow_id, "Gate Bridge", "awaiting_approval", now, "test", "default"),
        )
        conn.execute(
            "INSERT INTO studio_workflow_run_steps (step_run_id, run_id, step_id, "
            "step_name, status, started_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (step_run_id, run_id, "step_2", "Deploy Approval", "awaiting_approval", now),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id, step_run_id


def _step_status(step_run_id: str) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM studio_workflow_run_steps WHERE step_run_id=%s",
            (step_run_id,),
        ).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def test_studio_gate_appears_in_exactly_one_reviewer_inbox():
    """One parked gate produces exactly one wf_external_steps row, linked both ways."""
    run_id, step_run_id = _seed_parked_gate()

    step_id = gate_bridge.open_gate(run_id, step_run_id, "Deploy Approval", "approver")
    assert step_id, "gate was not published to the workflow_hitl inbox"

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_external_steps WHERE external_ref=%s", (step_run_id,)
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, "the gate must appear in exactly one reviewer inbox"
    step = dict(rows[0])
    assert step["external_system"] == "studio"
    assert step["status"] == "sent", "must be visible in the /wf/external pending inbox"
    # Bidirectional linkage — no new schema, external_ref is the back-pointer.
    assert gate_bridge.find_external_step(step_run_id)["id"] == step_id

    # Re-parking the same gate (e.g. after a resume) must not duplicate the row.
    assert gate_bridge.open_gate(run_id, step_run_id, "Deploy Approval", "approver") == step_id


def test_approving_from_workflow_hitl_releases_the_studio_gate():
    run_id, step_run_id = _seed_parked_gate()
    step_id = gate_bridge.open_gate(run_id, step_run_id, "Deploy Approval", "approver")

    assert external_steps.mark_complete(step_id, completed_by="reviewer@icdev")

    assert _step_status(step_run_id) == "approved", "Studio run gate was not released"

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status, completed_by FROM wf_external_steps WHERE id=%s", (step_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "completed"
    assert row["completed_by"] == "reviewer@icdev"


def test_approving_from_studio_completes_the_external_step():
    run_id, step_run_id = _seed_parked_gate()
    step_id = gate_bridge.open_gate(run_id, step_run_id, "Deploy Approval", "approver")

    assert wr.approve_step(step_run_id, actor="lead@icdev")

    assert _step_status(step_run_id) == "approved"

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status, completed_by FROM wf_external_steps WHERE id=%s", (step_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "completed", "external step left pending in the reviewer inbox"
    assert row["completed_by"] == "lead@icdev"


def test_second_decision_on_a_decided_gate_is_rejected():
    """No double-approval: whichever surface decides first wins, once."""
    run_id, step_run_id = _seed_parked_gate()
    step_id = gate_bridge.open_gate(run_id, step_run_id, "Deploy Approval", "approver")

    assert external_steps.mark_complete(step_id, completed_by="first@icdev")

    # Second decision through the reviewer inbox.
    assert external_steps.mark_complete(step_id, completed_by="second@icdev") is False
    # Second decision through Studio — the gate is no longer awaiting_approval.
    assert wr.approve_step(step_run_id, actor="second@icdev") is False

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT completed_by FROM wf_external_steps WHERE id=%s", (step_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["completed_by"] == "first@icdev", "a later decision overwrote the first"


def test_telegram_approval_also_closes_the_external_step():
    """`/approve <step_run_id>` writes the gate straight to the DB — it must
    still close the reviewer-inbox row, or Telegram leaves an orphan."""
    from tools.notifications.adapters import telegram_listener

    run_id, step_run_id = _seed_parked_gate()
    step_id = gate_bridge.open_gate(run_id, step_run_id, "Deploy Approval", "approver")

    assert telegram_listener._write_hitl_decision_to_db(step_run_id, "approve")

    assert _step_status(step_run_id) == "approved"
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM wf_external_steps WHERE id=%s", (step_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "completed"


def test_rejecting_from_studio_completes_the_external_step():
    run_id, step_run_id = _seed_parked_gate()
    step_id = gate_bridge.open_gate(run_id, step_run_id, "Deploy Approval", "approver")

    assert wr.reject_step(step_run_id, reason="missing evidence", actor="lead@icdev")

    assert _step_status(step_run_id) == "rejected"
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM wf_external_steps WHERE id=%s", (step_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "completed", "rejected gate left an orphan in the reviewer inbox"
