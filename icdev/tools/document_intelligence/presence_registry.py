# CUI // SP-CTI
"""DIC Presence Registry — lightweight "who is viewing this document?" tracking.

Stores active presence sessions in dic_presence_sessions with a TTL-based
expiry (default 45 s).  Clients call /presence/join on page load, send a
/presence/heartbeat every 20 s, and /presence/leave on unload.  The SSE
stream at /presence/stream pushes the live user list every 12 s.

Unlike the section-lock table, presence sessions are mutable (heartbeat
UPDATEs last_seen + expires_at) and are NOT append-only.

Usage::

    from tools.document_intelligence.presence_registry import (
        join_document, heartbeat, leave_document, get_present_users,
    )

    session_key = join_document("doc_abc123", "alice")
    heartbeat(session_key)
    users = get_present_users("doc_abc123")
    leave_document(session_key)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from tools.db.storage import get_connection

_DEFAULT_TTL = 45  # seconds
_HEARTBEAT_INTERVAL = 20  # client should beat at this cadence
_SSE_POLL_INTERVAL = 12  # seconds between SSE yields


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_iso(ttl: int = _DEFAULT_TTL) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()


def _is_expired(expires_at: str) -> bool:
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > exp
    except Exception:
        return True


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dic_presence_sessions (
            session_key       TEXT PRIMARY KEY,
            doc_id            TEXT NOT NULL,
            user_id           TEXT NOT NULL,
            joined_at         TEXT NOT NULL,
            last_seen         TEXT NOT NULL,
            expires_at        TEXT NOT NULL,
            active_section_id TEXT,
            tenant_id         TEXT,
            classification    TEXT DEFAULT 'CUI'
        )
    """)
    conn.commit()


def _ensure_active_section_col(conn) -> None:
    """Add active_section_id to tables created before this column existed."""
    try:
        conn.execute("ALTER TABLE dic_presence_sessions ADD COLUMN active_section_id TEXT")
        conn.commit()
    except Exception:
        pass  # column already exists


# ── Public API ────────────────────────────────────────────────────────────────

