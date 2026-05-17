# CUI // SP-CTI
"""SIEM Alert Forwarder — delivers ICDEV™ alerts to downstream SIEM endpoints.

Enforces a 5-second SLA for alert delivery (NIST AU-6, SI-4).
Records delivery metrics to the siem_delivery_log table for audit.

Air-gap safe: stdlib urllib only; no external dependencies.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger("icdev.siem_alert_forwarder")

# Requirement: alerts must be delivered to downstream SIEM within 5 seconds.
SLA_SECONDS = 5


class SIEMLatencyExceededError(TimeoutError):
    """Raised when a SIEM alert delivery exceeds the configured SLA."""

    def __init__(self, duration_ms: float, sla_seconds: int = SLA_SECONDS):
        self.duration_ms = duration_ms
        self.sla_seconds = sla_seconds
        super().__init__(
            f"SIEM delivery latency {duration_ms:.2f} ms exceeds SLA of {sla_seconds} s"
        )


def forward_alert(
    alert_payload: Dict[str, Any],
    siem_endpoint: str,
    siem_token: str = "",
    db_path: str | None = None,
) -> Dict[str, Any]:
    """Deliver an alert to a downstream SIEM endpoint within SLA_SECONDS.

    Args:
        alert_payload: Dict with alert fields (title, severity, source, etc.)
        siem_endpoint: URL to POST the alert to.
        siem_token: Optional bearer token for SIEM auth.
        db_path: Override DB path for testing.

    Returns:
        Dict with delivery result:
        - ``delivered`` (bool)
        - ``duration_ms`` (float)
        - ``sla_met`` (bool)
        - ``delivery_id`` (str)
        - ``error`` (str | None)
    """
    delivery_id = str(uuid.uuid4())
    start_ts = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    body = json.dumps(alert_payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ICDEV-SIEM-Forwarder/1.0",
    }
    if siem_token:
        headers["Authorization"] = f"Bearer {siem_token}"

    parsed = urlparse(siem_endpoint)
    if parsed.scheme not in {"http", "https"}:
        return {
            "delivery_id": delivery_id,
            "delivered": False,
            "duration_ms": 0.0,
            "sla_met": False,
            "error": f"Unsupported URL scheme: {parsed.scheme}",
        }

    error = None
    status_code = None
    try:
        req = Request(siem_endpoint, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=SLA_SECONDS) as resp:  # nosec B310 — scheme validated above to http/https only
            status_code = resp.status
            resp.read()
    except HTTPError as exc:
        status_code = exc.code
        error = f"HTTP {exc.code}: {exc.reason}"
    except URLError as exc:
        error = f"URL error: {exc.reason}"
    except TimeoutError:
        error = f"Delivery exceeded SLA of {SLA_SECONDS} seconds"
    except Exception as exc:
        error = str(exc)

    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    sla_met = (error is None) and (duration_ms <= SLA_SECONDS * 1000)

    if error is None and duration_ms > SLA_SECONDS * 1000:
        logger.error(
            "SIEM latency exceeded SLA: %s ms > %s s (delivery_id=%s)",
            duration_ms,
            SLA_SECONDS,
            delivery_id,
        )
        raise SIEMLatencyExceededError(duration_ms, SLA_SECONDS)

    # Persist to siem_delivery_log (append-only / immutable)
    try:
        from tools.db.storage import get_connection

        conn = get_connection(db_path)
        try:
            conn.execute(
                "INSERT INTO siem_delivery_log "
                "(id, alert_title, severity, siem_endpoint, status_code, "
                " duration_ms, sla_met, error, delivered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    delivery_id,
                    alert_payload.get("title", ""),
                    alert_payload.get("severity", ""),
                    siem_endpoint,
                    status_code,
                    duration_ms,
                    1 if sla_met else 0,
                    error,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Failed to write siem_delivery_log: %s", exc)

    return {
        "delivery_id": delivery_id,
        "delivered": error is None,
        "duration_ms": duration_ms,
        "sla_met": sla_met,
        "error": error,
    }
