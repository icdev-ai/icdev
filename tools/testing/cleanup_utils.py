# CUI // SP-CTI
"""Shared graceful-shutdown utilities for ICDEV™ test runners.

Installs SIGTERM/SIGINT handlers that terminate active subprocesses and
close any ICDEV DB connections, preventing idle-in-transaction leaks
when CI jobs are cancelled.

Usage in a runner script::

    from tools.testing.cleanup_utils import install_graceful_shutdown, run_with_cleanup
    install_graceful_shutdown(logger)
    ...
    proc = run_with_cleanup(cmd, capture_output=True, text=True, timeout=120)

The module degrades gracefully when ``psutil`` is absent (child-termination
becomes a best-effort no-op rather than a hard failure).
"""
from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
from typing import Optional

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore

# ── Global state ────────────────────────────────────────────────────────────
_shutdown_event = threading.Event()
_active_proc: Optional[subprocess.Popen] = None
_logger: Optional[logging.Logger] = None

# Windows does not support SIGTERM in the same way as POSIX; guard registration.
_SIGTERM_AVAILABLE = hasattr(signal, "SIGTERM")
_SIGINT_AVAILABLE = hasattr(signal, "SIGINT")


def _ensure_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger("icdev.cleanup")
    return _logger


def set_logger(logger: logging.Logger) -> None:
    """Override the default logger used for cleanup diagnostics."""
    global _logger
    _logger = logger


def is_shutdown_requested() -> bool:
    """Return ``True`` once a shutdown signal has been received."""
    return _shutdown_event.is_set()


def shutdown_event() -> threading.Event:
    """Return the global shutdown event for cross-thread polling."""
    return _shutdown_event


def set_active_process(proc: Optional[subprocess.Popen]) -> None:
    """Register *proc* as the currently running subprocess so that a
    signal handler can terminate it gracefully."""
    global _active_proc
    _active_proc = proc


def _signal_handler(signum: int, frame) -> None:
    """Handle SIGTERM/SIGINT: set shutdown flag, clean up children/DB, then
    restore the default handler so the process exits with the right signal."""
    log = _ensure_logger()
    try:
        sig_name = signal.Signals(signum).name
    except (ValueError, AttributeError):
        sig_name = str(signum)
    log.warning("Received %s — initiating graceful shutdown", sig_name)
    _shutdown_event.set()
    _terminate_active_subprocess()
    _terminate_children()
    _close_db_connections()
    # Restore default handler so the process terminates natively
    try:
        signal.signal(signum, signal.SIG_DFL)
    except (ValueError, OSError):
        pass
    # On POSIX, re-raise the signal to self so the process exits with the
    # correct exit code (128 + signum).
    if sys.platform != "win32":
        try:
            os.kill(os.getpid(), signum)
        except OSError:
            pass


def install_graceful_shutdown(logger: Optional[logging.Logger] = None) -> None:
    """Register SIGTERM/SIGINT handlers and an :mod:`atexit` cleanup hook.

    Safe to call multiple times; duplicate registrations are harmless
    because the handler is idempotent (``_shutdown_event`` is a one-shot).
    """
    if logger is not None:
        set_logger(logger)
    if _SIGTERM_AVAILABLE:
        try:
            signal.signal(signal.SIGTERM, _signal_handler)
        except (ValueError, OSError):
            pass
    if _SIGINT_AVAILABLE:
        try:
            signal.signal(signal.SIGINT, _signal_handler)
        except (ValueError, OSError):
            pass
    atexit.register(_atexit_cleanup)


def _atexit_cleanup() -> None:
    """Best-effort DB cleanup on normal process exit.
    Skips when a signal already triggered the full cleanup path."""
    if _shutdown_event.is_set():
        return
    _close_db_connections()


def _terminate_active_subprocess(timeout: float = 5.0) -> None:
    """Gracefully terminate the subprocess registered with
    :func:`set_active_process`, escalating to SIGKILL if needed."""
    proc = _active_proc
    if proc is None:
        return
    log = _ensure_logger()
    try:
        if proc.poll() is None:
            log.warning("Terminating active subprocess (pid=%s)", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                log.warning("Subprocess did not terminate in %.1fs — killing", timeout)
                proc.kill()
                proc.wait(timeout=2.0)
    except Exception as exc:
        log.warning("Error terminating active subprocess: %s", exc)


def _terminate_children(timeout: float = 3.0) -> None:
    """Walk the process tree from the current PID and terminate all
    descendants, escalating to SIGKILL for survivors."""
    if psutil is None:
        return
    log = _ensure_logger()
    current_pid = os.getpid()
    try:
        parent = psutil.Process(current_pid)
    except psutil.NoSuchProcess:
        return
    try:
        children = parent.children(recursive=True)
    except Exception:
        return
    if not children:
        return
    log.warning("Terminating %d child process(es)", len(children))
    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    gone, alive = psutil.wait_procs(children, timeout=timeout)
    for child in alive or []:
        try:
            log.warning("Child pid=%s did not terminate — killing", child.pid)
            child.kill()
        except psutil.NoSuchProcess:
            pass


def _close_db_connections() -> None:
    """Best-effort close of ICDEV™ PostgreSQL connection pools and any
    open SQLite connections."""
    log = _ensure_logger()
    try:
        from tools.db import storage
    except Exception as exc:
        log.debug("Could not import storage module: %s", exc)
        return

    # Close PG pool
    pool = getattr(storage, "_pg_pool", None)
    if pool is not None:
        try:
            log.info("Closing PG connection pool")
            pool.closeall()
        except Exception as exc:
            log.warning("Error closing PG pool: %s", exc)
        try:
            storage._pg_pool = None
        except Exception:
            pass

    # SQLite connections are per-thread; we can't enumerate them globally.
    # The idle-in-transaction guard in storage._pg_session_options() handles
    # PG; for SQLite the connection dies with the process.


def run_with_cleanup(cmd, **kwargs):
    """Signal-aware drop-in replacement for :func:`subprocess.run`.

    Registers the spawned :class:`subprocess.Popen` object so that a
    SIGTERM/SIGINT handler can terminate it before the runner itself exits.

    Supports the same keyword arguments as ``subprocess.run`` with the
    following exceptions:
    * ``capture_output`` is expanded to ``stdout=PIPE, stderr=PIPE``.
    * ``timeout`` is honoured via ``Popen.communicate(timeout=...)``.
    * ``check`` raises :class:`subprocess.CalledProcessError` on non-zero
      exit code.
    """
    capture_output = kwargs.pop("capture_output", False)
    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)

    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)

    proc = subprocess.Popen(cmd, **kwargs)
    set_active_process(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        stdout, stderr = proc.communicate()
        exc.stdout = stdout
        exc.stderr = stderr
        raise
    finally:
        set_active_process(None)

    ret = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, stdout, stderr)
    return ret
