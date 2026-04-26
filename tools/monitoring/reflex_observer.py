# CUI // SP-CTI
"""Reflex observer — thin timing/error wrapper for Genesis reflex dispatch.

Usage:
    from tools.monitoring.reflex_observer import observe
    result = observe(reflex_name, reflex.run, config, session)

The wrapper records wall-clock duration and success/failure to the
reflex_observations table (defined in init_icdev_db.py). Failures are
re-raised so the scheduler's existing error handler still fires.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _record(
    reflex_name: str,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    status: str,
    error_msg: str | None,
    result: Any,
) -> None:
    try:
        import json

        from tools.db.storage import get_connection

        result_json: str | None = None
        try:
            result_json = json.dumps(result, default=str)
        except Exception:
            pass

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO reflex_observations
                    (reflex_name, started_at, finished_at, duration_ms, status, error_msg, result_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [reflex_name, started_at, finished_at, duration_ms, status, error_msg, result_json],
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("reflex_observer: record failed: %s", exc)


def observe(
    reflex_name: str,
    reflex_fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call *reflex_fn* with *args/kwargs*, recording timing and outcome.

    Re-raises any exception after logging so the caller's error handling
    is unaffected.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    try:
        result = reflex_fn(*args, **kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)
        finished_at = datetime.now(timezone.utc).isoformat()
        logger.debug(
            "reflex_observer: %s completed in %dms", reflex_name, duration_ms
        )
        _record(reflex_name, started_at, finished_at, duration_ms, "ok", None, result)
        return result
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        finished_at = datetime.now(timezone.utc).isoformat()
        logger.warning(
            "reflex_observer: %s failed in %dms: %s", reflex_name, duration_ms, exc
        )
        _record(reflex_name, started_at, finished_at, duration_ms, "error", str(exc), None)
        raise
