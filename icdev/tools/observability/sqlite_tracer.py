#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
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

import atexit
import contextvars
import json
import sqlite3
import threading
import uuid
from tools.db.storage import get_connection, is_pg
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.observability.tracer import Span, Tracer

logger = get_logger("icdev.observability.sqlite_tracer")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "icdev.db"

# Backend-appropriate DB error tuple. Reads/writes route through
# tools.db.storage.get_connection, which targets PostgreSQL by default;
# PG raises psycopg2.Error subclasses that sqlite3.Error does not cover.
try:  # pragma: no cover - import guard
    import psycopg2

    _DB_ERRORS: tuple = (sqlite3.Error, psycopg2.Error)
except ImportError:  # sqlite-only install
    _DB_ERRORS = (sqlite3.Error,)


def _db_file_missing(db_path: Path) -> bool:
    """Whether the SQLite file gate should short-circuit I/O.

    Only meaningful when the effective backend is SQLite. Under the
    PG-primary runtime, reads/writes go through get_connection (PostgreSQL)
    and the .db path is ignored, so the file-existence gate must NOT apply.
    """
    return not is_pg() and not db_path.exists()

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
            self._events.append(
                {
                    "name": name,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "attributes": attributes or {},
                }
            )

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

        # Restore previous active span using the token saved at span start,
        # so ending a nested span re-exposes its parent (not None). Resetting
        # to None here would orphan subsequent sibling spans into new root
        # traces. Guarded against a token from a different context.
        try:
            _active_span_var.reset(self._context_token)
        except (ValueError, LookupError):
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
        # Drain any buffered-but-unflushed spans at process exit. Without this,
        # up to buffer_size-1 spans are silently lost when the process ends.
        atexit.register(self._atexit_flush)

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
        # Append and the threshold check happen under the lock so a concurrent
        # append cannot race the check-then-flush and double-flush.
        with self._write_lock:
            self._buffer.append(span)
            should_flush = len(self._buffer) >= self._buffer_size
        if should_flush:
            self.flush()

    def _atexit_flush(self) -> None:
        """Flush at interpreter exit; safe if the DB is already torn down."""
        try:
            self.flush()
        except Exception:  # noqa: BLE001 - never raise from an atexit handler
            pass

    def flush(self) -> None:
        """Write buffered spans to SQLite/PostgreSQL (thread-safe)."""
        # Atomically swap the buffer under the lock. Two concurrent flushes
        # cannot both drain the same spans: the second sees an empty buffer.
        with self._write_lock:
            if not self._buffer:
                return
            spans_to_write = list(self._buffer)
            self._buffer.clear()

        if _db_file_missing(self._db_path):
            logger.warning("Database not found at %s — spans discarded", self._db_path)
            return

        try:
            conn = get_connection(str(self._db_path))
            for span in spans_to_write:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO otel_spans
                           (id, trace_id, parent_span_id, name, kind,
                            start_time, end_time, duration_ms,
                            status_code, status_message,
                            attributes, events,
                            agent_id, project_id, classification)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
                except _DB_ERRORS as e:
                    logger.error("Failed to write span %s: %s", span.span_id, e)
            conn.commit()
            conn.close()
        except _DB_ERRORS as e:
            logger.error("Database error flushing spans: %s", e)

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
        if _db_file_missing(self._db_path):
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
            conn = get_connection(db_path=str(self._db_path))
            rows = conn.execute(
                f"SELECT * FROM otel_spans WHERE {where} ORDER BY start_time DESC LIMIT %s",  # nosec B608 -- table/column names are internal constants, not user input
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
        except _DB_ERRORS as e:
            logger.error("Query error: %s", e)
            return []
