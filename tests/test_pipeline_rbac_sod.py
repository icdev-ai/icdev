# CUI // SP-CTI
"""Security tests for pdx-sec-03 — RBAC + session-derived identity + SOP SoD.

Defect (broken access control, P1): pc_login_required only checked
``session['user_id']``, so EVERY authenticated user of ANY role could:
  * DELETE pipelines and delete/approve/reject SOPs (no elevated-role gate),
  * generate deploy bundles,
  * spoof another user's identity on collab join/leave/push (user_id from body),
  * self-approve a SOP they own (no separation of duties), and supply an
    arbitrary ``approved_by`` in the request body.

Fixes verified here (deny cases are mandatory — repo ABAC lesson):
  1. Write routes require a write-tier role (developer/pm/isso/admin); a caller
     with no/unknown role -> 403. Destructive/governance routes (pipeline
     DELETE, SOP approve/reject/delete) require an elevated role (admin/isso);
     a developer -> 403.
  2. SOP approve records the SESSION identity as approved_by, ignoring any
     body-supplied ``approved_by``.
  3. SOP self-approval (approver == SOP owner) -> 403 (NIST AC-5).
  4. Collab push attributes the op to the session identity, ignoring
     body ``user_id`` (identity spoofing).
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(**kw):
    """Plain dict — row_to_dict / _sop_to_dict accept these (have .keys())."""
    return kw


def _mock_conn(fetchone=None, fetchall=None):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall if fetchall is not None else []
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
    """Establish a session with an id and (optionally) a role.

    role=None simulates an authenticated caller whose role cannot be resolved
    from server-side auth state — the fail-closed case.
    """
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        if role is not None:
            sess["role"] = role
        else:
            sess.pop("role", None)


# ══════════════════════════════════════════════════════════════════════════════
# 1. RBAC — pipeline DELETE (elevated: admin/isso only)
# ══════════════════════════════════════════════════════════════════════════════

def test_delete_pipeline_denied_for_developer(client):
    """A write-tier developer must NOT delete a pipeline (elevated action) -> 403, no DELETE SQL."""
    pipe_id = str(uuid.uuid4())
    _login(client, role="developer")
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        resp = client.delete(f"/devops/api/pipelines/{pipe_id}")
    assert resp.status_code == 403, resp.get_data(as_text=True)
    delete_calls = [c for c in conn.execute.call_args_list
                    if "DELETE FROM pipelines" in str(c.args[0])]
    assert not delete_calls, "denied caller must never reach the DELETE"


def test_delete_pipeline_denied_for_unknown_role(client):
    """Authenticated caller with NO resolvable role fails closed -> 403."""
    pipe_id = str(uuid.uuid4())
    _login(client, role=None)
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        resp = client.delete(f"/devops/api/pipelines/{pipe_id}")
    assert resp.status_code == 403, resp.get_data(as_text=True)


def test_delete_pipeline_allowed_for_admin(client):
    """An elevated admin CAN delete a pipeline -> 200 + DELETE issued."""
    pipe_id = str(uuid.uuid4())
    _login(client, role="admin")
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        resp = client.delete(f"/devops/api/pipelines/{pipe_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    delete_calls = [c for c in conn.execute.call_args_list
                    if "DELETE FROM pipelines" in str(c.args[0])]
    assert delete_calls, "admin delete must reach the DELETE"


def test_delete_pipeline_allowed_for_isso(client):
    """The ISSO security authority CAN delete a pipeline -> 200."""
    pipe_id = str(uuid.uuid4())
    _login(client, role="isso")
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        resp = client.delete(f"/devops/api/pipelines/{pipe_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)


# ══════════════════════════════════════════════════════════════════════════════
# 2. RBAC — pipeline write (create requires write-tier role)
# ══════════════════════════════════════════════════════════════════════════════

def test_create_pipeline_denied_for_unknown_role(client):
    """Authenticated caller with no role cannot create a pipeline -> 403, no INSERT."""
    _login(client, role=None)
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        resp = client.post(
            "/devops/api/pipelines",
            data=json.dumps({"name": "X", "graph_json": {"nodes": [], "edges": []}}),
            content_type="application/json",
        )
    assert resp.status_code == 403, resp.get_data(as_text=True)
    insert_calls = [c for c in conn.execute.call_args_list
                    if "INSERT INTO pipelines" in str(c.args[0])]
    assert not insert_calls, "denied caller must never reach the INSERT"


def test_create_pipeline_allowed_for_developer(client):
    """A developer CAN create a pipeline -> 201."""
    _login(client, role="developer")
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"):
        resp = client.post(
            "/devops/api/pipelines",
            data=json.dumps({"name": "X", "graph_json": {"nodes": [], "edges": []}}),
            content_type="application/json",
        )
    assert resp.status_code == 201, resp.get_data(as_text=True)


# ══════════════════════════════════════════════════════════════════════════════
# 3. RBAC — deploy bundle generation requires write-tier role
# ══════════════════════════════════════════════════════════════════════════════

def test_deploy_bundle_denied_for_unknown_role(client):
    """A caller with no resolvable role cannot generate a deploy bundle -> 403."""
    pipe_id = str(uuid.uuid4())
    _login(client, role=None)
    conn = _mock_conn(fetchone=_row(
        id=pipe_id, name="X", graph_json='{"nodes":[],"edges":[]}',
        classification="CUI", target_csp="aws",
    ))
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn):
        resp = client.post(
            f"/devops/api/deploy/{pipe_id}",
            data=json.dumps({"target_csp": "auto"}),
            content_type="application/json",
        )
    assert resp.status_code == 403, resp.get_data(as_text=True)


# ══════════════════════════════════════════════════════════════════════════════
# 4. SOP approve — elevated role + session-derived identity + self-approval SoD
# ══════════════════════════════════════════════════════════════════════════════

def test_sop_approve_denied_for_developer(client):
    """A developer cannot approve a SOP (elevated governance action) -> 403."""
    sop_id = str(uuid.uuid4())
    _login(client, role="developer")
    with patch("tools.pipeline.blueprint._pdc_get_sop_by_id") as gp, \
         patch("tools.pipeline.blueprint._pdc_approve_sop") as ap, \
         patch("tools.pipeline.blueprint._audit"):
        gp.return_value = _row(id=sop_id, owner="someone-else",
                               approval_status="pending_review")
        resp = client.post(
            f"/devops/api/sops/{sop_id}/approve",
            data=json.dumps({"approved_by": "attacker"}),
            content_type="application/json",
        )
    assert resp.status_code == 403, resp.get_data(as_text=True)
    ap.assert_not_called()


def test_sop_self_approval_denied(client):
    """An elevated approver may NOT approve a SOP they own (separation of duties) -> 403."""
    sop_id = str(uuid.uuid4())
    # Approver session identity == the SOP's owner.
    _login(client, user_id="alice", role="isso")
    with patch("tools.pipeline.blueprint._pdc_get_sop_by_id") as gp, \
         patch("tools.pipeline.blueprint._pdc_approve_sop") as ap, \
         patch("tools.pipeline.blueprint._audit"):
        gp.return_value = _row(id=sop_id, owner="alice",
                               approval_status="pending_review")
        resp = client.post(
            f"/devops/api/sops/{sop_id}/approve",
            data=json.dumps({}),
            content_type="application/json",
        )
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert b"Separation of duties" in resp.data
    ap.assert_not_called(), "self-approval must never reach the approve write"


def test_sop_approve_records_session_identity_not_body(client):
    """approved_by is the SESSION identity even when the body supplies a different user."""
    sop_id = str(uuid.uuid4())
    _login(client, user_id="approver-bob", role="admin")
    with patch("tools.pipeline.blueprint._pdc_get_sop_by_id") as gp, \
         patch("tools.pipeline.blueprint._pdc_approve_sop") as ap, \
         patch("tools.pipeline.blueprint._audit_strict"), \
         patch("tools.pipeline.blueprint._audit"):
        gp.return_value = _row(id=sop_id, owner="carol",
                               approval_status="pending_review")
        ap.return_value = (_row(id=sop_id, approval_status="approved"), None)
        resp = client.post(
            f"/devops/api/sops/{sop_id}/approve",
            data=json.dumps({"approved_by": "spoofed-eve"}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    ap.assert_called_once()
    # approved_by kwarg (or 2nd positional) must be the session identity, never the body.
    _, kwargs = ap.call_args
    recorded = kwargs.get("approved_by")
    assert recorded == "approver-bob", f"approved_by must be session identity, got {recorded!r}"
    assert recorded != "spoofed-eve", "body-supplied approved_by must be ignored"


def test_sop_delete_denied_for_developer(client):
    """A developer cannot delete a SOP (elevated) -> 403, no delete."""
    sop_id = str(uuid.uuid4())
    _login(client, role="developer")
    with patch("tools.pipeline.blueprint._pdc_delete_sop") as dp, \
         patch("tools.pipeline.blueprint._audit"):
        resp = client.delete(f"/devops/api/sops/{sop_id}")
    assert resp.status_code == 403, resp.get_data(as_text=True)
    dp.assert_not_called()


def test_sop_reject_uses_session_identity_not_body(client):
    """SOP reject records the session identity as rejected_by, ignoring the body."""
    sop_id = str(uuid.uuid4())
    _login(client, user_id="isso-dan", role="isso")
    with patch("tools.pipeline.blueprint._pdc_reject_sop") as rp, \
         patch("tools.pipeline.blueprint._audit_strict"), \
         patch("tools.pipeline.blueprint._audit"):
        rp.return_value = (_row(id=sop_id, approval_status="rejected"), None)
        resp = client.post(
            f"/devops/api/sops/{sop_id}/reject",
            data=json.dumps({"reason": "incomplete", "rejected_by": "spoofed"}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    rp.assert_called_once()
    _, kwargs = rp.call_args
    assert kwargs.get("rejected_by") == "isso-dan", "rejected_by must be session identity"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Collab — session-derived identity (ignore body user_id / spoofing)
# ══════════════════════════════════════════════════════════════════════════════

def test_collab_push_ignores_body_user_id(client):
    """Collab push attributes the op to the session identity, not the body user_id."""
    design_id = str(uuid.uuid4())
    # The collab manager instance is created at blueprint-build time; patch the
    # class method so the live instance the blueprint holds is intercepted.
    with patch("tools.canvas.collaboration.CanvasCollabManager.push", return_value=7) as push_mock:
        _login(client, user_id="real-user", role="developer")
        resp = client.post(
            f"/devops/api/collab/{design_id}/push",
            data=json.dumps({"user_id": "spoofed-attacker", "op_type": "add", "data": {}}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    push_mock.assert_called_once()
    args, kwargs = push_mock.call_args
    # signature: push(design_id, user_id, op_type, data)
    passed_user = args[1] if len(args) > 1 else kwargs.get("user_id")
    assert passed_user == "real-user", f"push must use session identity, got {passed_user!r}"
    assert passed_user != "spoofed-attacker", "body user_id must be ignored"


def test_collab_join_ignores_body_user_id(client):
    """Collab join uses the session identity, not a body-supplied user_id."""
    design_id = str(uuid.uuid4())
    with patch("tools.canvas.collaboration.CanvasCollabManager.join", return_value={"ok": True}) as join_mock:
        _login(client, user_id="real-user", role="pm")
        resp = client.post(
            f"/devops/api/collab/{design_id}/join",
            data=json.dumps({"user_id": "spoofed", "user_name": "Mallory"}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    join_mock.assert_called_once()
    args, kwargs = join_mock.call_args
    passed_user = args[1] if len(args) > 1 else kwargs.get("user_id")
    assert passed_user == "real-user", f"join must use session identity, got {passed_user!r}"
