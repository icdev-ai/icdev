# CUI // SP-CTI
"""Async alert writer — decouples network forwarding from audit DB write.

DB Optimization: batched writes via a background queue (WAL mode + thread-local
connections) so the HTTP-forwarding critical path is never blocked by SQLite I/O.

Async Network Writer: the caller thread does the HTTP POST and returns immediately
once the network hop is confirmed; DB audit persists in the background writer
thread without adding to the caller's wall-clock latency.

This module is the integration point that combines both optimizations.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = get_logger("icdev.async_alert_writer")

_WRITE_TIMEOUT_SEC = 30     # background writer drains at most every 30s
_BATCH_SIZE = 50            # flush queue after this many records
_QUEUE_MAXSIZE = 10_000     # back-pressure ceiling


class AsyncDBWriter:
    """Thread-safe, batched SQLite writer for siem_delivery_log.

    A single background daemon thread drains a queue and commits in
    batches so concurrent callers never contend on SQLite locks.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._stop = threading.Event()
        # Schema (including WAL mode) must be set before the drain thread starts;
        # otherwise both threads race to run PRAGMA journal_mode=WAL and the
        # background thread gets "database is locked" on Windows.
        self._ensure_schema()
        self._worker = threading.Thread(
            target=self._drain_loop,
            name="async-db-writer",
            daemon=True,
        )
        self._worker.start()

    # ------------------------------------------------------------------
    def enqueue(self, record: dict) -> None:
        """Non-blocking enqueue — returns immediately."""
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            logger.warning("AsyncDBWriter queue full — dropping record %s", record.get("id"))

    def flush(self, timeout: float = 5.0) -> None:
        """Block until the queue is drained or timeout expires."""
        deadline = time.monotonic() + timeout
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.01)

    def shutdown(self, wait: bool = True) -> None:
        self._stop.set()
        if wait:
            self._worker.join(timeout=10)

    # ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        conn = self._open_conn()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS siem_delivery_log (
                    id            TEXT PRIMARY KEY,
                    alert_title   TEXT,
                    severity      TEXT,
                    siem_endpoint TEXT,
                    status_code   INTEGER,
                    duration_ms   REAL,
                    sla_met       INTEGER,
                    error         TEXT,
                    delivered_at  TEXT
                )"""
            )
            conn.commit()
        finally:
            conn.close()

    def _open_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(  # pg-ok: SQLite-WAL async alert buffer (local), not ICDEV storage
            self._db_path,
            timeout=30,
            check_same_thread=False,
        )
        # WAL mode: writers don't block readers; concurrent writers serialise
        # at the WAL level without full DB lock.
        conn.execute("PRAGMA journal_mode=WAL")  # pg-ok: SQLite-only local buffer
        conn.execute("PRAGMA synchronous=NORMAL")  # pg-ok: SQLite-only local buffer
        conn.execute("PRAGMA cache_size=-4096")   # 4 MB page cache  # pg-ok: SQLite-only local buffer
        return conn

    def _drain_loop(self) -> None:
        conn = self._open_conn()
        batch: list[dict] = []

        while not self._stop.is_set():
            try:
                record = self._queue.get(timeout=0.05)
                batch.append(record)
                self._queue.task_done()

                # Accumulate up to BATCH_SIZE before committing
                while len(batch) < _BATCH_SIZE:
                    try:
                        record = self._queue.get_nowait()
                        batch.append(record)
                        self._queue.task_done()
                    except queue.Empty:
                        break

                self._commit_batch(conn, batch)
                batch.clear()

            except queue.Empty:
                if batch:
                    self._commit_batch(conn, batch)
                    batch.clear()

        # Flush remainder
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._commit_batch(conn, batch)
        conn.close()

    @staticmethod
    def _commit_batch(conn: sqlite3.Connection, batch: list[dict]) -> None:
        try:
            conn.executemany(
                """INSERT OR IGNORE INTO siem_delivery_log
                   (id, alert_title, severity, siem_endpoint, status_code,
                    duration_ms, sla_met, error, delivered_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (
                        r["id"],
                        r.get("alert_title", ""),
                        r.get("severity", ""),
                        r.get("siem_endpoint", ""),
                        r.get("status_code"),
                        r.get("duration_ms", 0.0),
                        1 if r.get("sla_met") else 0,
                        r.get("error"),
                        r.get("delivered_at", datetime.now(timezone.utc).isoformat()),
                    )
                    for r in batch
                ],
            )
            conn.commit()
        except Exception as exc:
            logger.error("AsyncDBWriter batch commit failed: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level writer registry (one writer per db_path)
# ---------------------------------------------------------------------------
_writers: dict[str, AsyncDBWriter] = {}
_writers_lock = threading.Lock()


def get_writer(db_path: str) -> AsyncDBWriter:
    """Return (or create) the singleton AsyncDBWriter for db_path."""
    with _writers_lock:
        if db_path not in _writers:
            _writers[db_path] = AsyncDBWriter(db_path)
        return _writers[db_path]


def shutdown_all(wait: bool = True) -> None:
    """Gracefully stop all background writers."""
    with _writers_lock:
        for writer in _writers.values():
            writer.shutdown(wait=wait)
        _writers.clear()


# ---------------------------------------------------------------------------
# Optimised forward_alert_async
# ---------------------------------------------------------------------------

def forward_alert_async(
    alert_payload: Dict[str, Any],
    siem_endpoint: str,
    siem_token: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Async-optimised alert forwarder.

    Delivers an alert to a downstream SIEM endpoint and persists the audit
    record to SQLite via a background write queue.  The caller's latency is
    determined only by the network hop — DB I/O does not block the return.

    Returns the same shape as ``siem_alert_forwarder.forward_alert``.
    """
    from tools.siem_alert_forwarder import SIEMLatencyExceededError, SLA_SECONDS

    import json as _json
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlparse
    from urllib.request import Request, urlopen

    delivery_id = str(uuid.uuid4())
    start_ts = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    body = _json.dumps(alert_payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ICDEV-SIEM-Forwarder-Async/1.0",
    }
    if siem_token:
        headers["Authorization"] = f"Bearer {siem_token}"

    parsed = urlparse(siem_endpoint)
    if parsed.scheme not in {"http", "https"}:
        return {
            "delivery_id": delivery_id,
            "delivered": False,
            "duration_ms": 0.0,
            "sla_met": False,
            "error": f"Unsupported URL scheme: {parsed.scheme}",
        }

    error = None
    status_code = None
    _timed_out = False

    try:
        req = Request(siem_endpoint, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=SLA_SECONDS) as resp:  # nosec B310
            status_code = resp.status
            resp.read()
    except HTTPError as exc:
        status_code = exc.code
        error = f"HTTP {exc.code}: {exc.reason}"
    except URLError as exc:
        error = f"URL error: {exc.reason}"
    except TimeoutError:
        _timed_out = True
        error = f"Delivery exceeded SLA of {SLA_SECONDS} seconds"
    except Exception as exc:
        error = str(exc)

    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    sla_met = (error is None) and (duration_ms <= SLA_SECONDS * 1000)

    # ---- async DB write (non-blocking) ------------------------------------
    if db_path:
        writer = get_writer(db_path)
        writer.enqueue(
            {
                "id": delivery_id,
                "alert_title": alert_payload.get("title", ""),
                "severity": alert_payload.get("severity", ""),
                "siem_endpoint": siem_endpoint,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "sla_met": sla_met,
                "error": error,
                "delivered_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    if _timed_out or (error is None and duration_ms > SLA_SECONDS * 1000):
        raise SIEMLatencyExceededError(duration_ms, SLA_SECONDS)

    return {
        "delivery_id": delivery_id,
        "delivered": error is None,
        "duration_ms": duration_ms,
        "sla_met": sla_met,
        "start_ts": start_ts.isoformat(),
        "error": error,
    }
