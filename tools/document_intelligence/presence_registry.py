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


def heartbeat(doc_id: str, user_id: str, section_id: Optional[str] = None) -> str:
    """Upsert a presence row for user_id on doc_id. Returns session_id."""
    _ensure_table()

    from tools.db.storage import get_connection

    sid = _session_id(doc_id, user_id)
    last_seen = _now_iso()

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
                (sid, doc_id, user_id, section_id, last_seen, "default", "CUI"),
            )
        else:
            cur.execute(
                """
                INSERT OR REPLACE INTO dic_presence_sessions
                    (session_id, doc_id, user_id, active_section_id, last_seen,
                     tenant_id, classification)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, doc_id, user_id, section_id, last_seen, "default", "CUI"),
            )
        conn.commit()
    finally:
        conn.close()

    return sid


def get_presence(doc_id: str) -> list[dict]:
    """Return presence rows for doc_id whose last_seen is within 60 seconds."""
    _ensure_table()

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        if _is_pg():
            cur.execute(
                "SELECT session_id, doc_id, user_id, active_section_id, last_seen "
                "FROM dic_presence_sessions WHERE doc_id = %s",
                (doc_id,),
            )
        else:
            cur.execute(
                "SELECT session_id, doc_id, user_id, active_section_id, last_seen "
                "FROM dic_presence_sessions WHERE doc_id = ?",
                (doc_id,),
            )
        rows = cur.fetchall()
    finally:
        conn.close()

    _keys = ["session_id", "doc_id", "user_id", "active_section_id", "last_seen"]
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_PRESENCE_TTL_S)
    result = []
    for row in rows:
        r = dict(row) if hasattr(row, "keys") else dict(zip(_keys, row))
        if _parse_ts(str(r["last_seen"])) >= cutoff:
            result.append(r)
    return result


def cleanup_stale(doc_id: str) -> int:
    """Delete presence rows for doc_id older than 120 seconds. Returns count deleted."""
    _ensure_table()

    from tools.db.storage import get_connection

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_STALE_TTL_S)

    conn = get_connection()
    try:
        cur = conn.cursor()
        if _is_pg():
            cur.execute(
                "SELECT session_id, last_seen FROM dic_presence_sessions WHERE doc_id = %s",
                (doc_id,),
            )
        else:
            cur.execute(
                "SELECT session_id, last_seen FROM dic_presence_sessions WHERE doc_id = ?",
                (doc_id,),
            )
        rows = cur.fetchall()

        stale_ids = []
        for row in rows:
            if hasattr(row, "keys"):
                sid, ts = row["session_id"], row["last_seen"]
            else:
                sid, ts = row[0], row[1]
            if _parse_ts(str(ts)) < cutoff:
                stale_ids.append(sid)

        for sid in stale_ids:
            if _is_pg():
                cur.execute(
                    "DELETE FROM dic_presence_sessions WHERE session_id = %s", (sid,)
                )
            else:
                cur.execute(
                    "DELETE FROM dic_presence_sessions WHERE session_id = ?", (sid,)
                )
        conn.commit()
    finally:
        conn.close()

    return len(stale_ids)
