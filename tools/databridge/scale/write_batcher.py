# CUI // SP-CTI
"""Scale Write Batcher — WAL + batch flush for sync log and audit writes (D-SC-3).

Buffers DB writes in a thread-safe queue. A single background writer thread
holds one WAL-mode SQLite connection and flushes periodically, eliminating
per-sync DB open/close overhead. All writes are INSERT-only (NIST AU compliant).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from tools.db.storage import get_connection

logger = get_logger("databridge.scale.write_batcher")


@dataclass
class WriteItem:
    """A buffered DB write operation."""

    sql: str
    params: Tuple[Any, ...]
    timestamp: float = field(default_factory=time.monotonic)


class WriteBatcher:
    """Batched SQLite writer with WAL mode (D-SC-3).

    Collects INSERT operations in a queue and flushes them in batches
    via a single writer thread. Reduces SQLite lock contention from
    3 DB opens per sync to 1 persistent connection.
    """

    def __init__(
        self,
        db_path: str,
        flush_interval_ms: int = 500,
        max_buffer_size: int = 100,
    ):
        self._db_path = db_path
        self._flush_interval = flush_interval_ms / 1000.0
        self._max_buffer = max_buffer_size

        self._queue: queue.Queue[WriteItem] = queue.Queue(maxsize=max_buffer_size * 2)
        self._writer_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._conn: Optional[sqlite3.Connection] = None
        self._started = False
        self._stats = {
            "enqueued": 0,
            "flushed": 0,
            "items_written": 0,
            "flush_errors": 0,
        }
        self._stats_lock = threading.Lock()

    def start(self) -> None:
        """Start the background writer thread."""
        if self._started:
            return
        self._shutdown_event.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="scale-write-batcher",
            daemon=True,
        )
        self._writer_thread.start()
        self._started = True
        logger.info(
            "Write batcher started (flush=%dms, buffer=%d)",
            int(self._flush_interval * 1000),
            self._max_buffer,
        )

    def enqueue_sync_log(
        self,
        connection_id: str,
        sync_direction: str,
        table_name: str = "",
        rows_read: int = 0,
        rows_written: int = 0,
        rows_failed: int = 0,
        bytes_transferred: int = 0,
        sync_duration_ms: int = 0,
        incremental_key: str = "",
        incremental_value: str = "",
        error_details: str = "",
    ) -> None:
        """Buffer a sync log entry (matches sync_engine._log_sync signature)."""
        sql = """INSERT INTO db_sync_log
                 (connection_id, sync_direction, table_name,
                  rows_read, rows_written, rows_failed, bytes_transferred,
                  sync_duration_ms, incremental_key, incremental_value,
                  error_details, classification, synced_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CUI // SP-CTI', ?)"""
        params = (
            connection_id,
            sync_direction,
            table_name,
            rows_read,
            rows_written,
            rows_failed,
            bytes_transferred,
            sync_duration_ms,
            incremental_key,
            incremental_value,
            error_details,
            datetime.now(timezone.utc).isoformat(),
        )
        self._enqueue(WriteItem(sql=sql, params=params))

    def enqueue_audit(
        self,
        action: str,
        details: str,
        actor: str = "sync_engine",
    ) -> None:
        """Buffer an audit trail entry (INSERT-only, D-SC-7)."""
        sql = """INSERT INTO audit_trail
                 (id, event_type, actor, action, details, classification, created_at)
                 VALUES (?, ?, ?, ?, ?, 'CUI // SP-CTI', ?)"""
        params = (
            f"audit-{uuid.uuid4().hex[:12]}",
            action,
            actor,
            action,
            details,
            datetime.now(timezone.utc).isoformat(),
        )
        self._enqueue(WriteItem(sql=sql, params=params))

    def flush(self) -> int:
        """Force immediate flush. Returns items flushed."""
        items = self._drain_queue()
        if items:
            return self._flush_items(items)
        return 0

    def stop(self) -> None:
        """Signal shutdown, flush remaining items, close DB connection."""
        if not self._started:
            return
        self._shutdown_event.set()
        if self._writer_thread:
            self._writer_thread.join(timeout=10.0)

        # Final flush of anything remaining
        remaining = self._drain_queue()
        if remaining:
            self._flush_items(remaining)

        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self._started = False
        logger.info("Write batcher stopped")

    def stats(self) -> Dict[str, Any]:
        """Return write stats."""
        with self._stats_lock:
            return {
                "db_path": self._db_path,
                "flush_interval_ms": int(self._flush_interval * 1000),
                "max_buffer_size": self._max_buffer,
                "queue_size": self._queue.qsize(),
                "started": self._started,
                **self._stats,
            }

    # -- internal --

    def _enqueue(self, item: WriteItem) -> None:
        """Add item to queue. Non-blocking with overflow warning."""
        try:
            self._queue.put_nowait(item)
            with self._stats_lock:
                self._stats["enqueued"] += 1
        except queue.Full:
            logger.warning("Write batcher queue full, dropping item")

    def _drain_queue(self) -> List[WriteItem]:
        """Drain all available items from queue."""
        items: List[WriteItem] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items

    def _writer_loop(self) -> None:
        """Background writer: drain queue every flush_interval."""
        self._conn = self._open_conn()

        while not self._shutdown_event.is_set():
            # Wait for flush interval or shutdown
            self._shutdown_event.wait(timeout=self._flush_interval)

            items = self._drain_queue()
            if items:
                self._flush_items(items)

    def _flush_items(self, items: List[WriteItem]) -> int:
        """Write items to SQLite in a single transaction. Returns count written."""
        if not items:
            return 0

        if not self._conn:
            self._conn = self._open_conn()

        try:
            for item in items:
                self._conn.execute(item.sql, item.params)
            self._conn.commit()

            count = len(items)
            with self._stats_lock:
                self._stats["flushed"] += 1
                self._stats["items_written"] += count
            return count

        except Exception as exc:
            logger.error("Flush failed (%d items): %s", len(items), exc)
            with self._stats_lock:
                self._stats["flush_errors"] += 1
            # Try to reconnect for next flush
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = self._open_conn()
            return 0

    def _open_conn(self) -> sqlite3.Connection:
        """Open a WAL-mode SQLite connection."""
        conn = get_connection(db_path=str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
