# CUI // SP-CTI
"""Meta-test / recurrence guard for Security Design Canvas auth — shx-auth-01.

This is how 26 ZIG routes shipped without ``@sc_login_required``: nothing
asserted that *every* security_canvas view is authentication-wrapped. These
tests close that gap two ways:

  1. Structural: every view function registered by ``create_security_blueprint``
     carries the ``_sc_auth_wrapped`` attribute that ``sc_login_required`` stamps
     onto its wrapper. A route that forgets the decorator fails here.

  2. Behavioral: with no session and no bypass env vars, every GET url_rule
     returns 401 or 302 — never 200. An unauthenticated caller must never reach
     a handler.
"""
from __future__ import annotations

import re

import pytest


@pytest.fixture()
def app(monkeypatch):
    """Flask app with ONLY the security_canvas blueprint, no auth bypass."""
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)
    monkeypatch.setenv("ICDEV_SECURITY_ENABLED", "true")

    from flask import Flask
    from tools.security_canvas.blueprint import create_security_blueprint

    flask_app = Flask(__name__, template_folder="../tools/dashboard/templates")
    flask_app.config.update(TESTING=True, SECRET_KEY="test-secret", WTF_CSRF_ENABLED=False)

    bp = create_security_blueprint()
    assert bp is not None, "create_security_blueprint() returned None"
    flask_app.register_blueprint(bp, url_prefix="/security")
    return flask_app


def _security_endpoints(flask_app):
    return {
        name: view
        for name, view in flask_app.view_functions.items()
        if name.startswith("security_canvas.")
    }


def test_every_security_view_is_auth_wrapped(app):
    """Structural guard: every security_canvas view carries _sc_auth_wrapped."""
    views = _security_endpoints(app)
    assert views, "no security_canvas view functions registered"

    unwrapped = [
        name for name, view in views.items()
        if not getattr(view, "_sc_auth_wrapped", False)
    ]
    assert not unwrapped, (
        "These security_canvas routes are missing @sc_login_required "
        f"(no _sc_auth_wrapped attribute): {sorted(unwrapped)}"
    )


def _dummy_path(rule: str) -> str:
    """Substitute dummy values for any <converter:name> / <name> path params."""
    return re.sub(r"<[^>]+>", "1", rule)


def test_no_get_route_returns_200_without_auth(app):
    """Behavioral guard: unauthenticated GET must be 401 or 302 — never 200."""
    security_endpoints = set(_security_endpoints(app).keys())

    tested = 0
    with app.test_client() as client:
        for rule in app.url_map.iter_rules():
            if rule.endpoint not in security_endpoints:
                continue
            if "GET" not in (rule.methods or set()):
                continue
            path = _dummy_path(rule.rule)
            resp = client.get(path)
            tested += 1
            assert resp.status_code in (301, 302, 401), (
                f"Unauthenticated GET {path} (endpoint {rule.endpoint}) returned "
                f"{resp.status_code}; expected 401 or a redirect to /login"
            )
    assert tested > 0, "no GET routes were exercised"
