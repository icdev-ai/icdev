# CUI // SP-CTI
"""Geospatial dashboard — HTTP endpoint to trigger aggregation and WebSocket handler to stream updates.

Usage (inside create_app):
    from src.routes.dashboard import bp as geospatial_bp, register_socketio_handlers
    app.register_blueprint(geospatial_bp)
    register_socketio_handlers()
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from flask import Blueprint, jsonify, render_template

from src.clients.diplomatic_client import DiplomaticClient
from src.clients.osint_client import OSINTClient
from src.clients.satellite_client import SatelliteClient
from src.services.aggregator import AggregatorService
from src.services.aggregator_client import broadcast_payload
from src.workers.aggregator import build_payload

logger = logging.getLogger("icdev.geospatial")

bp = Blueprint(
    "geospatial_dashboard",
    __name__,
    template_folder="../../templates",
    static_folder="../../public",
    static_url_path="/geospatial/static",
)

# ------------------------------------------------------------------------------
# Celery lazy setup — optional so the file compiles even when Celery is missing.
# ------------------------------------------------------------------------------
try:
    from celery import Celery  # type: ignore[import-not-found]

    _CELERY_AVAILABLE = True
except ImportError:
    _CELERY_AVAILABLE = False

if _CELERY_AVAILABLE:
    celery_app = Celery(
        "geospatial",
        broker="redis://localhost:6379/0",
        backend="redis://localhost:6379/0",
    )
else:
    celery_app = None


# Demo GeoJSON fallback for environments where upstream clients are unavailable.
_DEMO_GEOJSON: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [35.5018, 33.8938]}, "properties": {"id": "demo-1", "source": "demo", "title": "Beirut", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "port"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [36.2765, 33.5138]}, "properties": {"id": "demo-2", "source": "demo", "title": "Damascus", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "capital"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [44.3661, 33.3152]}, "properties": {"id": "demo-3", "source": "demo", "title": "Baghdad", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "capital"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [51.3890, 35.6892]}, "properties": {"id": "demo-4", "source": "demo", "title": "Tehran", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "capital"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [55.2708, 25.2048]}, "properties": {"id": "demo-5", "source": "demo", "title": "Dubai", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "port"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [34.7818, 32.0853]}, "properties": {"id": "demo-6", "source": "demo", "title": "Tel Aviv", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "port"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [31.2357, 30.0444]}, "properties": {"id": "demo-7", "source": "demo", "title": "Cairo", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "capital"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [46.6753, 24.7136]}, "properties": {"id": "demo-8", "source": "demo", "title": "Riyadh", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "capital"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [51.5310, 25.2854]}, "properties": {"id": "demo-9", "source": "demo", "title": "Doha", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "port"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [47.9774, 29.3759]}, "properties": {"id": "demo-10", "source": "demo", "title": "Kuwait City", "content": "Demo marker", "timestamp": "2026-05-16T12:00:00Z", "metadata": {"category": "port"}}},
    ],
    "properties": {"sources": {"osint": 0, "satellite": 0, "diplomatic": 0}, "demo": True},
}


def _get_demo_geojson() -> dict[str, Any]:
    """Return a copy of the demo GeoJSON with a refreshed timestamp."""
    from datetime import datetime, timezone
    payload = json.loads(json.dumps(_DEMO_GEOJSON))
    payload["properties"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def _run_aggregation() -> dict[str, Any]:
    """Execute the aggregation service and return GeoJSON."""
    try:
        osint = OSINTClient(base_url="http://localhost:8001", api_key="")
        sat = SatelliteClient(base_url="http://localhost:8002", api_key="")
        dip = DiplomaticClient(base_url="http://localhost:8003", api_key="")
        svc = AggregatorService(osint, sat, dip)
        return svc.aggregate()
    except Exception as exc:
        logger.warning("Aggregation failed (%s); returning demo data", exc)
        return _get_demo_geojson()


# ------------------------------------------------------------------------------
# Celery task — geo_stream queue
# ------------------------------------------------------------------------------
if celery_app is not None:

    @celery_app.task(name="geo_stream", queue="geo_stream")
    def geo_stream_task() -> dict[str, Any]:
        """Celery task that aggregates intelligence feeds into GeoJSON."""
        return _run_aggregation()

else:
    geo_stream_task = None  # type: ignore[misc]


# ------------------------------------------------------------------------------
# HTTP routes
# ------------------------------------------------------------------------------
@bp.route("/geospatial")
def geospatial_page():
    """Render the geospatial dashboard map page."""
    return render_template("index.html")


@bp.route("/geospatial/table")
def geospatial_table_page():
    """Render the aggregation table dashboard view."""
    from pathlib import Path
    from flask import send_from_directory
    views_dir = Path(__file__).resolve().parent.parent / "views"
    return send_from_directory(str(views_dir), "dashboard.html")


@bp.route("/api/geospatial/aggregate", methods=["POST"])
def trigger_aggregation():
    """Trigger feed aggregation and return GeoJSON FeatureCollection."""
    return jsonify(_run_aggregation())


@bp.route("/api/geospatial/aggregate", methods=["GET"])
def get_aggregation():
    """Idempotent GET for the latest aggregated GeoJSON."""
    return trigger_aggregation()


# Thread-safe set to track Celery geo_stream tasks submitted by this process.
_pending_geo_tasks: set[str] = set()
_task_lock = threading.Lock()


@bp.route("/api/geospatial/trigger", methods=["GET"])
def trigger_immediate_aggregation():
    """Trigger an immediate aggregation task via Celery and return the task ID.

    If Celery is unavailable the aggregation runs synchronously.
    """
    if geo_stream_task is not None:
        task = geo_stream_task.delay()
        with _task_lock:
            _pending_geo_tasks.add(task.id)
        return jsonify({"task_id": task.id, "status": "submitted", "queue": "geo_stream"})
    return jsonify(_run_aggregation())


# ------------------------------------------------------------------------------
# WebSocket handlers — listen on /geospatial namespace, subscribe to geo_stream
# ------------------------------------------------------------------------------
def register_socketio_handlers():
    """Register SocketIO event handlers for the /geospatial namespace.

    Call this *after* init_socketio(app) has run so get_socketio() returns
    an actual instance rather than None.
    """
    from tools.dashboard.websocket import get_socketio

    socketio = get_socketio()
    if socketio is None:
        logger.info("SocketIO not available — geospatial WebSocket streaming disabled")
        return

    @socketio.on("connect", namespace="/geospatial")
    def handle_connect():
        from flask_socketio import emit
        emit("connected", {"status": "ok", "namespace": "/geospatial"})
        logger.debug("Client connected to /geospatial namespace")

    @socketio.on("disconnect", namespace="/geospatial")
    def handle_disconnect():
        logger.debug("Client disconnected from /geospatial namespace")

    @socketio.on("aggregate_request", namespace="/geospatial")
    def handle_aggregate_request():
        """Client asks for a fresh aggregation — run it and push the result back."""
        from flask_socketio import emit
        result = _run_aggregation()
        emit("aggregate_update", result, namespace="/geospatial", broadcast=True)
        # Also broadcast flat payload for table clients
        flat = build_payload(result.get("features", []))
        flat["source_counts"] = result.get("properties", {}).get("sources", {})
        broadcast_payload(flat)

    @socketio.on("subscribe_geo_stream", namespace="/geospatial")
    def handle_subscribe_geo_stream():
        """Client subscribes to the geo_stream queue — emit current state and start monitor."""
        from flask_socketio import emit
        emit("subscribed", {"queue": "geo_stream", "status": "active"}, namespace="/geospatial")
        logger.debug("Client subscribed to geo_stream on /geospatial namespace")

    # ------------------------------------------------------------------
    # Background thread: push a demo refresh every 30 s so the map stays
    # alive even when no upstream APIs are reachable.
    # ------------------------------------------------------------------
    def _background_stream():
        while True:
            time.sleep(30)
            try:
                socketio.emit("aggregate_update", _get_demo_geojson(), namespace="/geospatial")
            except Exception:
                pass

    thread = threading.Thread(target=_background_stream, daemon=True)
    thread.start()
    logger.info("Geospatial background stream thread started")

    # ------------------------------------------------------------------
    # Celery result monitor: when a geo_stream task finishes, broadcast
    # the aggregated GeoJSON to every connected /geospatial client.
    # ------------------------------------------------------------------
    if geo_stream_task is not None:

        def _celery_monitor():
            """Poll Celery events and emit task results over WebSocket."""
            try:
                from celery.events import EventReceiver  # type: ignore[import-not-found]

                def _on_task_succeeded(event):
                    uuid = event.get("uuid")
                    with _task_lock:
                        if uuid not in _pending_geo_tasks:
                            return
                        _pending_geo_tasks.discard(uuid)
                    result = event.get("result")
                    try:
                        payload = json.loads(result) if isinstance(result, str) else result
                    except Exception:
                        payload = _get_demo_geojson()
                    try:
                        socketio.emit(
                            "aggregate_update",
                            payload,
                            namespace="/geospatial",
                            broadcast=True,
                        )
                        logger.debug("Pushed geo_stream result to /geospatial clients")
                    except Exception:
                        pass
                    # Also broadcast flat payload for table clients
                    try:
                        features = payload.get("features", []) if isinstance(payload, dict) else []
                        flat = build_payload(features)
                        flat["source_counts"] = payload.get("properties", {}).get("sources", {}) if isinstance(payload, dict) else {}
                        broadcast_payload(flat)
                    except Exception:
                        pass

                with celery_app.connection() as conn:  # type: ignore[union-attr]
                    recv = EventReceiver(
                        conn,
                        handlers={"task-succeeded": _on_task_succeeded},
                        wake_after=10,
                    )
                    recv.capture(limit=None, timeout=None, wakeup=True)
            except Exception as exc:
                logger.warning("Celery event monitor exited (%s)", exc)

        monitor_thread = threading.Thread(target=_celery_monitor, daemon=True)
        monitor_thread.start()
        logger.info("Celery geo_stream monitor thread started")
