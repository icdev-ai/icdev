# CUI // SP-CTI
"""RTED E2E Smoke — rted-vv-01.

Exercises all four RTED epics through the DIC blueprint's HTTP routes:
  * Lock    — acquire / renew / release  (rted-lock-01/02/03)
  * History — save records edit history  (rted-hist-01/02)
  * Presence— join / heartbeat / leave   (rted-pres-01/02)
  * Conflict— hash mismatch → 409, force-save succeeds  (rted-conf-01/02)

Uses an in-memory SQLite DB with %s→? translation, patched into all DIC
modules via their get_connection entry points.
"""
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── Shared SQLite shim ────────────────────────────────────────────────────────

_DB_PATH = None  # set per-test via fixture


class _ShimConn:
    """sqlite3 connection wrapper that translates %s→? and exposes rowcount."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._db.row_factory = sqlite3.Row
        self._last_rowcount = 0

    def execute(self, sql, params=()):
        cur = self._db.execute(sql.replace("%s", "?"), params)
        self._last_rowcount = cur.rowcount
        return _ShimCursor(cur)

    def commit(self):
        self._db.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.commit()


class _ShimCursor:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


_MINIMAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS dic_sections (
    section_id   TEXT PRIMARY KEY,
    doc_id       TEXT,
    version_id   TEXT,
    collection_id TEXT,
    title        TEXT,
    content      TEXT DEFAULT '',
    status       TEXT DEFAULT 'draft',
    origin       TEXT DEFAULT 'human_authored',
    created_at   TEXT,
    tenant_id    TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS dic_section_locks (
    lock_id       TEXT PRIMARY KEY,
    section_id    TEXT NOT NULL,
    locked_by     TEXT NOT NULL,
    locked_at     TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    doc_id        TEXT,
    tenant_id     TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS dic_edit_history (
    edit_id       TEXT PRIMARY KEY,
    section_id    TEXT NOT NULL,
    doc_id        TEXT,
    version_id    TEXT,
    editor        TEXT NOT NULL,
    content_before TEXT,
    content_after  TEXT,
    char_delta    INTEGER,
    diff_summary  TEXT,
    edited_at     TEXT NOT NULL,
    tenant_id     TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS dic_presence_sessions (
    session_key   TEXT PRIMARY KEY,
    doc_id        TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    joined_at     TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    tenant_id     TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS dic_section_annotations (
    ann_id       TEXT PRIMARY KEY,
    section_id   TEXT NOT NULL,
    doc_id       TEXT,
    author       TEXT,
    category     TEXT,
    comment      TEXT,
    status       TEXT DEFAULT 'open',
    resolution_note TEXT,
    resolved_by  TEXT,
    resolved_at  TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    tenant_id    TEXT,
    classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS dic_collection_members (
    id           TEXT PRIMARY KEY,
    collection_id TEXT,
    user_id      TEXT,
    role         TEXT DEFAULT 'viewer',
    created_at   TEXT
);
"""

_DOC_ID = "doc-vv-smoke-001"
_SEC_ID = "sec-vv-smoke-001"
_COL_ID = "col-vv-smoke-001"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "rted_vv.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_MINIMAL_SCHEMA)
    conn.execute(
        "INSERT INTO dic_sections (section_id, doc_id, version_id, collection_id, title, content) "
        "VALUES (?,?,?,?,?,?)",
        (_SEC_ID, _DOC_ID, "ver-001", _COL_ID, "Section 1", "original content"),
    )
    # Give the test user 'editor' role
    conn.execute(
        "INSERT INTO dic_collection_members (id, collection_id, user_id, role) VALUES (?,?,?,?)",
        ("mem-01", _COL_ID, "vv_user", "editor"),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def app(db):
    flask_app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent.parent / "tools" / "dashboard" / "templates"),
    )
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"

    db_path = str(db)

    def _make_shim():
        raw = sqlite3.connect(db_path)
        return _ShimConn(raw)

    @contextmanager
    def _fake_get_connection():
        c = _make_shim()
        try:
            yield c
        finally:
            c.commit()

    def _fake_conn_fn():
        return _make_shim()

    patches = [
        patch("tools.document_intelligence.blueprint._conn", _fake_conn_fn),
        patch("tools.document_intelligence.lock_manager.get_connection", _fake_get_connection),
        patch("tools.document_intelligence.history_recorder.get_connection", _fake_get_connection),
        patch("tools.document_intelligence.presence_registry.get_connection", _fake_get_connection),
    ]
    for p in patches:
        p.start()

    # Patch _current_user and _require_role so auth doesn't block
    with patch("tools.document_intelligence.blueprint._current_user", return_value="vv_user"), \
         patch("tools.document_intelligence.blueprint._require_role", return_value=True), \
         patch("tools.document_intelligence.blueprint._security_context", return_value=("vv-tenant", "CUI")):

        from tools.document_intelligence.blueprint import dic_bp
        flask_app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
        yield flask_app

    for p in patches:
        p.stop()


@pytest.fixture()
def client(app):
    return app.test_client()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _post(client, path, payload=None):
    return client.post(
        path,
        data=json.dumps(payload or {}),
        content_type="application/json",
    )


def _delete(client, path, payload=None):
    return client.delete(
        path,
        data=json.dumps(payload or {}),
        content_type="application/json",
    )


# ── LOCK EPIC (rted-lock-01/02/03) ────────────────────────────────────────────

def test_lock_acquire_returns_200(client):
    r = _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/lock")
    assert r.status_code == 200
    data = r.get_json()
    assert "lock_id" in data
    assert data["locked_by"] == "vv_user"


