#!/usr/bin/env python3
"""ICDEV SaaS -- Rate Limiter.

CUI // SP-CTI

Per-tenant rate limiting with pluggable backends (D66 provider pattern).
In-memory backend for single-replica; Redis backend for multi-replica (HPA).

Tier limits:
  starter       60 req/min,   500 req/hr
  professional  300 req/min,  5 000 req/hr
  enterprise    unlimited

Usage:
    from tools.saas.rate_limiter import register_rate_limiter
    register_rate_limiter(app)
"""

import abc
import logging
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
# Tier rate limits (requests per window)
# ---------------------------------------------------------------------------
TIER_RATE_LIMITS = {
    "starter":      {"per_minute": 60,  "per_hour": 500},
    "professional": {"per_minute": 300, "per_hour": 5000},
    "enterprise":   {"per_minute": -1,  "per_hour": -1},  # unlimited
}


# ---------------------------------------------------------------------------
# Backend ABC (D66 provider pattern)
# ---------------------------------------------------------------------------
class RateLimiterBackend(abc.ABC):
    """Abstract rate limiter backend."""

    @abc.abstractmethod
    def check_and_increment(self, tenant_id: str, limits: dict) -> dict:
        """Check rate limit and increment counters if allowed.

        Returns:
            {"allowed": bool, "remaining": int, "reset_at": int, "limit": int}
        """

    @abc.abstractmethod
    def cleanup(self):
        """Remove expired window counters."""

    @abc.abstractmethod
    def reset_tenant(self, tenant_id: str):
        """Clear all rate-limit state for a tenant."""


