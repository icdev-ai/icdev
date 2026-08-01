# CUI // SP-CTI
"""ACE webhook delivery — POST instance payloads to external URLs.

Retries up to 3 times on 5xx responses with exponential backoff (1s, 2s, 4s).
Logs each delivery attempt to ace_webhook_log.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.webhook")

_DB_ENV = "ICDEV_ACE_DB_URL"
_TIMEOUT = 10
_MAX_RETRIES = 3


def deliver(instance_id: str, webhook_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST *payload* to *webhook_url*.

    Retries on 5xx up to _MAX_RETRIES times.  Logs one summary row to
    ace_webhook_log regardless of outcome.  Always returns a result dict;
    never raises.
    """
    try:
        import requests as _requests
    except ImportError:
        logger.warning("requests library not installed; webhook delivery skipped")
        return {"status_code": None, "attempt_count": 0, "error": "requests not installed"}

    status_code: int | None = None
    response_text: str | None = None
    last_attempted_at: str = ""
    attempt = 0

    for attempt in range(1, _MAX_RETRIES + 1):
        last_attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            resp = _requests.post(webhook_url, json=payload, timeout=_TIMEOUT)
            status_code = resp.status_code
            response_text = resp.text[:2000]
            if status_code < 500:
                break
            logger.warning(
                "Webhook to %s returned %d (attempt %d/%d)",
                webhook_url, status_code, attempt, _MAX_RETRIES,
            )
        except Exception as exc:
            response_text = str(exc)
            logger.warning(
                "Webhook to %s raised %s (attempt %d/%d)",
                webhook_url, exc, attempt, _MAX_RETRIES,
            )

        if attempt < _MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))  # 1s, 2s after attempts 1 and 2

    _log_attempt(instance_id, webhook_url, status_code, response_text, attempt, last_attempted_at)
    return {"status_code": status_code, "attempt_count": attempt}


def _log_attempt(
    instance_id: str,
    url: str,
    status_code: int | None,
    response: str | None,
    attempt_count: int,
    last_attempted_at: str,
) -> None:
    """Insert one row into ace_webhook_log (best-effort)."""
    try:
        from icdev.tools.db.storage import get_canvas_connection

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = get_canvas_connection(_DB_ENV)
        try:
            conn.execute(
                "INSERT INTO ace_webhook_log"
                " (instance_id, url, status_code, response, attempt_count, last_attempted_at, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (instance_id, url, status_code, response, attempt_count, last_attempted_at, now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("Failed to log webhook attempt for %s: %s", instance_id, exc)
