# CUI // SP-CTI
"""Cross-engine registration bridge.

Pushes approved ACF concepts into the Creative engine store (``creative_gaps``)
so downstream innovation pipelines can act on capability gaps surfaced by the
Foundry.  This replaces manual copy-paste for sending concepts to the Creative
engine store.

Usage:
    from tools.cross_register import push_to_creative
    result = push_to_creative(concept)
    # result == {"success": True, "gap_id": "cgap-...", "message": "..."}
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.cross_register")

_CREATIVE_GAPS_TABLE = "creative_gaps"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(concept: dict) -> str:
    """Content-addressed stable ID for the creative gap row."""
    payload = f"{concept.get('slug', '')}:{concept.get('proposed_capability', '')}"
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"cgap-{h}"


def _priority_from_score(score: Optional[float]) -> str:
    """Map a composite score to a creative_gaps priority."""
    if score is None:
        return "medium"
    s = float(score)
    if s >= 0.8:
        return "high"
    if s >= 0.5:
        return "medium"
    return "low"


def push_to_creative(concept: dict, *, conn: Optional[Any] = None) -> dict:
    """Insert one approved Foundry concept into the Creative engine store.

    Args:
        concept: A ``foundry_concepts`` row (or dict) with at minimum
            ``slug``, ``name``, ``problem_statement``, ``proposed_capability``,
            ``composite_score``, and ``status`` fields.
        conn: Optional existing DB connection (RLS-aware).  If omitted, a
            fresh ``get_connection()`` handle is opened and closed.

    Returns:
        dict with ``success`` bool, ``gap_id``, and ``message``.
    """
    if not isinstance(concept, dict):
        return {"success": False, "gap_id": None, "message": "concept must be a dict"}

    # Only approved concepts may be pushed.
    if concept.get("status") != "approved":
        return {
            "success": False,
            "gap_id": None,
            "message": (
                f"concept status='{concept.get('status')}' — "
                "only 'approved' may be pushed"
            ),
        }

    gap_id = _generate_id(concept)
    gap_type = concept.get("name", concept.get("slug", "capability_gap"))
    description = (
        f"Problem: {concept.get('problem_statement', '')}\n"
        f"Capability: {concept.get('proposed_capability', '')}\n"
        f"Target users: {concept.get('target_users', '')}"
    ).strip()
    priority = _priority_from_score(concept.get("composite_score"))
    created_at = _now_iso()

    from tools.db.storage import get_connection

    own_conn = conn is None
    c = conn or get_connection()
    try:
        c.execute(
            f"""INSERT INTO {_CREATIVE_GAPS_TABLE}
                (id, gap_type, description, priority, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (gap_id, gap_type, description, priority, "open", created_at),
        )
        if own_conn:
            c.commit()
        logger.info(
            "push_to_creative: inserted gap %s (type=%s, priority=%s)",
            gap_id,
            gap_type,
            priority,
        )
        return {
            "success": True,
            "gap_id": gap_id,
            "message": "Inserted into creative_gaps",
        }
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "UNIQUE constraint" in msg or "duplicate key" in msg:
            return {
                "success": False,
                "gap_id": gap_id,
                "message": f"Duplicate gap id: {gap_id}",
            }
        return {"success": False, "gap_id": gap_id, "message": f"Insert failed: {msg}"}
    finally:
        if own_conn:
            c.close()
