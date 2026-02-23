# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for the ICDEV SaaS Rate Limiter (tools/saas/rate_limiter.py).

Validates tier-based rate limiting (starter/professional/enterprise),
InMemoryBackend sliding-window tracking, cleanup of expired entries,
and tenant reset functionality.
"""

import time
from unittest.mock import patch

import pytest

try:
    from tools.saas.rate_limiter import (
        TIER_RATE_LIMITS,
        InMemoryBackend,
        check_rate_limit,
        cleanup_expired_windows,
        reset_tenant,
    )
    _IMPORT_OK = True
except ImportError:
    _IMPORT_OK = False

pytestmark = pytest.mark.skipif(not _IMPORT_OK, reason="tools.saas.rate_limiter not available")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_backend(monkeypatch):
    """Reset the global backend before each test to ensure isolation."""
    import tools.saas.rate_limiter as rl
    monkeypatch.setattr(rl, "_backend", None)
    # Force InMemoryBackend (bypass scaling config)
    backend = InMemoryBackend()
    monkeypatch.setattr(rl, "_backend", backend)
    yield backend


# ---------------------------------------------------------------------------
# Tier Rate Limit Configuration
# ---------------------------------------------------------------------------

class TestTierConfig:
    """Verify the tier rate limit constants."""

    def test_starter_per_minute(self):
        assert TIER_RATE_LIMITS["starter"]["per_minute"] == 60

    def test_professional_per_minute(self):
        assert TIER_RATE_LIMITS["professional"]["per_minute"] == 300

    def test_enterprise_unlimited(self):
        assert TIER_RATE_LIMITS["enterprise"]["per_minute"] == -1
        assert TIER_RATE_LIMITS["enterprise"]["per_hour"] == -1


# ---------------------------------------------------------------------------
# Enterprise (unlimited) tier
# ---------------------------------------------------------------------------

class TestEnterpriseTier:
    """Verify enterprise tier is always allowed."""

    def test_enterprise_always_allowed(self):
        result = check_rate_limit("tenant-ent-1", "enterprise")
        assert result["allowed"] is True
        assert result["remaining"] == -1
        assert result["limit"] == -1

    def test_enterprise_multiple_calls(self):
        for _ in range(200):
            result = check_rate_limit("tenant-ent-2", "enterprise")
            assert result["allowed"] is True


# ---------------------------------------------------------------------------
# Starter tier rate limiting
# ---------------------------------------------------------------------------

class TestStarterTier:
    """Verify starter tier enforces 60 req/min."""

    def test_first_request_allowed(self):
        result = check_rate_limit("tenant-s-1", "starter")
        assert result["allowed"] is True
        assert result["limit"] == 60

    def test_remaining_decreases(self):
        r1 = check_rate_limit("tenant-s-2", "starter")
        r2 = check_rate_limit("tenant-s-2", "starter")
        assert r2["remaining"] < r1["remaining"]

    def test_exceeds_per_minute_limit(self):
        tenant_id = "tenant-s-3"
        for _ in range(60):
            check_rate_limit(tenant_id, "starter")
        result = check_rate_limit(tenant_id, "starter")
        assert result["allowed"] is False
        assert result["remaining"] == 0

    def test_reset_at_is_future(self):
        result = check_rate_limit("tenant-s-4", "starter")
        if result["reset_at"] > 0:
            assert result["reset_at"] > int(time.time()) - 1


# ---------------------------------------------------------------------------
# Professional tier rate limiting
# ---------------------------------------------------------------------------

class TestProfessionalTier:
    """Verify professional tier has higher limits."""

    def test_professional_limit_is_300(self):
        result = check_rate_limit("tenant-p-1", "professional")
        assert result["limit"] == 300

    def test_professional_allows_more_than_60(self):
        tenant_id = "tenant-p-2"
        for _ in range(65):
            result = check_rate_limit(tenant_id, "professional")
        assert result["allowed"] is True


# ---------------------------------------------------------------------------
# Unknown tier falls back to starter
# ---------------------------------------------------------------------------

class TestUnknownTier:
    """Verify unknown tiers fall back to starter limits."""

    def test_unknown_tier_uses_starter_limit(self):
        result = check_rate_limit("tenant-u-1", "unknown_tier")
        assert result["limit"] == 60


# ---------------------------------------------------------------------------
# InMemoryBackend internals
# ---------------------------------------------------------------------------

class TestInMemoryBackend:
    """Verify InMemoryBackend behavior directly."""

    def test_check_and_increment_returns_dict(self):
        backend = InMemoryBackend()
        limits = {"per_minute": 10, "per_hour": 100}
        result = backend.check_and_increment("t1", limits)
        assert "allowed" in result
        assert "remaining" in result
        assert "reset_at" in result
        assert "limit" in result

    def test_reset_tenant_clears_state(self):
        backend = InMemoryBackend()
        limits = {"per_minute": 5, "per_hour": 50}
        for _ in range(5):
            backend.check_and_increment("t-reset", limits)
        # Should be at limit now
        result = backend.check_and_increment("t-reset", limits)
        assert result["allowed"] is False
        # Reset and verify
        backend.reset_tenant("t-reset")
        result = backend.check_and_increment("t-reset", limits)
        assert result["allowed"] is True

    def test_cleanup_removes_old_windows(self):
        backend = InMemoryBackend()
        limits = {"per_minute": 100, "per_hour": 1000}
        backend.check_and_increment("t-clean", limits)
        # Inject an old window key
        backend._store["t-clean"]["m:0"] = 999
        backend._store["t-clean"]["h:0"] = 999
        backend.cleanup()
        # Old keys should be gone
        assert "m:0" not in backend._store.get("t-clean", {})
        assert "h:0" not in backend._store.get("t-clean", {})


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

class TestModuleFunctions:
    """Verify cleanup_expired_windows and reset_tenant module functions."""

    def test_cleanup_expired_windows_no_error(self):
        check_rate_limit("tenant-cf-1", "starter")
        cleanup_expired_windows()  # Should not raise

    def test_reset_tenant_no_error(self):
        check_rate_limit("tenant-rt-1", "starter")
        reset_tenant("tenant-rt-1")  # Should not raise

    def test_reset_allows_requests_again(self):
        tenant_id = "tenant-rt-2"
        for _ in range(60):
            check_rate_limit(tenant_id, "starter")
        blocked = check_rate_limit(tenant_id, "starter")
        assert blocked["allowed"] is False
        reset_tenant(tenant_id)
        after_reset = check_rate_limit(tenant_id, "starter")
        assert after_reset["allowed"] is True


# [TEMPLATE: CUI // SP-CTI]
