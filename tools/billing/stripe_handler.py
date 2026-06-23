# CUI // SP-CTI
"""Stripe webhook handler — signature verification + billing_status sync.

Environment variables:
    ICDEV_STRIPE_WEBHOOK_SECRET  Stripe endpoint signing secret (whsec_...).

Handled event types:
    invoice.payment_succeeded  → billing_status = 'active'
    invoice.payment_failed     → billing_status = 'past_due'
    customer.subscription.deleted → billing_status = 'cancelled'
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_TOLERANCE_SECONDS = 300  # Stripe recommends 5 min

_STATUS_MAP: dict[str, str] = {
    "invoice.payment_succeeded": "active",
    "invoice.payment_failed": "past_due",
    "customer.subscription.deleted": "cancelled",
}


def verify_stripe_signature(payload: bytes, sig_header: str) -> bool:
    """Return True when the Stripe-Signature header is valid.

    Uses HMAC-SHA256 over ``t=<timestamp>.v1=<sig>`` as per Stripe docs.
    Returns False if the secret is not configured, signature is absent,
    or the timestamp is too old.
    """
    secret = os.environ.get("ICDEV_STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("stripe_handler: ICDEV_STRIPE_WEBHOOK_SECRET not set — rejecting")
        return False

    if not sig_header:
        return False

    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    except Exception:
        return False

    timestamp = parts.get("t")
    v1_sig = parts.get("v1")
    if not timestamp or not v1_sig:
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    if abs(time.time() - ts_int) > _TOLERANCE_SECONDS:
        logger.warning("stripe_handler: timestamp too old or in future (%s)", timestamp)
        return False

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, v1_sig)


def handle_webhook(event_type: str, event_data: dict) -> dict:
    """Dispatch a Stripe event to the appropriate handler.

    Args:
        event_type: Stripe event type string (e.g. ``invoice.payment_succeeded``).
        event_data: The ``data.object`` dict from the Stripe event payload.

    Returns:
        Dict with ``handled`` (bool) and optional ``tenant_id`` / ``billing_status``.
    """
    new_status = _STATUS_MAP.get(event_type)
    if new_status is None:
        return {"handled": False, "event_type": event_type}

    tenant_id = _extract_tenant_id(event_data)
    if not tenant_id:
        logger.warning("stripe_handler: could not extract tenant_id from %s", event_type)
        return {"handled": False, "event_type": event_type, "reason": "no_tenant_id"}

    _update_billing_status(tenant_id, new_status)
    return {"handled": True, "event_type": event_type, "tenant_id": tenant_id, "billing_status": new_status}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_tenant_id(event_data: dict) -> str | None:
    """Extract tenant_id from Stripe metadata or customer field."""
    metadata = event_data.get("metadata") or {}
    if isinstance(metadata, dict):
        tid = metadata.get("tenant_id") or metadata.get("icdev_tenant_id")
        if tid:
            return str(tid)

    customer = event_data.get("customer")
    if customer:
        return str(customer)

    return None


def _update_billing_status(tenant_id: str, billing_status: str) -> None:
    """UPDATE tenants SET billing_status = ? WHERE id = ?"""
    try:
        from tools.db.storage import get_connection

        with get_connection() as conn:
            conn.execute(
                "UPDATE tenants SET billing_status = ? WHERE id = ?",
                (billing_status, tenant_id),
            )
    except Exception as exc:
        logger.error("stripe_handler: failed to update billing_status: %s", exc)
