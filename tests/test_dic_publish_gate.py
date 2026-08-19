# CUI // SP-CTI
"""Tests for ground-dic-05: DIC publish gate on unresolved placeholders +
numeric-claims consistency merged into consistency_checker.

Covers:
  - consistency_checker.check_numeric_claims accepts dic_sections-shaped rows
  - consistency_checker.check_version_consistency reports placeholders +
    numeric conflicts for a version's sections
  - POST /api/review/<id>/approve (type=version) blocks with 409 while any
    section has unresolved [PLACEHOLDER] tokens, unless force=true
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── SQLite shim (replaces %s → ? for PG-style SQL) ────────────────────────────

class _FakeConn:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._db.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._db.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._db.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.commit()


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS dic_versions (
        version_id TEXT PRIMARY KEY, doc_id TEXT, version_no INTEGER,
        origin TEXT, status TEXT, review_notes TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS dic_sections (
        section_id TEXT PRIMARY KEY, version_id TEXT, doc_id TEXT,
        heading TEXT, content TEXT, status TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS dic_review_notes (
        note_id TEXT PRIMARY KEY, item_id TEXT, item_type TEXT,
        note_text TEXT, reviewer_id TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS dic_ssp_fragments (
        fragment_id TEXT PRIMARY KEY, status TEXT,
        reviewed_by TEXT, reviewed_at TEXT
    )""",
    # cef-ui-03: /api/review/<id>/approve now audits the human decision
    # fail-closed, BEFORE the status moves, so audit_trail is part of this
    # route's substrate. Kept in step with tests/conftest.py's audit_trail,
    # which mirrors the LIVE PostgreSQL shape.
    """CREATE TABLE IF NOT EXISTS audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
        event_type TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        details TEXT, affected_files TEXT, classification TEXT DEFAULT 'CUI',
        ip_address TEXT, session_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        hash TEXT, previous_hash TEXT, signature TEXT
    )""",
]


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
def db(tmp_path):
    path = str(tmp_path / "dic_publish_gate.db")
    conn = sqlite3.connect(path)
    for ddl in _SCHEMA:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    return path


def _seed_version(db, version_id, sections):
    """Insert a pending_review version with (heading, content) sections."""
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at) "
        "VALUES (?, ?, 1, 'ai_generated', 'pending_review', '2026-07-08T00:00:00Z')",
        (version_id, f"doc-{version_id}"),
    )
    for i, (heading, content) in enumerate(sections, start=1):
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending_review', ?)",
            (f"sec-{version_id}-{i}", version_id, f"doc-{version_id}",
             heading, content, f"2026-07-08T00:00:0{i}Z"),
        )
    conn.commit()
    conn.close()


