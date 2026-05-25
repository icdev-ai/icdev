# CUI // SP-CTI
"""Scale Backpressure Controller — memory-aware stream pause/resume (D-SC-6).

Monitors memory usage and pauses/resumes stream producers when thresholds
are exceeded. Uses psutil if available, falls back to /proc/self/status,
then no-op (Windows without psutil = no-op with warning).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import os
import threading
from typing import Any, Dict, Set

logger = get_logger("databridge.scale.backpressure")


def _detect_memory_backend() -> str:
    """Detect available memory monitoring backend."""
    try:
        import psutil  # noqa: F401

        return "psutil"
    except ImportError:
        pass
    if os.path.exists("/proc/self/status"):
        return "proc"
    return "none"


class BackpressureController:
    """Memory and queue-depth backpressure for streams (D-SC-6).

    Fallback chain: psutil → /proc/self/status → no-op.
    """

    def __init__(
        self,
        memory_threshold_mb: int = 400,
        pause_on_threshold: bool = True,
    ):
        self._threshold_bytes = memory_threshold_mb * 1024 * 1024
        self._threshold_mb = memory_threshold_mb
        self._pause_on_threshold = pause_on_threshold
        self._backend = _detect_memory_backend()
        self._paused_streams: Set[str] = set()
        self._lock = threading.Lock()

        if self._backend == "none":
            logger.warning(
                "No memory monitoring available (install psutil for backpressure). Backpressure will be disabled."
            )

    def get_memory_bytes(self) -> int:
        """Return current process RSS in bytes. Returns -1 if unavailable."""
        if self._backend == "psutil":
            return self._get_memory_psutil()
        elif self._backend == "proc":
            return self._get_memory_proc()
        return -1

    def should_pause(self) -> bool:
        """True if memory exceeds threshold and pause_on_threshold is enabled."""
        if not self._pause_on_threshold:
            return False
        mem = self.get_memory_bytes()
        if mem < 0:
            return False
        return mem > self._threshold_bytes

    def pause_stream(self, stream_id: str) -> None:
        """Mark a stream as paused."""
        with self._lock:
            self._paused_streams.add(stream_id)
        logger.info("Stream %s paused (backpressure)", stream_id)

    def resume_stream(self, stream_id: str) -> None:
        """Resume a paused stream."""
        with self._lock:
            self._paused_streams.discard(stream_id)
        logger.info("Stream %s resumed", stream_id)

    def is_paused(self, stream_id: str) -> bool:
        """Check if a specific stream is paused."""
        with self._lock:
            return stream_id in self._paused_streams

    def resume_all(self) -> int:
        """Resume all paused streams. Returns count resumed."""
        with self._lock:
            count = len(self._paused_streams)
            self._paused_streams.clear()
        return count

    def status(self) -> Dict[str, Any]:
        """Return memory usage, threshold, paused streams."""
        mem = self.get_memory_bytes()
        with self._lock:
            paused = list(self._paused_streams)
        return {
            "backend": self._backend,
            "memory_bytes": mem,
            "memory_mb": round(mem / (1024 * 1024), 1) if mem > 0 else -1,
            "threshold_mb": self._threshold_mb,
            "pause_on_threshold": self._pause_on_threshold,
            "exceeds_threshold": mem > self._threshold_bytes if mem > 0 else False,
            "paused_streams": paused,
            "paused_count": len(paused),
        }

    # -- backends --

    @staticmethod
    def _get_memory_psutil() -> int:
        """Get RSS via psutil."""
        try:
            import psutil

            return psutil.Process().memory_info().rss
        except Exception:
            return -1

    @staticmethod
    def _get_memory_proc() -> int:
        """Get RSS via /proc/self/status (Linux only)."""
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # VmRSS: 12345 kB
                        parts = line.split()
                        return int(parts[1]) * 1024
        except Exception:
            pass
        return -1
