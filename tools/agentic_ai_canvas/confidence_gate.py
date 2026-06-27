# CUI // SP-CTI
"""AADC Confidence Gate — Safety layer for the Agentic Research Pipeline.

Implements the `confidence-threshold` node type.  When the Synthesis LLM
produces a draft answer the Confidence Gate decides whether to:

  * **Proceed**  (score >= threshold) → route to output-validator downstream
  * **Escalate** (score < threshold)  → route to hitl-gate / human review

Public API
----------
evaluate(design_id, output_text, score, *, threshold, node_id) -> dict
    Core gate evaluation.  Returns a routing decision with full audit context.

get_config(design_id) -> dict
    Return current gate configuration for a design.

update_config(design_id, threshold, *, low_confidence_action) -> dict
    Persist updated gate configuration.

Persistence
-----------
Gate decisions are written to `aadc_confidence_events` (append-only, NIST AU-2)
and gate configs are stored in `aadc_gate_configs`.  Both live in the AADC
canvas database returned by `get_canvas_connection`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger
from tools.db.storage import sql_placeholder

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 0.70          # minimum confidence to auto-proceed
DEFAULT_LOW_ACTION = "escalate"   # "escalate" | "block" | "retry"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn():
    """Return a canvas DB connection for the AADC tables."""
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection("AADC_DB_PATH")


def _ensure_tables(conn) -> None:
    """Create gate tables if they don't exist (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS aadc_confidence_events (
            id          TEXT PRIMARY KEY,
            design_id   TEXT NOT NULL,
            node_id     TEXT,
            score       REAL NOT NULL,
            threshold   REAL NOT NULL,
            decision    TEXT NOT NULL,
            detail      TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_aadc_confidence_design
            ON aadc_confidence_events(design_id, created_at);

        CREATE TABLE IF NOT EXISTS aadc_gate_configs (
            design_id           TEXT PRIMARY KEY,
            threshold           REAL NOT NULL DEFAULT 0.70,
            low_confidence_action TEXT NOT NULL DEFAULT 'escalate',
            updated_at          TEXT NOT NULL
        );
    """)
    conn.commit()


def _audit_event(conn, design_id: str, node_id: Optional[str],
                 score: float, threshold: float, decision: str, detail: str) -> str:
    """Append a confidence gate decision to aadc_confidence_events."""
    eid = f"cg-{uuid.uuid4().hex[:10]}"
    placeholders = ",".join([sql_placeholder(conn)] * 8)
    conn.execute(
        "INSERT INTO aadc_confidence_events "
        "(id, design_id, node_id, score, threshold, decision, detail, created_at) "
        f"VALUES ({placeholders})",
        (eid, design_id, node_id, score, threshold, decision, detail,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return eid


# ---------------------------------------------------------------------------
# Gate configuration
# ---------------------------------------------------------------------------
def get_config(design_id: str) -> dict:
    """Return the confidence gate configuration for a design.

    Falls back to defaults when no config row exists.
    """
    try:
        conn = _get_conn()
        try:
            _ensure_tables(conn)
            row = conn.execute(
                "SELECT threshold, low_confidence_action FROM aadc_gate_configs "
                f"WHERE design_id = {sql_placeholder(conn)}", (design_id,)
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("confidence_gate: get_config DB error: %s", exc)
        row = None

    if row:
        return {
            "design_id": design_id,
            "threshold": row[0],
            "low_confidence_action": row[1],
        }
    return {
        "design_id": design_id,
        "threshold": DEFAULT_THRESHOLD,
        "low_confidence_action": DEFAULT_LOW_ACTION,
    }


def update_config(design_id: str, threshold: float,
                  low_confidence_action: str = DEFAULT_LOW_ACTION) -> dict:
    """Persist gate configuration for a design (upsert).

    Args:
        design_id: AADC design ID.
        threshold: Minimum confidence score to auto-proceed (0.0–1.0).
        low_confidence_action: One of 'escalate' | 'block' | 'retry'.

    Returns:
        Updated configuration dict.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be 0.0–1.0, got {threshold}")
    if low_confidence_action not in ("escalate", "block", "retry"):
        raise ValueError("low_confidence_action must be escalate|block|retry")

    try:
        conn = _get_conn()
        try:
            _ensure_tables(conn)
            placeholders = ",".join([sql_placeholder(conn)] * 4)
            conn.execute(
                "INSERT INTO aadc_gate_configs (design_id, threshold, low_confidence_action, updated_at) "
                f"VALUES ({placeholders}) "
                "ON CONFLICT(design_id) DO UPDATE SET "
                "  threshold=excluded.threshold, "
                "  low_confidence_action=excluded.low_confidence_action, "
                "  updated_at=excluded.updated_at",
                (design_id, threshold, low_confidence_action,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("confidence_gate: update_config DB error: %s", exc)

    return {
        "design_id": design_id,
        "threshold": threshold,
        "low_confidence_action": low_confidence_action,
    }
