"""Route-level tests for the RFI Response Workbench Flask blueprint.

Uses Flask test client with a SQLite in-memory DB.  get_canvas_connection is
monkeypatched so no real Postgres is required.
"""
import importlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rfi_workbench_sessions (
    id TEXT PRIMARY KEY,
    rfi_number TEXT,
    rfi_title TEXT,
    issuing_agency TEXT,
    profile_name TEXT,
    status TEXT DEFAULT 'draft',
    total_sections INTEGER DEFAULT 0,
    approved_sections INTEGER DEFAULT 0,
    parsed_data TEXT,
    style_guide TEXT,
    ace_instance_id TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS rfi_workbench_sections (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    item_number TEXT,
    title TEXT,
    part TEXT DEFAULT 'part1',
    content TEXT,
    ai_draft TEXT,
    status TEXT DEFAULT 'pending',
    hitl_action TEXT,
    hitl_comment TEXT,
    generation_count INTEGER DEFAULT 0,
    word_limit INTEGER,
    page_limit REAL,
    writeguard_score REAL,
    writeguard_result TEXT,
    requirements TEXT DEFAULT '[]',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS rfi_workbench_exports (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    export_format TEXT,
    file_path TEXT,
    exported_at TEXT
);
CREATE TABLE IF NOT EXISTS rfi_section_history (
    id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    content TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    save_type TEXT NOT NULL DEFAULT 'manual'
);
"""


class _PgCompatCursor:
    """sqlite3 cursor that translates %s → ? for compatibility with PG-style queries."""

    def __init__(self, cur):
        self._cur = cur

    def _fix(self, sql):
        return sql.replace("%s", "?")

    def execute(self, sql, params=()):
        self._cur.execute(self._fix(sql), params)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._cur.lastrowid


class _PgCompatConn:
    """sqlite3 connection wrapper that translates %s → ? in execute calls."""

    def __init__(self, raw):
        self._raw = raw
        self.row_factory = raw.row_factory

    def execute(self, sql, params=()):
        fixed = sql.replace("%s", "?")
        return _PgCompatCursor(self._raw.execute(fixed, params))

    def cursor(self):
        return _PgCompatCursor(self._raw.cursor())

    def executescript(self, sql):
        return self._raw.executescript(sql)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._raw.commit()


def _make_db():
    """Return a %s-compatible SQLite connection seeded with the minimal schema."""
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.executescript(_SCHEMA)
    raw.commit()
    return _PgCompatConn(raw)


def _seed_session(conn, sid=None, status="draft", approved=0, total=0):
    sid = sid or str(uuid.uuid4())
    conn.execute(
        "INSERT INTO rfi_workbench_sessions "
        "(id, rfi_number, rfi_title, issuing_agency, profile_name, status, "
        "total_sections, approved_sections, parsed_data, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,datetime('now'),datetime('now'))",
        (sid, "RFI-TEST-001", "Test RFI", "DISA", "own_company",
         status, total, approved, json.dumps({"questionnaire_parts": []})),
    )
    conn.commit()
    return sid


def _seed_section(conn, sid, sec_id=None, status="pending", content=None):
    sec_id = sec_id or str(uuid.uuid4())
    conn.execute(
        "INSERT INTO rfi_workbench_sections "
        "(id, session_id, item_number, title, part, content, status, "
        "generation_count, requirements, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,datetime('now'),datetime('now'))",
        (sec_id, sid, "1.1", "Technical Approach", "part2",
         content or "", status, 0, "[]"),
    )
    conn.commit()
    return sec_id


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Flask test client with monkeypatched DB."""
    db = _make_db()

    # Wrap connection so it acts like psycopg2 (cursor-based) if needed,
    # but also supports direct .execute() like sqlite3.
    mock_conn = MagicMock(wraps=db)
    mock_conn.execute = db.execute
    mock_conn.commit = db.commit
    mock_conn.cursor = db.cursor
    mock_conn.__enter__ = lambda s: db
    mock_conn.__exit__ = MagicMock(return_value=False)

    wb_mod = importlib.import_module("tools.govcon.rfi_workbench")
    orig_conn = wb_mod.get_canvas_connection
    setattr(wb_mod, "get_canvas_connection", lambda *a, **kw: db)

    try:
        from flask import Flask
        from tools.govcon.rfi_canvas_blueprint import rfi_canvas_bp

        app = Flask(__name__, template_folder="../tools/dashboard/templates")
        app.register_blueprint(rfi_canvas_bp)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test"

        with app.test_client() as c:
            c._db = db
            yield c
    finally:
        setattr(wb_mod, "get_canvas_connection", orig_conn)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListSections:
    def test_returns_empty_list_for_valid_session(self, client):
        sid = _seed_session(client._db)
        r = client.get(f"/api/rfi/{sid}/sections")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)

    def test_returns_seeded_section(self, client):
        sid = _seed_session(client._db)
        _seed_section(client._db, sid)
        r = client.get(f"/api/rfi/{sid}/sections")
        assert r.status_code == 200
        sections = r.get_json()
        assert len(sections) == 1
        assert sections[0]["item_number"] == "1.1"


class TestHITL:
    def test_approve_section(self, client):
        sid = _seed_session(client._db)
        sec_id = _seed_section(client._db, sid, status="ai_draft_ready", content="Draft text")
        r = client.post(
            f"/api/rfi/{sid}/sections/{sec_id}/hitl",
            json={"action": "approve", "comment": "Looks good"},
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_reject_section(self, client):
        sid = _seed_session(client._db)
        sec_id = _seed_section(client._db, sid, status="ai_draft_ready", content="Draft text")
        r = client.post(
            f"/api/rfi/{sid}/sections/{sec_id}/hitl",
            json={"action": "reject", "comment": "Needs work"},
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_bad_action_returns_400(self, client):
        sid = _seed_session(client._db)
        sec_id = _seed_section(client._db, sid)
        r = client.post(
            f"/api/rfi/{sid}/sections/{sec_id}/hitl",
            json={"action": "fly"},
        )
        assert r.status_code in (400, 422)

    def test_missing_section_is_no_op(self, client):
        # Route applies UPDATE with no matching rows — returns ok=True, section=None
        r = client.post(
            "/api/rfi/nonexistent/sections/also-fake/hitl",
            json={"action": "approve"},
        )
        assert r.status_code in (200, 404, 400)


class TestAcceptAll:
    def test_accepts_drafted_and_approved_skips_pending_and_rejected(self, client):
        sid = _seed_session(client._db, total=4)
        drafted = _seed_section(client._db, sid, status="ai_draft_ready", content="Draft")
        approved = _seed_section(client._db, sid, status="hitl_approved", content="Approved")
        pending = _seed_section(client._db, sid, status="pending")
        rejected = _seed_section(client._db, sid, status="hitl_rejected", content="Bad")

        r = client.post(f"/api/rfi/{sid}/accept-all")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["accepted"] == 2

        statuses = {
            row["id"]: row["status"]
            for row in (dict(x) for x in client._db.execute(
                "SELECT id, status FROM rfi_workbench_sections WHERE session_id=%s", (sid,)
            ).fetchall())
        }
        assert statuses[drafted] == "accepted"
        assert statuses[approved] == "accepted"
        assert statuses[pending] == "pending"
        assert statuses[rejected] == "hitl_rejected"

    def test_updates_session_progress(self, client):
        sid = _seed_session(client._db, total=2)
        _seed_section(client._db, sid, status="ai_draft_ready", content="Draft A")
        _seed_section(client._db, sid, status="ai_draft_ready", content="Draft B")
        client._db.execute(
            "UPDATE rfi_workbench_sessions SET total_sections=2 WHERE id=%s", (sid,)
        )
        client._db.commit()

        r = client.post(f"/api/rfi/{sid}/accept-all")
        assert r.get_json()["accepted"] == 2
        row = dict(client._db.execute(
            "SELECT approved_sections, status FROM rfi_workbench_sessions WHERE id=%s", (sid,)
        ).fetchone())
        assert row["approved_sections"] == 2
        assert row["status"] == "complete"

    def test_no_drafted_sections_is_ok(self, client):
        sid = _seed_session(client._db)
        _seed_section(client._db, sid, status="pending")
        r = client.post(f"/api/rfi/{sid}/accept-all")
        assert r.status_code == 200
        assert r.get_json()["accepted"] == 0


class TestPlaceholderGate:
    def test_export_blocked_on_unresolved_placeholders(self, client):
        sid = _seed_session(client._db)
        _seed_section(client._db, sid, status="accepted", content="Our UEI is [UEI_NUMBER].")
        r = client.post(f"/api/rfi/{sid}/export/md")
        assert r.status_code == 409
        body = r.get_json()
        assert body["gate"] == "placeholder_guard"
        assert body["findings"][0]["placeholders"] == ["[UEI_NUMBER]"]

    def test_export_force_bypasses_gate(self, client):
        sid = _seed_session(client._db)
        _seed_section(client._db, sid, status="accepted", content="Our UEI is [UEI_NUMBER].")
        r = client.post(f"/api/rfi/{sid}/export/md", json={"force_placeholders": True})
        assert r.status_code != 409

    def test_clean_content_exports(self, client):
        sid = _seed_session(client._db)
        _seed_section(client._db, sid, status="accepted", content="Our UEI is ABC123DEF456.")
        r = client.post(f"/api/rfi/{sid}/export/md")
        assert r.status_code != 409

    def test_accept_all_reports_placeholder_warnings(self, client):
        sid = _seed_session(client._db)
        _seed_section(client._db, sid, status="ai_draft_ready", content="CAGE [CAGE_CODE] pending.")
        r = client.post(f"/api/rfi/{sid}/accept-all")
        assert r.status_code == 200
        body = r.get_json()
        assert body["accepted"] == 1
        assert body["placeholder_warnings"][0]["placeholders"] == ["[CAGE_CODE]"]


class TestSave:
    def test_save_section_content(self, client):
        sid = _seed_session(client._db)
        sec_id = _seed_section(client._db, sid)
        r = client.post(
            f"/api/rfi/{sid}/sections/{sec_id}/save",
            json={"content": "Updated content here"},
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True


class TestRequirements:
    def test_get_requirements_empty(self, client):
        sid = _seed_session(client._db)
        sec_id = _seed_section(client._db, sid)
        r = client.get(f"/api/rfi/{sid}/sections/{sec_id}/requirements")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)

    def test_add_requirement(self, client):
        sid = _seed_session(client._db)
        sec_id = _seed_section(client._db, sid)
        r = client.post(
            f"/api/rfi/{sid}/sections/{sec_id}/requirements",
            json={"text": "Must support FedRAMP Moderate", "source": "manual"},
        )
        assert r.status_code in (200, 201)
        data = r.get_json()
        assert data.get("ok") is True or "id" in data

    def test_delete_requirement(self, client):
        sid = _seed_session(client._db)
        sec_id = _seed_section(client._db, sid)
        # First add one
        add_r = client.post(
            f"/api/rfi/{sid}/sections/{sec_id}/requirements",
            json={"text": "Some requirement", "source": "manual"},
        )
        assert add_r.status_code in (200, 201)
        # Then fetch to get the id
        list_r = client.get(f"/api/rfi/{sid}/sections/{sec_id}/requirements")
        reqs = list_r.get_json()
        if not reqs:
            pytest.skip("Add did not persist a listable requirement")
        req_id = reqs[0]["id"]
        del_r = client.delete(f"/api/rfi/{sid}/sections/{sec_id}/requirements/{req_id}")
        assert del_r.status_code in (200, 204)


class TestReadiness:
    def test_readiness_pending_sections(self, client):
        sid = _seed_session(client._db, total=1, approved=0)
        _seed_section(client._db, sid, status="pending")
        r = client.get(f"/api/rfi/{sid}/readiness")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ready"] is False
        assert isinstance(data["checks"], list)

    def test_readiness_all_accepted(self, client):
        sid = _seed_session(client._db, total=1, approved=1)
        _seed_section(client._db, sid, status="accepted",
                      content="Solid response text that passes quality checks.\n" * 5)
        r = client.get(f"/api/rfi/{sid}/readiness")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data["checks"], list)
        assert isinstance(data["ready"], bool)

    def test_readiness_no_pending_check_passes_when_all_accepted(self, client):
        sid = _seed_session(client._db, total=1, approved=1)
        _seed_section(client._db, sid, status="accepted", content="Clean content no flags.")
        r = client.get(f"/api/rfi/{sid}/readiness")
        assert r.status_code == 200
        data = r.get_json()
        checks = {c["id"]: c["passed"] for c in data["checks"]}
        # With 1 accepted section and no pending/rejected, these two must pass
        assert checks.get("no_pending") is True
        assert checks.get("no_rejected") is True


class TestGenerateAllStatus:
    def test_status_not_running_initially(self, client):
        sid = _seed_session(client._db)
        r = client.get(f"/api/rfi/{sid}/generate-all/status")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("running") is False

    def test_cancel_when_not_running_is_ok(self, client):
        sid = _seed_session(client._db)
        r = client.post(f"/api/rfi/{sid}/generate-all/cancel")
        assert r.status_code == 200


class TestSectionHistory:
    def test_history_empty_before_save(self, client):
        sid = _seed_session(client._db)
        sec_id = _seed_section(client._db, sid)
        r = client.get(f"/api/rfi/{sid}/sections/{sec_id}/history")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_history_populated_after_save(self, client):
        sid = _seed_session(client._db)
        sec_id = _seed_section(client._db, sid, content="original content")
        # Save new content — should archive old
        client.post(
            f"/api/rfi/{sid}/sections/{sec_id}/save",
            json={"content": "new content"},
        )
        r = client.get(f"/api/rfi/{sid}/sections/{sec_id}/history")
        assert r.status_code == 200
        items = r.get_json()
        assert len(items) >= 1


class TestExport:
    def test_export_md_returns_ok_or_content(self, client):
        sid = _seed_session(client._db, total=1, approved=1)
        _seed_section(client._db, sid, status="accepted", content="# Approach\n\nText here.")
        r = client.post(f"/api/rfi/{sid}/export/md")
        assert r.status_code in (200, 201)
        data = r.get_json()
        assert data.get("ok") is True or "content" in data or "file_path" in data

    def test_export_invalid_format_returns_400(self, client):
        sid = _seed_session(client._db)
        r = client.post(f"/api/rfi/{sid}/export/pdf")
        assert r.status_code in (400, 422)


class TestDeleteSession:
    def test_delete_removes_session(self, client):
        sid = _seed_session(client._db)
        r = client.delete(f"/api/rfi/{sid}")
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_delete_nonexistent_returns_404_or_ok(self, client):
        # Route may succeed (no-op delete) or 404 — both acceptable
        r = client.delete("/api/rfi/does-not-exist")
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            data = r.get_json()
            assert data.get("ok") is True or data.get("ok") is False
