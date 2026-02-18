#!/usr/bin/env python3
"""ICDEV SaaS -- Rate Limiter.

CUI // SP-CTI

Per-tenant rate limiting using in-memory counters with thread-safe locking.
Production deployments should swap the in-memory store for Redis (the
check_rate_limit / increment interface stays the same).

Tier limits:
  starter       60 req/min,   500 req/hr
  professional  300 req/min,  5 000 req/hr
  enterprise    unlimited

Usage:
    from tools.saas.rate_limiter import register_rate_limiter
    register_rate_limiter(app)
"""

import logging
import os
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.rate_limiter")

# ---------------------------------------------------------------------------
# In-memory rate limit store  {tenant_id: {window_key: count}}
# ---------------------------------------------------------------------------
_rate_store = defaultdict(lambda: defaultdict(int))
_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Tier rate limits (requests per window)
# ---------------------------------------------------------------------------
TIER_RATE_LIMITS = {
    "starter":      {"per_minute": 60,  "per_hour": 500},
    "professional": {"per_minute": 300, "per_hour": 5000},
    "enterprise":   {"per_minute": -1,  "per_hour": -1},  # unlimited
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_window_key(window: str) -> str:
    """Return a time-bucketed key for the current minute or hour."""
    now = int(time.time())
    if window == "minute":
        return f"m:{now // 60}"
    return f"h:{now // 3600}"


# ---------------------------------------------------------------------------
# Core rate-check
# ---------------------------------------------------------------------------
def check_rate_limit(tenant_id: str, tier: str) -> dict:
    """Check whether tenant_id is within its tier rate limits.

    Returns a dict:
        {"allowed": bool, "remaining": int, "reset_at": int, "limit": int}

    remaining and limit are -1 for enterprise (unlimited).
    """
    limits = TIER_RATE_LIMITS.get(tier, TIER_RATE_LIMITS["starter"])

    # Enterprise -- unlimited
    if limits["per_minute"] == -1:
        return {"allowed": True, "remaining": -1, "reset_at": 0, "limit": -1}

    minute_key = _get_window_key("minute")
    hour_key = _get_window_key("hour")

    with _lock:
        # ---- per-minute check ----
        min_count = _rate_store[tenant_id][minute_key]
        if min_count >= limits["per_minute"]:
            reset_at = ((int(time.time()) // 60) + 1) * 60
            return {
                "allowed": False,
                "remaining": 0,
                "reset_at": reset_at,
                "limit": limits["per_minute"],
            }

        # ---- per-hour check ----
        hr_count = _rate_store[tenant_id][hour_key]
        if hr_count >= limits["per_hour"]:
            reset_at = ((int(time.time()) // 3600) + 1) * 3600
            return {
                "allowed": False,
                "remaining": 0,
                "reset_at": reset_at,
                "limit": limits["per_hour"],
            }

        # Allowed -- increment both windows
        _rate_store[tenant_id][minute_key] = min_count + 1
        _rate_store[tenant_id][hour_key] = hr_count + 1
        remaining = limits["per_minute"] - min_count - 1

    reset_at = ((int(time.time()) // 60) + 1) * 60
    return {
        "allowed": True,
        "remaining": remaining,
        "reset_at": reset_at,
        "limit": limits["per_minute"],
    }


# ---------------------------------------------------------------------------
# Flask middleware
# ---------------------------------------------------------------------------
def register_rate_limiter(app):
    """Register rate-limiting middleware on a Flask app.

    Must be registered after auth middleware so that g.tenant_id and
    g.auth_info are available.
    """
    from flask import g, jsonify, request as flask_request

    @app.before_request
    def _check_rate():
        tenant_id = getattr(g, "tenant_id", None)
        auth_info = getattr(g, "auth_info", None)
        if not tenant_id or not auth_info:
            return None  # Public endpoint or not yet authenticated

        tier = auth_info.get("tenant_tier", "starter")
        result = check_rate_limit(tenant_id, tier)

        if not result["allowed"]:
            retry_after = max(result["reset_at"] - int(time.time()), 1)
            resp = jsonify({
                "error": "Rate limit exceeded",
                "code": "RATE_LIMITED",
                "retry_after": retry_after,
                "limit": result["limit"],
            })
            resp.status_code = 429
            resp.headers["Retry-After"] = str(retry_after)
            return resp

        # Stash result so after_request can add headers
        g._rate_result = result
        return None

    @app.after_request
    def _add_rate_headers(response):
        tenant_id = getattr(g, "tenant_id", None)
        auth_info = getattr(g, "auth_info", None)
        rate = getattr(g, "_rate_result", None)
        if tenant_id and auth_info:
            tier = auth_info.get("tenant_tier", "starter")
            limits = TIER_RATE_LIMITS.get(tier, TIER_RATE_LIMITS["starter"])
            response.headers["X-RateLimit-Limit"] = str(limits["per_minute"])
            if rate:
                response.headers["X-RateLimit-Remaining"] = str(rate["remaining"])
                response.headers["X-RateLimit-Reset"] = str(rate["reset_at"])
        return response


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
def cleanup_expired_windows():
    """Remove expired rate-limit window counters.

    Call periodically (e.g. every 5 minutes) from a background thread or
    scheduler to prevent unbounded memory growth.
    """
    now = int(time.time())
    current_minute = f"m:{now // 60}"
    current_hour = f"h:{now // 3600}"

    with _lock:
        for tenant_id in list(_rate_store.keys()):
            for key in list(_rate_store[tenant_id].keys()):
                if key != current_minute and key != current_hour:
                    del _rate_store[tenant_id][key]
            if not _rate_store[tenant_id]:
                del _rate_store[tenant_id]


def reset_tenant(tenant_id: str):
    """Clear all rate-limit state for a tenant (admin use)."""
    with _lock:
        _rate_store.pop(tenant_id, None)
