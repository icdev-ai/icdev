
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI — ICDEV Pipeline SLA Handler
# Classification: CUI — Controlled Unclassified Information
"""
IL5 SLA timeout enforcement for the ingestion and display pipeline.

Raises TimeoutError if the combined ingestion + display pipeline exceeds 30 seconds.
"""
import logging
import time
from contextlib import contextmanager

logger = get_logger(__name__)

IL5_PIPELINE_TIMEOUT_SECONDS = 30


def check_il5_timeout(start_time: float, *, label: str = "pipeline") -> None:
    """Raise TimeoutError if elapsed time since start_time exceeds the IL5 SLA (30 s).

    Args:
        start_time: Unix timestamp returned by time.monotonic() at pipeline start.
        label: Human-readable name for the operation used in log messages.

    Raises:
        TimeoutError: When elapsed >= IL5_PIPELINE_TIMEOUT_SECONDS.
    """
    elapsed = time.monotonic() - start_time
    if elapsed >= IL5_PIPELINE_TIMEOUT_SECONDS:
        msg = (
            f"IL5 SLA violation: {label!r} exceeded {IL5_PIPELINE_TIMEOUT_SECONDS}s "
            f"(elapsed={elapsed:.2f}s)"
        )
        logger.error(msg)
        raise TimeoutError(msg)
    logger.debug("IL5 SLA check passed for %r: %.2fs elapsed", label, elapsed)


@contextmanager
def il5_timeout_context(label: str = "pipeline"):
    """Context manager that enforces the IL5 30-second SLA on exit.

    Usage::

        with il5_timeout_context("ingest+display"):
            run_ingestion()
            run_display()

    Raises:
        TimeoutError: When the block takes >= 30 seconds.
    """
    start = time.monotonic()
    try:
        yield start
    finally:
        check_il5_timeout(start, label=label)
