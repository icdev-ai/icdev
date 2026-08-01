# CUI // SP-CTI
"""Tests: promote/export gate on unresolved placeholders (ground-prop-03).

Covers:
    - tools.govcon.response_drafter.unresolved_placeholders()
    - tools.govcon.response_drafter.approve_draft() placeholder gate
    - PUT /api/govcon/drafts/<id>/approve 409 gate=placeholder_guard
"""

import importlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

rd = importlib.import_module("tools.govcon.response_drafter")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SqliteCompatConn:
    """sqlite3 connection that accepts %s param placeholders (PG style)."""

    def __init__(self, path):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("%s", "?"), params)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal_section_drafts (
    id TEXT PRIMARY KEY,
    section_id TEXT,
    opportunity_id TEXT,
    shall_statement_id TEXT,
    capability_ids TEXT DEFAULT '[]',
    knowledge_block_ids TEXT DEFAULT '[]',
    draft_content TEXT,
    draft_method TEXT,
    confidence_score REAL DEFAULT 0.0,
    confidence REAL DEFAULT 0.0,
    domain_category TEXT,
    generation_model TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    reviewer_notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT,
    classification TEXT
);

CREATE TABLE IF NOT EXISTS proposal_sections (
    id TEXT PRIMARY KEY,
    status TEXT,
    notes TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS proposal_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT,
    entity_id TEXT,
    old_status TEXT,
    new_status TEXT,
    changed_by TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT,
    created_at TEXT,
    event_type TEXT,
    actor TEXT,
    action TEXT,
    details TEXT,
    session_id TEXT
);
"""

_CLEAN_CONTENT = "We provide zero-trust networking via our platform."
_PLACEHOLDER_CONTENT = (
    "Our UEI is [UEI_NUMBER] and our CAGE code is [CAGE_CODE]. "
    "We provide zero-trust networking."
)


def _make_db(tmp_path):
    db_path = tmp_path / "gate_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    rows = [
        ("draft-clean", _CLEAN_CONTENT, "draft"),
        ("draft-tokens", _PLACEHOLDER_CONTENT, "draft"),
        ("draft-approved", _CLEAN_CONTENT, "approved"),
    ]
    for draft_id, content, status in rows:
        conn.execute(
            "INSERT INTO proposal_section_drafts "
            "(id, opportunity_id, draft_content, status, created_at, metadata) "
            "VALUES (?, 'opp-1', ?, ?, '2026-07-01T00:00:00', '{}')",
            (draft_id, content, status),
        )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# unresolved_placeholders()
# ---------------------------------------------------------------------------

class TestUnresolvedPlaceholders:
    def test_finds_tokens_in_content(self):
        tokens = rd.unresolved_placeholders({"draft_content": _PLACEHOLDER_CONTENT})
        assert "[UEI_NUMBER]" in tokens
        assert "[CAGE_CODE]" in tokens

    def test_clean_content_returns_empty(self):
        assert rd.unresolved_placeholders({"draft_content": _CLEAN_CONTENT}) == []

    def test_empty_content_returns_empty(self):
        assert rd.unresolved_placeholders({"draft_content": ""}) == []
        assert rd.unresolved_placeholders({}) == []

    def test_falls_back_to_metadata_when_scanner_unavailable(self):
        row = {
            "draft_content": _PLACEHOLDER_CONTENT,
            "metadata": json.dumps({"placeholder_tokens": ["[FROM_META]"]}),
        }
        import tools.quality.content_grounding as cg

        def _boom(_text):
            raise RuntimeError("scanner unavailable")

        with patch.object(cg, "find_placeholders", _boom):
            assert rd.unresolved_placeholders(row) == ["[FROM_META]"]


# ---------------------------------------------------------------------------
# approve_draft() gate (response_drafter)
# ---------------------------------------------------------------------------

class TestApproveDraftGate:
    @pytest.fixture()
    def db_path(self, tmp_path, monkeypatch):
        path = _make_db(tmp_path)
        monkeypatch.setattr(rd, "_get_db", lambda: _SqliteCompatConn(path))
        return path

    def _rows(self, db_path, sql, params=()):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return rows

    def test_blocked_when_tokens_remain(self, db_path):
        result = rd.approve_draft("draft-tokens")
        assert result["status"] == "blocked"
        assert result["gate"] == "placeholder_guard"
        assert "[UEI_NUMBER]" in result["placeholder_tokens"]

    def test_blocked_writes_no_approved_row(self, db_path):
        rd.approve_draft("draft-tokens")
        rows = self._rows(
            db_path,
            "SELECT * FROM proposal_section_drafts WHERE status = 'approved' "
            "AND id != 'draft-approved'",
        )
        assert rows == []

    def test_clean_draft_approves(self, db_path):
        result = rd.approve_draft("draft-clean")
        assert result["status"] == "ok"
        assert result["original_draft_id"] == "draft-clean"

    def test_clean_draft_no_override_audit(self, db_path):
        rd.approve_draft("draft-clean")
        rows = self._rows(
            db_path,
            "SELECT * FROM audit_trail WHERE action = 'placeholder_guard_override'",
        )
        assert rows == []

    def test_force_override_approves(self, db_path):
        result = rd.approve_draft("draft-tokens", force_placeholders=True)
        assert result["status"] == "ok"

    def test_force_override_writes_audit_entry(self, db_path):
        rd.approve_draft("draft-tokens", reviewer="alice", force_placeholders=True)
        rows = self._rows(
            db_path,
            "SELECT * FROM audit_trail WHERE action = 'placeholder_guard_override'",
        )
        assert len(rows) == 1
        assert "alice" in rows[0]["details"]
        assert "[UEI_NUMBER]" in rows[0]["details"]

    def test_force_override_recorded_in_metadata(self, db_path):
        result = rd.approve_draft("draft-tokens", force_placeholders=True)
        rows = self._rows(
            db_path,
            "SELECT metadata FROM proposal_section_drafts WHERE id = ?",
            (result["approved_draft_id"],),
        )
        meta = json.loads(rows[0]["metadata"])
        assert "[UEI_NUMBER]" in meta["placeholder_guard_override"]

    def test_unknown_draft_still_errors(self, db_path):
        result = rd.approve_draft("nope")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# PUT /api/govcon/drafts/<id>/approve gate (dashboard API)
# ---------------------------------------------------------------------------

_PERMIT = SimpleNamespace(permit=True, policy_name="test-permit", reason="test")


class TestApproveEndpointGate:
    @pytest.fixture()
    def api_app(self, tmp_path):
        db_path = _make_db(tmp_path)

        from tools.dashboard.api.govcon import govcon_api

        flask_app = Flask(__name__)
        flask_app.config["TESTING"] = True
        flask_app.register_blueprint(govcon_api)
        return flask_app, db_path

    def _put(self, api_app_pair, draft_id, body=None):
        flask_app, db_path = api_app_pair
        import tools.security.abac_engine as abac

        with patch(
            "tools.dashboard.api.govcon._get_db",
            side_effect=lambda: _SqliteCompatConn(db_path),
        ), patch.object(abac, "evaluate", return_value=_PERMIT):
            with flask_app.test_client() as c:
                return c.put(
                    f"/api/govcon/drafts/{draft_id}/approve", json=body or {}
                )

    def test_blocked_returns_409(self, api_app):
        resp = self._put(api_app, "draft-tokens")
        assert resp.status_code == 409

    def test_blocked_payload_names_gate_and_tokens(self, api_app):
        data = self._put(api_app, "draft-tokens").get_json()
        assert data["gate"] == "placeholder_guard"
        assert "[UEI_NUMBER]" in data["placeholder_tokens"]

    def test_blocked_writes_no_approved_row(self, api_app):
        _flask_app, db_path = api_app
        self._put(api_app, "draft-tokens")
        conn = sqlite3.connect(str(db_path))
        n = conn.execute(
            "SELECT COUNT(*) FROM proposal_section_drafts "
            "WHERE status = 'approved' AND id != 'draft-approved'"
        ).fetchone()[0]
        conn.close()
        assert n == 0

    def test_clean_draft_approves_200(self, api_app):
        resp = self._put(api_app, "draft-clean")
        assert resp.status_code == 200
        assert resp.get_json()["approved"] is True

    def test_force_override_approves_200(self, api_app):
        resp = self._put(api_app, "draft-tokens", {"force_placeholders": True})
        assert resp.status_code == 200

    def test_force_override_writes_audit_entry(self, api_app):
        _flask_app, db_path = api_app
        self._put(
            api_app,
            "draft-tokens",
            {"force_placeholders": True, "reviewed_by": "bob"},
        )
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_trail WHERE action = 'placeholder_guard_override'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert "bob" in rows[0]["details"]
        assert "[CAGE_CODE]" in rows[0]["details"]

    def test_unknown_draft_returns_404(self, api_app):
        resp = self._put(api_app, "nope")
        assert resp.status_code == 404
