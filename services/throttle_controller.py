# CUI // SP-CTI
"""Throttle controller — rate-limits LLM and pipeline calls based on budget variance."""

from __future__ import annotations

import logging
import threading
import time
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

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._monitor.initialize()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="throttle-controller")
        self._thread.start()
        logger.info("ThrottleController started (poll every %ss)", self._poll_interval)

    def stop(self) -> None:
        self._running = False

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
            time.sleep(self._poll_interval)
