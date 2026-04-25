#!/usr/bin/env python3
# CUI // SP-CTI
"""Cross-canvas event bus: persistent publish + in-process subscribe.

Usage:
    from tools.canvas.event_bus import publish, subscribe

    subscribe('ndc', 'topology_saved', lambda src, evt, payload: ...)
    event_id = publish('ndc', 'topology_saved', {'topo_id': 'test-1'})
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from tools.db.storage import get_connection

# in-process subscriber registry: {(canvas_id, event_type): [handler_fn, ...]}
_subscribers: dict[tuple[str, str], list[Callable]] = defaultdict(list)


def publish(
    source_canvas: str,
    event_type: str,
    payload: dict,
    target_canvas: str | None = None,
) -> str:
    """Write event row to canvas_events and dispatch to in-process subscribers.

    Returns the new event id.
    """
    event_id = str(uuid.uuid4())
    payload_json = json.dumps(payload)
    created_at = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO canvas_events "
            "(id, source_canvas, target_canvas, event_type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, source_canvas, target_canvas, event_type, payload_json, created_at),
        )
        conn.commit()
    finally:
        conn.close()

    # dispatch to listeners registered for this specific canvas
    for handler in list(_subscribers.get((source_canvas, event_type), [])):
        handler(source_canvas, event_type, payload)
    # dispatch to wildcard listeners (canvas_id="*")
    for handler in list(_subscribers.get(("*", event_type), [])):
        handler(source_canvas, event_type, payload)

    return event_id


def subscribe(canvas_id: str, event_type: str, handler_fn: Callable) -> None:
    """Register an in-process listener for events from canvas_id.

    Use canvas_id="*" to receive events from any canvas.
    """
    _subscribers[(canvas_id, event_type)].append(handler_fn)


def unsubscribe(canvas_id: str, event_type: str, handler_fn: Callable) -> None:
    """Remove a previously registered listener (no-op if not found)."""
    key = (canvas_id, event_type)
    try:
        _subscribers[key].remove(handler_fn)
    except (KeyError, ValueError):
        pass


def mark_consumed(event_id: str) -> None:
    """Stamp consumed_at on an event row after a subscriber processes it."""
    consumed_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE canvas_events SET consumed_at = ? WHERE id = ?",
            (consumed_at, event_id),
        )
        conn.commit()
    finally:
        conn.close()
