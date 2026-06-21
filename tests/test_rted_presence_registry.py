# CUI // SP-CTI
"""Tests for the DIC presence registry (rted-pres-01)."""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── SQLite shim ───────────────────────────────────────────────────────────────

class _FakeConn:
    def __init__(self, db):
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


@pytest.fixture()
def fake_conn(tmp_path):
    db = sqlite3.connect(str(tmp_path / "presence_test.db"))
    conn = _FakeConn(db)
    yield conn
    db.close()


@pytest.fixture(autouse=True)
def patch_get_connection(fake_conn):
    from contextlib import contextmanager

    @contextmanager
    def _fake():
        yield fake_conn

    with patch("tools.document_intelligence.presence_registry.get_connection", _fake):
        yield


from tools.document_intelligence.presence_registry import (  # noqa: E402
    join_document,
    heartbeat,
    leave_document,
    get_present_users,
    purge_stale,
    _ensure_table,
    _now_iso,
)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_join_returns_session_key():
    key = join_document("doc-001", "alice")
    assert key.startswith("ps_")


def test_join_user_appears_in_present():
    join_document("doc-002", "alice")
    users = get_present_users("doc-002")
    assert len(users) == 1
    assert users[0]["user_id"] == "alice"


def test_join_same_user_twice_returns_same_key():
    k1 = join_document("doc-003", "alice")
    k2 = join_document("doc-003", "alice")
    assert k1 == k2  # re-join renews and returns existing key


def test_two_users_both_present():
    join_document("doc-004", "alice")
    join_document("doc-004", "bob")
    users = get_present_users("doc-004")
    ids = {u["user_id"] for u in users}
    assert ids == {"alice", "bob"}


def test_heartbeat_returns_true_for_valid_key():
    key = join_document("doc-005", "alice")
    assert heartbeat(key) is True


def test_heartbeat_returns_false_for_unknown_key():
    assert heartbeat("ps_nosuchkey") is False


def test_leave_returns_true_for_joined_user():
    key = join_document("doc-006", "alice")
    assert leave_document(key) is True


def test_leave_removes_from_present(fake_conn):
    key = join_document("doc-007", "alice")
    leave_document(key)
    users = get_present_users("doc-007")
    assert users == []


def test_leave_unknown_key_returns_false():
    assert leave_document("ps_ghost") is False


def test_get_present_users_empty_for_unknown_doc():
    users = get_present_users("doc-999-ghost")
    assert users == []


def test_expired_session_not_returned(fake_conn):
    _ensure_table(fake_conn)
    past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    fake_conn.execute(
        "INSERT INTO dic_presence_sessions "
        "(session_key, doc_id, user_id, joined_at, last_seen, expires_at, classification) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ps_stale1", "doc-010", "ghost", _now_iso(), _now_iso(), past, "CUI"),
    )
    fake_conn.commit()
    users = get_present_users("doc-010")
    assert users == []


def test_purge_stale_removes_expired(fake_conn):
    _ensure_table(fake_conn)
    past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    fake_conn.execute(
        "INSERT INTO dic_presence_sessions "
        "(session_key, doc_id, user_id, joined_at, last_seen, expires_at, classification) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ps_purge1", "doc-011", "ghost", _now_iso(), _now_iso(), past, "CUI"),
    )
    fake_conn.commit()
    removed = purge_stale()
    assert removed >= 1


def test_purge_stale_scoped_to_doc(fake_conn):
    _ensure_table(fake_conn)
    past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    fake_conn.execute(
        "INSERT INTO dic_presence_sessions "
        "(session_key, doc_id, user_id, joined_at, last_seen, expires_at, classification) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ps_scope1", "doc-012", "ghost", _now_iso(), _now_iso(), past, "CUI"),
    )
    # Active session on a different doc — should NOT be purged
    join_document("doc-013", "active_user")
    fake_conn.commit()
    removed = purge_stale("doc-012")
    assert removed >= 1
    # doc-013 user is still there
    users = get_present_users("doc-013")
    assert len(users) == 1
