# CUI // SP-CTI
"""Application factory — instantiates and starts background services at startup."""

from __future__ import annotations

import logging

from services import BudgetMonitor, ThrottleController, start_budget_services

logger = logging.getLogger("icdev.app_factory")

_budget_monitor: BudgetMonitor | None = None
_throttle_controller: ThrottleController | None = None


def create_app() -> dict:
    """Initialize and start budget monitor and throttle controller as background tasks."""
    global _budget_monitor, _throttle_controller

    _throttle_controller = start_budget_services()
    # start_budget_services() returns None when background services are disabled
    # for this process (under pytest, or ICDEV_BACKGROUND_SERVICES=0). Both
    # globals are already annotated Optional; dereferencing here would make the
    # documented opt-out crash app startup instead of skipping the services.
    _budget_monitor = _throttle_controller._monitor if _throttle_controller else None

    if _throttle_controller is None:
        logger.info("App factory: background services disabled, nothing started")
    else:
        logger.info("App factory: budget monitor and throttle controller started")
    return {
        "budget_monitor": _budget_monitor,
        "throttle_controller": _throttle_controller,
    }


def get_budget_monitor() -> BudgetMonitor | None:
    return _budget_monitor


def get_throttle_controller() -> ThrottleController | None:
    return _throttle_controller
