# CUI // SP-CTI
"""Tests for DIC suggestion API routes (dsyn-adapt-04).

Covers: GET /api/suggestions, GET /api/suggestions/<id>,
        POST .../accept, POST .../reject
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── SQLite shim ───────────────────────────────────────────────────────────────

class _ShimConn:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._db.row_factory = sqlite3.Row
        self.rowcount = 0

    def execute(self, sql, params=()):
        cur = self._db.execute(sql.replace("%s", "?"), params)
        self.rowcount = cur.rowcount
        return _ShimCur(cur)

    def commit(self):
        self._db.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.commit()


class _ShimCur:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount
        self.description = cur.description

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dic_sections (
    section_id    TEXT PRIMARY KEY,
    doc_id        TEXT,
    version_id    TEXT,
    collection_id TEXT,
    heading       TEXT,
    content       TEXT DEFAULT '',
    status        TEXT DEFAULT 'draft',
    origin        TEXT DEFAULT 'human_authored',
    created_at    TEXT,
    created_by    TEXT,
    tenant_id     TEXT,
    classification TEXT DEFAULT 'CUI',
    citations_json TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS dic_team_access (
    access_id     TEXT PRIMARY KEY,
    collection_id TEXT,
    user_id       TEXT,
    role          TEXT DEFAULT 'viewer',
    tenant_id     TEXT
);
CREATE TABLE IF NOT EXISTS dic_documents (
    doc_id        TEXT PRIMARY KEY,
    collection_id TEXT,
    filename      TEXT,
    tenant_id     TEXT
);
CREATE TABLE IF NOT EXISTS dic_suggestions (
    suggestion_id     TEXT PRIMARY KEY,
    section_id        TEXT,
    doc_id            TEXT,
    collection_id     TEXT,
    trigger_event_id  TEXT,
    canvas_source     TEXT DEFAULT 'unknown',
    suggested_content TEXT NOT NULL DEFAULT '',
    current_content   TEXT,
    rationale         TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    tenant_id         TEXT,
    classification    TEXT NOT NULL DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS dic_suggestion_decisions (
    decision_id   TEXT PRIMARY KEY,
    suggestion_id TEXT NOT NULL,
    decision      TEXT NOT NULL,
    decided_by    TEXT,
    decided_at    TEXT NOT NULL,
    note          TEXT,
    tenant_id     TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI'
);
-- cef-ui-03: accept/reject now audit the human decision fail-closed, BEFORE the
-- section is touched, so audit_trail is part of these routes' substrate. Kept in
-- step with tests/conftest.py's audit_trail, which mirrors the LIVE PG shape.
CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT,
    event_type     TEXT NOT NULL,
    actor          TEXT NOT NULL,
    action         TEXT NOT NULL,
    details        TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    ip_address     TEXT,
    session_id     TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hash           TEXT,
    previous_hash  TEXT,
    signature      TEXT
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
"""

_COL_ID = "col-api-test-001"
_SEC_ID = "sec-api-test-001"
_DOC_ID = "doc-api-test-001"


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "sug_api.db")
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    # Seed section with content
    conn.execute(
        "INSERT INTO dic_sections (section_id, doc_id, collection_id, heading, content) VALUES (?,?,?,?,?)",
        (_SEC_ID, _DOC_ID, _COL_ID, "Test Section", "original content here"),
    )
    # Seed documents so _collection_id_from_doc works
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id) VALUES (?,?)",
        (_DOC_ID, _COL_ID),
    )
    # Give test user editor role
    conn.execute(
        "INSERT INTO dic_team_access (access_id, collection_id, user_id, role) VALUES (?,?,?,?)",
        ("acc-01", _COL_ID, "test_user", "editor"),
    )
    conn.commit()
    conn.close()
    return path


def _make_shim(db_path: str) -> _ShimConn:
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    return _ShimConn(raw)


def _audit_actions(db_path: str) -> list[str]:
    """The HITL decision actions recorded against this fixture's audit_trail."""
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT action FROM audit_trail WHERE event_type = 'dic.hitl_decision' "
            "ORDER BY id").fetchall()]
    finally:
        conn.close()


