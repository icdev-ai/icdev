#!/usr/bin/env python3
# CUI // SP-CTI
"""SQLite Trace Backend — Air-gapped span storage (D280).

Writes spans to the `otel_spans` table in icdev.db.
Designed for IL5/IL6 environments with no network access to collectors.

Features:
  - Append-only span storage (D6 compliance)
  - Hierarchical span nesting via parent_span_id
  - contextvar-based active span tracking
  - Thread-safe SQLite writes
  - Span buffering with configurable flush interval

Usage:
    from tools.observability.sqlite_tracer import SQLiteTracer
    tracer = SQLiteTracer()

    with tracer.start_span("my_op", attributes={"key": "val"}) as span:
        span.set_attribute("result", "ok")
"""

import contextvars
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.observability.tracer import NullSpan, Span, Tracer

logger = logging.getLogger("icdev.observability.sqlite_tracer")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "icdev.db"

# Active span tracking via contextvars
_active_span_var: contextvars.ContextVar[Optional["SQLiteSpan"]] = contextvars.ContextVar(
    "icdev_active_span", default=None
)


class SQLiteSpan(Span):
    """Span implementation backed by SQLite (D280)."""

    def __init__(
        self,
        name: str,
        trace_id: str,
        span_id: str,
        parent_span_id: Optional[str] = None,
        kind: str = "INTERNAL",
        attributes: Optional[Dict[str, Any]] = None,
        tracer: Optional["SQLiteTracer"] = None,
    ):
        self._name = name
        self._trace_id = trace_id
        self._span_id = span_id
        self._parent_span_id = parent_span_id
        self._kind = kind
        self._attributes: Dict[str, Any] = attributes or {}
        self._events: List[Dict[str, Any]] = []
        self._status_code = "UNSET"
        self._status_message = ""
        self._start_time = datetime.now(timezone.utc)
        self._end_time: Optional[datetime] = None
        self._duration_ms: int = 0
        self._tracer = tracer
        self._ended = False

        # Set as active span
        self._context_token = _active_span_var.set(self)

    @property
    def span_id(self) -> str:
        return self._span_id

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def parent_span_id(self) -> Optional[str]:
        return self._parent_span_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def start_time(self) -> datetime:
        return self._start_time

    @property
    def end_time(self) -> Optional[datetime]:
        return self._end_time

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def status_code(self) -> str:
        return self._status_code

    @property
    def attributes(self) -> Dict[str, Any]:
        return dict(self._attributes)

    def set_attribute(self, key: str, value: Any) -> None:
        if not self._ended:
            self._attributes[key] = value

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        if not self._ended:
            self._events.append({
                "name": name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "attributes": attributes or {},
            })

    def set_status(self, code: str, message: str = "") -> None:
        if not self._ended:
            self._status_code = code
            self._status_message = message

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        self._end_time = datetime.now(timezone.utc)
        delta = self._end_time - self._start_time
        self._duration_ms = int(delta.total_seconds() * 1000)

        # Restore previous active span
        _active_span_var.set(None)

        # Persist to SQLite
        if self._tracer:
            self._tracer._record_span(self)

    def _raw_status_code(self) -> str:
        return self._status_code

    def to_dict(self) -> Dict[str, Any]:
        """Serialize span to dictionary for storage/export."""
        return {
            "id": self._span_id,
            "trace_id": self._trace_id,
            "parent_span_id": self._parent_span_id,
            "name": self._name,
            "kind": self._kind,
            "start_time": self._start_time.isoformat(),
            "end_time": self._end_time.isoformat() if self._end_time else None,
            "duration_ms": self._duration_ms,
            "status_code": self._status_code,
            "status_message": self._status_message,
            "attributes": self._attributes,
            "events": self._events,
        }


