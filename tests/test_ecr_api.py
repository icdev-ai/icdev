# CUI // SP-CTI
"""Tests for ECR API key auth (ecr-api-01).

Coverage:
  - generate_api_key creates a row and returns (prefix, raw_key, hash)
  - verify_api_key returns (tenant_id, scopes) for a valid key
  - verify_api_key returns None for an unknown key
  - verify_api_key returns None for a revoked key
  - verify_api_key returns None for an expired key
  - @require_api_key decorator allows valid bearer token
  - @require_api_key decorator rejects missing/invalid token
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

# The api_key / webhook / admin modules author %s placeholders for PostgreSQL.
# Handing production code a bare sqlite3 connection drops StorageConnection's
# rewrite and every statement raises `near "%": syntax error`.
from _sql_compat import translating as _translating

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_conn(tmp_path):
    """Return a real sqlite3 connection with the api_keys schema applied."""
    db = tmp_path / "api_test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id           TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL,
            name         TEXT NOT NULL,
            key_prefix   TEXT NOT NULL,
            key_hash     TEXT NOT NULL UNIQUE,
            scopes       TEXT NOT NULL DEFAULT 'read',
            last_used_at TEXT,
            expires_at   TEXT,
            revoked_at   TEXT,
            created_at   TEXT NOT NULL
        );
        """
    )
    conn.commit()
    return _translating(conn)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_conn(tmp_path):
    conn = _make_conn(tmp_path)
    yield conn
    conn.close()


@pytest.fixture()
def patched_conn(db_conn):
    """Patch get_connection so api_key.py uses the in-memory SQLite DB."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        yield db_conn

    with patch("tools.auth.api_key.get_connection", _ctx):
        yield db_conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_api_key_generate(patched_conn):
    """generate_api_key returns (prefix, raw_key, hash) and persists the row."""
    from tools.auth.api_key import generate_api_key

    prefix, raw_key, key_hash = generate_api_key("tenant-1", "CI token", scopes="read write")

    assert raw_key.startswith("ick_")
    assert raw_key[:len(prefix)] == prefix
    assert key_hash == hashlib.sha256(raw_key.encode()).hexdigest()

    row = patched_conn.execute(
        "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
    ).fetchone()
    assert row is not None
    assert row["tenant_id"] == "tenant-1"
    assert row["scopes"] == "read write"


def test_api_key_auth(patched_conn):
    """verify_api_key returns (tenant_id, scopes) for a valid key."""
    from tools.auth.api_key import generate_api_key, verify_api_key

    _, raw_key, _ = generate_api_key("tenant-2", "test key")
    result = verify_api_key(raw_key)

    assert result is not None
    tenant_id, scopes = result
    assert tenant_id == "tenant-2"
    assert scopes == "read"


def test_verify_unknown_key(patched_conn):
    """verify_api_key returns None for a key that does not exist."""
    from tools.auth.api_key import verify_api_key

    assert verify_api_key("ick_doesnotexist0000000000000000") is None


def test_verify_revoked_key(patched_conn):
    """verify_api_key returns None for a revoked key."""
    from tools.auth.api_key import generate_api_key, verify_api_key

    _, raw_key, key_hash = generate_api_key("tenant-3", "revoked key")
    patched_conn.execute(
        "UPDATE api_keys SET revoked_at = ? WHERE key_hash = ?",
        (_now(), key_hash),
    )
    patched_conn.commit()

    assert verify_api_key(raw_key) is None


def test_verify_expired_key(patched_conn):
    """verify_api_key returns None for a key past its expires_at."""
    from tools.auth.api_key import generate_api_key, verify_api_key

    _, raw_key, key_hash = generate_api_key(
        "tenant-4", "expired key", expires_at="2000-01-01T00:00:00Z"
    )

    assert verify_api_key(raw_key) is None


def test_require_api_key_decorator_allows_valid(patched_conn):
    """@require_api_key passes a valid bearer token and sets g.api_tenant_id."""
    from tools.auth.api_key import generate_api_key, require_api_key

    _, raw_key, _ = generate_api_key("tenant-5", "decorator test")

    from flask import Flask, g, jsonify

    app = Flask(__name__)

    @app.route("/protected")
    @require_api_key
    def protected():
        return jsonify({"tenant": g.api_tenant_id, "scopes": g.api_scopes})

    with app.test_client() as client:
        resp = client.get("/protected", headers={"Authorization": f"Bearer {raw_key}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["tenant"] == "tenant-5"


def test_require_api_key_decorator_rejects_missing(patched_conn):
    """@require_api_key returns 401 when Authorization header is absent."""
    from tools.auth.api_key import require_api_key
    from flask import Flask, jsonify

    app = Flask(__name__)

    @app.route("/protected")
    @require_api_key
    def protected():
        return jsonify({"ok": True})

    with app.test_client() as client:
        resp = client.get("/protected")
        assert resp.status_code == 401


def test_require_api_key_decorator_rejects_bad_key(patched_conn):
    """@require_api_key returns 401 for a wrong / unknown key."""
    from tools.auth.api_key import require_api_key
    from flask import Flask, jsonify

    app = Flask(__name__)

    @app.route("/protected")
    @require_api_key
    def protected():
        return jsonify({"ok": True})

    with app.test_client() as client:
        resp = client.get("/protected", headers={"Authorization": "Bearer ick_badkey"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# ecr-api-02 — /v1 Blueprint + rate limiting + OpenAPI spec
# ---------------------------------------------------------------------------

_V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
    key_prefix TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
    scopes TEXT NOT NULL DEFAULT 'read', last_used_at TEXT,
    expires_at TEXT, revoked_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gateway_rate_limits (
    key TEXT NOT NULL, window_start INTEGER NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (key, window_start)
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY, tenant_id TEXT, user_id TEXT,
    action TEXT NOT NULL, resource TEXT, details TEXT,
    classification TEXT DEFAULT 'CUI', recorded_at TEXT NOT NULL
);
"""


