from __future__ import annotations

from tools.logging.icdev_logger import get_logger
"""Usage analytics: event collection and Flask middleware for dashboard routes."""

import hashlib
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from tools.db.storage import get_connection

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "usage_analytics_config.yaml"
_log = get_logger(__name__)


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


class EventCollector:
    def __init__(self) -> None:
        cfg = _load_config()
        collection = cfg.get("collection", {})
        self.exclude_routes: set = set(collection.get("exclude_routes", []))
        self.exclude_ips: set = set(collection.get("exclude_ips", []))

    def record(
        self,
        route: str,
        method: str,
        status_code: int,
        duration_ms: int,
        skill_invoked: str | None = None,
        user_session: str | None = None,
        ip: str | None = None,
        feature_tag: str | None = None,
    ) -> str:
        if route in self.exclude_routes:
            return ""
        if ip and ip in self.exclude_ips:
            return ""

        ip_hash = hashlib.sha256(ip.encode()).hexdigest() if ip else None
        event_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            conn = get_connection()
            try:
                conn.execute(
                    """INSERT INTO usage_events
                       (id, route, method, status_code, duration_ms, skill_invoked,
                        user_session, ip_hash, feature_tag, occurred_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (event_id, route, method, status_code, duration_ms,
                     skill_invoked, user_session, ip_hash, feature_tag, now_iso),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            _log.warning("EventCollector.record failed: %s", exc)
            return ""

        return event_id


def track_request(app) -> None:
    """Register before_request/after_request hooks to auto-record all dashboard routes."""
    _collector = EventCollector()
    _start_attr = "_usage_start"

    @app.before_request
    def _before():
        from flask import g
        g.__dict__[_start_attr] = time.monotonic()

    @app.after_request
    def _after(response):
        from flask import g, request
        start = g.__dict__.pop(_start_attr, None)
        duration_ms = int((time.monotonic() - start) * 1000) if start is not None else 0
        try:
            route = request.path
            parts = route.strip("/").split("/")
            feature_tag = parts[0] if parts and parts[0] else None
            _collector.record(
                route=route,
                method=request.method,
                status_code=response.status_code,
                duration_ms=duration_ms,
                feature_tag=feature_tag,
            )
        except Exception as exc:
            _log.warning("track_request after_request failed: %s", exc)
        return response