class SQLiteTracer(Tracer):
    """SQLite-backed tracer for air-gapped environments (D280).

    Writes completed spans to the `otel_spans` table in icdev.db.
    Thread-safe via a lock on write operations.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        classification: str = "CUI",
    ):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._agent_id = agent_id
        self._project_id = project_id
        self._classification = classification
        self._write_lock = threading.Lock()
        self._buffer: List[SQLiteSpan] = []
        self._buffer_size = 10  # Flush after N spans

    def start_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        kind: str = "INTERNAL",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> SQLiteSpan:
        """Create and start a new SQLite-backed span."""
        # Inherit trace_id from parent or active span or generate new
        active = _active_span_var.get()
        if parent:
            trace_id = parent.trace_id
            parent_id = parent.span_id
        elif active:
            trace_id = active.trace_id
            parent_id = active.span_id
        else:
            trace_id = uuid.uuid4().hex
            parent_id = None

        span_id = uuid.uuid4().hex[:16]

        # Merge default attributes
        attrs = {}
        if self._agent_id:
            attrs["icdev.agent_id"] = self._agent_id
        if self._project_id:
            attrs["icdev.project_id"] = self._project_id
        if attributes:
            attrs.update(attributes)

        return SQLiteSpan(
            name=name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_id,
            kind=kind,
            attributes=attrs,
            tracer=self,
        )

    def get_active_span(self) -> Optional[Span]:
        return _active_span_var.get()

    def _record_span(self, span: SQLiteSpan) -> None:
        """Record a completed span to the buffer, flush if needed."""
        self._buffer.append(span)
        if len(self._buffer) >= self._buffer_size:
            self.flush()

    def flush(self) -> None:
        """Write buffered spans to SQLite (thread-safe)."""
        if not self._buffer:
            return

        with self._write_lock:
            spans_to_write = list(self._buffer)
            self._buffer.clear()

        if not self._db_path.exists():
            logger.warning("Database not found at %s — spans discarded", self._db_path)
            return

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=5)
            for span in spans_to_write:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO otel_spans
                           (id, trace_id, parent_span_id, name, kind,
                            start_time, end_time, duration_ms,
                            status_code, status_message,
                            attributes, events,
                            agent_id, project_id, classification)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            span.span_id,
                            span.trace_id,
                            span.parent_span_id,
                            span.name,
                            span.kind,
                            span.start_time.isoformat(),
                            span.end_time.isoformat() if span.end_time else None,
                            span.duration_ms,
                            span.status_code,
                            span._status_message,
                            json.dumps(span.attributes),
                            json.dumps(span._events),
                            span.attributes.get("icdev.agent_id", self._agent_id),
                            span.attributes.get("icdev.project_id", self._project_id),
                            self._classification,
                        ),
                    )
                except sqlite3.Error as e:
                    logger.error("Failed to write span %s: %s", span.span_id, e)
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            logger.error("SQLite error flushing spans: %s", e)

    def query_spans(
        self,
        trace_id: Optional[str] = None,
        project_id: Optional[str] = None,
        name: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query stored spans from SQLite.

        Args:
            trace_id: Filter by trace ID.
            project_id: Filter by project ID.
            name: Filter by span name (exact match).
            limit: Maximum results.

        Returns:
            List of span dictionaries.
        """
        if not self._db_path.exists():
            return []

        conditions = []
        params = []

        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if name:
            conditions.append("name = ?")
            params.append(name)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM otel_spans WHERE {where} ORDER BY start_time DESC LIMIT ?",
                params,
            ).fetchall()
            conn.close()

            return [
                {
                    "id": row["id"],
                    "trace_id": row["trace_id"],
                    "parent_span_id": row["parent_span_id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "duration_ms": row["duration_ms"],
                    "status_code": row["status_code"],
                    "status_message": row["status_message"],
                    "attributes": json.loads(row["attributes"]) if row["attributes"] else {},
                    "events": json.loads(row["events"]) if row["events"] else [],
                    "agent_id": row["agent_id"],
                    "project_id": row["project_id"],
                    "classification": row["classification"],
                }
                for row in rows
            ]
        except sqlite3.Error as e:
            logger.error("Query error: %s", e)
            return []
