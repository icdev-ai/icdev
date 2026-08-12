# CUI // SP-CTI
"""Minimal Flask app carrying the real ``govcon_api`` blueprint, with RBAC.

The write/sensitive GovCon endpoints are gated by
``@require_role(*GOVCON_WRITE_ROLES)`` (prop-fix-09). ``require_role`` reads
``g.current_user``, which the dashboard populates in an app-level
``before_request`` — so a bare ``Flask(__name__)`` that only registers the
blueprint answers **401 to every request**, and a suite written before the gate
landed fails on all of its endpoint assertions at once with ``resp.get_json()``
returning ``None``.

The fix is to authenticate, never to strip the decorator: ``tools/dashboard/auth.py``
is what enforces it in production, and a test app that routes around it is
testing nothing. :func:`build_govcon_api_app` injects the same ``g.current_user``
contract the middleware does, and takes ``role=None`` so a suite can assert the
unauthenticated 401 and the wrong-role 403 as first-class behaviour.

Full-stack coverage of the gate (real users, real API keys, ``log_auth_event``)
lives in ``tests/test_govcon_rbac.py``; this helper exists so the endpoint
suites can exercise their own endpoint *through* the gate cheaply.
"""
from __future__ import annotations

from typing import Any, Optional

from flask import Flask, g

__all__ = ["build_govcon_api_app", "WRITE_ROLE", "NON_WRITE_ROLE"]

# A role inside GOVCON_WRITE_ROLES, and one outside it. Both are asserted
# against the live tuple in the suites, so a change to the role list surfaces
# as a named failure rather than a mystery 403.
WRITE_ROLE = "bd"
NON_WRITE_ROLE = "viewer"


def build_govcon_api_app(
    *,
    role: Optional[str] = WRITE_ROLE,
    user_id: str = "test-user",
) -> Flask:
    """Flask app with the govcon_api blueprint.

    ``role`` is the role of the authenticated caller; pass ``None`` to leave the
    request unauthenticated (``g.current_user`` unset), which is what the
    dashboard looks like to a caller with no session or API key.
    """
    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)

    if role is not None:
        user: dict[str, Any] = {"id": user_id, "role": role, "email": f"{user_id}@example.mil"}

        @flask_app.before_request
        def _inject_current_user():  # noqa: ANN202 - Flask hook
            g.current_user = user

    return flask_app
