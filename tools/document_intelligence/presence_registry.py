# CUI // SP-CTI
"""Presence registry for real-time document co-editing awareness.

Tracks which users are actively viewing or editing a document by maintaining
a heartbeat table (dic_presence_sessions). Rows are upserted on each heartbeat,
not appended — this table is NOT append-only.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_PRESENCE_TTL_S = 60
_STALE_TTL_S = 120

_TABLE_READY = False

_TABLE_SQL_PG = """
CREATE TABLE IF NOT EXISTS dic_presence_sessions (
    session_id          TEXT        PRIMARY KEY,
    doc_id              TEXT        NOT NULL,
    user_id             TEXT        NOT NULL,
    active_section_id   TEXT,
    last_seen           TEXT        NOT NULL,
    tenant_id           TEXT        DEFAULT 'default',
    classification      TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_presence_sessions_doc ON dic_presence_sessions(doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_presence_sessions_last_seen ON dic_presence_sessions(last_seen);
"""

_TABLE_SQL_SQLITE = """
CREATE TABLE IF NOT EXISTS dic_presence_sessions (
    session_id          TEXT    PRIMARY KEY,
    doc_id              TEXT    NOT NULL,
    user_id             TEXT    NOT NULL,
    active_section_id   TEXT,
    last_seen           TEXT    NOT NULL,
    tenant_id           TEXT    DEFAULT 'default',
    classification      TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_presence_sessions_doc ON dic_presence_sessions(doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_presence_sessions_last_seen ON dic_presence_sessions(last_seen);
"""


def _is_pg() -> bool:
    return os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower() in (
        "postgresql", "postgres", "pg"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _session_id(doc_id: str, user_id: str) -> str:
    return f"{user_id}:{doc_id}"


def _ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            sql = _TABLE_SQL_PG if _is_pg() else _TABLE_SQL_SQLITE
            for stmt in sql.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
            conn.commit()
            _TABLE_READY = True
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("dic_presence_sessions table init error: %s", exc)


# ── Session-key API ────────────────────────────────────────────────────────────

def join_document(
    doc_id: str,
    user_id: str,
    ttl_seconds: int = _PRESENCE_TTL_S,
    tenant_id: str = "",
) -> str:
    """Register user_id as present on doc_id. Returns session_id (the session_key).

    If the user already has a session on this doc, the existing key is renewed.
    """
    _ensure_table()
    from tools.db.storage import get_connection

    sid = _session_id(doc_id, user_id)
    now = _now_iso()

    conn = get_connection()
    try:
        cur = conn.cursor()
        if _is_pg():
            cur.execute(
                """
                INSERT INTO dic_presence_sessions
                    (session_id, doc_id, user_id, last_seen, tenant_id, classification)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    last_seen  = EXCLUDED.last_seen,
                    tenant_id  = EXCLUDED.tenant_id
                """,
                (sid, doc_id, user_id, now, tenant_id or "default", "CUI"),
            )
        else:
            cur.execute(
                """
                INSERT OR REPLACE INTO dic_presence_sessions
                    (session_id, doc_id, user_id, last_seen, tenant_id, classification)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sid, doc_id, user_id, now, tenant_id or "default", "CUI"),
            )
        conn.commit()
    finally:
        conn.close()

    return sid


def leave_document(session_key: str) -> bool:
    """Remove the presence session identified by session_key. Returns True if it existed."""
    _ensure_table()
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        if _is_pg():
            cur.execute(
                "DELETE FROM dic_presence_sessions WHERE session_id = %s",
                (session_key,),
            )
        else:
            cur.execute(
                "DELETE FROM dic_presence_sessions WHERE session_id = ?",
                (session_key,),
            )
        conn.commit()
        return (cur.rowcount or 0) > 0
    finally:
        conn.close()


def get_present_users(doc_id: str) -> list[dict]:
    """Return active (non-expired) users viewing doc_id."""
    _ensure_table()
    from tools.db.storage import get_connection

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_PRESENCE_TTL_S)
    cutoff_iso = cutoff.isoformat(timespec="seconds")

    conn = get_connection()
    try:
        cur = conn.cursor()
        if _is_pg():
            cur.execute(
                "SELECT session_id, user_id, active_section_id, last_seen "
                "FROM dic_presence_sessions WHERE doc_id = %s AND last_seen >= %s",
                (doc_id, cutoff_iso),
            )
        else:
            cur.execute(
                "SELECT session_id, user_id, active_section_id, last_seen "
                "FROM dic_presence_sessions WHERE doc_id = ? AND last_seen >= ?",
                (doc_id, cutoff_iso),
            )
        rows = cur.fetchall()
    finally:
        conn.close()

    _keys = ["session_id", "user_id", "active_section_id", "last_seen"]
    return [
        dict(r) if hasattr(r, "keys") else dict(zip(_keys, r))
        for r in rows
    ]


def ping(
    doc_id: str,
    user_id: str,
    section_id: Optional[str] = None,
    ttl_seconds: int = _PRESENCE_TTL_S,
) -> str:
    """Upsert a presence session recording active_section_id. Returns session_key."""
    _ensure_table()
    from tools.db.storage import get_connection

    sid = _session_id(doc_id, user_id)
    now = _now_iso()

    conn = get_connection()
    try:
        cur = conn.cursor()
        if _is_pg():
            cur.execute(
                """
                INSERT INTO dic_presence_sessions
                    (session_id, doc_id, user_id, active_section_id, last_seen,
                     tenant_id, classification)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    last_seen         = EXCLUDED.last_seen,
                    active_section_id = EXCLUDED.active_section_id
                """,
                (sid, doc_id, user_id, section_id, now, "default", "CUI"),
            )
        else:
            cur.execute(
                """
                INSERT OR REPLACE INTO dic_presence_sessions
                    (session_id, doc_id, user_id, active_section_id, last_seen,
                     tenant_id, classification)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, doc_id, user_id, section_id, now, "default", "CUI"),
            )
        conn.commit()
    finally:
        conn.close()

    return sid


# ── Legacy API (kept for backward compatibility) ───────────────────────────────

def heartbeat(doc_id: str, user_id: str, section_id: Optional[str] = None) -> str:
    """Upsert a presence row for user_id on doc_id. Returns session_id.

    Delegates to ping() — kept for backward compatibility.
    """
    return ping(doc_id, user_id, section_id=section_id)


def get_presence(doc_id: str) -> list[dict]:
    """Return presence rows for doc_id whose last_seen is within 60 seconds.

    Delegates to get_present_users() — kept for backward compatibility.
    """
    rows = get_present_users(doc_id)
    # Add doc_id field expected by legacy callers
    for r in rows:
        r.setdefault("doc_id", doc_id)
    return rows


def cleanup_stale(doc_id: str) -> int:
    """Delete presence rows for doc_id older than 120 seconds. Returns count deleted."""
    _ensure_table()
    from tools.db.storage import get_connection

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_STALE_TTL_S)
    cutoff_iso = cutoff.isoformat(timespec="seconds")

    conn = get_connection()
    try:
        cur = conn.cursor()
        if _is_pg():
            cur.execute(
                "DELETE FROM dic_presence_sessions WHERE doc_id = %s AND last_seen < %s",
                (doc_id, cutoff_iso),
            )
        else:
            cur.execute(
                "DELETE FROM dic_presence_sessions WHERE doc_id = ? AND last_seen < ?",
                (doc_id, cutoff_iso),
            )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


purge_stale = cleanup_stale