@pytest.fixture()
def app(db, monkeypatch):
    flask_app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent.parent / "tools" / "dashboard" / "templates"),
    )
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"

    db_path = db
    # audit_logger binds `get_connection` at import and is not one of the
    # patched seams below, so point the ambient storage at this fixture's own
    # SQLite file instead of stubbing the writer out. The audit row these routes
    # now write is then a real one, written by the real writer.
    import tools.db.storage as _storage
    monkeypatch.setenv("ICDEV_DB_PATH", db_path)
    monkeypatch.setattr(_storage, "DB_PATH", db_path, raising=False)

    def _fake_conn():
        return _make_shim(db_path)

    @contextmanager
    def _fake_gc():
        c = _make_shim(db_path)
        try:
            yield c
        finally:
            c.commit()

    patches = [
        patch("tools.document_intelligence.blueprint._conn", _fake_conn),
        patch("tools.document_intelligence.suggestion_store.get_connection", _fake_gc),
        patch("tools.document_intelligence.history_recorder.get_connection", _fake_gc),
    ]
    for p in patches:
        p.start()

    with patch("tools.document_intelligence.blueprint._current_user", return_value="test_user"), \
         patch("tools.document_intelligence.blueprint._security_context", return_value=("test-tenant", "CUI")):
        from tools.document_intelligence.blueprint import dic_bp
        flask_app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
        yield flask_app

    for p in patches:
        p.stop()


@pytest.fixture()
def client(app):
    return app.test_client()


def _post(client, path, payload=None):
    return client.post(
        path,
        data=json.dumps(payload or {}),
        content_type="application/json",
    )


def _seed_suggestion(db_path: str, **kwargs) -> str:
    """Insert a suggestion row directly and return suggestion_id."""
    from datetime import datetime, timezone
    sid = f"sug_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO dic_suggestions
           (suggestion_id, section_id, doc_id, collection_id, canvas_source,
            suggested_content, current_content, rationale, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sid,
            kwargs.get("section_id", _SEC_ID),
            kwargs.get("doc_id", _DOC_ID),
            kwargs.get("collection_id", _COL_ID),
            kwargs.get("canvas_source", "ndc"),
            kwargs.get("suggested_content", "AI suggested text"),
            kwargs.get("current_content", "original content here"),
            kwargs.get("rationale", "Network topology changed"),
            kwargs.get("status", "pending"),
            now, now,
        ),
    )
    conn.commit()
    conn.close()
    return sid


# ── GET /api/suggestions ──────────────────────────────────────────────────────

def test_list_returns_empty_when_no_suggestions(client):
    r = client.get("/document-intelligence/api/suggestions")
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] == 0
    assert data["suggestions"] == []


def test_list_returns_pending_suggestion(client, db):
    sid = _seed_suggestion(db)
    r = client.get("/document-intelligence/api/suggestions")
    assert r.status_code == 200
    ids = [s["suggestion_id"] for s in r.get_json()["suggestions"]]
    assert sid in ids


def test_list_filters_by_collection_id(client, db):
    sid_a = _seed_suggestion(db, collection_id=_COL_ID)
    sid_b = _seed_suggestion(db, collection_id="col-other-999")
    r = client.get(f"/document-intelligence/api/suggestions?collection_id={_COL_ID}")
    ids = [s["suggestion_id"] for s in r.get_json()["suggestions"]]
    assert sid_a in ids
    assert sid_b not in ids


def test_list_filters_by_canvas_source(client, db):
    sid_ndc = _seed_suggestion(db, canvas_source="ndc")
    sid_zig = _seed_suggestion(db, canvas_source="zig")
    r = client.get("/document-intelligence/api/suggestions?canvas_source=ndc")
    ids = [s["suggestion_id"] for s in r.get_json()["suggestions"]]
    assert sid_ndc in ids
    assert sid_zig not in ids


def test_list_filters_by_status(client, db):
    sid_pending = _seed_suggestion(db, status="pending")
    sid_rejected = _seed_suggestion(db, status="rejected")
    r = client.get("/document-intelligence/api/suggestions?status=pending")
    ids = [s["suggestion_id"] for s in r.get_json()["suggestions"]]
    assert sid_pending in ids
    assert sid_rejected not in ids


# ── GET /api/suggestions/<id> ─────────────────────────────────────────────────

def test_detail_returns_suggestion(client, db):
    sid = _seed_suggestion(db, rationale="Test rationale detail")
    r = client.get(f"/document-intelligence/api/suggestions/{sid}")
    assert r.status_code == 200
    data = r.get_json()
    assert data["suggestion_id"] == sid
    assert data["rationale"] == "Test rationale detail"


def test_detail_returns_404_for_missing(client):
    r = client.get("/document-intelligence/api/suggestions/sug_ghost_id")
    assert r.status_code == 404


# ── POST .../accept ───────────────────────────────────────────────────────────

def test_accept_returns_200(client, db):
    sid = _seed_suggestion(db, suggested_content="Accepted content")
    r = _post(client, f"/document-intelligence/api/suggestions/{sid}/accept")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "accepted"
    assert data["suggestion_id"] == sid
    # cef-ui-03: the decision is audited BEFORE the section is touched, so an
    # accept can never reach the document unrecorded.
    assert _audit_actions(db) == ["dic_suggestion.accepted"]


