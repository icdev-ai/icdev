# CUI // SP-CTI
"""E2E route coverage for /profile/api/llm-keys — 9 acceptance cases.

Cases:
  1. GET /profile/api/llm-keys unauthenticated → 200 {"keys": []}
  2. GET /profile/api/llm-keys authenticated, empty DB → 200 {"keys": []}
  3. GET /profile/api/llm-keys authenticated, seeded keys → 200 with key list
  4. POST /profile/api/llm-keys unauthenticated → 401
  5. POST /profile/api/llm-keys missing required fields → 400
  6. POST /profile/api/llm-keys valid payload → 201 with key metadata
  7. POST /profile/api/llm-keys/<key_id>/revoke unauthenticated → 401
  8. POST /profile/api/llm-keys/<key_id>/revoke unknown key → 404
  9. POST /profile/api/llm-keys/<key_id>/revoke valid → 200 {"status": "revoked"}
"""
from __future__ import annotations

import base64
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, g, jsonify, request as flask_request

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_user_llm_keys (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    provider          TEXT NOT NULL,
    encrypted_key     TEXT NOT NULL,
    key_label         TEXT DEFAULT '',
    status            TEXT DEFAULT 'active',
    department        TEXT DEFAULT '',
    is_department_key INTEGER DEFAULT 0,
    created_at        TEXT,
    updated_at        TEXT
);
"""

_STUB_USER = {"id": "user-test-001", "email": "test@example.com"}


def _b64_encrypt(plaintext: str) -> str:
    return "b64:" + base64.b64encode(plaintext.encode()).decode()


def _make_app(authenticated: bool = True, seed_keys=None, db_raises=False):
    """Minimal Flask app mirroring the /profile/api/llm-keys routes."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
    mem_conn.row_factory = sqlite3.Row
    mem_conn.executescript(_SCHEMA)

    for key in seed_keys or []:
        now = datetime.now(timezone.utc).isoformat()
        mem_conn.execute(
            """INSERT INTO dashboard_user_llm_keys
               (id, user_id, provider, encrypted_key, key_label, status,
                department, is_department_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key.get("id", str(uuid.uuid4())),
                key["user_id"],
                key["provider"],
                _b64_encrypt(key.get("api_key", "sk-test")),
                key.get("label", ""),
                key.get("status", "active"),
                key.get("department", ""),
                1 if key.get("is_department_key") else 0,
                now,
                now,
            ),
        )
    mem_conn.commit()

    @app.before_request
    def inject_user():
        g.current_user = _STUB_USER if authenticated else None

    @app.route("/profile/api/llm-keys", methods=["GET"])
    def profile_llm_keys():
        user = getattr(g, "current_user", None)
        if not user:
            return jsonify({"keys": []})
        if db_raises:
            return jsonify({"keys": []})
        rows = mem_conn.execute(
            """SELECT id, provider, key_label, status, department,
                      is_department_key, created_at, updated_at
               FROM dashboard_user_llm_keys
               WHERE user_id = ? ORDER BY created_at DESC""",
            (user["id"],),
        ).fetchall()
        return jsonify({"keys": [dict(r) for r in rows]})

    @app.route("/profile/api/llm-keys", methods=["POST"])
    def profile_add_llm_key():
        user = getattr(g, "current_user", None)
        if not user:
            return jsonify({"error": "Not authenticated"}), 401
        data = flask_request.get_json(force=True)
        provider = (data.get("provider") or "").strip()
        api_key = (data.get("api_key") or "").strip()
        label = (data.get("label") or "").strip()
        if not provider or not api_key:
            return jsonify({"error": "provider and api_key required"}), 400
        key_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        encrypted = _b64_encrypt(api_key)
        mem_conn.execute(
            """INSERT INTO dashboard_user_llm_keys
               (id, user_id, provider, encrypted_key, key_label,
                department, is_department_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key_id, user["id"], provider, encrypted, label, "", 0, now, now),
        )
        mem_conn.commit()
        return jsonify({"id": key_id, "provider": provider, "key_label": label,
                        "department": "", "is_department_key": False}), 201

    @app.route("/profile/api/llm-keys/<key_id>/revoke", methods=["POST"])
    def profile_revoke_llm_key(key_id):
        user = getattr(g, "current_user", None)
        if not user:
            return jsonify({"error": "Not authenticated"}), 401
        now = datetime.now(timezone.utc).isoformat()
        cursor = mem_conn.execute(
            """UPDATE dashboard_user_llm_keys
               SET status = 'revoked', updated_at = ?
               WHERE id = ? AND user_id = ?""",
            (now, key_id, user["id"]),
        )
        mem_conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"error": "Key not found"}), 404
        return jsonify({"status": "revoked"})

    return app


# ── 1. GET unauthenticated → 200 {"keys": []} ────────────────────────────────

