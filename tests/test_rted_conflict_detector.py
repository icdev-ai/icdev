# CUI // SP-CTI
"""Tests for the DIC conflict detector (rted-conf-01)."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.document_intelligence.conflict_detector import (  # noqa: E402
    compute_hash,
    check_conflict,
    get_section_state,
)


# ── Minimal in-memory SQLite fixture ─────────────────────────────────────────

class _FakeConn:
    """Minimal conn shim; conflict_detector uses ? placeholders via existing conn."""

    def __init__(self, db):
        self._db = db
        self._db.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        # conflict_detector passes its caller's conn which uses ?
        return self._db.execute(sql, params)

    def commit(self):
        self._db.commit()

    def close(self):
        pass


@pytest.fixture()
def conn(tmp_path):
    db = sqlite3.connect(str(tmp_path / "conf_test.db"))
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE dic_sections (
            section_id TEXT PRIMARY KEY,
            content    TEXT
        )
    """)
    db.commit()
    c = _FakeConn(db)
    yield c
    db.close()


def _insert(conn, section_id, content):
    conn.execute(
        "INSERT OR REPLACE INTO dic_sections (section_id, content) VALUES (?, ?)",
        (section_id, content),
    )
    conn.commit()


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_compute_hash_returns_8_hex_chars():
    h = compute_hash("hello world")
    assert len(h) == 8
    int(h, 16)  # must be valid hex


def test_compute_hash_same_content_same_hash():
    assert compute_hash("abc") == compute_hash("abc")


def test_compute_hash_different_content_different_hash():
    assert compute_hash("abc") != compute_hash("abcd")


def test_compute_hash_empty_string():
    h = compute_hash("")
    assert len(h) == 8


def test_get_section_state_returns_content_and_hash(conn):
    _insert(conn, "sec-01", "some content")
    state = get_section_state(conn, "sec-01")
    assert state is not None
    assert state["content"] == "some content"
    assert state["hash"] == compute_hash("some content")


def test_get_section_state_returns_none_for_missing(conn):
    assert get_section_state(conn, "sec-ghost") is None


def test_check_conflict_no_conflict_when_hash_matches(conn):
    _insert(conn, "sec-02", "hello")
    h = compute_hash("hello")
    result = check_conflict(conn, "sec-02", h)
    assert result["conflict"] is False
    assert result["current_hash"] == h


def test_check_conflict_conflict_when_hash_differs(conn):
    _insert(conn, "sec-03", "new content by someone else")
    old_hash = compute_hash("my old content")
    result = check_conflict(conn, "sec-03", old_hash)
    assert result["conflict"] is True
    assert result["current_content"] == "new content by someone else"
    assert "current_hash" in result


def test_check_conflict_missing_section_returns_no_conflict(conn):
    result = check_conflict(conn, "sec-ghost", "deadbeef")
    assert result["conflict"] is False


def test_check_conflict_exposes_current_content(conn):
    _insert(conn, "sec-04", "their version")
    result = check_conflict(conn, "sec-04", "aaaaaaaa")
    assert result["current_content"] == "their version"


def test_check_conflict_empty_content(conn):
    _insert(conn, "sec-05", "")
    h = compute_hash("")
    result = check_conflict(conn, "sec-05", h)
    assert result["conflict"] is False
