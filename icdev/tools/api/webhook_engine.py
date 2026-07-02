# CUI // SP-CTI
"""Webhook delivery engine.

dispatch_event(event_type, payload, tenant_id) — queues delivery rows for all
  enabled endpoints whose event_types list includes event_type (or '*').

A background daemon thread (_retry_worker) polls for pending deliveries and
retries up to _MAX_ATTEMPTS times with exponential backoff.  Each delivery is
signed with HMAC-SHA256 so receivers can verify authenticity.

NIST 800-53: AU-2 (event delivery audit), SC-8 (transmission integrity via
             HMAC), SI-10 (input validation on payload), CA-7 (monitoring).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection

_MAX_ATTEMPTS = 5
# Minimum seconds to wait before each retry (indexed by attempt number already made)
_BACKOFF = [0, 10, 30, 120, 300]

_worker_lock = threading.Lock()
_worker_started = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sign_payload(secret: str, body: bytes) -> str:
    """Return 'sha256=<hex>' HMAC signature for *body* using *secret*."""
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def verify_signature(secret: str, body: bytes, header_value: str) -> bool:
    """Return True if *header_value* matches the HMAC of *body*."""
    expected = sign_payload(secret, body)
    return hmac.compare_digest(expected, header_value)


def _endpoint_matches(event_types_str: str, event_type: str) -> bool:
    types = {t.strip() for t in event_types_str.replace(",", " ").split() if t.strip()}
    return "*" in types or event_type in types


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def dispatch_event(event_type: str, payload: dict[str, Any], tenant_id: str) -> int:
    """Queue delivery rows for every matching enabled endpoint.

    Returns the number of delivery rows created.
    """
    queued = 0
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, event_types FROM webhook_endpoints "
            "WHERE tenant_id = %s AND enabled = 1",
            (tenant_id,),
        ).fetchall()
        for row in rows:
            endpoint_id, event_types = row[0], row[1]
            if not _endpoint_matches(event_types, event_type):
                continue
            delivery_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO webhook_deliveries
                    (id, endpoint_id, event_type, payload, status, attempts,
                     last_attempted_at, delivered_at)
                VALUES (%s, %s, %s, %s, 'pending', 0, NULL, NULL)
                """,
                (delivery_id, endpoint_id, event_type, json.dumps(payload)),
            )
            queued += 1
        conn.commit()

    if queued:
        _ensure_worker()
    return queued


# ---------------------------------------------------------------------------
# HTTP delivery
# ---------------------------------------------------------------------------

def _send_delivery(url: str, secret: str, event_type: str, payload_json: str) -> bool:
    """POST the payload to *url*; return True on 2xx, False otherwise."""
    body = payload_json.encode("utf-8")
    sig = sign_payload(secret, body)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-ICDEV-Event": event_type,
            "X-ICDEV-Signature": sig,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


# ---------------------------------------------------------------------------
# Retry worker
# ---------------------------------------------------------------------------

def _process_pending(conn) -> int:
    """Attempt delivery for eligible pending rows. Return count processed."""
    now_iso = _now_iso()
    now_ts = time.time()
    rows = conn.execute(
        "SELECT d.id, d.endpoint_id, d.event_type, d.payload, d.attempts, "
        "       d.last_attempted_at, e.url, e.secret "
        "FROM webhook_deliveries d "
        "JOIN webhook_endpoints e ON e.id = d.endpoint_id "
        "WHERE d.status = 'pending' AND d.attempts < %s",
        (_MAX_ATTEMPTS,),
    ).fetchall()

    processed = 0
    for row in rows:
        (delivery_id, endpoint_id, event_type, payload_json,
         attempts, last_attempted_at, url, secret) = row

        # Backoff guard: skip if not enough time has passed since last attempt
        if last_attempted_at and attempts > 0:
            delay = _BACKOFF[min(attempts, len(_BACKOFF) - 1)]
            last_ts = _iso_to_ts(last_attempted_at)
            if last_ts and (now_ts - last_ts) < delay:
                continue

        success = _send_delivery(url, secret, event_type, payload_json or "{}")
        new_attempts = attempts + 1

        if success:
            conn.execute(
                "UPDATE webhook_deliveries "
                "SET status = 'delivered', attempts = %s, "
                "    last_attempted_at = %s, delivered_at = %s "
                "WHERE id = %s",
                (new_attempts, now_iso, now_iso, delivery_id),
            )
        else:
            new_status = "failed" if new_attempts >= _MAX_ATTEMPTS else "pending"
            conn.execute(
                "UPDATE webhook_deliveries "
                "SET status = %s, attempts = %s, last_attempted_at = %s "
                "WHERE id = %s",
                (new_status, new_attempts, now_iso, delivery_id),
            )
        processed += 1

    conn.commit()
    return processed


def _iso_to_ts(iso: str) -> float | None:
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _retry_worker() -> None:
    """Daemon thread: poll and retry pending deliveries."""
    while True:
        try:
            with get_connection() as conn:
                _process_pending(conn)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(5)


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            t = threading.Thread(target=_retry_worker, daemon=True, name="webhook-retry")
            t.start()
            _worker_started = True
