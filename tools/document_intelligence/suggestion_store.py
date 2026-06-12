# CUI // SP-CTI
"""DIC Suggestion Store — lifecycle management for canvas-triggered doc update suggestions.

Tables (lazy-init via _ensure_tables):
  dic_suggestions            — mutable; one row per AI-drafted or crowdsourced suggestion
  dic_suggestion_decisions   — append-only, NIST AU; one row per accept/reject decision

Public API:
  create_suggestion(...)  -> str  (suggestion_id)
  get_pending_suggestions(collection_id=None, canvas_source=None) -> list[dict]
  get_suggestion(suggestion_id) -> dict | None
  decide_suggestion(suggestion_id, decision, decided_by, note='') -> bool
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection

_VALID_DECISIONS = ("accepted", "rejected")
_VALID_STATUSES = ("pending", "accepted", "rejected", "superseded")


# ── Schema ────────────────────────────────────────────────────────────────────

def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dic_suggestions (
            suggestion_id       TEXT    PRIMARY KEY,
            section_id          TEXT,
            doc_id              TEXT,
            collection_id       TEXT,
            trigger_event_id    TEXT,
            canvas_source       TEXT    NOT NULL DEFAULT 'unknown',
            suggested_content   TEXT    NOT NULL DEFAULT '',
            current_content     TEXT,
            rationale           TEXT,
            status              TEXT    NOT NULL DEFAULT 'pending',
            created_at          TEXT    NOT NULL,
            updated_at          TEXT,
            tenant_id           TEXT,
            classification      TEXT    NOT NULL DEFAULT 'CUI'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dic_suggestion_decisions (
            decision_id     TEXT    PRIMARY KEY,
            suggestion_id   TEXT    NOT NULL,
            decision        TEXT    NOT NULL,
            decided_by      TEXT,
            decided_at      TEXT    NOT NULL,
            note            TEXT,
            tenant_id       TEXT,
            classification  TEXT    NOT NULL DEFAULT 'CUI'
        )
        """
    )
    conn.commit()


# ── Public API ────────────────────────────────────────────────────────────────

def create_suggestion(
    *,
    section_id: str = "",
    doc_id: str = "",
    collection_id: str = "",
    trigger_event_id: str | None = None,
    canvas_source: str = "unknown",
    suggested_content: str,
    current_content: str = "",
    rationale: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> str:
    """Insert a new pending suggestion and return its suggestion_id."""
    suggestion_id = f"sug_{uuid.uuid4().hex[:16]}"
    now = _now()
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO dic_suggestions
                (suggestion_id, section_id, doc_id, collection_id,
                 trigger_event_id, canvas_source, suggested_content,
                 current_content, rationale, status,
                 created_at, updated_at, tenant_id, classification)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s)
            """,
            (
                suggestion_id, section_id, doc_id, collection_id,
                trigger_event_id, canvas_source, suggested_content,
                current_content, rationale,
                now, now, tenant_id, classification,
            ),
        )
        conn.commit()
    return suggestion_id


def get_pending_suggestions(
    collection_id: str | None = None,
    canvas_source: str | None = None,
    status: str = "pending",
) -> list[dict]:
    """Return suggestions filtered by optional collection_id, canvas_source, and status."""
    clauses = ["status = %s"]
    params: list = [status]

    if collection_id:
        clauses.append("collection_id = %s")
        params.append(collection_id)
    if canvas_source:
        clauses.append("canvas_source = %s")
        params.append(canvas_source)

    sql = f"SELECT * FROM dic_suggestions WHERE {' AND '.join(clauses)} ORDER BY created_at DESC"

    with get_connection() as conn:
        _ensure_tables(conn)
        rows = conn.execute(sql, params).fetchall()

    return [_row_to_dict(r) for r in rows]


def get_suggestion(suggestion_id: str) -> dict | None:
    """Return a single suggestion by ID, or None if not found."""
    with get_connection() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT * FROM dic_suggestions WHERE suggestion_id = %s",
            (suggestion_id,),
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def decide_suggestion(
    suggestion_id: str,
    decision: str,
    decided_by: str,
    note: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Accept or reject a pending suggestion.

    Returns True on success, False if the suggestion doesn't exist or
    was already decided.  Raises ValueError for an invalid decision value.
    """
    if decision not in _VALID_DECISIONS:
        raise ValueError(f"decision must be one of {_VALID_DECISIONS}, got '{decision}'")

    now = _now()
    decision_id = f"dec_{uuid.uuid4().hex[:16]}"

    with get_connection() as conn:
        _ensure_tables(conn)

        row = conn.execute(
            "SELECT status FROM dic_suggestions WHERE suggestion_id = %s",
            (suggestion_id,),
        ).fetchone()

        if row is None:
            return False

        current_status = _col(row, "status", 0)
        if current_status != "pending":
            return False

        conn.execute(
            "UPDATE dic_suggestions SET status=%s, updated_at=%s WHERE suggestion_id=%s",
            (decision, now, suggestion_id),
        )
        conn.execute(
            """
            INSERT INTO dic_suggestion_decisions
                (decision_id, suggestion_id, decision, decided_by,
                 decided_at, note, tenant_id, classification)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                decision_id, suggestion_id, decision, decided_by,
                now, note, tenant_id, classification,
            ),
        )
        conn.commit()

    return True


def get_decisions_for_suggestion(suggestion_id: str) -> list[dict]:
    """Return all decision rows for a suggestion (audit trail)."""
    with get_connection() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM dic_suggestion_decisions WHERE suggestion_id = %s ORDER BY decided_at",
            (suggestion_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _col(row, name: str, index: int):
    """Access a row column by name (sqlite3.Row) or positional index (tuple)."""
    if isinstance(row, (list, tuple)):
        return row[index]
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return {}
