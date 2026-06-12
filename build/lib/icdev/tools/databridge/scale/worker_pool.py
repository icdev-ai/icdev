# CUI // SP-CTI
"""Scale Worker Pool — ThreadPoolExecutor with semaphore-gated concurrency (D-SC-1).

Wraps existing synchronous sync_read/sync_write in a thread pool with
configurable max_workers and max_concurrent_syncs enforcement via Semaphore.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

logger = get_logger("databridge.scale.worker_pool")


class ScaleWorkerPool:
    """Thread pool with semaphore-gated sync execution (D-SC-5)."""

    def __init__(
        self,
        max_workers: int = 10,
        max_concurrent_syncs: int = 5,
        sync_timeout_seconds: int = 300,
    ):
        self._max_workers = max_workers
        self._max_concurrent = max_concurrent_syncs
        self._timeout = sync_timeout_seconds

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="scale-sync",
        )
        self._semaphore = threading.Semaphore(max_concurrent_syncs)
        self._active_futures: Dict[str, Future] = {}
        self._lock = threading.Lock()
        self._stats = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "timed_out": 0,
            "cancelled": 0,
        }
        self._shutdown = False

    def submit_sync(
        self,
        sync_fn: Callable[..., Any],
        *args: Any,
        sync_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Future:
        """Submit a sync operation as a future.

        Acquires semaphore before execution, releases after.
        Returns Future with the same return type as sync_fn.
        """
        if self._shutdown:
            raise RuntimeError("Worker pool is shut down")

        sync_id = sync_id or f"sync-{uuid.uuid4().hex[:12]}"

        def _guarded_exec() -> Any:
            self._semaphore.acquire()
            try:
                return sync_fn(*args, **kwargs)
            finally:
                self._semaphore.release()

        future = self._executor.submit(_guarded_exec)

        with self._lock:
            self._stats["submitted"] += 1
            self._active_futures[sync_id] = future

        def _on_done(f: Future) -> None:
            with self._lock:
                self._active_futures.pop(sync_id, None)
                if f.cancelled():
                    self._stats["cancelled"] += 1
                elif f.exception() is not None:
                    self._stats["failed"] += 1
                else:
                    self._stats["completed"] += 1

        future.add_done_callback(_on_done)
        return future

    def get_result(self, future: Future, timeout: Optional[float] = None) -> Any:
        """Block until future completes or timeout. Returns result or raises."""
        t = timeout if timeout is not None else self._timeout
        try:
            return future.result(timeout=t)
        except Exception:
            with self._lock:
                self._stats["timed_out"] += 1
            raise

    def get_active_count(self) -> int:
        """Return number of currently active (not yet done) futures."""
        with self._lock:
            return sum(1 for f in self._active_futures.values() if not f.done())

    def cancel(self, sync_id: str) -> bool:
        """Cancel a pending sync by ID. Returns True if cancelled."""
        with self._lock:
            future = self._active_futures.get(sync_id)
        if future:
            return future.cancel()
        return False

    def shutdown(self, wait: bool = True, timeout: float = 30.0) -> None:
        """Graceful shutdown: wait for active syncs, then kill executor."""
        self._shutdown = True
        logger.info("Shutting down worker pool (wait=%s, timeout=%.1fs)", wait, timeout)
        self._executor.shutdown(wait=wait)
        with self._lock:
            self._active_futures.clear()

    def status(self) -> Dict[str, Any]:
        """Return pool stats."""
        with self._lock:
            active = sum(1 for f in self._active_futures.values() if not f.done())
            return {
                "max_workers": self._max_workers,
                "max_concurrent_syncs": self._max_concurrent,
                "sync_timeout_seconds": self._timeout,
                "active": active,
                "pending_futures": len(self._active_futures),
                "shutdown": self._shutdown,
                **self._stats,
            }
