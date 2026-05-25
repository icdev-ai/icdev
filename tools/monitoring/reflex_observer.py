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
from tools.logging.icdev_logger import get_logger

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

logger = get_logger(__name__)


def _extract_artifact_count(result: Any) -> int:
    if isinstance(result, dict):
        for key in ("refreshed", "n", "count"):
            val = result.get(key)
            if isinstance(val, int):
                return val
    return 0


def observe(
    reflex_name: str,
    run_fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call *run_fn* with *args/kwargs*, recording timing and outcome.

    Re-raises any exception after logging so the caller's error handling
    is unaffected.
    """
    obs_id = "robs-" + str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    status = "error"
    error_msg: str | None = None
    result: Any = None

    try:
        result = run_fn(*args, **kwargs)
        status = "done"
        return result
    except Exception as exc:
        error_msg = str(exc)
        logger.warning(
            "reflex_observer[%s]: %s failed: %s", obs_id, reflex_name, exc
        )
        raise
    finally:
        duration_ms = int((time.monotonic() - t0) * 1000)
        finished_at = datetime.now(timezone.utc).isoformat()
        artifact_count = _extract_artifact_count(result)

        result_json: str | None = None
        try:
            result_json = json.dumps(result, default=str)
        except Exception:
            pass

        logger.debug(
            "reflex_observer[%s]: %s %s in %dms (artifacts=%d)",
            obs_id, reflex_name, status, duration_ms, artifact_count,
        )

        # Capture active OTel span identifiers for end-to-end traceability
        _trace_id: str | None = None
        _span_id: str | None = None
        try:
            from tools.observability import get_tracer  # noqa: PLC0415
            _active = get_tracer().get_active_span()
            if _active:
                _trace_id = getattr(_active, "trace_id", None)
                _span_id = getattr(_active, "span_id", None)
        except Exception:
            pass

        try:
            from tools.db.storage import get_connection

            conn = get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO reflex_observations
                        (reflex_name, started_at, finished_at, duration_ms,
                         status, artifact_count, error_msg, result_json,
                         trace_id, span_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        reflex_name,
                        started_at,
                        finished_at,
                        duration_ms,
                        status,
                        artifact_count,
                        error_msg,
                        result_json,
                        _trace_id,
                        _span_id,
                    ],
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as db_exc:
            logger.debug("reflex_observer[%s]: DB record failed: %s", obs_id, db_exc)
