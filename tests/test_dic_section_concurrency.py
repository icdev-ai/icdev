# CUI // SP-CTI
"""Optimistic-concurrency tests for DIC collaborative section editing.

Closes the "collaborative document update" gap: two team members editing the same
section must not silently clobber each other. The content-update path stamps an
integer ``rev`` on every write and refuses a stale edit (HTTP 409) without
overwriting the newer content.

These tests exercise ``_update_section_content`` directly against a throwaway
SQLite table, so they need neither the Flask app, the role layer, nor the full
conftest schema (which would otherwise force PostgreSQL fixtures).
"""
import sqlite3

import pytest

from tools.document_intelligence.blueprint import _update_section_content


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE dic_sections (
            section_id  TEXT PRIMARY KEY,
            version_id  TEXT,
            doc_id      TEXT,
            heading     TEXT,
            content     TEXT,
            status      TEXT DEFAULT 'draft',
            origin      TEXT DEFAULT 'ai_generated',
            created_at  TEXT,
            created_by  TEXT,
            rev         INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
        "status, origin, created_at, created_by, rev) VALUES "
        "('s1','v1','d1','Intro','original', 'draft', 'ai_generated', 'now', 'alice', 1)"
    )
    conn.commit()
    return conn


def test_update_increments_rev_and_marks_human_authored():
    conn = _make_db()
    res = _update_section_content(conn, "s1", "edited by alice", base_rev=1, editor="alice")
    assert res["ok"] is True
    assert res["rev"] == 2
    row = conn.execute(
        "SELECT content, status, origin, created_by, rev FROM dic_sections WHERE section_id='s1'"
    ).fetchone()
    assert row[0] == "edited by alice"
    assert row[1] == "draft"
    assert row[2] == "human_authored"
    assert row[3] == "alice"
    assert row[4] == 2


def test_stale_base_rev_is_rejected_without_clobbering():
    conn = _make_db()
    # Alice saves first: rev 1 -> 2.
    first = _update_section_content(conn, "s1", "alice version", base_rev=1, editor="alice")
    assert first["ok"] is True and first["rev"] == 2

    # Bob still holds the stale rev 1 and tries to save.
    second = _update_section_content(conn, "s1", "bob version", base_rev=1, editor="bob")
    assert second["ok"] is False
    assert second["conflict"] is True
    assert second["current_rev"] == 2
    assert second["current_content"] == "alice version"

    # Bob's stale write must NOT have overwritten Alice's content.
    row = conn.execute("SELECT content, rev FROM dic_sections WHERE section_id='s1'").fetchone()
    assert row[0] == "alice version"
    assert row[1] == 2


def test_fresh_base_rev_after_conflict_succeeds():
    conn = _make_db()
    _update_section_content(conn, "s1", "alice version", base_rev=1, editor="alice")
    # Bob re-reads the current rev (2) and re-applies — now it lands.
    res = _update_section_content(conn, "s1", "bob merged version", base_rev=2, editor="bob")
    assert res["ok"] is True
    assert res["rev"] == 3
    row = conn.execute("SELECT content FROM dic_sections WHERE section_id='s1'").fetchone()
    assert row[0] == "bob merged version"


def test_legacy_client_without_base_rev_still_writes_and_bumps_rev():
    conn = _make_db()
    res = _update_section_content(conn, "s1", "legacy save", base_rev=None, editor="carol")
    assert res["ok"] is True
    assert res["rev"] == 2
    row = conn.execute("SELECT content, rev FROM dic_sections WHERE section_id='s1'").fetchone()
    assert row[0] == "legacy save"
    assert row[1] == 2


def test_missing_section_reports_missing():
    conn = _make_db()
    res = _update_section_content(conn, "does-not-exist", "x", base_rev=1, editor="alice")
    assert res["ok"] is False
    assert res.get("missing") is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