def join_document(
    doc_id: str,
    user_id: str,
    ttl_seconds: int = _DEFAULT_TTL,
    tenant_id: str = "",
) -> str:
    """Register *user_id* as present on *doc_id*.

    Returns the session_key the client should use for heartbeat/leave calls.
    If the user already has a session on this doc, the existing key is renewed.
    """
    session_key = f"ps_{uuid.uuid4().hex[:20]}"
    now = _now_iso()
    exp = _expires_iso(ttl_seconds)

    with get_connection() as conn:
        _ensure_table(conn)
        # Purge stale rows for this user+doc first
        conn.execute(
            "DELETE FROM dic_presence_sessions WHERE doc_id = ? AND user_id = ? AND expires_at < ?",
            (doc_id, user_id, now),
        )
        # Check if user already has an active session on this doc
        existing = conn.execute(
            "SELECT session_key FROM dic_presence_sessions WHERE doc_id = ? AND user_id = ? LIMIT 1",
            (doc_id, user_id),
        ).fetchone()
        if existing:
            # Renew the existing session instead of creating a duplicate
            old_key = dict(existing)["session_key"]
            conn.execute(
                "UPDATE dic_presence_sessions SET last_seen = ?, expires_at = ? WHERE session_key = ?",
                (now, exp, old_key),
            )
            conn.commit()
            return old_key

        conn.execute(
            """INSERT INTO dic_presence_sessions
               (session_key, doc_id, user_id, joined_at, last_seen, expires_at, tenant_id, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_key, doc_id, user_id, now, now, exp, tenant_id or "", "CUI"),
        )
        conn.commit()
    return session_key


def heartbeat(session_key: str, ttl_seconds: int = _DEFAULT_TTL) -> bool:
    """Renew the TTL for *session_key*. Returns True if found, False otherwise."""
    now = _now_iso()
    exp = _expires_iso(ttl_seconds)
    with get_connection() as conn:
        _ensure_table(conn)
        cur = conn.execute(
            "UPDATE dic_presence_sessions SET last_seen = ?, expires_at = ? WHERE session_key = ?",
            (now, exp, session_key),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0


def leave_document(session_key: str) -> bool:
    """Remove the presence session. Returns True if it existed."""
    with get_connection() as conn:
        _ensure_table(conn)
        cur = conn.execute(
            "DELETE FROM dic_presence_sessions WHERE session_key = ?",
            (session_key,),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0


def get_present_users(doc_id: str) -> list[dict]:
    """Return active (non-expired) users viewing *doc_id*."""
    now = _now_iso()
    with get_connection() as conn:
        _ensure_table(conn)
        # Purge expired rows opportunistically
        conn.execute(
            "DELETE FROM dic_presence_sessions WHERE doc_id = ? AND expires_at < ?",
            (doc_id, now),
        )
        conn.commit()
        rows = conn.execute(
            """SELECT session_key, user_id, joined_at, last_seen, expires_at
               FROM dic_presence_sessions
               WHERE doc_id = ?
               ORDER BY joined_at ASC""",
            (doc_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def ping(doc_id: str, user_id: str, section_id: str, ttl_seconds: int = _DEFAULT_TTL) -> str:
    """Upsert a presence session, recording the user's active_section_id. Returns session_key."""
    now = _now_iso()
    exp = _expires_iso(ttl_seconds)
    with get_connection() as conn:
        _ensure_table(conn)
        _ensure_active_section_col(conn)
        conn.execute(
            "DELETE FROM dic_presence_sessions WHERE doc_id = ? AND user_id = ? AND expires_at < ?",
            (doc_id, user_id, now),
        )
        existing = conn.execute(
            "SELECT session_key FROM dic_presence_sessions WHERE doc_id = ? AND user_id = ? LIMIT 1",
            (doc_id, user_id),
        ).fetchone()
        if existing:
            old_key = dict(existing)["session_key"]
            conn.execute(
                "UPDATE dic_presence_sessions SET last_seen = ?, expires_at = ?, active_section_id = ? WHERE session_key = ?",
                (now, exp, section_id, old_key),
            )
            conn.commit()
            return old_key
        session_key = f"ps_{uuid.uuid4().hex[:20]}"
        conn.execute(
            """INSERT INTO dic_presence_sessions
               (session_key, doc_id, user_id, joined_at, last_seen, expires_at, active_section_id, tenant_id, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_key, doc_id, user_id, now, now, exp, section_id, "", "CUI"),
        )
        conn.commit()
    return session_key


def get_presence(doc_id: str) -> list[dict]:
    """Return active users viewing doc_id: [{user_id, active_section_id, last_seen}]."""
    now = _now_iso()
    with get_connection() as conn:
        _ensure_table(conn)
        _ensure_active_section_col(conn)
        conn.execute(
            "DELETE FROM dic_presence_sessions WHERE doc_id = ? AND expires_at < ?",
            (doc_id, now),
        )
        conn.commit()
        rows = conn.execute(
            """SELECT user_id, active_section_id, last_seen
               FROM dic_presence_sessions
               WHERE doc_id = ?
               ORDER BY joined_at ASC""",
            (doc_id,),
        ).fetchall()
    return [
        {
            "user_id": dict(r)["user_id"],
            "active_section_id": dict(r).get("active_section_id"),
            "last_seen": dict(r)["last_seen"],
        }
        for r in rows
    ]


def purge_stale(doc_id: str = "") -> int:
    """Delete all expired presence sessions. If doc_id provided, scope to that doc."""
    now = _now_iso()
    with get_connection() as conn:
        _ensure_table(conn)
        if doc_id:
            cur = conn.execute(
                "DELETE FROM dic_presence_sessions WHERE doc_id = ? AND expires_at < ?",
                (doc_id, now),
            )
        else:
            cur = conn.execute(
                "DELETE FROM dic_presence_sessions WHERE expires_at < ?",
                (now,),
            )
        conn.commit()
        return cur.rowcount or 0
