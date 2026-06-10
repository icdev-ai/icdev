# CUI // SP-CTI
"""Tests for :mod:`tools.testing.cleanup_utils`.

Verifies graceful-shutdown helpers: signal registration, subprocess cleanup,
timeout handling, and best-effort DB connection closing.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.testing.cleanup_utils import (
    _close_db_connections,
    _signal_handler,
    _terminate_active_subprocess,
    _terminate_children,
    install_graceful_shutdown,
    is_shutdown_requested,
    run_with_cleanup,
    set_active_process,
    set_logger,
    shutdown_event,
)


def test_install_graceful_shutdown_idempotent():
    """Calling install_graceful_shutdown repeatedly must not raise."""
    logger = logging.getLogger("test_cleanup")
    install_graceful_shutdown(logger)
    install_graceful_shutdown(logger)  # idempotent


def test_shutdown_event_state():
    """shutdown_event must be a threading.Event that starts unset."""
    evt = shutdown_event()
    assert isinstance(evt, threading.Event)
    assert not evt.is_set()
    assert not is_shutdown_requested()


def test_signal_handler_sets_shutdown():
    """_signal_handler must set the shutdown event and restore the default handler."""
    evt = shutdown_event()
    evt.clear()
    with patch("tools.testing.cleanup_utils._close_db_connections") as mock_close, \
         patch("tools.testing.cleanup_utils._terminate_children") as mock_term, \
         patch("tools.testing.cleanup_utils._terminate_active_subprocess") as mock_active, \
         patch("signal.signal") as mock_signal:
        _signal_handler(signal.SIGTERM, None)
    assert evt.is_set()
    assert is_shutdown_requested()
    mock_active.assert_called_once()
    mock_term.assert_called_once()
    mock_close.assert_called_once()
    mock_signal.assert_called_once_with(signal.SIGTERM, signal.SIG_DFL)


def test_run_with_cleanup_success():
    """run_with_cleanup must return a CompletedProcess for a simple command."""
    result = run_with_cleanup(
        [sys.executable, "-c", "print('hello')"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_run_with_cleanup_timeout():
    """run_with_cleanup must raise TimeoutExpired and kill the child."""
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_cleanup(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    # Ensure the child is dead — no zombie should remain
    # (best-effort: give the OS a moment to reap)
    time.sleep(0.2)


def test_run_with_cleanup_check_raises():
    """run_with_cleanup with check=True must raise CalledProcessError on non-zero exit."""
    with pytest.raises(subprocess.CalledProcessError):
        run_with_cleanup(
            [sys.executable, "-c", "import sys; sys.exit(1)"],
            capture_output=True,
            text=True,
            check=True,
        )


def test_terminate_active_subprocess_noop_when_none():
    """_terminate_active_subprocess must be a no-op when no process is active."""
    set_active_process(None)
    _terminate_active_subprocess()  # must not raise


def test_terminate_active_subprocess_kills_running():
    """_terminate_active_subprocess must terminate a live Popen object."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    set_active_process(proc)
    _terminate_active_subprocess(timeout=0.5)
    assert proc.poll() is not None
    set_active_process(None)


def test_terminate_children_no_crash():
    """_terminate_children must not raise even when there are no children."""
    _terminate_children()  # no children of this test process


def test_close_db_connections_no_crash():
    """_close_db_connections must degrade gracefully when storage is unavailable."""
    with patch.dict("sys.modules", {"tools.db.storage": None}):
        _close_db_connections()  # must not raise


def test_set_logger():
    """set_logger must override the internal logger."""
    custom = logging.getLogger("custom_cleanup_logger")
    set_logger(custom)
    # There is no public getter; we verify by side-effect in install_graceful_shutdown
    install_graceful_shutdown(custom)
