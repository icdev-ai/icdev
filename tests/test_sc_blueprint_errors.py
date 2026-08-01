# CUI // SP-CTI
"""Error-handling hardening for the Security Design Canvas blueprint — shx-hyg-01.

Covers the three surgical improvements made to
``tools/security_canvas/blueprint.py``:

  1. ``_audit`` no longer swallows audit-write failures silently. A failed
     INSERT into ``sc_audit`` is logged as a warning (non-repudiation), but the
     request still succeeds — an audit outage must never 500 a page.
  2. Error responses carry explicit HTTP status codes (representative 404 / 400
     lookups are asserted here).
  3. Mutating routes that require body fields reject a body that fails to parse
     as JSON with ``400 {"error": "invalid JSON body"}`` via the shared
     ``_require_json()`` helper, instead of silently coercing to ``{}``.

The fixture pattern mirrors the sibling SDC route tests (ICDEV_AUTH_BYPASS +
init_db + a security_canvas-only Flask app).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest


def _build_app():
    """Build a Flask app hosting only the security_canvas blueprint."""
    from flask import Flask
    from tools.security_canvas.blueprint import create_security_blueprint
    from tools.security_canvas.db.init_db import init_db

    try:
        init_db()
    except Exception:
        pass

    os.environ.setdefault("ICDEV_SECURITY_ENABLED", "true")

    flask_app = Flask(__name__, template_folder="../tools/dashboard/templates")
    flask_app.config.update(TESTING=True, SECRET_KEY="test-secret", WTF_CSRF_ENABLED=False)

    bp = create_security_blueprint()
    assert bp is not None, "create_security_blueprint() returned None"
    flask_app.register_blueprint(bp, url_prefix="/security")
    return flask_app


@pytest.fixture(autouse=True)
def _bypass_auth(monkeypatch):
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "true")
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)


@pytest.fixture()
def app():
    return _build_app()


# ── Task 1: audit-write failure is logged, not swallowed, and never 500s ──────

class _AuditFailProxy:
    """Wraps a real connection; raises on any ``INSERT INTO sc_audit`` write.

    All other SQL passes straight through to the real connection so the route's
    primary work still succeeds — only the audit write fails.
    """

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def execute(self, sql, *args, **kwargs):
        if "INSERT INTO sc_audit" in sql:
            raise RuntimeError("simulated audit write failure")
        return self._real.execute(sql, *args, **kwargs)

    def __enter__(self):
        object.__setattr__(self, "_real", self._real.__enter__())
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_audit_write_failure_logs_warning_and_does_not_500(monkeypatch):
    """A failed audit INSERT is logged (warning) but the request still returns 201."""
    import tools.security_canvas.blueprint as bp_mod
    from tools.security_canvas.db import init_db as init_db_mod

    # Ensure schema exists BEFORE we patch get_connection.
    try:
        init_db_mod.init_db()
    except Exception:
        pass

    real_get_connection = init_db_mod.get_connection

    def _patched_get_connection(*a, **k):
        return _AuditFailProxy(real_get_connection(*a, **k))

    # Patch BEFORE the blueprint is created so its closure captures the proxy.
    monkeypatch.setattr(init_db_mod, "get_connection", _patched_get_connection)

    fake_logger = MagicMock()
    monkeypatch.setattr(bp_mod, "logger", fake_logger)

    app = _build_app()

    with app.test_client() as client:
        resp = client.post("/security/api/designs", json={"name": "Audit Fail Design"})

    # Request must succeed despite the audit outage.
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert resp.get_json().get("id")

    # And the failure must have been surfaced as a warning.
    warn_msgs = [c.args[0] for c in fake_logger.warning.call_args_list if c.args]
    assert any("sc_audit write failed" in m for m in warn_msgs), (
        f"expected an 'sc_audit write failed' warning; saw: {warn_msgs}"
    )


# ── Task 2: representative error routes carry explicit status codes ───────────

def test_get_missing_design_returns_404(app):
    with app.test_client() as client:
        resp = client.get("/security/api/designs/does-not-exist-xyz")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_get_missing_runbook_returns_404(app):
    with app.test_client() as client:
        resp = client.get("/security/api/runbooks/no-such-runbook")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_missing_required_field_returns_400(app):
    """Empty body stays lenient ({}) so the handler's own required-field 400 fires."""
    with app.test_client() as client:
        resp = client.post("/security/api/iac/scan-text", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "content is required"


def test_zig_targets_missing_fields_returns_400(app):
    with app.test_client() as client:
        resp = client.post("/security/api/zig/targets", json={})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False


# ── Task 3: mutating routes reject un-parseable JSON bodies with 400 ──────────

@pytest.mark.parametrize(
    "path",
    [
        "/security/api/iac/scan-text",
        "/security/api/iac/scan-file",
        "/security/api/iac/scan-directory",
        "/security/api/import/threat-model",
        "/security/api/zig/targets",
        "/security/api/zig/targets/test-app/ingest",
        "/security/api/iqe-query",
        "/security/api/twin/test-design/chat-delta",
        "/security/api/sops",
    ],
)
def test_mutating_route_rejects_invalid_json_with_400(app, path):
    """A non-empty body that is not valid JSON → 400 {'error': 'invalid JSON body'}."""
    with app.test_client() as client:
        resp = client.post(path, data="{not valid json", content_type="application/json")
    assert resp.status_code == 400, f"{path} returned {resp.status_code}"
    assert resp.get_json()["error"] == "invalid JSON body"


# ── Task 3 corollary: a valid mutating request still succeeds end-to-end ──────

def test_valid_create_design_succeeds_end_to_end(app):
    with app.test_client() as client:
        resp = client.post("/security/api/designs", json={"name": "Happy Path Design"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["id"]
    assert body["name"] == "Happy Path Design"

    # And it is retrievable — proving the write actually landed.
    with app.test_client() as client:
        got = client.get(f"/security/api/designs/{body['id']}")
    assert got.status_code == 200
    assert got.get_json()["id"] == body["id"]
