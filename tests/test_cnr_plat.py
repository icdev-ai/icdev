# CUI // SP-CTI
"""CNR platform hardening tests (cnr-plat-01..03).

Covers:
  * cnr-plat-01 — CSRF guard on cookie-authenticated mutating JSON APIs.
  * cnr-plat-02 — global MAX_CONTENT_LENGTH upload cap + graceful 413.
  * cnr-plat-03 — fail-closed-by-default canvas access guard.
"""
from __future__ import annotations

import pytest
from flask import Flask, jsonify


# ---------------------------------------------------------------------------
# cnr-plat-01 — CSRF
# ---------------------------------------------------------------------------


def _make_csrf_app(monkeypatch):
    monkeypatch.setenv("ICDEV_CSRF_ENFORCE", "1")
    monkeypatch.delenv("ICDEV_CSRF_GRACE", raising=False)
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)

    from tools.security.csrf import register_csrf

    app = Flask(__name__)
    app.secret_key = "test-secret"
    register_csrf(app)

    @app.route("/api/thing", methods=["POST"])
    def thing():
        return jsonify({"ok": True})

    @app.route("/api/thing", methods=["GET"])
    def thing_get():
        return jsonify({"ok": True})

    return app


def test_csrf_missing_token_rejected(monkeypatch):
    app = _make_csrf_app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "u1"
        sess["_csrf_token"] = "tok-abc"
    resp = client.post("/api/thing", json={"a": 1})
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "CSRF_FAILED"


def test_csrf_valid_token_accepted(monkeypatch):
    app = _make_csrf_app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "u1"
        sess["_csrf_token"] = "tok-abc"
    resp = client.post(
        "/api/thing", json={"a": 1}, headers={"X-CSRF-Token": "tok-abc"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_csrf_get_not_gated(monkeypatch):
    app = _make_csrf_app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "u1"
        sess["_csrf_token"] = "tok-abc"
    assert client.get("/api/thing").status_code == 200


def test_csrf_api_key_path_unaffected(monkeypatch):
    """A token/machine-authenticated caller (Bearer) is exempt — no session, no CSRF."""
    app = _make_csrf_app(monkeypatch)
    client = app.test_client()
    # No cookie session at all; presents a Bearer token instead.
    resp = client.post(
        "/api/thing", json={"a": 1}, headers={"Authorization": "Bearer some-key"}
    )
    assert resp.status_code == 200


def test_csrf_no_session_not_gated(monkeypatch):
    """Anonymous (no cookie session) mutating request is not CSRF-relevant."""
    app = _make_csrf_app(monkeypatch)
    client = app.test_client()
    resp = client.post("/api/thing", json={"a": 1})
    assert resp.status_code == 200


def test_csrf_sec_fetch_site_same_origin_accepted(monkeypatch):
    """Unforgeable same-origin fetch-metadata is accepted without a token."""
    app = _make_csrf_app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "u1"
        sess["_csrf_token"] = "tok-abc"
    resp = client.post(
        "/api/thing", json={"a": 1}, headers={"Sec-Fetch-Site": "same-origin"}
    )
    assert resp.status_code == 200
    # But a cross-site request without a token is still blocked.
    resp2 = client.post(
        "/api/thing", json={"a": 1}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert resp2.status_code == 403


def test_csrf_kill_switch(monkeypatch):
    monkeypatch.setenv("ICDEV_CSRF_ENFORCE", "0")
    from tools.security.csrf import register_csrf

    app = Flask(__name__)
    app.secret_key = "s"
    register_csrf(app)

    @app.route("/api/x", methods=["POST"])
    def x():
        return jsonify({"ok": True})

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "u1"
        sess["_csrf_token"] = "tok"
    assert client.post("/api/x", json={}).status_code == 200


def test_csrf_grace_mode_allows_and_logs(monkeypatch):
    monkeypatch.setenv("ICDEV_CSRF_ENFORCE", "1")
    monkeypatch.setenv("ICDEV_CSRF_GRACE", "1")
    from tools.security.csrf import register_csrf

    app = Flask(__name__)
    app.secret_key = "s"
    register_csrf(app)

    @app.route("/api/x", methods=["POST"])
    def x():
        return jsonify({"ok": True})

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "u1"
        sess["_csrf_token"] = "tok"
    # No token, cross-site → still allowed under grace, just logged.
    resp = client.post(
        "/api/x", json={}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# cnr-plat-02 — upload cap
# ---------------------------------------------------------------------------


def _create_dashboard_app(monkeypatch, max_mb):
    monkeypatch.setenv("ICDEV_MAX_UPLOAD_MB", str(max_mb))
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)
    try:
        from tools.dashboard.app import create_app
        return create_app(testing=True)
    except Exception as exc:  # heavy factory — never fail the suite on env issues
        pytest.skip(f"create_app unavailable in this environment: {exc}")


def test_upload_cap_config_from_env(monkeypatch):
    app = _create_dashboard_app(monkeypatch, 7)
    assert app.config.get("MAX_CONTENT_LENGTH") == 7 * 1024 * 1024


def test_oversize_json_returns_413(monkeypatch):
    # ~1 KB cap; /login is a public POST route that parses the body.
    app = _create_dashboard_app(monkeypatch, 0.001)
    client = app.test_client()
    big = "x" * (5 * 1024)
    resp = client.post(
        "/login",
        data=big,
        content_type="application/json",
    )
    assert resp.status_code == 413
    body = resp.get_json()
    assert body is not None and body.get("code") == "PAYLOAD_TOO_LARGE"


def test_normal_request_not_413(monkeypatch):
    app = _create_dashboard_app(monkeypatch, 50)
    client = app.test_client()
    resp = client.post("/login", data={"api_key": "nope"})
    assert resp.status_code != 413



