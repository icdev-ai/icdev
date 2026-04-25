# CUI // SP-CTI
"""Cross-canvas event bus — publish/subscribe for canvas-to-canvas events.

publish(source_canvas, event_type, payload_dict) → writes a row to canvas_events.
subscribe(canvas_id, event_type, handler_fn)     → registers an in-process listener.
dispatch_pending(canvas_id)                      → fires registered handlers for
                                                    unconsumed events targeted at canvas_id.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Callable

from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# In-process subscriber registry: {(canvas_id, event_type): [handler_fn, ...]}
# ---------------------------------------------------------------------------
_LISTENERS: dict[tuple[str, str], list[Callable]] = {}


def subscribe(canvas_id: str, event_type: str, handler_fn: Callable) -> None:
    key = (canvas_id, event_type)
    _LISTENERS.setdefault(key, []).append(handler_fn)


def publish(
    source_canvas: str,
    event_type: str,
    payload_dict: dict,
    *,
    target_canvas: str | None = None,
) -> str:
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    payload_json = json.dumps(payload_dict)

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO canvas_events
                (id, source_canvas, target_canvas, event_type, payload_json, created_at, consumed_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (event_id, source_canvas, target_canvas, event_type, payload_json, now),
        )
        conn.commit()
    finally:
        conn.close()

    # Fire in-process listeners immediately
    _dispatch_to_listeners(source_canvas, event_type, event_id, payload_dict)
    if target_canvas:
        _dispatch_to_listeners(target_canvas, event_type, event_id, payload_dict)

    return event_id


def _dispatch_to_listeners(
    canvas_id: str, event_type: str, event_id: str, payload: dict
) -> None:
    for key in ((canvas_id, event_type), (canvas_id, "*")):
        for handler in _LISTENERS.get(key, []):
            try:
                handler(event_id, canvas_id, event_type, payload)
            except Exception:  # noqa: BLE001 — never let a subscriber crash the bus
                pass


def dispatch_pending(canvas_id: str) -> int:
    """Fire handlers for unconsumed events targeting canvas_id; mark them consumed."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, source_canvas, event_type, payload_json
            FROM canvas_events
            WHERE target_canvas = ? AND consumed_at IS NULL
            ORDER BY created_at
            """,
            (canvas_id,),
        ).fetchall()

        fired = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            event_id = row[0] if isinstance(row, (list, tuple)) else row["id"]
            _src = row[1] if isinstance(row, (list, tuple)) else row["source_canvas"]
            etype = row[2] if isinstance(row, (list, tuple)) else row["event_type"]
            payload = json.loads(
                row[3] if isinstance(row, (list, tuple)) else row["payload_json"]
            )
            _dispatch_to_listeners(canvas_id, etype, event_id, payload)
            conn.execute(
                "UPDATE canvas_events SET consumed_at = ? WHERE id = ?",
                (now, event_id),
            )
            fired += 1

        if fired:
            conn.commit()
        return fired
    finally:
        conn.close()
