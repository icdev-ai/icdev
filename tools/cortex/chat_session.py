# CUI // SP-CTI
"""Cortex chat session store — THIN, detachable turn persistence.

The /cortex chat surface needs conversations to persist and reload per
session. Rather than build a third chat manager, this module keeps a
deliberately *thin* session model over three canvas-owned tables:

    cortex_chat_sessions   — one row per session (mode/domain/title/status)
    cortex_messages   — one row per turn (user + assistant), with the TRUST
                        labels (grounded/confidence/citations/governance) that
                        produced the assistant turn
    (facade-outcome audit is written by the governance pipeline into the
    canonical cortex_audit table — ctx-govern-03/04 — not here)

DOCUMENTED COUPLING RISK: ``tools.dashboard.chat_manager`` is the platform's
full chat engine, but it is a thread-per-context agent loop deeply coupled to
RICOAS (constitution injection, KG/compliance context, inception phases). Its
``send_message`` spawns an LLM agent loop — reusing it would hijack Cortex's
own facade routing. So we reuse its *shape* (session row + append-only turn
rows, ``get_connection`` + best-effort/idempotent writes) but keep an isolated
store here. Everything Cortex-specific lives behind these functions, so if the
platform later grows a shared, RICOAS-free session primitive this module can
detach onto it without touching the blueprint.

Every write is best-effort: a missing table (fresh checkout, migration not yet
run) degrades to a no-op rather than raising, mirroring the blueprint's
skeleton behaviour.

CONNECTION OWNERSHIP (ctx-perf-03): every write takes an optional ``conn``. Called
standalone it opens, commits and closes its own connection, exactly as before;
given one it borrows it and never closes it — the same convention
``db/init_db.record_governed_call`` uses to collapse the governance audit pair
onto a single connection. One chat turn is three of these writes plus the
blueprint's history insert, so threading one connection through all four takes a
turn from four connections to one. A borrowed connection is rolled back (not
closed) when a write fails: on PostgreSQL a failed statement poisons the whole
transaction, so without the rollback one broken write would silently take its
siblings down with it.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    from tools.db.storage import get_connection

    return get_connection()


def _to_bool_int(value: Any) -> int:
    """0/1 for the grounded column.

    An INTEGER column, so the bound value must be an ``int``: psycopg2 adapts a
    Python ``bool`` to a PG ``boolean`` literal, which PostgreSQL refuses for an
    INTEGER column (the mirror image of the ``blocked``/``air_gap`` note in
    db/init_db.py, where the column IS boolean and an int fails).
    """
    return 1 if value else 0


def _rollback(conn) -> None:
    """Undo a failed write on a BORROWED connection so its siblings still land."""
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — rollback is itself best-effort
        pass


def ensure_session(
    session_id: str,
    *,
    user_id: str,
    mode: str,
    domain: str,
    tenant_id: str,
    classification: str,
    title: str = "",
    conn=None,
) -> None:
    """Upsert the session row: insert on first turn, else bump mode/domain/updated_at.

    Best-effort — never raises. The session row is metadata only; turns live in
    cortex_messages. Pass ``conn`` to share the caller's connection (it is
    committed but never closed here); omit it to open and close one.
    """
    now = _now()
    own_conn = conn is None
    try:
        if own_conn:
            conn = _conn()
        try:
            row = conn.execute(
                "SELECT session_id FROM cortex_chat_sessions WHERE session_id = %s",
                (session_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE cortex_chat_sessions SET mode = %s, domain = %s, updated_at = %s "
                    "WHERE session_id = %s",
                    (mode, domain, now, session_id),
                )
            else:
                conn.execute(
                    "INSERT INTO cortex_chat_sessions "
                    "(session_id, user_id, mode, domain, title, status, "
                    "classification, tenant_id, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (session_id, user_id, mode, domain, title[:120], "active",
                     classification, tenant_id, now, now),
                )
            conn.commit()
        finally:
            if own_conn:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cortex.session: ensure_session skipped: %s", exc)
        if not own_conn and conn is not None:
            _rollback(conn)


def record_turn(
    session_id: str,
    role: str,
    content: str,
    *,
    facade: str = "",
    grounded: bool = False,
    confidence: str = "",
    citations: Optional[list] = None,
    governance: Optional[dict] = None,
    tenant_id: str = "default",
    classification: str = "CUI",
    conn=None,
) -> Optional[int]:
    """Append one conversation turn to cortex_messages. Returns the turn number.

    ``citations`` and ``governance`` are stored as JSON so the reload endpoint
    can rehydrate the exact TRUST labels the UI rendered. Best-effort: returns
    None if the table is unavailable. Pass ``conn`` to share the caller's
    connection (committed, never closed here); omit it to open and close one.

    The MAX(turn_number) read and the INSERT stay on the SAME connection either
    way, so a shared connection does not change which turn number this write
    sees — the previous turn's commit is already visible to it.
    """
    now = _now()
    own_conn = conn is None
    try:
        if own_conn:
            conn = _conn()
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(turn_number), 0) FROM cortex_messages "
                "WHERE session_id = %s",
                (session_id,),
            ).fetchone()
            turn_number = (row[0] if row else 0) + 1
            conn.execute(
                "INSERT INTO cortex_messages "
                "(message_id, session_id, turn_number, role, content, facade, "
                "grounded, confidence, citations, governance, classification, "
                "tenant_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    uuid.uuid4().hex,
                    session_id,
                    turn_number,
                    role,
                    content,
                    facade,
                    _to_bool_int(grounded),
                    confidence or "",
                    json.dumps(citations or [], default=str),
                    json.dumps(governance or {}, default=str),
                    classification,
                    tenant_id,
                    now,
                ),
            )
            conn.commit()
            return turn_number
        finally:
            if own_conn:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cortex.session: record_turn skipped: %s", exc)
        if not own_conn and conn is not None:
            _rollback(conn)
        return None


def load_turns(session_id: str, limit: int = 200) -> list[dict]:
    """Return conversation turns for a session, oldest first (for reload)."""
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT turn_number, role, content, facade, grounded, confidence, "
                "citations, governance, created_at FROM cortex_messages "
                "WHERE session_id = %s ORDER BY turn_number LIMIT %s",
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cortex.session: load_turns skipped: %s", exc)
        return []

    turns: list[dict] = []
    for r in rows:
        turns.append(
            {
                "turn_number": r[0],
                "role": r[1],
                "content": r[2],
                "facade": r[3],
                "grounded": bool(r[4]),
                "confidence": r[5],
                "citations": _load_json(r[6], []),
                "governance": _load_json(r[7], {}),
                "created_at": r[8],
            }
        )
    return turns


def load_session(session_id: str) -> Optional[dict]:
    """Return the session metadata row, or None if it does not exist."""
    try:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT session_id, user_id, mode, domain, title, status, "
                "created_at, updated_at FROM cortex_chat_sessions WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cortex.session: load_session skipped: %s", exc)
        return None
    if not row:
        return None
    return {
        "session_id": row[0],
        "user_id": row[1],
        "mode": row[2],
        "domain": row[3],
        "title": row[4],
        "status": row[5],
        "created_at": row[6],
        "updated_at": row[7],
    }



def _load_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default