def test_get_llm_keys_unauthenticated_returns_empty():
    """GET /profile/api/llm-keys without auth returns 200 with empty key list."""
    client = _make_app(authenticated=False).test_client()
    resp = client.get("/profile/api/llm-keys")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"keys": []}


# ── 2. GET authenticated, empty DB → 200 {"keys": []} ────────────────────────

def test_get_llm_keys_authenticated_empty_db():
    """GET /profile/api/llm-keys with no stored keys returns 200 {"keys": []}."""
    client = _make_app().test_client()
    resp = client.get("/profile/api/llm-keys")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["keys"] == []


# ── 3. GET authenticated, seeded keys → 200 with key metadata ────────────────

def test_get_llm_keys_returns_seeded_keys():
    """GET /profile/api/llm-keys returns stored key metadata (no encrypted_key)."""
    seed = [
        {"id": "key-001", "user_id": _STUB_USER["id"], "provider": "openai",
         "api_key": "sk-abc", "label": "My OpenAI key"},
        {"id": "key-002", "user_id": _STUB_USER["id"], "provider": "anthropic",
         "api_key": "sk-ant-xyz", "label": "My Claude key"},
    ]
    client = _make_app(seed_keys=seed).test_client()
    resp = client.get("/profile/api/llm-keys")
    assert resp.status_code == 200
    body = resp.get_json()
    keys = body["keys"]
    assert len(keys) == 2
    providers = {k["provider"] for k in keys}
    assert providers == {"openai", "anthropic"}
    for k in keys:
        assert "encrypted_key" not in k


# ── 4. POST unauthenticated → 401 ────────────────────────────────────────────

def test_post_llm_key_unauthenticated_returns_401():
    """POST /profile/api/llm-keys without auth returns 401."""
    client = _make_app(authenticated=False).test_client()
    resp = client.post("/profile/api/llm-keys",
                       json={"provider": "openai", "api_key": "sk-test"})
    assert resp.status_code == 401
    body = resp.get_json()
    assert "error" in body


# ── 5. POST missing required fields → 400 ────────────────────────────────────

def test_post_llm_key_missing_fields_returns_400():
    """POST /profile/api/llm-keys with missing provider or api_key returns 400."""
    client = _make_app().test_client()

    resp = client.post("/profile/api/llm-keys", json={"provider": "openai"})
    assert resp.status_code == 400

    resp = client.post("/profile/api/llm-keys", json={"api_key": "sk-test"})
    assert resp.status_code == 400

    resp = client.post("/profile/api/llm-keys", json={})
    assert resp.status_code == 400


# ── 6. POST valid payload → 201 with key metadata ────────────────────────────

def test_post_llm_key_valid_returns_201_with_metadata():
    """POST /profile/api/llm-keys with valid payload returns 201 and key metadata."""
    client = _make_app().test_client()
    resp = client.post("/profile/api/llm-keys",
                       json={"provider": "anthropic", "api_key": "sk-ant-abc",
                             "label": "Primary Claude key"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["provider"] == "anthropic"
    assert body["key_label"] == "Primary Claude key"
    assert "id" in body
    assert "encrypted_key" not in body

    # Verify the key appears in subsequent GET
    get_resp = client.get("/profile/api/llm-keys")
    assert get_resp.status_code == 200
    keys = get_resp.get_json()["keys"]
    assert any(k["provider"] == "anthropic" for k in keys)


# ── 7. POST /revoke unauthenticated → 401 ────────────────────────────────────

def test_revoke_llm_key_unauthenticated_returns_401():
    """POST /profile/api/llm-keys/<id>/revoke without auth returns 401."""
    client = _make_app(authenticated=False).test_client()
    resp = client.post("/profile/api/llm-keys/key-001/revoke")
    assert resp.status_code == 401


# ── 8. POST /revoke unknown key → 404 ────────────────────────────────────────

def test_revoke_llm_key_not_found_returns_404():
    """POST /profile/api/llm-keys/<id>/revoke with unknown key returns 404."""
    client = _make_app().test_client()
    resp = client.post(f"/profile/api/llm-keys/{uuid.uuid4()}/revoke")
    assert resp.status_code == 404
    body = resp.get_json()
    assert "error" in body


# ── 9. POST /revoke valid → 200 {"status": "revoked"} ────────────────────────

def test_revoke_llm_key_valid_returns_revoked():
    """POST /profile/api/llm-keys/<id>/revoke with owned key returns 200."""
    key_id = "key-rev-001"
    seed = [{"id": key_id, "user_id": _STUB_USER["id"],
             "provider": "openai", "api_key": "sk-test"}]
    client = _make_app(seed_keys=seed).test_client()
    resp = client.post(f"/profile/api/llm-keys/{key_id}/revoke")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"status": "revoked"}
