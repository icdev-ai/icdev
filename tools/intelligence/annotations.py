#!/usr/bin/env python3
# CUI // SP-CTI
"""Analyst Annotation Layer — sg_analyst_annotations.

Allows analysts to attach typed annotations to any KG node, conflict event,
ORBAT entry, or supply chain node in the Strategos knowledge model.

    entity_type  : "kg_node" | "conflict_event" | "orbat_unit" | "supply_node" | "signal"
    entity_id    : string ID of the target entity (node_id, event id, unit id, etc.)
    annotation_type : ANNOTATION_TYPES constant — assessed | unconfirmed | disputed | source_tag
    content      : free-text analyst note (max 2 000 chars, sanitised on write)
    analyst_id   : author identifier (defaults to "analyst" for single-user installs)
"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.db.storage import get_connection, is_pg

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANNOTATION_TYPES = ["assessed", "unconfirmed", "disputed", "source_tag"]

ENTITY_TYPES = [
    "kg_node",
    "conflict_event",
    "orbat_unit",
    "supply_node",
    "signal",
]

ANNOTATION_ICON = {
    "assessed":    "✔",
    "unconfirmed": "?",
    "disputed":    "✗",
    "source_tag":  "⚑",
}

_MAX_CONTENT = 2_000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ph() -> str:
    return "%s" if is_pg() else "?"


def _sanitise(text: str) -> str:
    """Strip leading/trailing whitespace and cap at _MAX_CONTENT chars."""
    return text.strip()[:_MAX_CONTENT]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_annotation(
    entity_type: str,
    entity_id: str,
    annotation_type: str,
    content: str,
    analyst_id: str = "analyst",
) -> dict:
    """Insert an annotation row.  Returns the new row as a dict.

    Raises ValueError for invalid entity_type or annotation_type.
    Table is append-only (NIST AU-9).
    """
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {ENTITY_TYPES}")
    if annotation_type not in ANNOTATION_TYPES:
        raise ValueError(f"annotation_type must be one of {ANNOTATION_TYPES}")

    clean_content = _sanitise(content)
    if not clean_content:
        raise ValueError("content must not be empty")

    ph = _ph()
    returning = " RETURNING id" if is_pg() else ""
    sql = (
        f"INSERT INTO sg_analyst_annotations "  # nosec B608 — all values passed as parameterized placeholders
        f"(entity_type, entity_id, annotation_type, content, analyst_id, created_at) "
        f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph}){returning}"
    )
    created_at = _now()
    conn = get_connection()
    try:
        cur = conn.execute(sql, (entity_type, entity_id, annotation_type, clean_content, analyst_id, created_at))
        conn.commit()
        row_id = cur.fetchone()[0] if is_pg() else cur.lastrowid
    finally:
        conn.close()

    return {
        "id": row_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "annotation_type": annotation_type,
        "content": clean_content,
        "analyst_id": analyst_id,
        "created_at": created_at,
    }


def get_annotations(entity_type: str, entity_id: str) -> list[dict]:
    """Return all annotations for a specific entity, newest-first."""
    ph = _ph()
    sql = (
        "SELECT id, entity_type, entity_id, annotation_type, content, analyst_id, created_at "  # nosec B608 — parameterized
        "FROM sg_analyst_annotations "
        f"WHERE entity_type={ph} AND entity_id={ph} "
        "ORDER BY created_at DESC"
    )
    conn = get_connection()
    try:
        rows = conn.execute(sql, (entity_type, entity_id)).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    finally:
        conn.close()


def get_annotation_counts(entity_type: str, entity_ids: list[str]) -> dict[str, int]:
    """Return {entity_id: count} for a batch of IDs (same entity_type).

    Used by UI list views to render annotation badges without N+1 queries.
    """
    if not entity_ids:
        return {}
    ph = _ph()
    placeholders = ",".join([ph] * len(entity_ids))
    sql = (
        "SELECT entity_id, COUNT(*) as cnt "  # nosec B608 — placeholders built from len(entity_ids), no raw injection
        "FROM sg_analyst_annotations "
        f"WHERE entity_type={ph} AND entity_id IN ({placeholders}) "
        "GROUP BY entity_id"
    )
    conn = get_connection()
    try:
        rows = conn.execute(sql, [entity_type, *entity_ids]).fetchall()
        return {r["entity_id"]: r["cnt"] for r in rows}
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return {}
        raise
    finally:
        conn.close()


def get_annotation_summary(entity_type: str, entity_id: str) -> dict:
    """Return count-by-type summary + most-recent annotation for a single entity."""
    rows = get_annotations(entity_type, entity_id)
    counts: dict[str, int] = {t: 0 for t in ANNOTATION_TYPES}
    for r in rows:
        counts[r["annotation_type"]] = counts.get(r["annotation_type"], 0) + 1
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "total": len(rows),
        "by_type": counts,
        "latest": rows[0] if rows else None,
    }
