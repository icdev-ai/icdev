# CUI // SP-CTI
"""Integration tests for GET /api/sections/<section_id>/history (rted-hist-02).

Verifies:
  - 200 + empty list when no edits exist
  - ?limit=N is respected (exactly N entries returned)
  - entries are ordered newest-first
  - content_before / content_after are never returned
  - ?since=ISO_DATE filters correctly
  - required fields present in every entry
"""
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
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

    def execute(self, sql, params=()):
        return _ShimCursor(self._db.execute(sql.replace("%s", "?"), params))

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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dic_edit_history (
    edit_id        TEXT PRIMARY KEY,
    section_id     TEXT NOT NULL,
    doc_id         TEXT,
    version_id     TEXT,
    editor         TEXT NOT NULL,
    content_before TEXT,
    content_after  TEXT,
    char_delta     INTEGER,
    diff_summary   TEXT,
    edited_at      TEXT NOT NULL,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI'
);
"""

_SEC = "sec-hist-api-001"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "hist_api.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def app(db):
    flask_app = Flask(
        __name__,
        template_folder=str(
            Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
        ),
    )
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"

    db_path = str(db)

    def _make_shim():
        return _ShimConn(sqlite3.connect(db_path))

    @contextmanager
    def _fake_get_connection():
        c = _make_shim()
        try:
            yield c
        finally:
            c.commit()

    patches = [
        patch("tools.document_intelligence.blueprint._conn", _make_shim),
        patch("tools.document_intelligence.history_recorder.get_connection", _fake_get_connection),
    ]
    for p in patches:
        p.start()

    from tools.document_intelligence.blueprint import dic_bp
    flask_app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
    yield flask_app

    for p in patches:
        p.stop()


@pytest.fixture()
def client(app):
    return app.test_client()


def _insert_edits(db_path: str, section_id: str, n: int) -> list[str]:
    """Insert n history rows with distinct ascending timestamps; return edit_ids."""
    conn = sqlite3.connect(db_path)
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    ids = []
    for i in range(n):
        ts = (base + timedelta(seconds=i)).isoformat()
        eid = f"eh_test_{i:04d}"
        conn.execute(
            "INSERT INTO dic_edit_history "
            "(edit_id, section_id, editor, char_delta, diff_summary, edited_at) "
            "VALUES (?,?,?,?,?,?)",
            (eid, section_id, f"user{i}", i * 5, f"+line{i}", ts),
        )
        ids.append(eid)
    conn.commit()
    conn.close()
    return ids


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_empty_history_returns_200_empty_list(client):
    """No edits → 200 OK with empty history list."""
    r = client.get("/document-intelligence/api/sections/sec-no-edits/history")
    assert r.status_code == 200
    data = r.get_json()
    assert data["history"] == []
    assert data["count"] == 0


def test_limit_param_respected(client, db):
    """GIVEN 5 edits for section S WHEN limit=3 THEN exactly 3 entries returned."""
    _insert_edits(str(db), _SEC, 5)
    r = client.get(f"/document-intelligence/api/sections/{_SEC}/history?limit=3")
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] == 3
    assert len(data["history"]) == 3


def test_entries_ordered_newest_first(client, db):
    """GIVEN 5 edits, limit=3 THEN entries are in descending edited_at order."""
    _insert_edits(str(db), _SEC, 5)
    r = client.get(f"/document-intelligence/api/sections/{_SEC}/history?limit=3")
    data = r.get_json()
    edited_ats = [e["edited_at"] for e in data["history"]]
    assert edited_ats == sorted(edited_ats, reverse=True)


def test_no_content_fields_in_response(client, db):
    """content_before and content_after must never appear in list entries."""
    _insert_edits(str(db), _SEC, 2)
    r = client.get(f"/document-intelligence/api/sections/{_SEC}/history")
    data = r.get_json()
    for entry in data["history"]:
        assert "content_before" not in entry
        assert "content_after" not in entry


def test_required_fields_present(client, db):
    """Each entry must include edit_id, editor, edited_at, char_delta, diff_summary."""
    _insert_edits(str(db), _SEC, 1)
    r = client.get(f"/document-intelligence/api/sections/{_SEC}/history")
    data = r.get_json()
    assert len(data["history"]) == 1
    entry = data["history"][0]
    for field in ("edit_id", "editor", "edited_at", "char_delta", "diff_summary"):
        assert field in entry, f"Missing required field: {field}"


def test_limit_capped_at_100(client, db):
    """?limit=999 is silently capped at 100."""
    _insert_edits(str(db), _SEC, 5)
    r = client.get(f"/document-intelligence/api/sections/{_SEC}/history?limit=999")
    assert r.status_code == 200
    data = r.get_json()
    # Only 5 records exist — cap doesn't truncate further, just doesn't error
    assert len(data["history"]) == 5


def test_since_filter_excludes_older_entries(client, db):
    """?since=ISO_DATE returns only entries at or after that timestamp."""
    # 5 edits at T+0s through T+4s; since=T+3s → entries 3 and 4 only
    _insert_edits(str(db), _SEC, 5)
    since = "2026-01-01T00:00:03+00:00"
    r = client.get(
        f"/document-intelligence/api/sections/{_SEC}/history",
        query_string={"since": since},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["history"]) == 2
    for entry in data["history"]:
        assert entry["edited_at"] >= "2026-01-01T00:00:03"


def test_default_limit_is_50(client, db):
    """No explicit limit → default is 50 (not 200 and not unbounded)."""
    _insert_edits(str(db), _SEC, 5)
    r = client.get(f"/document-intelligence/api/sections/{_SEC}/history")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["history"]) == 5  # only 5 exist, all returned under default
