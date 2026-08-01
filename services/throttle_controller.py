# CUI // SP-CTI
"""Throttle controller — rate-limits LLM and pipeline calls based on budget variance."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from .budget_monitor import BudgetMonitor, BudgetVariance

logger = logging.getLogger("icdev.services.throttle_controller")


class ThrottleController:
    """Background service that enforces spend limits by throttling LLM calls."""

    def __init__(self, monitor: Optional[BudgetMonitor] = None, poll_interval: int = 60) -> None:
        self._monitor = monitor or BudgetMonitor()
        self._poll_interval = poll_interval
        self._throttled: dict[str, bool] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # Event rather than a bare flag so stop() can interrupt the poll wait.
        # With time.sleep(poll_interval) the thread stayed alive for up to a
        # full interval after stop(), which in a test process meant a "stopped"
        # controller still holding a DB connection while the next test ran.
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._monitor.initialize()
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="throttle-controller")
        self._thread.start()
        logger.info("ThrottleController started (poll every %ss)", self._poll_interval)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the loop and wait for it to actually exit.

        Returning before the thread has stopped is how a "stopped" controller
        keeps polling the database underneath whatever runs next.
        """
        self._running = False
        self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    def is_throttled(self, resource: str) -> bool:
        return self._throttled.get(resource, False)

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while self._running:
            try:
                variances: list[BudgetVariance] = self._monitor.calculate_variance()
                for v in variances:
                    if v.over_budget:
                        if not self._throttled.get(v.resource):
                            logger.warning("Budget exceeded for %s (%.1f%%) — throttling", v.resource, v.pct_used)
                        self._throttled[v.resource] = True
                    elif v.pct_used >= 90.0:
                        logger.warning("Budget near limit for %s (%.1f%%) — soft throttle", v.resource, v.pct_used)
                        self._throttled[v.resource] = True
                    else:
                        self._throttled[v.resource] = False
            except Exception:
                logger.exception("ThrottleController poll error")
            # Interruptible: returns immediately once stop() fires.
            if self._stop_event.wait(self._poll_interval):
                break
