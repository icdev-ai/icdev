# CUI // SP-CTI
"""Aggregator client — bridges the aggregation worker output to the dashboard
WebSocket layer (and optional HTTP polling cache).

Usage::

    from src.services.aggregator_client import run_and_broadcast, get_latest_payload

    # Trigger aggregation and push result to all connected dashboard clients
    payload = run_and_broadcast(mock=True)

    # Or retrieve the latest cached payload for a polling endpoint
    payload = get_latest_payload()
"""

from __future__ import annotations

import logging
from typing import Any

from src.workers.aggregator import run_worker
from tools.dashboard.websocket import get_socketio

logger = logging.getLogger("icdev.aggregator_client")

_latest_payload: dict[str, Any] = {}


def get_latest_payload() -> dict[str, Any]:
    """Return the most recently aggregated flat payload."""
    return dict(_latest_payload)


def broadcast_payload(payload: dict[str, Any]) -> None:
    """Emit *payload* as a ``table_update`` event on the ``/geospatial`` namespace.

    Safe to call when SocketIO is not initialized (no-op).
    """
    socketio = get_socketio()
    if socketio is None:
        logger.debug("SocketIO unavailable — skipping broadcast")
        return

    try:
        socketio.emit("table_update", payload, namespace="/geospatial", broadcast=True)
        logger.debug("Broadcasted table_update with %s events", payload.get("event_count", 0))
    except Exception as exc:
        logger.warning("Broadcast failed: %s", exc)


def run_and_broadcast(*, mock: bool = False, output_path: str | None = None) -> dict[str, Any]:
    """Execute the aggregation worker and broadcast the flat payload.

    Args:
        mock: Use synthetic data instead of live APIs.
        output_path: Optional file path to write JSON output (passed to worker).

    Returns:
        Flat payload dict with ``generated_at``, ``event_count``, ``events``,
        and ``source_counts``.
    """
    payload = run_worker(mock=mock, output_path=output_path)
    global _latest_payload
    _latest_payload = payload
    broadcast_payload(payload)
    return payload
