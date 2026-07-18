# CUI // SP-CTI
"""API integration tests for the HITL Workflow Flask blueprint."""
from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture()
def client(tmp_path):
    """Create a Flask test client with the wf blueprint mounted."""
    os.environ["ICDEV_HITL_ENABLED"] = "true"
    os.environ["ICDEV_DEV_MODE"] = "true"  # enables X-Dev-User header auth bypass

    try:
        from tools.workflow_hitl.blueprint import create_wf_blueprint
    except ImportError as exc:
        pytest.skip(f"wf blueprint not importable: {exc}")

    from flask import Flask, g, request
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    @app.before_request
    def _fake_auth():
        # Simulate the dashboard auth middleware: an X-Test-Role header stands in
        # for a logged-in user so @require_role on the instance-mutation routes
        # sees g.current_user. Absent header == anonymous (g.current_user unset).
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-test", "role": role, "tenant_id": "t-test"}

    bp = create_wf_blueprint()
    app.register_blueprint(bp, url_prefix="/api/v1/wf")

    with app.test_client() as c:
        yield c


def _json(resp):
    return json.loads(resp.data)


# ── feature flag gate ─────────────────────────────────────────────────────────

class TestFeatureFlag:
    def test_disabled_returns_503(self, tmp_path):
        os.environ["ICDEV_HITL_ENABLED"] = "false"
        try:
            from tools.workflow_hitl.blueprint import create_wf_blueprint
            from flask import Flask
            app = Flask(__name__)
            app.config["TESTING"] = True
            bp = create_wf_blueprint()
            app.register_blueprint(bp, url_prefix="/api/v1/wf")
            with app.test_client() as c:
                resp = c.get("/api/v1/wf/templates")
                assert resp.status_code == 503
        finally:
            os.environ["ICDEV_HITL_ENABLED"] = "true"


# ── templates ─────────────────────────────────────────────────────────────────

class TestTemplateRoutes:
    def test_list_templates(self, client):
        with patch("tools.workflow_hitl.blueprint.template_manager") as m:
            m.list_templates.return_value = []
            resp = client.get("/api/v1/wf/templates")
            assert resp.status_code == 200

    def test_get_template_not_found(self, client):
        with patch("tools.workflow_hitl.blueprint.template_manager") as m:
            m.get.return_value = None
            resp = client.get("/api/v1/wf/templates/nonexistent")
            assert resp.status_code == 404

    def test_create_template(self, client):
        with patch("tools.workflow_hitl.blueprint.template_manager") as m:
            m.create.return_value = "wft-abc123"
            resp = client.post(
                "/api/v1/wf/templates",
                json={"name": "Test Template"},
                content_type="application/json",
            )
            assert resp.status_code in (200, 201)
            data = _json(resp)
            assert "id" in data or "template_id" in data

    def test_fork_template(self, client):
        with patch("tools.workflow_hitl.blueprint.template_manager") as m:
            m.get.return_value = {"id": "t1", "name": "T"}
            m.fork.return_value = "wft-fork001"
            resp = client.post(
                "/api/v1/wf/templates/t1/fork",
                json={"name": "Forked Template"},
                content_type="application/json",
            )
            assert resp.status_code in (200, 201)


# ── teams ─────────────────────────────────────────────────────────────────────

_AUTH = {"headers": {"X-Dev-User": "test-user"}}


class TestTeamRoutes:
    def test_list_teams(self, client):
        with patch("tools.workflow_hitl.blueprint.team_manager") as m:
            m.list_teams.return_value = []
            resp = client.get("/api/v1/wf/teams", **_AUTH)
            assert resp.status_code == 200
            data = _json(resp)
            # endpoint wraps in {"teams": [], "count": 0}
            assert "teams" in data or isinstance(data, list)

    def test_create_team(self, client):
        with patch("tools.workflow_hitl.blueprint.team_manager") as m:
            m.create_team.return_value = "wft-001"
            resp = client.post(
                "/api/v1/wf/teams",
                json={"name": "Alpha Team"},
                content_type="application/json",
                **_AUTH,
            )
            assert resp.status_code in (200, 201)

    def test_get_team_not_found(self, client):
        with patch("tools.workflow_hitl.blueprint.team_manager") as m:
            m.get_team.return_value = None
            resp = client.get("/api/v1/wf/teams/nonexistent", **_AUTH)
            assert resp.status_code == 404

    def test_add_member(self, client):
        team = {"id": "team-1", "name": "Squad"}
        with patch("tools.workflow_hitl.blueprint.team_manager") as m:
            m.get_team.return_value = team
            m.add_member.return_value = "mem-001"
            resp = client.post(
                "/api/v1/wf/teams/team-1/members",
                json={"user_id": "user-42", "role_label": "reviewer"},
                content_type="application/json",
                **_AUTH,
            )
            assert resp.status_code in (200, 201)

    def test_remove_member(self, client):
        team = {"id": "team-1", "name": "Squad"}
        with patch("tools.workflow_hitl.blueprint.team_manager") as m:
            m.get_team.return_value = team
            m.remove_member.return_value = True
            resp = client.delete("/api/v1/wf/teams/team-1/members/user-42", **_AUTH)
            assert resp.status_code in (200, 204)


