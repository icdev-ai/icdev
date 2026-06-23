# CUI // SP-CTI
"""Usage metering — record_usage() and get_usage_summary().

All writes are fire-and-forget background threads so callers never block.
Reads (get_usage_summary) are synchronous and safe to call from any context.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Internal write — runs in background thread
# ---------------------------------------------------------------------------

def _write_event(
    tenant_id: str,
    event_type: str,
    quantity: float,
    model: str | None,
    canvas_key: str | None,
    extra: dict,
) -> None:
    """Insert one row into usage_events.  Swallows all exceptions."""
    try:
        from tools.db.storage import get_connection

        row_id = str(uuid.uuid4())
        recorded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        metadata = json.dumps(extra) if extra else None

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO usage_events "
                "(id, tenant_id, event_type, quantity, model, canvas_key, metadata, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row_id, tenant_id, event_type, quantity, model, canvas_key, metadata, recorded_at),
            )
    except Exception as exc:
        logger.debug("metering._write_event failed (non-blocking): %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_usage(
    tenant_id: str,
    event_type: str,
    quantity: float = 1.0,
    **kwargs: Any,
) -> None:
    """Fire-and-forget insert into usage_events.

    Args:
        tenant_id:  Tenant identifier (required).
        event_type: One of llm_token, api_call, storage_mb, canvas_load, coworker_task.
        quantity:   Numeric magnitude (tokens, MB, count, …).
        **kwargs:   Optional: model, canvas_key, plus arbitrary metadata fields.
    """
    if not tenant_id:
        return

    model = kwargs.pop("model", None)
    canvas_key = kwargs.pop("canvas_key", None)

    t = threading.Thread(
        target=_write_event,
        args=(tenant_id, event_type, quantity, model, canvas_key, kwargs),
        daemon=True,
        name=f"metering-{event_type}",
    )
    t.start()


def get_usage_summary(tenant_id: str, since: str | None = None) -> dict:
    """Return total quantity by event_type for tenant since a given timestamp.

    Args:
        tenant_id: Tenant to query.
        since:     ISO-8601 datetime string lower bound (inclusive).  If None,
                   returns all-time totals.

    Returns:
        Dict mapping event_type → total_quantity, e.g.
        {"llm_token": 12345.0, "api_call": 87.0}
    """
    try:
        from tools.db.storage import get_connection

        if since:
            sql = (
                "SELECT event_type, SUM(quantity) AS total "
                "FROM usage_events "
                "WHERE tenant_id = ? AND recorded_at >= ? "
                "GROUP BY event_type"
            )
            params = (tenant_id, since)
        else:
            sql = (
                "SELECT event_type, SUM(quantity) AS total "
                "FROM usage_events "
                "WHERE tenant_id = ? "
                "GROUP BY event_type"
            )
            params = (tenant_id,)

        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()

        return {row[0]: float(row[1]) for row in rows}
    except Exception as exc:
        logger.debug("metering.get_usage_summary failed: %s", exc)
        return {}
