# CUI // SP-CTI
"""ICDEV™ IL5 SLA Timeout Handler — 30-second ingestion-and-display pipeline guard.

Enforces the 30-second end-to-end SLA for the IL5 combined ingestion and
display pipeline (source publication → client display). Provides:

  - ``check_il5_timeout()``  — checkpoint function; call at any pipeline stage
  - ``IL5PipelineTimer``     — context manager that auto-enforces the deadline
  - ``il5_timeout``          — decorator variant for pipeline functions

A failure is logged (NIST AU-2/AU-12) and ``TimeoutError`` is raised on breach.

NIST 800-53: AU-2, AU-12 (audit), SI-12 (information handling and retention).

Usage::

    from tools.il5.sla_handler import check_il5_timeout, IL5PipelineTimer
    from datetime import datetime, timezone

    # Checkpoint pattern
    start = datetime.now(timezone.utc)
    run_ingestion_step()
    check_il5_timeout(start)        # raises TimeoutError if > 30 s elapsed
    run_display_step()
    check_il5_timeout(start)        # second checkpoint before final display

    # Context manager pattern
    with IL5PipelineTimer() as timer:
        run_ingestion_step()
        run_display_step()
    # TimeoutError raised automatically if wall-clock exceeds 30 s

    # Decorator pattern
    @il5_timeout
    def run_pipeline(source_id: str) -> dict:
        ...
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import threading
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from tools.il5.ingestion import SLA_SECONDS

_CLASSIFICATION = "CUI"
_IMPACT_LEVEL = "IL5"

log = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def check_il5_timeout(
    start_time: datetime,
    *,
    timeout_s: int = SLA_SECONDS,
    label: str = "il5_pipeline",
) -> float:
    """Check whether the IL5 pipeline has exceeded the SLA deadline.

    Call at any checkpoint during ingestion or display processing. If the
    wall-clock elapsed time since *start_time* exceeds *timeout_s*, a
    NIST AU-2 failure event is logged and ``TimeoutError`` is raised.

    Args:
        start_time: UTC datetime when the pipeline started.
        timeout_s:  SLA threshold in seconds. Defaults to 30 (IL5 requirement).
        label:      Label included in log and exception messages.

    Returns:
        Elapsed seconds (float) when within budget.

    Raises:
        TimeoutError: If elapsed time exceeds *timeout_s*.
    """
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    elapsed_s: float = (now - start_time).total_seconds()

    if elapsed_s > timeout_s:
        msg = (
            f"[{_CLASSIFICATION} // {_IMPACT_LEVEL}] SLA BREACH — "
            f"{label} exceeded {timeout_s}s limit "
            f"(elapsed={elapsed_s:.3f}s)"
        )
        log.error(
            "[%s // %s] SLA BREACH — %s elapsed=%.3fs limit=%ds",
            _CLASSIFICATION,
            _IMPACT_LEVEL,
            label,
            elapsed_s,
            timeout_s,
        )
        raise TimeoutError(msg)

    log.debug(
        "[%s // %s] SLA check OK — %s elapsed=%.3fs / %ds",
        _CLASSIFICATION,
        _IMPACT_LEVEL,
        label,
        elapsed_s,
        timeout_s,
    )
    return elapsed_s


class IL5PipelineTimer:
    """Context manager that enforces the IL5 30-second SLA end-to-end.

    Starts a background ``threading.Timer`` that sets a breach flag after
    *timeout_s* seconds. On ``__exit__`` (or at any ``check()`` call)
    the flag is evaluated and ``TimeoutError`` raised if set.

    The timer is cancelled immediately on clean exit so it does not fire
    after the pipeline completes normally.

    Example::

        with IL5PipelineTimer() as timer:
            do_ingestion()
            timer.check()       # optional mid-pipeline checkpoint
            do_display()
        # TimeoutError raised here if wall-clock > 30 s
    """

    def __init__(
        self,
        timeout_s: int = SLA_SECONDS,
        label: str = "il5_pipeline",
    ) -> None:
        self.timeout_s = timeout_s
        self.label = label
        self._start: Optional[datetime] = None
        self._breached = threading.Event()
        self._timer: Optional[threading.Timer] = None

    def __enter__(self) -> "IL5PipelineTimer":
        self._start = datetime.now(timezone.utc)
        self._breached.clear()
        self._timer = threading.Timer(self.timeout_s, self._breached.set)
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if self._timer is not None:
            self._timer.cancel()
        if exc_type is None:
            self.check()
        return False

    def check(self) -> float:
        """Checkpoint: raises TimeoutError if the SLA window has expired."""
        if self._start is None:
            raise RuntimeError("IL5PipelineTimer must be used as a context manager")
        return check_il5_timeout(self._start, timeout_s=self.timeout_s, label=self.label)

    @property
    def elapsed_s(self) -> float:
        """Elapsed seconds since the timer was entered."""
        if self._start is None:
            return 0.0
        return (datetime.now(timezone.utc) - self._start).total_seconds()


def il5_timeout(
    func: Optional[F] = None,
    *,
    timeout_s: int = SLA_SECONDS,
    label: Optional[str] = None,
) -> Any:
    """Decorator that enforces the IL5 30-second SLA on a pipeline function.

    Wraps the decorated callable with ``IL5PipelineTimer``. Any return value
    is passed through; ``TimeoutError`` propagates if the SLA is exceeded.

    Can be used with or without arguments::

        @il5_timeout
        def run_pipeline(): ...

        @il5_timeout(timeout_s=20, label="fast_path")
        def run_fast_pipeline(): ...
    """
    def decorator(fn: F) -> F:
        _label = label or fn.__name__

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with IL5PipelineTimer(timeout_s=timeout_s, label=_label):
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    if func is not None:
        return decorator(func)
    return decorator
