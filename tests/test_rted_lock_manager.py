# CUI // SP-CTI
"""Tests for the DIC section lock manager (rted-lock-01)."""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Minimal in-memory SQLite shim ─────────────────────────────────────────────

class _FakeConn:
    """Thin wrapper around sqlite3 that mimics the ICDEV storage cursor interface."""

    def __init__(self, db):
        self._db = db
        self._db.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        # Translate %s placeholders to ? for SQLite
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
    db = sqlite3.connect(str(tmp_path / "lock_test.db"))
    conn = _FakeConn(db)
    yield conn
    db.close()


@pytest.fixture(autouse=True)
def patch_get_connection(fake_conn):
    from contextlib import contextmanager

    @contextmanager
    def _fake_get_connection():
        yield fake_conn

    with patch("tools.document_intelligence.lock_manager.get_connection", _fake_get_connection):
        yield


# ── Import under patch ────────────────────────────────────────────────────────

from tools.document_intelligence.lock_manager import (  # noqa: E402
    acquire_lock,
    get_lock,
    purge_expired_locks,
    release_lock,
    renew_lock,
)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_acquire_returns_lock_dict():
    lock = acquire_lock("sec-001", "user_a")
    assert lock is not None
    assert lock["locked_by"] == "user_a"
    assert lock["section_id"] == "sec-001"
    assert "expires_at" in lock
    assert "lock_id" in lock


def test_double_acquire_different_user_returns_none():
    acquire_lock("sec-002", "user_a")
    result = acquire_lock("sec-002", "user_b")
    assert result is None


def test_same_user_reacquire_renews_and_returns_lock():
    lock1 = acquire_lock("sec-003", "user_a")
    lock2 = acquire_lock("sec-003", "user_a")
    assert lock2 is not None
    assert lock2["locked_by"] == "user_a"
    # Renewed expiry should be >= original
    assert lock2["expires_at"] >= lock1["expires_at"]


def test_release_by_holder_returns_true():
    acquire_lock("sec-004", "user_a")
    assert release_lock("sec-004", "user_a") is True


def test_release_by_non_holder_returns_false():
    acquire_lock("sec-005", "user_a")
    assert release_lock("sec-005", "user_b") is False


def test_release_nonexistent_lock_returns_false():
    assert release_lock("sec-999", "user_a") is False


def test_get_lock_returns_dict_for_active_lock():
    acquire_lock("sec-006", "user_a")
    lock = get_lock("sec-006")
    assert lock is not None
    assert lock["locked_by"] == "user_a"


def test_get_lock_returns_none_when_no_lock():
    assert get_lock("sec-007-unlocked") is None


def test_get_lock_expires_stale_row(fake_conn):
    # Insert a lock with past expiry directly
    from tools.document_intelligence.lock_manager import _ensure_table, _now_iso
    _ensure_table(fake_conn)
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    fake_conn.execute(
        "INSERT INTO dic_section_locks (lock_id, section_id, locked_by, locked_at, expires_at, classification) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("lk_stale", "sec-008", "user_a", _now_iso(), past, "CUI"),
    )
    fake_conn.commit()
    result = get_lock("sec-008")
    assert result is None


def test_acquire_after_expiry_succeeds(fake_conn):
    from tools.document_intelligence.lock_manager import _ensure_table, _now_iso
    _ensure_table(fake_conn)
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    fake_conn.execute(
        "INSERT INTO dic_section_locks (lock_id, section_id, locked_by, locked_at, expires_at, classification) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("lk_old", "sec-009", "user_a", _now_iso(), past, "CUI"),
    )
    fake_conn.commit()
    lock = acquire_lock("sec-009", "user_b")
    assert lock is not None
    assert lock["locked_by"] == "user_b"


def test_renew_by_holder_returns_true():
    acquire_lock("sec-010", "user_a")
    assert renew_lock("sec-010", "user_a") is True


def test_renew_by_non_holder_returns_false():
    acquire_lock("sec-011", "user_a")
    assert renew_lock("sec-011", "user_b") is False


def test_purge_expired_removes_stale(fake_conn):
    from tools.document_intelligence.lock_manager import _ensure_table, _now_iso
    _ensure_table(fake_conn)
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    fake_conn.execute(
        "INSERT INTO dic_section_locks (lock_id, section_id, locked_by, locked_at, expires_at, classification) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("lk_purge", "sec-012", "user_a", _now_iso(), past, "CUI"),
    )
    fake_conn.commit()
    removed = purge_expired_locks()
    assert removed >= 1
    assert get_lock("sec-012") is None


def test_purge_keeps_active_locks():
    acquire_lock("sec-013", "user_a", ttl_seconds=300)
    purge_expired_locks()
    assert get_lock("sec-013") is not None
