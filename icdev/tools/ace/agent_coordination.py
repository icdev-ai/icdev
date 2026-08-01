# CUI // SP-CTI
"""Agent coordination bus — shared key-value store for sibling agent loops.

Agents assigned the same ``coordination_namespace`` can exchange typed
artifacts via :func:`post_result` and :func:`read_result` without
spawning subagents.  The store is backed by the ACE canvas DB.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.agent_coordination")

_DB_ENV = "ACE_DB_PATH"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_coordination (
    id          TEXT PRIMARY KEY,
    namespace   TEXT NOT NULL DEFAULT '',
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL DEFAULT 'null',
    posted_by   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agcoord_ns_key
    ON agent_coordination (namespace, key);
"""

_NOT_FOUND = object()  # sentinel


def _get_conn():
    try:
        from tools.db.storage import get_canvas_connection
        return get_canvas_connection(_DB_ENV)
    except Exception:
        return None


def _ensure_table(conn) -> None:
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    except Exception:
        pass  # table may already exist


def post_result(namespace: str, key: str, value: object, *, posted_by: str = "") -> str:
    """Upsert *value* at *key* in *namespace*.

    Returns a human-readable confirmation string suitable for returning to the LLM.
    Raises ``RuntimeError`` if the DB is unavailable.
    """
    conn = _get_conn()
    if conn is None:
        raise RuntimeError("agent_coordination DB unavailable")

    now = datetime.now(timezone.utc).isoformat()
    value_json = json.dumps(value)
    row_id = f"agc-{uuid.uuid4().hex[:12]}"

    try:
        _ensure_table(conn)
        try:
            # PostgreSQL path
            conn.execute(
                "INSERT INTO agent_coordination "
                "(id, namespace, key, value_json, posted_by, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (namespace, key) DO UPDATE SET "
                "value_json=EXCLUDED.value_json, posted_by=EXCLUDED.posted_by, "
                "updated_at=EXCLUDED.updated_at",
                (row_id, namespace, key, value_json, posted_by, now, now),
            )
        except Exception:
            # SQLite path (INSERT OR REPLACE)
            conn.execute(
                "INSERT OR REPLACE INTO agent_coordination "
                "(id, namespace, key, value_json, posted_by, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (row_id, namespace, key, value_json, posted_by, now, now),
            )
        conn.commit()
    finally:
        conn.close()

    return f"Posted key={key!r} ({len(value_json)} bytes) to namespace {namespace!r}"


def read_result(namespace: str, key: str) -> object:
    """Read value at *key* in *namespace*.

    Returns the parsed Python value, or the sentinel :data:`_NOT_FOUND` if
    the key does not exist.  Raises ``RuntimeError`` if the DB is unavailable.
    """
    conn = _get_conn()
    if conn is None:
        raise RuntimeError("agent_coordination DB unavailable")

    try:
        _ensure_table(conn)
        try:
            row = conn.execute(
                "SELECT value_json FROM agent_coordination WHERE namespace=%s AND key=%s",
                (namespace, key),
            ).fetchone()
        except Exception:
            row = conn.execute(
                "SELECT value_json FROM agent_coordination WHERE namespace=%s AND key=%s",
                (namespace, key),
            ).fetchone()
    finally:
        conn.close()

    if row is None:
        return _NOT_FOUND
    return json.loads(row[0])


def list_results(namespace: str) -> list[dict]:
    """Return all entries in *namespace* ordered by ``updated_at`` DESC."""
    conn = _get_conn()
    if conn is None:
        return []

    try:
        _ensure_table(conn)
        try:
            rows = conn.execute(
                "SELECT key, value_json, posted_by, updated_at FROM agent_coordination "
                "WHERE namespace=%s ORDER BY updated_at DESC",
                (namespace,),
            ).fetchall()
        except Exception:
            rows = conn.execute(
                "SELECT key, value_json, posted_by, updated_at FROM agent_coordination "
                "WHERE namespace=%s ORDER BY updated_at DESC",
                (namespace,),
            ).fetchall()
    finally:
        conn.close()

    return [
        {"key": r[0], "value_json": r[1], "posted_by": r[2], "updated_at": r[3]}
        for r in rows
    ]