# ── instances ─────────────────────────────────────────────────────────────────

class TestInstanceRoutes:
    def test_list_instances(self, client):
        with patch("tools.workflow_hitl.blueprint.get_connection") as mock_conn:
            mock_c = MagicMock()
            mock_c.__enter__ = lambda s: s
            mock_c.__exit__ = MagicMock(return_value=False)
            mock_c.execute.return_value.fetchall.return_value = []
            mock_conn.return_value = mock_c
            resp = client.get("/api/v1/wf/instances")
            assert resp.status_code == 200

    def test_approve_instance(self, client):
        with patch("tools.workflow_hitl.blueprint.WorkflowEngine") as MockEngine:
            eng = MockEngine.return_value
            eng.get_status.return_value = None  # triggers 404
            resp = client.post(
                "/api/v1/wf/instances/wfi-001/approve",
                json={"decision": "approve", "rating": 5, "submitted_by": "user-1"},
                content_type="application/json",
                headers={"X-Dev-User": "test-user", "X-Test-Role": "pm"},
            )
            assert resp.status_code in (200, 201, 400, 404)

    def test_kickback_instance(self, client):
        with patch("tools.workflow_hitl.blueprint.WorkflowEngine") as MockEngine:
            eng = MockEngine.return_value
            eng.get_status.return_value = None
            resp = client.post(
                "/api/v1/wf/instances/wfi-001/kickback",
                json={"kickback_reason": "Incomplete spec", "submitted_by": "user-1"},
                content_type="application/json",
                headers={"X-Dev-User": "test-user", "X-Test-Role": "pm"},
            )
            assert resp.status_code in (200, 201, 400, 404)


# ── coverage ──────────────────────────────────────────────────────────────────

class TestCoverageRoute:
    def test_coverage_returns_dict(self, client):
        with patch("tools.workflow_hitl.blueprint.get_connection") as mock_conn:
            mock_c = MagicMock()
            mock_c.__enter__ = lambda s: s
            mock_c.__exit__ = MagicMock(return_value=False)
            # fetchone()[0] must return an int for COUNT(*) queries
            mock_c.execute.return_value.fetchone.return_value = (0,)
            mock_c.execute.return_value.fetchall.return_value = []
            mock_conn.return_value = mock_c
            resp = client.get("/api/v1/wf/coverage")
            assert resp.status_code == 200


# ── external steps ────────────────────────────────────────────────────────────

class TestExternalStepRoutes:
    def test_list_external_steps(self, client):
        with patch("tools.workflow_hitl.blueprint.get_connection") as mock_conn:
            mock_c = MagicMock()
            mock_c.__enter__ = lambda s: s
            mock_c.__exit__ = MagicMock(return_value=False)
            mock_c.execute.return_value.fetchall.return_value = []
            mock_conn.return_value = mock_c
            resp = client.get("/api/v1/wf/external")
            assert resp.status_code == 200

    def test_webhook_complete_bad_token_returns_400_or_403(self, client):
        with patch("tools.workflow_hitl.blueprint.external_steps") as m:
            m.verify_webhook_token.return_value = False
            resp = client.post(
                "/api/v1/wf/external/step-001/complete?token=badtoken",
                content_type="application/json",
            )
            assert resp.status_code in (400, 403, 404)


# ── document templates ────────────────────────────────────────────────────────

class TestDocumentRoutes:
    def test_list_doc_templates(self, client):
        with patch("tools.workflow_hitl.blueprint.document_manager") as m:
            m.list_templates.return_value = []
            resp = client.get("/api/v1/wf/doc-templates")
            assert resp.status_code == 200

    def test_submit_document(self, client):
        # Route is /approvals/<approval_id>/documents (POST)
        # The route first queries wf_approvals, so we also need to mock get_connection
        with patch("tools.workflow_hitl.blueprint.document_manager") as m:
            m.submit_document.return_value = "wfds-001"
            with patch("tools.workflow_hitl.blueprint.get_connection") as mock_conn:
                mock_c = MagicMock()
                mock_c.__enter__ = lambda s: s
                mock_c.__exit__ = MagicMock(return_value=False)
                mock_c.execute.return_value.fetchone.return_value = {
                    "id": "wfa-001", "instance_id": "wfi-001", "stage": "review"
                }
                mock_conn.return_value = mock_c
                resp = client.post(
                    "/api/v1/wf/approvals/wfa-001/documents",
                    json={
                        "doc_template_id": "dt-001",
                        "stage": "review",
                        "submitted_by": "user-1",
                        "submission_json": {"checked": True},
                    },
                    content_type="application/json",
                    **_AUTH,
                )
                assert resp.status_code in (200, 201)