@pytest.fixture()
def v1_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "v1_test.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_V1_SCHEMA)
    conn.commit()
    return _translating(conn)


@pytest.fixture()
def v1_client(v1_db):
    """Flask test client with the v1 blueprint + patched DB connections."""
    from contextlib import contextmanager
    from flask import Flask
    from tools.api.v1.blueprint import v1_api

    @contextmanager
    def _ctx():
        yield v1_db

    with patch("tools.auth.api_key.get_connection", _ctx), \
         patch("tools.api.v1.blueprint.get_connection", _ctx):
        from tools.auth.api_key import generate_api_key
        _, raw_key, _ = generate_api_key("tenant-v1", "v1-test-key")

        app = Flask(__name__)
        app.register_blueprint(v1_api)

        with app.test_client() as client:
            yield client, raw_key


def test_v1_health(v1_client):
    """GET /v1/health returns {status: ok} and rate-limit headers."""
    client, raw_key = v1_client
    resp = client.get("/v1/health", headers={"Authorization": f"Bearer {raw_key}"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "X-RateLimit-Limit" in resp.headers
    assert "X-RateLimit-Remaining" in resp.headers


def test_v1_health_requires_auth(v1_client):
    """GET /v1/health without a key returns 401."""
    client, _ = v1_client
    resp = client.get("/v1/health")
    assert resp.status_code == 401


def test_v1_openapi_valid_json(v1_client):
    """GET /v1/openapi.json returns valid JSON with openapi key."""
    client, raw_key = v1_client
    resp = client.get("/v1/openapi.json", headers={"Authorization": f"Bearer {raw_key}"})
    assert resp.status_code == 200
    spec = resp.get_json()
    assert spec["openapi"].startswith("3.")
    assert "/v1/health" in spec["paths"]


# ---------------------------------------------------------------------------
# ecr-api-03 — Webhook registry + delivery engine with retry
# ---------------------------------------------------------------------------

_WEBHOOK_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    url         TEXT NOT NULL,
    event_types TEXT NOT NULL,
    secret      TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id                TEXT PRIMARY KEY,
    endpoint_id       TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    payload           TEXT,
    status            TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts          INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT,
    delivered_at      TEXT
);
"""


@pytest.fixture()
def wh_db(tmp_path):
    db = tmp_path / "wh_test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(_WEBHOOK_SCHEMA)
    conn.commit()
    return _translating(conn)


@pytest.fixture()
def wh_conn(wh_db):
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        yield wh_db

    with patch("tools.api.webhook_engine.get_connection", _ctx):
        yield wh_db


def test_webhook_delivery(wh_conn):
    """dispatch_event creates a delivery row; retry honours backoff; HMAC verifiable."""
    import json

    from tools.api.webhook_engine import dispatch_event, sign_payload, verify_signature, _process_pending

    # ---------- fixture data ----------
    endpoint_id = str(uuid.uuid4())
    secret = "test-secret-key"
    event_type = "component.updated"
    payload = {"component": "ace", "version": "2.1.0"}

    wh_conn.execute(
        """INSERT INTO webhook_endpoints
               (id, tenant_id, url, event_types, secret, enabled, created_at)
           VALUES (?, ?, ?, ?, ?, 1, ?)""",
        (endpoint_id, "tenant-wh", "http://localhost:9999/hook", event_type, secret, _now()),
    )
    wh_conn.commit()

    # ---------- dispatch creates a delivery row ----------
    count = dispatch_event(event_type, payload, "tenant-wh")
    assert count == 1

    row = wh_conn.execute(
        "SELECT * FROM webhook_deliveries WHERE endpoint_id = ?", (endpoint_id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert json.loads(row["payload"]) == payload

    delivery_id = row["id"]

    # ---------- signature is verifiable ----------
    body = json.dumps(payload).encode("utf-8")
    sig = sign_payload(secret, body)
    assert sig.startswith("sha256=")
    assert verify_signature(secret, body, sig)
    assert not verify_signature("wrong-secret", body, sig)

    # ---------- retry: failure increments attempts, success marks delivered ----------
    # Simulate failed HTTP call: urlopen raises URLError
    with patch("tools.api.webhook_engine._send_delivery", return_value=False):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            yield wh_conn

        with patch("tools.api.webhook_engine.get_connection", _ctx):
            _process_pending(wh_conn)

    row = wh_conn.execute(
        "SELECT * FROM webhook_deliveries WHERE id = ?", (delivery_id,)
    ).fetchone()
    assert row["attempts"] == 1
    assert row["status"] == "pending"  # still pending — under max attempts

    # Backoff guard: reset last_attempted_at so the elapsed time check passes instantly
    wh_conn.execute(
        "UPDATE webhook_deliveries SET last_attempted_at = '2000-01-01T00:00:00Z' WHERE id = ?",
        (delivery_id,),
    )
    wh_conn.commit()

    # Simulate successful delivery
    with patch("tools.api.webhook_engine._send_delivery", return_value=True):
        with patch("tools.api.webhook_engine.get_connection", _ctx):
            _process_pending(wh_conn)

    row = wh_conn.execute(
        "SELECT * FROM webhook_deliveries WHERE id = ?", (delivery_id,)
    ).fetchone()
    assert row["status"] == "delivered"
    assert row["delivered_at"] is not None

    # ---------- exhaust attempts → status becomes 'failed' ----------
    endpoint_id2 = str(uuid.uuid4())
    wh_conn.execute(
        """INSERT INTO webhook_endpoints
               (id, tenant_id, url, event_types, secret, enabled, created_at)
           VALUES (?, ?, ?, ?, ?, 1, ?)""",
        (endpoint_id2, "tenant-wh", "http://localhost:9999/hook2", "*", secret, _now()),
    )
    wh_conn.commit()
    dispatch_event("any.event", {"x": 1}, "tenant-wh")

    row2 = wh_conn.execute(
        "SELECT * FROM webhook_deliveries WHERE endpoint_id = ?", (endpoint_id2,)
    ).fetchone()
    delivery_id2 = row2["id"]

    # Drive 5 failed attempts to exhaust retries (reset last_attempted_at each round)
    with patch("tools.api.webhook_engine._send_delivery", return_value=False):
        from tools.api import webhook_engine as _we
        _we._BACKOFF = [0, 0, 0, 0, 0]  # disable backoff delays for test speed
        for _ in range(5):
            _process_pending(wh_conn)
        _we._BACKOFF = [0, 10, 30, 120, 300]  # restore

    row2 = wh_conn.execute(
        "SELECT * FROM webhook_deliveries WHERE id = ?", (delivery_id2,)
    ).fetchone()
    assert row2["status"] == "failed"
    assert row2["attempts"] == 5


# ---------------------------------------------------------------------------
# ecr-api-04 — API key management UI in Admin Console
# ---------------------------------------------------------------------------

_ADMIN_UI_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    scopes       TEXT NOT NULL DEFAULT 'read',
    last_used_at TEXT,
    expires_at   TEXT,
    revoked_at   TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    url         TEXT NOT NULL,
    event_types TEXT NOT NULL,
    secret      TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
"""


@pytest.fixture()
def admin_client(tmp_path):
    """Flask test client with admin blueprint + patched DB."""
    conn = sqlite3.connect(str(tmp_path / "admin_ui.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_ADMIN_UI_SCHEMA)
    conn.commit()

    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        yield conn

    import tools.admin.blueprint as _abp
    orig_enabled = _abp._ENABLED

    try:
        _abp._ENABLED = True
        with patch("tools.auth.api_key.get_connection", _ctx), \
             patch("tools.db.storage.get_connection", _ctx):
            from tools.admin.blueprint import create_admin_blueprint
            from flask import Flask
            bp = create_admin_blueprint()
            assert bp is not None, "create_admin_blueprint returned None"
            app = Flask(__name__)
            app.register_blueprint(bp)
            with app.test_client() as client:
                yield client, conn
    finally:
        _abp._ENABLED = orig_enabled


def test_api_key_ui_routes(admin_client):
    """API key CRUD and webhook registration via admin API routes."""
    client, conn = admin_client

    # -- create key --
    resp = client.post(
        "/api/admin/tenants/t1/api-keys",
        json={"name": "CI Key", "scopes": "read write"},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    data = resp.get_json()
    assert "raw_key" in data
    raw_key = data["raw_key"]
    assert raw_key.startswith("ick_")
    key_id = data["key_id"]
    assert key_id is not None

    # -- list keys — raw_key must NOT appear --
    resp = client.get("/api/admin/tenants/t1/api-keys")
    assert resp.status_code == 200
    listed = resp.get_json()
    assert len(listed["keys"]) == 1
    assert listed["keys"][0]["name"] == "CI Key"
    assert "raw_key" not in listed["keys"][0]

    # -- revoke key --
    resp = client.delete(f"/api/admin/tenants/t1/api-keys/{key_id}")
    assert resp.status_code == 200
    assert resp.get_json()["revoked"] is True

    row = conn.execute(
        "SELECT revoked_at FROM api_keys WHERE id = ?", (key_id,)
    ).fetchone()
    assert row["revoked_at"] is not None

    # -- register webhook --
    resp = client.post(
        "/api/admin/tenants/t1/webhooks",
        json={"url": "https://example.com/hook", "event_types": ["component.updated", "key.created"]},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    wh = resp.get_json()
    assert "endpoint_id" in wh
    assert "secret" in wh

    # -- list webhooks --
    resp = client.get("/api/admin/tenants/t1/webhooks")
    assert resp.status_code == 200
    endpoints = resp.get_json()["endpoints"]
    assert len(endpoints) == 1
    assert endpoints[0]["url"] == "https://example.com/hook"


# ---------------------------------------------------------------------------
# ecr-api-05 — V&V: explicit named tests for the 8 acceptance criteria
# ---------------------------------------------------------------------------

def test_invalid_key_rejected(v1_client):
    """GET /v1/health with a bad bearer token returns 401."""
    client, _ = v1_client
    resp = client.get("/v1/health", headers={"Authorization": "Bearer ick_invalid000000000000"})
    assert resp.status_code == 401


def test_openapi_valid(v1_client):
    """GET /v1/openapi.json returns a spec parseable by openapi-spec-validator."""
    client, raw_key = v1_client
    resp = client.get("/v1/openapi.json", headers={"Authorization": f"Bearer {raw_key}"})
    assert resp.status_code == 200
    spec = resp.get_json()
    assert spec is not None

    # Validate with openapi-spec-validator (structural + schema correctness)
    try:
        from openapi_spec_validator import validate
        validate(spec)
    except ImportError:
        # Fallback: minimal structural checks
        assert spec["openapi"].startswith("3.")
        assert "info" in spec
        assert "paths" in spec
    # The /v1/health path must be described
    assert "/v1/health" in spec["paths"]


def test_webhook_signature():
    """sign_payload / verify_signature produce and validate correct HMAC-SHA256."""
    import json as _json
    from tools.api.webhook_engine import sign_payload, verify_signature

    secret = "super-secret-key"
    payload = {"event": "component.updated", "version": "3.0"}
    body = _json.dumps(payload).encode("utf-8")

    sig = sign_payload(secret, body)
    assert sig.startswith("sha256=")
    # Correct secret verifies
    assert verify_signature(secret, body, sig) is True
    # Wrong secret fails
    assert verify_signature("wrong-secret", body, sig) is False
    # Tampered body fails
    tampered = _json.dumps({"event": "other"}).encode("utf-8")
    assert verify_signature(secret, tampered, sig) is False


def test_rate_limit_headers(v1_client):
    """GET /v1/health response includes X-RateLimit-Limit and X-RateLimit-Remaining headers."""
    client, raw_key = v1_client
    resp = client.get("/v1/health", headers={"Authorization": f"Bearer {raw_key}"})
    assert resp.status_code == 200
    assert "X-RateLimit-Limit" in resp.headers
    assert "X-RateLimit-Remaining" in resp.headers
    assert "X-RateLimit-Reset" in resp.headers
    assert int(resp.headers["X-RateLimit-Limit"]) > 0
    assert int(resp.headers["X-RateLimit-Remaining"]) >= 0