def test_accept_updates_section_content(client, db):
    sid = _seed_suggestion(db, suggested_content="New approved content")
    _post(client, f"/document-intelligence/api/suggestions/{sid}/accept")
    # Verify section content changed
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT content FROM dic_sections WHERE section_id=?", (_SEC_ID,)).fetchone()
    conn.close()
    assert row[0] == "New approved content"


def test_accept_records_edit_history(client, db):
    sid = _seed_suggestion(db, suggested_content="History recorded content")
    _post(client, f"/document-intelligence/api/suggestions/{sid}/accept")
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT * FROM dic_edit_history WHERE section_id=?", (_SEC_ID,)).fetchall()
    conn.close()
    assert len(rows) >= 1


def test_accept_marks_suggestion_accepted(client, db):
    sid = _seed_suggestion(db)
    _post(client, f"/document-intelligence/api/suggestions/{sid}/accept")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status FROM dic_suggestions WHERE suggestion_id=?", (sid,)).fetchone()
    conn.close()
    assert row[0] == "accepted"


def test_accept_inserts_decision_row(client, db):
    sid = _seed_suggestion(db)
    _post(client, f"/document-intelligence/api/suggestions/{sid}/accept")
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT * FROM dic_suggestion_decisions WHERE suggestion_id=?", (sid,)
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][2] == "accepted"  # decision column


def test_accept_returns_new_hash(client, db):
    sid = _seed_suggestion(db, suggested_content="Hash check content")
    r = _post(client, f"/document-intelligence/api/suggestions/{sid}/accept")
    data = r.get_json()
    assert "new_hash" in data
    assert len(data["new_hash"]) == 8


def test_accept_already_accepted_returns_409(client, db):
    sid = _seed_suggestion(db)
    _post(client, f"/document-intelligence/api/suggestions/{sid}/accept")
    r = _post(client, f"/document-intelligence/api/suggestions/{sid}/accept")
    assert r.status_code == 409


def test_accept_missing_suggestion_returns_404(client):
    r = _post(client, "/document-intelligence/api/suggestions/sug_missing/accept")
    assert r.status_code == 404


def test_accept_viewer_gets_403(client, db):
    sid = _seed_suggestion(db)
    with patch("tools.document_intelligence.blueprint._require_role", return_value=False):
        r = _post(client, f"/document-intelligence/api/suggestions/{sid}/accept")
    assert r.status_code == 403


# ── POST .../reject ───────────────────────────────────────────────────────────

def test_reject_returns_200(client, db):
    sid = _seed_suggestion(db)
    r = _post(client, f"/document-intelligence/api/suggestions/{sid}/reject", {"note": "Outdated."})
    assert r.status_code == 200
    assert r.get_json()["status"] == "rejected"
    # cef-ui-03: a rejection is audited as deliberately as an acceptance.
    assert _audit_actions(db) == ["dic_suggestion.rejected"]


def test_reject_marks_suggestion_rejected(client, db):
    sid = _seed_suggestion(db)
    _post(client, f"/document-intelligence/api/suggestions/{sid}/reject")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status FROM dic_suggestions WHERE suggestion_id=?", (sid,)).fetchone()
    conn.close()
    assert row[0] == "rejected"


def test_reject_inserts_decision_row_with_note(client, db):
    sid = _seed_suggestion(db)
    _post(client, f"/document-intelligence/api/suggestions/{sid}/reject", {"note": "Not applicable."})
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT note FROM dic_suggestion_decisions WHERE suggestion_id=?", (sid,)
    ).fetchone()
    conn.close()
    assert row[0] == "Not applicable."


def test_reject_already_decided_returns_409(client, db):
    sid = _seed_suggestion(db)
    _post(client, f"/document-intelligence/api/suggestions/{sid}/reject")
    r = _post(client, f"/document-intelligence/api/suggestions/{sid}/reject")
    assert r.status_code == 409


def test_reject_missing_suggestion_returns_404(client):
    r = _post(client, "/document-intelligence/api/suggestions/sug_ghost/reject")
    assert r.status_code == 404


def test_reject_does_not_change_section_content(client, db):
    sid = _seed_suggestion(db, suggested_content="Should not be applied")
    _post(client, f"/document-intelligence/api/suggestions/{sid}/reject")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT content FROM dic_sections WHERE section_id=?", (_SEC_ID,)).fetchone()
    conn.close()
    assert row[0] == "original content here"
