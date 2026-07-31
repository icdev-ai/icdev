# CUI // SP-CTI
"""Regression tests for background-service suppression in test processes.

``start_budget_services`` runs at Flask app-creation time, so before this guard
existed every test that built the dashboard app spawned a ``throttle-controller``
thread that polled the database every 60s for the rest of the session. When a
test held a write lock the poll blocked on it, and pytest-timeout's only method
on Windows is ``thread`` — which kills the interpreter rather than failing one
test. The whole suite aborted, naming an unrelated file.

Acceptance criteria:
- Under pytest, ``start_budget_services()`` starts nothing and returns None.
- ``ICDEV_BACKGROUND_SERVICES=1`` forces it on anyway; ``=0`` forces it off.
- ``stop()`` does not return until the poller thread has actually exited.
- Callers tolerate the ``None`` return.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import services
from services.throttle_controller import ThrottleController

POLLER = "throttle-controller"


def _poller_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name == POLLER]


# ── Auto-detection ────────────────────────────────────────────────────────────


def test_disabled_under_pytest():
    """The suite itself is the environment this guard exists for."""
    assert services.background_services_enabled() is False


def test_start_returns_none_and_spawns_no_thread():
    before = set(threading.enumerate())
    assert services.start_budget_services() is None
    assert _poller_threads() == []
    assert set(threading.enumerate()) == before


def test_enabled_when_no_pytest_markers(monkeypatch):
    """A production-shaped process must still start its services."""
    monkeypatch.delenv("ICDEV_BACKGROUND_SERVICES", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    assert services.background_services_enabled() is True


# ── Explicit override, both directions ────────────────────────────────────────


def test_override_forces_on_under_pytest(monkeypatch):
    monkeypatch.setenv("ICDEV_BACKGROUND_SERVICES", "1")
    assert services.background_services_enabled() is True

    controller = services.start_budget_services(poll_interval=300)
    try:
        assert controller is not None
        assert _poller_threads() == [POLLER]
    finally:
        controller.stop()
    assert _poller_threads() == []


def test_override_forces_off_outside_pytest(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.setenv("ICDEV_BACKGROUND_SERVICES", "0")
    assert services.background_services_enabled() is False
    assert services.start_budget_services() is None


# ── stop() is synchronous and interruptible ───────────────────────────────────


def test_stop_interrupts_long_poll_and_joins():
    """With time.sleep(poll_interval), stop() returned while the thread lived on
    for up to a full interval — still holding a DB connection under the next
    test. It must not return until the thread is gone."""
    monitor = MagicMock()
    monitor.calculate_variance.return_value = []
    controller = ThrottleController(monitor=monitor, poll_interval=300)
    controller.start()
    assert _poller_threads() == [POLLER]

    started = time.monotonic()
    controller.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"stop() blocked for {elapsed:.1f}s on a 300s poll"
    assert _poller_threads() == []


def test_stop_is_idempotent():
    monitor = MagicMock()
    monitor.calculate_variance.return_value = []
    controller = ThrottleController(monitor=monitor, poll_interval=300)
    controller.start()
    controller.stop()
    controller.stop()  # must not raise on an already-stopped controller
    assert _poller_threads() == []


# ── Callers tolerate None ─────────────────────────────────────────────────────


def test_app_factory_survives_disabled_services(monkeypatch):
    """app_factory dereferenced the return value, so the documented
    ICDEV_BACKGROUND_SERVICES=0 opt-out crashed startup with AttributeError."""
    monkeypatch.setenv("ICDEV_BACKGROUND_SERVICES", "0")
    import app_factory

    result = app_factory.create_app()
    assert result == {"budget_monitor": None, "throttle_controller": None}
    assert app_factory.get_throttle_controller() is None
    assert app_factory.get_budget_monitor() is None
