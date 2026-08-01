# CUI // SP-CTI
"""nav-sec-05 — Strategos + ZIG state-changing routes require an approver role.

Before this fix the *registered* Strategos blueprint (``apps/strategos/blueprint.py``)
carried ZERO ``@require_role`` decorators, so any authenticated user — including a
lowest-privilege ``developer`` whose own product may be under review — could
HITL-approve/resolve/bulk/delete, approve or delete an OPORD, advance/set-phase/
delete an F3EAD target, approve an intel brief, or resolve a CCIR trigger. The two
ZIG mutating endpoints in ``tools/security_canvas/blueprint.py`` (capability-status
PATCH and the global ``/api/zig/assess`` POST) relied on ``sc_login_required``
alone, i.e. any authenticated session.

Each of those routes is now hard-gated with the shared dashboard ``@require_role``
decorator (401 anonymous, 403 wrong role, allowed for approver-class roles), and the
recorded approver/actor identity comes from the resolved session user
(``g.current_user``) — never from a spoofable request-body field.
"""
from __future__ import annotations

import os

import pytest


# Gated Strategos routes, grouped by (method, path). Each is an
# approval / resolution / workflow-advance / authoritative-deletion action.
STRATEGOS_ROUTES = [
    ("POST", "/strategos/hitl/action/1"),            # HITL page decision (APPROVE/REJECT)
    ("POST", "/api/strategos/hitl/i1/resolve"),      # HITL resolve
    ("POST", "/api/strategos/hitl/bulk"),            # HITL bulk resolve
    ("POST", "/api/strategos/hitl/delete"),          # HITL bulk delete
    ("PATCH", "/api/strategos/briefs/b1/approve"),   # brief approval
    ("POST", "/api/strategos/ccir/triggers/e1/resolve"),  # CCIR trigger resolution
    ("POST", "/api/strategos/opord/o1/approve"),     # OPORD approval
    ("DELETE", "/api/strategos/opord/o1"),           # OPORD deletion
    ("POST", "/api/strategos/f3ead/targets/t1/advance"),  # F3EAD workflow advance
    ("PATCH", "/api/strategos/f3ead/targets/t1/phase"),   # F3EAD set-phase
    ("DELETE", "/api/strategos/f3ead/targets/t1"),   # F3EAD target deletion
]

# The two in-scope ZIG mutating endpoints.
ZIG_ROUTES = [
    ("PATCH", "/security/api/zig/capabilities/cap-1"),
    ("POST", "/security/api/zig/assess"),
]


def _dispatch(client, method, path, headers=None):
    return client.open(
        path,
        method=method,
        json={},
        content_type="application/json",
        headers=headers or {},
    )


@pytest.fixture()
def strategos_client():
    # No auth bypass / env key — role enforcement must stand on its own.
    for var in ("ICDEV_AUTH_BYPASS", "ICDEV_DASHBOARD_API_KEY", "ICDEV_DASHBOARD_DEV_AUTOLOGIN"):
        os.environ.pop(var, None)

    try:
        from apps.strategos.blueprint import (
            create_strategos_api_blueprint,
            create_strategos_blueprint,
        )
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"strategos blueprint not importable: {exc}")

    from flask import Flask, g, request, session

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    @app.before_request
    def _fake_auth():
        # Stand in for the dashboard auth middleware: X-Test-Role -> logged-in
        # user with that role (both g.current_user and the session cookie, so
        # both @require_role and any session check are satisfied). No header ==
        # anonymous (g.current_user unset).
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-session", "role": role, "tenant_id": "t"}
            session["user_id"] = "u-session"

    app.register_blueprint(create_strategos_blueprint(), url_prefix="/strategos")
    app.register_blueprint(create_strategos_api_blueprint(), url_prefix="/api/strategos")
    with app.test_client() as c:
        yield c


@pytest.fixture()
def zig_client():
    os.environ.pop("ICDEV_AUTH_BYPASS", None)
    os.environ.pop("ICDEV_DASHBOARD_API_KEY", None)
    os.environ["ICDEV_SECURITY_ENABLED"] = "true"

    try:
        from tools.security_canvas.blueprint import create_security_blueprint
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"security_canvas blueprint not importable: {exc}")

    bp = create_security_blueprint()
    if bp is None:  # pragma: no cover
        pytest.skip("security_canvas blueprint disabled")

    from flask import Flask, g, request, session

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-session", "role": role, "tenant_id": "t"}
            session["user_id"] = "u-session"

    app.register_blueprint(bp, url_prefix="/security")
    with app.test_client() as c:
        yield c


# ── deny cases (the core segregation-of-duties fix) ───────────────────────────

@pytest.mark.parametrize("method,path", STRATEGOS_ROUTES)
def test_strategos_anonymous_is_401(strategos_client, method, path):
    resp = _dispatch(strategos_client, method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", STRATEGOS_ROUTES)
def test_strategos_developer_is_403(strategos_client, method, path):
    # A developer must NOT be able to approve / resolve / advance / delete
    # authoritative Strategos state.
    resp = _dispatch(strategos_client, method, path, {"X-Test-Role": "developer"})
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", ZIG_ROUTES)
def test_zig_anonymous_is_401(zig_client, method, path):
    resp = _dispatch(zig_client, method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", ZIG_ROUTES)
def test_zig_developer_is_403(zig_client, method, path):
    resp = _dispatch(zig_client, method, path, {"X-Test-Role": "developer"})
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


# ── allow cases (approver-class roles clear the gate) ─────────────────────────

@pytest.mark.parametrize("method,path", STRATEGOS_ROUTES)
def test_strategos_isso_not_blocked(strategos_client, method, path):
    # An approver-class role clears @require_role; the request proceeds to the
    # handler (which may 4xx on a missing record / uninitialised table / bad
    # payload, or 2xx). The point is that it is NOT rejected by the RBAC gate.
    resp = _dispatch(strategos_client, method, path, {"X-Test-Role": "isso"})
    assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", ZIG_ROUTES)
def test_zig_admin_not_blocked(zig_client, method, path):
    resp = _dispatch(zig_client, method, path, {"X-Test-Role": "admin"})
    assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}"


# ── recorded actor identity comes from the session, not the body ──────────────

def test_hitl_resolve_records_session_actor_not_body(strategos_client):
    """The ``resolved_by`` written for a HITL resolve must be the resolved
    session user (g.current_user), never a body-supplied field."""
    from unittest.mock import MagicMock, patch

    captured = {}

    class _FakeConn:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            r = MagicMock()
            r.rowcount = 1
            return r

        def commit(self):
            pass

        def close(self):
            pass

    with patch("apps.strategos.blueprint.get_connection", return_value=_FakeConn()):
        resp = strategos_client.post(
            "/api/strategos/hitl/i1/resolve",
            json={"decision": "approve", "resolved_by": "spoofed-attacker"},
            content_type="application/json",
            headers={"X-Test-Role": "pm"},
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    # params = (decision, now, resolved_by, item_id)
    assert "u-session" in captured["params"], captured["params"]
    assert "spoofed-attacker" not in captured["params"], captured["params"]
