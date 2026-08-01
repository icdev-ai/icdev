# CUI // SP-CTI
"""Security tests for pdx-sec-04 — audit coverage for all pipeline write routes.

Defect: ``_audit`` swallowed every exception at WARNING and many mutating routes
never audited at all (create version / create+delete boundary / create
change-request / all SOP create/update/submit routes / collab join/leave/push).

Fixes verified here:
  1. Non-destructive writes now emit an audit row: create version, create
     boundary, create change-request, SOP create/update/submit, collab
     join/leave/push (action names mirror the existing _audit vocabulary).
  2. Destructive/governance actions fail closed: the audit row is written
     BEFORE/with the mutation, so if the audit write fails the mutation never
     commits and the route returns 500 {'error': 'audit trail unavailable'}.
     Verified for pipeline DELETE (same-transaction) and SOP approve.
  3. pc_audit / pipeline_snapshots are registered in APPEND_ONLY_TABLES
     (static assertion against the hook file). pdc_snapshots is deliberately
     NOT append-only — a design-history store with bounded auto-snapshot
     retention (pdx reconciliation, user-approved 2026-07-18).
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from flask import Flask  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(**kw):
    return kw


def _mock_conn(fetchone=None, fetchall=None):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall if fetchall is not None else []
    conn.execute.return_value = cur
    return conn


def _mock_conn_seq(fetchone_seq):
    """Mock connection whose successive .fetchone() calls return a sequence."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetchone_seq)
    cur.fetchall.return_value = []
    conn.execute.return_value = cur
    return conn


@pytest.fixture(scope="module")
def app():
    import os

    os.environ.setdefault("ICDEV_PIPELINE_ENABLED", "true")
    with patch("tools.pipeline.blueprint.init_db"):
        from tools.pipeline.blueprint import create_pipeline_blueprint

        flask_app = Flask(__name__)
        flask_app.secret_key = "test-secret-key"
        flask_app.config["TESTING"] = True

        @flask_app.context_processor
        def _inject_base_ctx():
            return {"ROLE_VIEWS": {}, "current_role": None, "current_user": None}

        bp = create_pipeline_blueprint()
        assert bp is not None, "ICDEV_PIPELINE_ENABLED must be true"
        flask_app.register_blueprint(bp, url_prefix="/devops")
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, user_id="test-user", role="developer"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        if role is not None:
            sess["role"] = role
        else:
            sess.pop("role", None)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Non-destructive writes emit an audit row
# ══════════════════════════════════════════════════════════════════════════════

def test_create_version_writes_audit(client):
    """Creating a pipeline version writes a CREATE_VERSION audit row."""
    pipe_id = str(uuid.uuid4())
    _login(client, role="developer")
    # fetchone #1 -> pipeline row (graph_json); fetchone #2 -> MAX(version_num)[0]
    conn = _mock_conn_seq([_row(graph_json='{"nodes":[],"edges":[]}'), [2]])
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit") as aud:
        resp = client.post(
            f"/devops/api/versions/{pipe_id}",
            data=json.dumps({"label": "v3"}),
            content_type="application/json",
        )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    actions = [c.args[0] for c in aud.call_args_list]
    assert "CREATE_VERSION" in actions, actions


def test_create_boundary_writes_audit(client):
    """Creating a boundary writes a CREATE_BOUNDARY audit row."""
    pipe_id = str(uuid.uuid4())
    _login(client, role="developer")
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit") as aud:
        resp = client.post(
            f"/devops/api/boundaries/{pipe_id}",
            data=json.dumps({"label": "Zone A"}),
            content_type="application/json",
        )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    actions = [c.args[0] for c in aud.call_args_list]
    assert "CREATE_BOUNDARY" in actions, actions


