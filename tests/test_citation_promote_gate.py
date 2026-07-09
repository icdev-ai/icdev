# CUI // SP-CTI
"""Tests: promote gate on citation defects for proposals (trust-cite-02).

Covers:
    - tools.govcon.response_drafter.citation_findings()
    - tools.govcon.response_drafter.approve_draft() citation gate
    - PUT /api/govcon/drafts/<id>/approve 409 gate=citation_guard
"""

import importlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

rd = importlib.import_module("tools.govcon.response_drafter")


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
    id TEXT PRIMARY KEY, status TEXT, notes TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT, entity_id TEXT,
    old_status TEXT, new_status TEXT, changed_by TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT, created_at TEXT, event_type TEXT, actor TEXT, action TEXT,
    details TEXT, session_id TEXT
);
"""

# (id, content, kb_ids, method)
_ROWS = [
    ("cite-missing", "Our platform delivers zero-trust networking.", '["kb1"]', "two_tier_llm"),
    ("cite-valid", "Our platform delivers zero-trust networking [source: kb1].", '["kb1"]', "two_tier_llm"),
    ("cite-hallucinated", "Zero-trust networking [source: kb9].", '["kb1"]', "two_tier_llm"),
    ("cite-template", "Canned prose with no inline citations.", '["kb1"]', "template"),
    ("cite-nokb", "Uncited prose but no knowledge blocks used.", "[]", "two_tier_llm"),
]


def _make_db(tmp_path):
    db_path = tmp_path / "cite_gate.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    for draft_id, content, kb_ids, method in _ROWS:
        conn.execute(
            "INSERT INTO proposal_section_drafts "
            "(id, opportunity_id, shall_statement_id, knowledge_block_ids, draft_content, "
            " draft_method, status, created_at, metadata) "
            "VALUES (?, 'opp-1', 'shall-1', ?, ?, ?, 'draft', '2026-07-01T00:00:00', '{}')",
            (draft_id, kb_ids, content, method),
        )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# citation_findings()
# ---------------------------------------------------------------------------

class TestCitationFindings:
    def test_missing_flagged_for_grounded_llm_draft(self):
        f = rd.citation_findings(
            {"draft_content": "Uncited.", "knowledge_block_ids": '["kb1"]', "draft_method": "two_tier_llm"}
        )
        assert any(x["issue"] == "missing_citations" for x in f)

    def test_valid_citation_passes(self):
        f = rd.citation_findings(
            {"draft_content": "X [source: kb1].", "knowledge_block_ids": '["kb1"]', "draft_method": "two_tier_llm"}
        )
        assert f == []

    def test_hallucinated_citation_flagged(self):
        f = rd.citation_findings(
            {"draft_content": "X [source: kb9].", "knowledge_block_ids": '["kb1"]', "draft_method": "two_tier_llm"}
        )
        assert any(x["issue"] == "hallucinated_citation" for x in f)

    def test_template_draft_not_required(self):
        f = rd.citation_findings(
            {"draft_content": "Canned.", "knowledge_block_ids": '["kb1"]', "draft_method": "template"}
        )
        assert f == []

    def test_no_kb_not_required(self):
        f = rd.citation_findings(
            {"draft_content": "Uncited.", "knowledge_block_ids": "[]", "draft_method": "two_tier_llm"}
        )
        assert f == []


# ---------------------------------------------------------------------------
# approve_draft() citation gate
# ---------------------------------------------------------------------------

class TestApproveDraftCitationGate:
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

    def test_blocked_when_citations_missing(self, db_path):
        result = rd.approve_draft("cite-missing")
        assert result["status"] == "blocked"
        assert result["gate"] == "citation_guard"

    def test_blocked_when_citation_hallucinated(self, db_path):
        result = rd.approve_draft("cite-hallucinated")
        assert result["status"] == "blocked"
        assert result["gate"] == "citation_guard"

    def test_valid_citation_approves(self, db_path):
        result = rd.approve_draft("cite-valid")
        assert result["status"] == "ok"

    def test_template_draft_approves(self, db_path):
        assert rd.approve_draft("cite-template")["status"] == "ok"

    def test_no_kb_draft_approves(self, db_path):
        assert rd.approve_draft("cite-nokb")["status"] == "ok"

    def test_force_override_approves_and_audits(self, db_path):
        result = rd.approve_draft("cite-missing", reviewer="alice", force_citations=True)
        assert result["status"] == "ok"
        rows = self._rows(db_path, "SELECT * FROM audit_trail WHERE action = 'citation_guard_override'")
        assert len(rows) == 1
        assert "alice" in rows[0]["details"]

    def test_force_override_recorded_in_metadata(self, db_path):
        result = rd.approve_draft("cite-missing", force_citations=True)
        rows = self._rows(
            db_path,
            "SELECT metadata FROM proposal_section_drafts WHERE id = ?",
            (result["approved_draft_id"],),
        )
        meta = json.loads(rows[0]["metadata"])
        assert "citation_guard_override" in meta


# ---------------------------------------------------------------------------
# PUT /api/govcon/drafts/<id>/approve citation gate
# ---------------------------------------------------------------------------

_PERMIT = SimpleNamespace(permit=True, policy_name="test-permit", reason="test")


class TestApproveEndpointCitationGate:
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
                return c.put(f"/api/govcon/drafts/{draft_id}/approve", json=body or {})

    def test_blocked_returns_409(self, api_app):
        resp = self._put(api_app, "cite-missing")
        assert resp.status_code == 409
        assert resp.get_json()["gate"] == "citation_guard"

    def test_valid_citation_approves_200(self, api_app):
        resp = self._put(api_app, "cite-valid")
        assert resp.status_code == 200

    def test_force_override_approves_200(self, api_app):
        resp = self._put(api_app, "cite-missing", {"force_citations": True})
        assert resp.status_code == 200