# ---------------------------------------------------------------------------
# In-Memory Backend (single-replica, default)
# ---------------------------------------------------------------------------
class InMemoryBackend(RateLimiterBackend):
    """Thread-safe in-memory rate limiter. Good for single replica."""

    def __init__(self):
        self._store = defaultdict(lambda: defaultdict(int))
        self._lock = threading.Lock()

    @staticmethod
    def _window_key(window: str) -> str:
        now = int(time.time())
        if window == "minute":
            return f"m:{now // 60}"
        return f"h:{now // 3600}"

    def check_and_increment(self, tenant_id: str, limits: dict) -> dict:
        minute_key = self._window_key("minute")
        hour_key = self._window_key("hour")

        with self._lock:
            # Per-minute check
            min_count = self._store[tenant_id][minute_key]
            if min_count >= limits["per_minute"]:
                reset_at = ((int(time.time()) // 60) + 1) * 60
                return {
                    "allowed": False,
                    "remaining": 0,
                    "reset_at": reset_at,
                    "limit": limits["per_minute"],
                }

            # Per-hour check
            hr_count = self._store[tenant_id][hour_key]
            if hr_count >= limits["per_hour"]:
                reset_at = ((int(time.time()) // 3600) + 1) * 3600
                return {
                    "allowed": False,
                    "remaining": 0,
                    "reset_at": reset_at,
                    "limit": limits["per_hour"],
                }

            # Allowed — increment both windows
            self._store[tenant_id][minute_key] = min_count + 1
            self._store[tenant_id][hour_key] = hr_count + 1
            remaining = limits["per_minute"] - min_count - 1

        reset_at = ((int(time.time()) // 60) + 1) * 60
        return {
            "allowed": True,
            "remaining": remaining,
            "reset_at": reset_at,
            "limit": limits["per_minute"],
        }

    def cleanup(self):
        now = int(time.time())
        current_minute = f"m:{now // 60}"
        current_hour = f"h:{now // 3600}"

        with self._lock:
            for tenant_id in list(self._store.keys()):
                for key in list(self._store[tenant_id].keys()):
                    if key != current_minute and key != current_hour:
                        del self._store[tenant_id][key]
                if not self._store[tenant_id]:
                    del self._store[tenant_id]

    def reset_tenant(self, tenant_id: str):
        with self._lock:
            self._store.pop(tenant_id, None)


# ---------------------------------------------------------------------------
# Redis Backend (multi-replica, for HPA deployments)
# ---------------------------------------------------------------------------
class RedisBackend(RateLimiterBackend):
    """Redis-based rate limiter using INCR + EXPIRE for atomic counting.

    Required when running multiple API Gateway replicas (autoscaling.enabled).
    Config: args/scaling_config.yaml → rate_limiter.redis.*
    """

    def __init__(self, host: str, port: int = 6379, db: int = 0,
                 password: str = "", key_prefix: str = "icdev:rate:"):
        try:
            import redis
            self._client = redis.Redis(
                host=host, port=port, db=db,
                password=password or None,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            self._client.ping()
            logger.info("Redis rate limiter connected: %s:%d", host, port)
        except ImportError:
            raise RuntimeError(
                "redis package required for RedisBackend. "
                "Install: pip install redis"
            )
        except Exception as e:
            raise RuntimeError(f"Redis connection failed: {e}")
        self._prefix = key_prefix

    def _key(self, tenant_id: str, window: str) -> str:
        now = int(time.time())
        if window == "minute":
            bucket = now // 60
            return f"{self._prefix}{tenant_id}:m:{bucket}"
        bucket = now // 3600
        return f"{self._prefix}{tenant_id}:h:{bucket}"

    def check_and_increment(self, tenant_id: str, limits: dict) -> dict:
        # Circuit breaker protection for Redis (D146)
        try:
            from tools.resilience.circuit_breaker import get_circuit_breaker
            cb = get_circuit_breaker("redis")
            if not cb.allow_request():
                logger.warning("Redis circuit breaker OPEN — skipping rate check")
                return {"allowed": True, "remaining": -1, "reset_at": 0, "limit": -1}
        except ImportError:
            cb = None

        try:
            pipe = self._client.pipeline(transaction=True)

            min_key = self._key(tenant_id, "minute")
            hr_key = self._key(tenant_id, "hour")

            # Atomic INCR + GET current counts
            pipe.incr(min_key)
            pipe.expire(min_key, 120)        # TTL 2 minutes (covers current + next)
            pipe.incr(hr_key)
            pipe.expire(hr_key, 7200)        # TTL 2 hours
            results = pipe.execute()

            if cb:
                cb.record_success()

            min_count = results[0]           # INCR returns new value
            hr_count = results[2]

            # Check per-minute
            if min_count > limits["per_minute"]:
                reset_at = ((int(time.time()) // 60) + 1) * 60
                return {
                    "allowed": False,
                    "remaining": 0,
                    "reset_at": reset_at,
                    "limit": limits["per_minute"],
                }

            # Check per-hour
            if hr_count > limits["per_hour"]:
                reset_at = ((int(time.time()) // 3600) + 1) * 3600
                return {
                    "allowed": False,
                    "remaining": 0,
                    "reset_at": reset_at,
                    "limit": limits["per_hour"],
                }

            remaining = limits["per_minute"] - min_count
            reset_at = ((int(time.time()) // 60) + 1) * 60
            return {
                "allowed": True,
                "remaining": remaining,
                "reset_at": reset_at,
                "limit": limits["per_minute"],
            }
        except Exception as exc:
            if cb:
                cb.record_failure()
            logger.warning("Redis rate limiter error: %s — allowing request", exc)
            return {"allowed": True, "remaining": -1, "reset_at": 0, "limit": -1}

    def cleanup(self):
        # Redis TTL handles expiration automatically — no-op
        pass

    def reset_tenant(self, tenant_id: str):
        pattern = f"{self._prefix}{tenant_id}:*"
        keys = self._client.keys(pattern)
        if keys:
            self._client.delete(*keys)


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------
_backend: RateLimiterBackend | None = None


def _load_scaling_config() -> dict:
    """Load rate limiter config from args/scaling_config.yaml."""
    try:
        import yaml
        config_path = BASE_DIR / "args" / "scaling_config.yaml"
        if config_path.exists():
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
            return config.get("rate_limiter", {})
    except Exception:
        pass
    return {}


def get_backend() -> RateLimiterBackend:
    """Get or create the rate limiter backend.

    Reads args/scaling_config.yaml to determine backend type.
    Falls back to InMemoryBackend if config missing or Redis unavailable.
    """
    global _backend
    if _backend is not None:
        return _backend

    config = _load_scaling_config()
    backend_type = config.get("backend", "in_memory")

    if backend_type == "redis":
        redis_cfg = config.get("redis", {})
        try:
            _backend = RedisBackend(
                host=redis_cfg.get("host", "localhost"),
                port=redis_cfg.get("port", 6379),
                db=redis_cfg.get("db", 0),
                password=redis_cfg.get("password_secret", ""),
                key_prefix=redis_cfg.get("key_prefix", "icdev:rate:"),
            )
            return _backend
        except RuntimeError as e:
            logger.warning("Redis backend failed, falling back to in-memory: %s", e)

    _backend = InMemoryBackend()
    return _backend


# ---------------------------------------------------------------------------
# Core rate-check (backward-compatible API)
# ---------------------------------------------------------------------------
def check_rate_limit(tenant_id: str, tier: str) -> dict:
    """Check whether tenant_id is within its tier rate limits.

    Returns a dict:
        {"allowed": bool, "remaining": int, "reset_at": int, "limit": int}

    remaining and limit are -1 for enterprise (unlimited).
    """
    limits = TIER_RATE_LIMITS.get(tier, TIER_RATE_LIMITS["starter"])

    # Enterprise — unlimited
    if limits["per_minute"] == -1:
        return {"allowed": True, "remaining": -1, "reset_at": 0, "limit": -1}

    return get_backend().check_and_increment(tenant_id, limits)


# ---------------------------------------------------------------------------
# Flask middleware
# ---------------------------------------------------------------------------
def register_rate_limiter(app):
    """Register rate-limiting middleware on a Flask app.

    Must be registered after auth middleware so that g.tenant_id and
    g.auth_info are available.
    """
    from flask import g, jsonify

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
# Housekeeping (backward-compatible)
# ---------------------------------------------------------------------------
def cleanup_expired_windows():
    """Remove expired rate-limit window counters."""
    get_backend().cleanup()


def reset_tenant(tenant_id: str):
    """Clear all rate-limit state for a tenant (admin use)."""
    get_backend().reset_tenant(tenant_id)
