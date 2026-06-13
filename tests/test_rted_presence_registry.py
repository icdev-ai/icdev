# CUI // SP-CTI
"""Tests for tools/document_intelligence/presence_registry.py.

Covers:
- heartbeat upsert: calling heartbeat twice yields one row with updated last_seen
- get_presence: returns users active within 60 seconds, excludes older entries
- cleanup_stale: removes rows whose last_seen is more than 120 seconds old
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


from tools.db.storage import get_connection
from tools.document_intelligence import presence_registry
from tools.document_intelligence.presence_registry import (
    cleanup_stale,
    get_presence,
    heartbeat,
)


def _unique_doc() -> str:
    return f"doc-pres-{uuid.uuid4().hex[:8]}"


def _unique_user() -> str:
    return f"user-{uuid.uuid4().hex[:6]}"


def _stale_iso(seconds_ago: int = 130) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return dt.isoformat(timespec="seconds")


def _insert_presence(doc_id: str, user_id: str, last_seen: str) -> str:
    """Directly insert a presence row for test setup (bypasses heartbeat timestamp)."""
    from tools.document_intelligence.presence_registry import _session_id
    sid = _session_id(doc_id, user_id)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO dic_presence_sessions "
            "(session_id, doc_id, user_id, active_section_id, last_seen, tenant_id, classification) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, doc_id, user_id, None, last_seen, "default", "CUI"),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


# ---------------------------------------------------------------------------
# Ensure table exists before any test
# ---------------------------------------------------------------------------

def setup_module(module):
    presence_registry._TABLE_READY = False
    presence_registry._ensure_table()


# ---------------------------------------------------------------------------
# heartbeat upsert
# ---------------------------------------------------------------------------

def test_heartbeat_creates_row():
    doc_id = _unique_doc()
    user_id = _unique_user()

    sid = heartbeat(doc_id, user_id)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT session_id, doc_id, user_id FROM dic_presence_sessions WHERE session_id = ?",
            (sid,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[1] == doc_id
    assert row[2] == user_id


def test_heartbeat_upsert_updates_last_seen():
    doc_id = _unique_doc()
    user_id = _unique_user()

    sid1 = heartbeat(doc_id, user_id)

    # Insert an artificially old timestamp directly
    old_ts = _stale_iso(300)
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE dic_presence_sessions SET last_seen = ? WHERE session_id = ?",
            (old_ts, sid1),
        )
        conn.commit()
    finally:
        conn.close()

    # Second heartbeat must update last_seen
    heartbeat(doc_id, user_id)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT last_seen FROM dic_presence_sessions WHERE session_id = ?",
            (sid1,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    ts = datetime.fromisoformat(row[0])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    assert ts > datetime.now(timezone.utc) - timedelta(seconds=5), (
        f"last_seen should be recent after upsert but got {row[0]}"
    )


def test_heartbeat_upsert_does_not_duplicate_rows():
    doc_id = _unique_doc()
    user_id = _unique_user()

    heartbeat(doc_id, user_id)
    heartbeat(doc_id, user_id)
    heartbeat(doc_id, user_id)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM dic_presence_sessions WHERE doc_id = ? AND user_id = ?",
            (doc_id, user_id),
        )
        count = cur.fetchone()[0]
    finally:
        conn.close()

    assert count == 1, f"Expected 1 row after 3 heartbeats but got {count}"


def test_heartbeat_records_section_id():
    doc_id = _unique_doc()
    user_id = _unique_user()
    sid = heartbeat(doc_id, user_id, section_id="sec-intro")

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT active_section_id FROM dic_presence_sessions WHERE session_id = ?",
            (sid,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "sec-intro"


# ---------------------------------------------------------------------------
# get_presence — only returns recent entries
# ---------------------------------------------------------------------------

def test_get_presence_returns_active_user():
    doc_id = _unique_doc()
    user_id = _unique_user()

    heartbeat(doc_id, user_id)

    active = get_presence(doc_id)

    user_ids = [r["user_id"] for r in active]
    assert user_id in user_ids


def test_get_presence_excludes_old_entries():
    doc_id = _unique_doc()
    user_id = _unique_user()

    # Insert a row that is 90 seconds old (within cleanup range but outside presence TTL)
    _insert_presence(doc_id, user_id, _stale_iso(90))

    active = get_presence(doc_id)

    user_ids = [r["user_id"] for r in active]
    assert user_id not in user_ids, (
        "User with last_seen 90s ago should not appear in get_presence (TTL=60s)"
    )


def test_get_presence_empty_when_no_users():
    doc_id = _unique_doc()
    assert get_presence(doc_id) == []


def test_get_presence_multiple_users():
    doc_id = _unique_doc()
    users = [_unique_user() for _ in range(3)]

    for u in users:
        heartbeat(doc_id, u)

    active = get_presence(doc_id)
    active_user_ids = {r["user_id"] for r in active}

    for u in users:
        assert u in active_user_ids


def test_get_presence_isolates_by_doc():
    doc_a = _unique_doc()
    doc_b = _unique_doc()
    user_a = _unique_user()
    user_b = _unique_user()

    heartbeat(doc_a, user_a)
    heartbeat(doc_b, user_b)

    presence_a = {r["user_id"] for r in get_presence(doc_a)}
    presence_b = {r["user_id"] for r in get_presence(doc_b)}

    assert user_a in presence_a
    assert user_b not in presence_a
    assert user_b in presence_b
    assert user_a not in presence_b


# ---------------------------------------------------------------------------
# cleanup_stale
# ---------------------------------------------------------------------------

def test_cleanup_stale_removes_old_row():
    doc_id = _unique_doc()
    user_id = _unique_user()

    _insert_presence(doc_id, user_id, _stale_iso(130))

    deleted = cleanup_stale(doc_id)

    assert deleted == 1
    assert get_presence(doc_id) == []


def test_cleanup_stale_preserves_recent_row():
    doc_id = _unique_doc()
    user_id = _unique_user()

    heartbeat(doc_id, user_id)

    deleted = cleanup_stale(doc_id)

    assert deleted == 0
    active = get_presence(doc_id)
    assert any(r["user_id"] == user_id for r in active)


def test_cleanup_stale_mixed_removes_only_old():
    doc_id = _unique_doc()
    stale_user = _unique_user()
    fresh_user = _unique_user()

    _insert_presence(doc_id, stale_user, _stale_iso(130))
    heartbeat(doc_id, fresh_user)

    deleted = cleanup_stale(doc_id)

    assert deleted == 1
    active_ids = {r["user_id"] for r in get_presence(doc_id)}
    assert stale_user not in active_ids
    assert fresh_user in active_ids


def test_cleanup_stale_returns_zero_when_nothing_stale():
    doc_id = _unique_doc()
    assert cleanup_stale(doc_id) == 0


def test_cleanup_stale_acceptance_scenario():
    """Acceptance: last_seen 130s ago → deleted, get_presence returns empty."""
    doc_id = _unique_doc()
    user_id = _unique_user()

    _insert_presence(doc_id, user_id, _stale_iso(130))

    # Verify user exists before cleanup
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM dic_presence_sessions WHERE doc_id = ? AND user_id = ?",
            (doc_id, user_id),
        )
        count_before = cur.fetchone()[0]
    finally:
        conn.close()
    assert count_before == 1

    cleanup_stale(doc_id)

    assert get_presence(doc_id) == []


def test_get_presence_acceptance_scenario():
    """Acceptance: heartbeat within 60s → user appears in get_presence."""
    doc_id = _unique_doc()
    user_id = _unique_user()

    heartbeat(doc_id, user_id)

    active = get_presence(doc_id)

    assert any(r["user_id"] == user_id for r in active), (
        "User should appear in get_presence within 60s of heartbeat"
    )
