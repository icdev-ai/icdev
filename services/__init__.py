# CUI // SP-CTI
"""Services package."""

from __future__ import annotations

import os
from typing import Optional

from .budget_monitor import BudgetMonitor
from .throttle_controller import ThrottleController

__all__ = [
    "BudgetMonitor",
    "ThrottleController",
    "start_budget_services",
    "background_services_enabled",
]

#: Explicit override, both directions. Unset falls through to auto-detection.
_ENV_FLAG = "ICDEV_BACKGROUND_SERVICES"


def background_services_enabled() -> bool:
    """Whether long-running background services may start in this process.

    Disabled automatically under pytest. ``start_budget_services`` is called at
    Flask app-creation time, so ANY test that imports the dashboard app spawns a
    ``throttle-controller`` thread that polls the database every 60s for the rest
    of the session. When a test holds a SQLite write lock, that poll blocks on
    it — and pytest-timeout's only method on Windows is ``thread``, which kills
    the interpreter rather than failing one test. A whole suite aborts, naming an
    unrelated file.

    Nothing reads the controller: ``app.extensions["throttle_controller"]`` is
    assigned and never consumed anywhere in the codebase. In a test process it is
    pure cost — a background DB poller with no reader, able to take the run down
    with it.

    ``ICDEV_BACKGROUND_SERVICES=1`` forces it on (for a test that genuinely
    exercises the service); ``=0`` forces it off in production-shaped runs.
    """
    override = os.environ.get(_ENV_FLAG, "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return True
    if override in ("0", "false", "no", "off"):
        return False

    # PYTEST_CURRENT_TEST is set per-test; PYTEST_VERSION for the whole session
    # (pytest 7+). Checking both covers collection-time imports as well as
    # in-test app creation.
    return not (
        os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PYTEST_VERSION")
    )


def start_budget_services(poll_interval: int = 60) -> Optional[ThrottleController]:
    """Start the budget monitor and throttling controller as background services.

    Returns ``None`` — starting nothing — when background services are disabled
    for this process. Callers must tolerate that: ``tools/dashboard/app.py``
    parks the result in ``app.extensions`` and never reads it back, but
    ``app_factory.create_app`` does dereference it, so it null-checks.
    """
    if not background_services_enabled():
        return None

    monitor = BudgetMonitor()
    controller = ThrottleController(monitor=monitor, poll_interval=poll_interval)
    controller.start()
    return controller