@pytest.fixture()
def app(db, monkeypatch):
    flask_app = Flask(
        __name__,
        template_folder=str(
            Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
        ),
    )
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"

    # audit_logger binds `get_connection` at import and is not one of the
    # patched seams below, so point the ambient storage at this fixture's own
    # SQLite file instead of stubbing the writer out. The audit row this route
    # now writes is then a real one, written by the real writer.
    import tools.db.storage as _storage
    monkeypatch.setenv("ICDEV_DB_PATH", db)
    monkeypatch.setattr(_storage, "DB_PATH", db, raising=False)

    def _make_shim():
        return _FakeConn(sqlite3.connect(db))

    @contextmanager
    def _fake_get_connection():
        c = _make_shim()
        try:
            yield c
        finally:
            c.commit()

    patches = [
        patch("tools.document_intelligence.blueprint._conn", _make_shim),
        patch("tools.document_intelligence.blueprint._require_role",
              lambda *a, **kw: True),
        patch("tools.document_intelligence.blueprint._collection_id_from_version",
              lambda _vid: "default"),
        patch("tools.document_intelligence.consistency_checker.get_connection",
              _fake_get_connection),
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


_BASE = "/document-intelligence"


def _version_status(db, version_id):
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT status FROM dic_versions WHERE version_id = ?", (version_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ══════════════════════════════════════════════════════════════════════════════
# consistency_checker.check_numeric_claims (merged from content_grounding)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckNumericClaims:
    def test_detects_rom_total_conflict_across_dic_sections(self):
        from tools.document_intelligence.consistency_checker import check_numeric_claims
        sections = [
            {"heading": "Cost Estimate", "content": "The ROM total is $1.2M for phase one."},
            {"heading": "Executive Summary", "content": "Overall ROM total: $1.5M."},
        ]
        conflicts = check_numeric_claims(sections)
        types = [c["type"] for c in conflicts]
        assert "rom_total_mismatch" in types
        rom = next(c for c in conflicts if c["type"] == "rom_total_mismatch")
        assert rom["severity"] == "error"
        assert "Cost Estimate" in rom["sections"]
        assert "Executive Summary" in rom["sections"]

    def test_detects_prototype_timeline_conflict(self):
        from tools.document_intelligence.consistency_checker import check_numeric_claims
        sections = [
            {"heading": "Schedule", "content": "Prototype delivery 6 months after award."},
            {"heading": "Approach", "content": "A working prototype within 9 months of award."},
        ]
        conflicts = check_numeric_claims(sections)
        assert any(c["type"] == "prototype_timeline_mismatch" for c in conflicts)

    def test_consistent_sections_produce_no_conflicts(self):
        from tools.document_intelligence.consistency_checker import check_numeric_claims
        sections = [
            {"heading": "Cost", "content": "ROM total of $2M."},
            {"heading": "Summary", "content": "The ROM total remains $2M."},
        ]
        assert check_numeric_claims(sections) == []

    def test_empty_sections(self):
        from tools.document_intelligence.consistency_checker import check_numeric_claims
        assert check_numeric_claims([]) == []


# ══════════════════════════════════════════════════════════════════════════════
# consistency_checker.check_version_consistency
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckVersionConsistency:
    def test_reports_placeholders_and_conflicts(self, app, db):
        _seed_version(db, "ver-gate1", [
            ("Overview", "Contact us at [POC_EMAIL] for details. ROM total: $1M."),
            ("Cost", "The ROM total is $3M."),
        ])
        from tools.document_intelligence.consistency_checker import check_version_consistency
        report = check_version_consistency("ver-gate1")
        assert report["section_count"] == 2
        assert len(report["placeholders"]) == 1
        assert report["placeholders"][0]["item_number"] == "Overview"
        assert "[POC_EMAIL]" in report["placeholders"][0]["placeholders"]
        assert any(c["type"] == "rom_total_mismatch" for c in report["numeric_conflicts"])

    def test_clean_version_passes(self, app, db):
        _seed_version(db, "ver-clean1", [
            ("Overview", "Everything resolved. See [our site](https://x.gov)."),
            ("Cost", "ROM total: $3M."),
        ])
        from tools.document_intelligence.consistency_checker import check_version_consistency
        report = check_version_consistency("ver-clean1")
        assert report["placeholders"] == []
        assert report["numeric_conflicts"] == []

    def test_unknown_version_is_empty_pass(self, app):
        from tools.document_intelligence.consistency_checker import check_version_consistency
        report = check_version_consistency("ver-does-not-exist")
        assert report["placeholders"] == []
        assert report["numeric_conflicts"] == []
        assert report["section_count"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Publish gate on POST /api/review/<id>/approve (type=version)
# ══════════════════════════════════════════════════════════════════════════════

class TestApprovePublishGate:
    def test_blocks_approve_with_unresolved_placeholders(self, client, db):
        _seed_version(db, "ver-blk1", [
            ("Overview", "POC is [POC_NAME] at [POC_EMAIL]."),
            ("Scope", "All resolved here."),
        ])
        r = client.post(f"{_BASE}/api/review/ver-blk1/approve",
                        json={"type": "version"})
        assert r.status_code == 409
        body = r.get_json()
        assert body["error"] == "unresolved_placeholders"
        assert body["placeholders"][0]["item_number"] == "Overview"
        # version must NOT have moved out of pending_review
        assert _version_status(db, "ver-blk1") == "pending_review"

    def test_force_override_approves_and_records_note(self, client, db):
        _seed_version(db, "ver-frc1", [
            ("Overview", "POC is [POC_NAME]."),
        ])
        r = client.post(f"{_BASE}/api/review/ver-frc1/approve",
                        json={"type": "version", "force": True,
                              "reviewer": "reviewer-1"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "approved"
        assert body["forced"] is True
        assert _version_status(db, "ver-frc1") == "approved"
        # override is audited via a review note
        conn = sqlite3.connect(db)
        note = conn.execute(
            "SELECT note_text FROM dic_review_notes WHERE item_id = 'ver-frc1'"
        ).fetchone()
        conn.close()
        assert note is not None
        assert "FORCE-APPROVED" in note[0]
        assert "[POC_NAME]" in note[0]

    def test_clean_version_approves_normally(self, client, db):
        _seed_version(db, "ver-ok1", [
            ("Overview", "All placeholders resolved."),
        ])
        r = client.post(f"{_BASE}/api/review/ver-ok1/approve",
                        json={"type": "version"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "approved"
        assert "forced" not in body
        assert _version_status(db, "ver-ok1") == "approved"
        # cef-ui-03: dic_versions holds only the CURRENT status, so without this
        # row an approval leaves no evidence of who published it. Written BEFORE
        # the status moves, and fail-closed.
        assert _audit_actions(db) == ["dic_version.approved"]

    def test_reject_is_audited_as_deliberately_as_approve(self, client, db):
        """cef-ui-03: a surface that records only its positive outcome can
        answer 'was this ever approved?' but never 'was this ever reviewed?'."""
        _seed_version(db, "ver-rej1", [("Overview", "Nope.")])
        r = client.post(f"{_BASE}/api/review/ver-rej1/reject",
                        json={"type": "version", "reviewer": "alice"})
        assert r.status_code == 200
        assert _version_status(db, "ver-rej1") == "rejected"
        assert _audit_actions(db) == ["dic_version.rejected"]

    def test_numeric_conflicts_reported_but_do_not_block(self, client, db):
        _seed_version(db, "ver-num1", [
            ("Cost", "ROM total: $1M."),
            ("Summary", "The ROM total is $2M."),
        ])
        r = client.post(f"{_BASE}/api/review/ver-num1/approve",
                        json={"type": "version"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "approved"
        assert any(c["type"] == "rom_total_mismatch"
                   for c in body["numeric_conflicts"])
        assert _version_status(db, "ver-num1") == "approved"

    def test_fragment_approve_path_unaffected(self, client, db):
        """Non-version approves skip the gate entirely."""
        r = client.post(f"{_BASE}/api/review/frag-1/approve",
                        json={"type": "fragment"})
        assert r.status_code == 200
        assert r.get_json()["status"] == "approved"
        # cef-ui-03: this fixture's dic_ssp_fragments is the narrow legacy shape
        # acoic cannot read, so the route takes its fallback — which used to
        # UPDATE with no audit at all. There is no longer an unaudited branch.
        assert _audit_actions(db) == ["dic_ssp_fragment.approved"]