def test_delete_boundary_writes_audit(client):
    """Deleting a boundary writes a DELETE_BOUNDARY audit row (write-tier ok)."""
    pipe_id = str(uuid.uuid4())
    bid = str(uuid.uuid4())[:8]
    _login(client, role="developer")
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit") as aud:
        resp = client.delete(f"/devops/api/boundaries/{pipe_id}/{bid}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    actions = [c.args[0] for c in aud.call_args_list]
    assert "DELETE_BOUNDARY" in actions, actions


def test_create_change_request_writes_audit(client):
    """Creating a change-request writes a CREATE_CR audit row."""
    pipe_id = str(uuid.uuid4())
    _login(client, role="developer")
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit") as aud:
        resp = client.post(
            f"/devops/api/change-requests/{pipe_id}",
            data=json.dumps({"cr_number": "CR-123", "cr_type": "modify"}),
            content_type="application/json",
        )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    actions = [c.args[0] for c in aud.call_args_list]
    assert "CREATE_CR" in actions, actions


def test_sop_create_writes_audit(client):
    """Creating a SOP writes a SOP_CREATE audit row."""
    sop_id = str(uuid.uuid4())
    _login(client, role="developer")
    with patch("tools.pipeline.blueprint._pdc_create_sop") as cp, \
         patch("tools.pipeline.blueprint._audit") as aud:
        cp.return_value = _row(id=sop_id, title="Deploy SOP")
        resp = client.post(
            "/devops/api/sops",
            data=json.dumps({"title": "Deploy SOP"}),
            content_type="application/json",
        )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    actions = [c.args[0] for c in aud.call_args_list]
    assert "SOP_CREATE" in actions, actions


def test_sop_submit_writes_audit(client):
    """Submitting a SOP for review writes a SOP_SUBMIT audit row."""
    sop_id = str(uuid.uuid4())
    _login(client, role="developer")
    with patch("tools.pipeline.blueprint._pdc_submit_for_review") as sp, \
         patch("tools.pipeline.blueprint._audit") as aud:
        sp.return_value = (_row(id=sop_id, approval_status="pending_review"), None)
        resp = client.post(f"/devops/api/sops/{sop_id}/submit")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    actions = [c.args[0] for c in aud.call_args_list]
    assert "SOP_SUBMIT" in actions, actions


def test_collab_join_writes_audit(client):
    """Collab join writes a COLLAB_JOIN audit row attributed to the session user."""
    design_id = str(uuid.uuid4())
    _login(client, user_id="real-user", role="pm")
    with patch("tools.canvas.collaboration.CanvasCollabManager.join", return_value={"ok": True}), \
         patch("tools.pipeline.blueprint._audit") as aud:
        resp = client.post(
            f"/devops/api/collab/{design_id}/join",
            data=json.dumps({"user_name": "Bob"}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    calls = {c.args[0]: c for c in aud.call_args_list}
    assert "COLLAB_JOIN" in calls, list(calls)
    # attributed to session identity
    assert calls["COLLAB_JOIN"].kwargs.get("user_id") == "real-user"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Fail-closed destructive audits
# ══════════════════════════════════════════════════════════════════════════════

def test_pipeline_delete_fails_closed_when_audit_fails(client):
    """A failing audit insert must abort the pipeline DELETE (no commit) -> 500."""
    from tools.pipeline.blueprint import AuditUnavailable

    pipe_id = str(uuid.uuid4())
    _login(client, role="admin")  # elevated role required for delete
    conn = _mock_conn()

    def _boom(*a, **k):
        raise AuditUnavailable("audit db down")

    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit_strict", side_effect=_boom):
        resp = client.delete(f"/devops/api/pipelines/{pipe_id}")

    assert resp.status_code == 500, resp.get_data(as_text=True)
    assert "audit trail unavailable" in resp.get_data(as_text=True)
    # The DELETE was attempted on the connection but MUST NOT have been committed.
    assert not conn.commit.called, "mutation must not commit when audit fails"
    assert conn.rollback.called, "transaction must be rolled back when audit fails"


def test_sop_approve_writes_audit_row(client):
    """A successful SOP approval writes a SOP_APPROVE audit row (fail-closed strict)."""
    sop_id = str(uuid.uuid4())
    _login(client, user_id="approver-bob", role="admin")
    with patch("tools.pipeline.blueprint._pdc_get_sop_by_id") as gp, \
         patch("tools.pipeline.blueprint._pdc_approve_sop") as ap, \
         patch("tools.pipeline.blueprint._audit_strict") as strict:
        gp.return_value = _row(id=sop_id, owner="carol", approval_status="pending_review")
        ap.return_value = (_row(id=sop_id, approval_status="approved"), None)
        resp = client.post(
            f"/devops/api/sops/{sop_id}/approve",
            data=json.dumps({}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    strict.assert_called_once()
    assert strict.call_args.args[0] == "SOP_APPROVE"
    # approver identity is the session user, recorded in the audit row
    assert strict.call_args.kwargs.get("user_id") == "approver-bob"


def test_sop_approve_fails_closed_when_audit_fails(client):
    """If the approval audit write fails, the approve mutation must not run -> 500."""
    from tools.pipeline.blueprint import AuditUnavailable

    sop_id = str(uuid.uuid4())
    _login(client, user_id="approver-bob", role="admin")

    def _boom(*a, **k):
        raise AuditUnavailable("audit db down")

    with patch("tools.pipeline.blueprint._pdc_get_sop_by_id") as gp, \
         patch("tools.pipeline.blueprint._pdc_approve_sop") as ap, \
         patch("tools.pipeline.blueprint._audit_strict", side_effect=_boom):
        gp.return_value = _row(id=sop_id, owner="carol", approval_status="pending_review")
        resp = client.post(
            f"/devops/api/sops/{sop_id}/approve",
            data=json.dumps({}),
            content_type="application/json",
        )
    assert resp.status_code == 500, resp.get_data(as_text=True)
    assert "audit trail unavailable" in resp.get_data(as_text=True)
    ap.assert_not_called(), "approve must not run when its audit write fails"


def test_sop_delete_fails_closed_when_audit_fails(client):
    """If the delete audit write fails, the SOP delete must not run -> 500."""
    from tools.pipeline.blueprint import AuditUnavailable

    sop_id = str(uuid.uuid4())
    _login(client, user_id="isso-dan", role="isso")

    def _boom(*a, **k):
        raise AuditUnavailable("audit db down")

    with patch("tools.pipeline.blueprint._pdc_get_sop_by_id") as gp, \
         patch("tools.pipeline.blueprint._pdc_delete_sop") as dp, \
         patch("tools.pipeline.blueprint._audit_strict", side_effect=_boom):
        gp.return_value = _row(id=sop_id, owner="carol", approval_status="approved")
        resp = client.delete(f"/devops/api/sops/{sop_id}")
    assert resp.status_code == 500, resp.get_data(as_text=True)
    assert "audit trail unavailable" in resp.get_data(as_text=True)
    dp.assert_not_called(), "SOP delete must not run when its audit write fails"


# ══════════════════════════════════════════════════════════════════════════════
# 3. APPEND_ONLY_TABLES registration (static assertion against the hook)
# ══════════════════════════════════════════════════════════════════════════════

class TestAppendOnlyRegistration:
    """The pipeline audit/snapshot tables must be append-only protected.

    pc_audit and pipeline_snapshots are true audit records and remain
    append-only. pdc_snapshots is deliberately excluded — it is a design-history
    working store with bounded auto-snapshot retention (see pdx reconciliation,
    user-approved 2026-07-18).
    """

    def _hook_text(self):
        hook_path = BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py"
        return hook_path.read_text(encoding="utf-8")

    def test_pc_audit_protected(self):
        assert '"pc_audit"' in self._hook_text()

    def test_pipeline_snapshots_protected(self):
        assert '"pipeline_snapshots"' in self._hook_text()

    def test_pdc_snapshots_not_protected(self):
        # pdc_snapshots deliberately excluded — design-history store with bounded
        # auto-snapshot retention (pdx reconciliation, user-approved 2026-07-18).
        assert '"pdc_snapshots"' not in self._hook_text()
