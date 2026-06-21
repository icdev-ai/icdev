# CUI // SP-CTI
"""Tests for the DIC section edit history recorder (rted-hist-01/02)."""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── SQLite shim (mirrors pattern from test_rted_lock_manager.py) ──────────────

class _FakeConn:
    def __init__(self, db):
        self._db = db
        self._db.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        sql_sq = sql.replace("%s", "?")
        return self._db.execute(sql_sq, params)

    def commit(self):
        self._db.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.commit()


@pytest.fixture()
def fake_conn(tmp_path):
    db = sqlite3.connect(str(tmp_path / "history_test.db"))
    conn = _FakeConn(db)
    yield conn
    db.close()


@pytest.fixture(autouse=True)
def patch_get_connection(fake_conn):
    from contextlib import contextmanager

    @contextmanager
    def _fake_get_connection():
        yield fake_conn

    with patch("tools.document_intelligence.history_recorder.get_connection", _fake_get_connection):
        yield


from tools.document_intelligence.history_recorder import (  # noqa: E402
    get_section_history,
    record_edit,
    _unified_diff_summary,
)


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_record_edit_returns_edit_id():
    edit_id = record_edit("sec-001", "alice", "old text", "new text")
    assert edit_id is not None
    assert edit_id.startswith("eh_")


def test_record_edit_noop_for_identical_content():
    result = record_edit("sec-002", "alice", "same", "same")
    assert result is None


def test_record_edit_char_delta_positive(fake_conn):
    from tools.document_intelligence.history_recorder import _ensure_table
    record_edit("sec-003", "alice", "short", "much longer text here")
    _ensure_table(fake_conn)
    row = fake_conn.execute(
        "SELECT char_delta FROM dic_edit_history WHERE section_id = ?", ("sec-003",)
    ).fetchone()
    assert row is not None
    assert dict(row)["char_delta"] > 0


def test_record_edit_char_delta_negative(fake_conn):
    from tools.document_intelligence.history_recorder import _ensure_table
    record_edit("sec-004", "alice", "much longer text here", "short")
    _ensure_table(fake_conn)
    row = fake_conn.execute(
        "SELECT char_delta FROM dic_edit_history WHERE section_id = ?", ("sec-004",)
    ).fetchone()
    assert row is not None
    assert dict(row)["char_delta"] < 0


def test_record_edit_diff_summary_nonempty(fake_conn):
    from tools.document_intelligence.history_recorder import _ensure_table
    record_edit("sec-005", "bob", "line one\nline two", "line one\nline two edited")
    _ensure_table(fake_conn)
    row = fake_conn.execute(
        "SELECT diff_summary FROM dic_edit_history WHERE section_id = ?", ("sec-005",)
    ).fetchone()
    assert row is not None
    summary = dict(row)["diff_summary"]
    assert len(summary) > 0
    assert "+" in summary or "-" in summary


def test_record_edit_empty_before_content():
    edit_id = record_edit("sec-006", "alice", "", "brand new content")
    assert edit_id is not None


def test_record_edit_multiple_saves_all_recorded(fake_conn):
    from tools.document_intelligence.history_recorder import _ensure_table
    record_edit("sec-007", "alice", "v1", "v2")
    record_edit("sec-007", "alice", "v2", "v3")
    _ensure_table(fake_conn)
    rows = fake_conn.execute(
        "SELECT edit_id FROM dic_edit_history WHERE section_id = ?", ("sec-007",)
    ).fetchall()
    assert len(rows) == 2


def test_get_section_history_returns_list():
    record_edit("sec-008", "alice", "before", "after")
    history = get_section_history("sec-008")
    assert isinstance(history, list)
    assert len(history) == 1
    entry = history[0]
    assert entry["section_id"] == "sec-008"
    assert entry["editor"] == "alice"
    assert "char_delta" in entry
    assert "diff_summary" in entry
    assert "edited_at" in entry


def test_get_section_history_empty_for_unknown_section():
    history = get_section_history("sec-999-unknown")
    assert history == []


def test_get_section_history_ordered_most_recent_first():
    record_edit("sec-010", "alice", "v1", "v2")
    record_edit("sec-010", "bob", "v2", "v3")
    history = get_section_history("sec-010")
    assert len(history) == 2
    # Most recent (bob's edit) should be first
    assert history[0]["editor"] == "bob"
    assert history[1]["editor"] == "alice"


def test_unified_diff_summary_truncates_long_diff():
    big_before = "\n".join(f"line {i}" for i in range(100))
    big_after = "\n".join(f"edited line {i}" for i in range(100))
    summary = _unified_diff_summary(big_before, big_after, max_lines=5)
    assert "more lines" in summary


def test_unified_diff_summary_empty_for_no_change():
    summary = _unified_diff_summary("same", "same")
    assert summary == ""