def test_lock_status_shows_locked(client):
    _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/lock")
    r = client.get(f"/document-intelligence/api/sections/{_SEC_ID}/lock")
    assert r.status_code == 200
    data = r.get_json()
    assert data["locked"] is True
    assert data["locked_by"] == "vv_user"


def test_lock_second_user_gets_409(client):
    _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/lock")
    with patch("tools.document_intelligence.blueprint._current_user", return_value="other_user"):
        r = _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/lock")
    assert r.status_code == 409


def test_lock_renew_returns_200(client):
    _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/lock")
    r = client.put(f"/document-intelligence/api/sections/{_SEC_ID}/lock/renew")
    assert r.status_code == 200


def test_lock_release_returns_200(client):
    _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/lock")
    r = _delete(client, f"/document-intelligence/api/sections/{_SEC_ID}/lock")
    assert r.status_code == 200
    data = r.get_json()
    assert data["released"] is True


# ── HISTORY EPIC (rted-hist-01/02) ────────────────────────────────────────────

def test_save_records_history(client):
    _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/content",
          {"content": "updated content for history test"})
    r = client.get(f"/document-intelligence/api/sections/{_SEC_ID}/history")
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] >= 1
    entry = data["history"][0]
    assert entry["editor"] == "vv_user"
    assert "char_delta" in entry
    assert "diff_summary" in entry


def test_save_noop_no_history(client, db):
    # Set content to known value first
    _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/content",
          {"content": "stable content"})
    # Save identical content — should not add another history row
    count_before = client.get(
        f"/document-intelligence/api/sections/{_SEC_ID}/history"
    ).get_json()["count"]
    _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/content",
          {"content": "stable content"})
    count_after = client.get(
        f"/document-intelligence/api/sections/{_SEC_ID}/history"
    ).get_json()["count"]
    assert count_after == count_before


def test_history_returns_hash_on_save(client):
    r = _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/content",
              {"content": "hashed save content"})
    assert r.status_code == 200
    data = r.get_json()
    assert "new_hash" in data
    assert len(data["new_hash"]) == 8


# ── PRESENCE EPIC (rted-pres-01/02) ───────────────────────────────────────────

def test_presence_join_returns_session_key(client):
    r = _post(client, f"/document-intelligence/api/documents/{_DOC_ID}/presence/join",
              {"user_id": "alice"})
    assert r.status_code == 200
    data = r.get_json()
    assert "session_key" in data
    assert data["session_key"].startswith("ps_")


def test_presence_list_shows_joined_user(client):
    _post(client, f"/document-intelligence/api/documents/{_DOC_ID}/presence/join",
          {"user_id": "bob"})
    r = client.get(f"/document-intelligence/api/documents/{_DOC_ID}/presence")
    assert r.status_code == 200
    data = r.get_json()
    ids = [u["user_id"] for u in data["users"]]
    assert "bob" in ids


def test_presence_heartbeat_returns_ok(client):
    join_r = _post(client, f"/document-intelligence/api/documents/{_DOC_ID}/presence/join",
                   {"user_id": "carol"})
    key = join_r.get_json()["session_key"]
    r = _post(client, f"/document-intelligence/api/documents/{_DOC_ID}/presence/heartbeat",
              {"session_key": key})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_presence_leave_removes_user(client):
    join_r = _post(client, f"/document-intelligence/api/documents/{_DOC_ID}/presence/join",
                   {"user_id": "dave"})
    key = join_r.get_json()["session_key"]
    _delete(client, f"/document-intelligence/api/documents/{_DOC_ID}/presence/leave",
            {"session_key": key})
    r = client.get(f"/document-intelligence/api/documents/{_DOC_ID}/presence")
    ids = [u["user_id"] for u in r.get_json()["users"]]
    assert "dave" not in ids


def test_presence_heartbeat_unknown_key(client):
    r = _post(client, f"/document-intelligence/api/documents/{_DOC_ID}/presence/heartbeat",
              {"session_key": "ps_ghost"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is False


# ── CONFLICT EPIC (rted-conf-01/02) ───────────────────────────────────────────

def test_conflict_hash_endpoint_returns_hash(client):
    r = client.get(f"/document-intelligence/api/sections/{_SEC_ID}/hash")
    assert r.status_code == 200
    data = r.get_json()
    assert "hash" in data
    assert len(data["hash"]) == 8


def test_save_with_matching_hash_succeeds(client):
    # Fetch current hash
    h = client.get(f"/document-intelligence/api/sections/{_SEC_ID}/hash").get_json()["hash"]
    r = _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/content",
              {"content": "updated with correct hash", "expected_hash": h})
    assert r.status_code == 200


def test_save_with_wrong_hash_returns_409(client):
    r = _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/content",
              {"content": "my edit", "expected_hash": "deadbeef"})
    assert r.status_code == 409
    data = r.get_json()
    assert data["conflict"] is True
    assert "current_content" in data
    assert "current_hash" in data


def test_force_save_overrides_conflict(client):
    r = _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/content",
              {"content": "forced content", "expected_hash": "deadbeef", "force": True})
    assert r.status_code == 200
    assert r.get_json()["status"] == "updated"


def test_409_exposes_current_content(client):
    r = _post(client, f"/document-intelligence/api/sections/{_SEC_ID}/content",
              {"content": "my draft", "expected_hash": "00000000"})
    assert r.status_code == 409
    data = r.get_json()
    # current_content is what's actually in the DB — not empty
    assert data["current_content"] != ""
