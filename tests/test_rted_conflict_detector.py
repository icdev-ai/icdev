# CUI // SP-CTI
"""Tests for the DIC conflict detector (rted-conf-01)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import _sql_compat  # noqa: E402
from tools.document_intelligence.conflict_detector import (  # noqa: E402
    compute_hash,
    check_conflict,
    get_section_state,
)


# ── Minimal on-disk SQLite fixture ───────────────────────────────────────────

@pytest.fixture()
def conn(tmp_path):
    """A translating connection, standing in for the caller's ``get_connection()``.

    ``conflict_detector`` authors PG-native ``%s`` placeholders and runs them on
    whatever connection its caller hands it. At runtime that is a
    ``StorageConnection``, which rewrites ``%s`` -> ``?`` on SQLite. A bare
    ``sqlite3`` connection has no such layer, so every query raised
    ``sqlite3.OperationalError: near "%": syntax error`` — 7 of this file's 11
    tests. ``_sql_compat`` delegates to the same ``translate_sql`` the runtime
    uses, so this fixture cannot drift from the behaviour it stands in for.
    """
    db = _sql_compat.connect(tmp_path / "conf_test.db")
    db.execute("""
        CREATE TABLE dic_sections (
            section_id TEXT PRIMARY KEY,
            content    TEXT
        )
    """)
    db.commit()
    yield db
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
