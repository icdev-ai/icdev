# CUI // SP-CTI
"""nav-sec-04 — HITL governance-gate mutation routes require an approver role.

Before this fix ``tools/workflow_hitl/blueprint.py`` had ZERO role gates: the
instance-mutation routes (approve / kickback / escalate / cancel) only checked
that *some* authenticated user was present, so any role — including
``developer`` (whose own work may be under review) — could approve or kick back
a governance gate and be recorded as the approver, defeating segregation of
duties for the Human-In-The-Loop layer.

Each mutation route is now hard-gated with the established dashboard
``@require_role`` decorator against ``_WF_APPROVAL_ROLES`` (401 anonymous,
403 wrong role, allowed for reviewer/approver-class roles). The recorded
approver identity comes from the resolved session user (``g.current_user``),
never from a spoofable request-body field.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

MUTATION_ROUTES = [
    "/api/v1/wf/instances/wfi-001/approve",
    "/api/v1/wf/instances/wfi-001/kickback",
    "/api/v1/wf/instances/wfi-001/escalate",
    "/api/v1/wf/instances/wfi-001/cancel",
]


@pytest.fixture()
def client():
    os.environ["ICDEV_HITL_ENABLED"] = "true"
    # Dev-mode OFF so the header-based bypass is not what grants access here —
    # role enforcement must stand on its own via g.current_user.
    os.environ["ICDEV_DEV_MODE"] = "false"

    try:
        from tools.workflow_hitl.blueprint import create_wf_blueprint
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"wf blueprint not importable: {exc}")

    from flask import Flask, g, request

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    @app.before_request
    def _fake_auth():
        # Stand in for the dashboard auth middleware: X-Test-Role -> logged-in
        # user with that role. No header == anonymous (g.current_user unset).
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-session", "role": role, "tenant_id": "t"}

    app.register_blueprint(create_wf_blueprint(), url_prefix="/api/v1/wf")
    with app.test_client() as c:
        yield c


# ── deny cases ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", MUTATION_ROUTES)
def test_anonymous_is_401(client, path):
    resp = client.post(path, json={}, content_type="application/json")
    assert resp.status_code == 401, f"{path} -> {resp.status_code}"


@pytest.mark.parametrize("path", MUTATION_ROUTES)
def test_developer_role_is_403(client, path):
    # This is the core segregation-of-duties fix: a developer must NOT be able
    # to approve / kick back / escalate / cancel a governance gate.
    resp = client.post(
        path,
        json={},
        content_type="application/json",
        headers={"X-Test-Role": "developer"},
    )
    assert resp.status_code == 403, f"{path} -> {resp.status_code}"


# ── allow cases ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["admin", "pm", "isso", "co", "cor", "reviewer"])
def test_approver_role_passes_the_gate(client, role):
    # An approver-class role clears @require_role; the request then proceeds to
    # instance resolution (mocked to "not found" -> 404). The point is that it
    # is NOT rejected by the RBAC gate (401/403).
    with patch("tools.workflow_hitl.blueprint.WorkflowEngine") as MockEngine:
        MockEngine.return_value.get_status.return_value = None
        resp = client.post(
            "/api/v1/wf/instances/wfi-001/approve",
            json={"decision": "approve"},
            content_type="application/json",
            headers={"X-Test-Role": role},
        )
    assert resp.status_code not in (401, 403), f"role={role} -> {resp.status_code}"
    assert resp.status_code == 404


# ── recorded approver identity comes from the session, not the body ───────────

def test_approver_recorded_from_session_not_body(client):
    """The submitter written to the feedback/approval record must be the
    resolved session user (g.current_user), never a body-supplied field."""
    status = {
        "approvals": [{"id": "wfa-1", "status": "pending"}],
    }
    with patch("tools.workflow_hitl.blueprint.WorkflowEngine") as MockEngine, patch(
        "tools.workflow_hitl.blueprint.feedback_mod"
    ) as mock_fb:
        MockEngine.return_value.get_status.return_value = status
        mock_fb.submit_feedback.return_value = "wff-1"
        resp = client.post(
            "/api/v1/wf/instances/wfi-001/approve",
            # Attacker attempts to spoof the approver via the request body.
            json={"decision": "approve", "submitted_by": "spoofed-attacker"},
            content_type="application/json",
            headers={"X-Test-Role": "pm"},
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert mock_fb.submit_feedback.called
    _, kwargs = mock_fb.submit_feedback.call_args
    assert kwargs.get("submitted_by") == "u-session"
    assert kwargs.get("submitted_by") != "spoofed-attacker"
