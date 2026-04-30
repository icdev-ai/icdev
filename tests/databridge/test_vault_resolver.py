# CUI // SP-CTI
"""Unit tests for tools/databridge/resolvers/vault_resolver.py."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.databridge.resolvers.vault_resolver import (
    SecretResolverError,
    _cache_get,
    _cache_set,
    _clear_caches,
    resolve,
)


@pytest.fixture(autouse=True)
def _reset_resolver_caches():
    """Clear positive and negative caches before every test for isolation."""
    _clear_caches()
    yield
    _clear_caches()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault_response(data: dict) -> dict:
    """Build a minimal hvac-style KV v1 response."""
    return {"data": data}


def _make_vault_response_v2(data: dict) -> dict:
    """Build a minimal hvac-style KV v2 response (nested data.data)."""
    return {"data": {"data": data}}


# ---------------------------------------------------------------------------
# resolve() — argument validation
# ---------------------------------------------------------------------------

class TestResolveValidation:
    def test_rejects_non_vault_prefix(self) -> None:
        with pytest.raises(SecretResolverError, match="Not a vault ref"):
            resolve("aws:some/path#field")

    def test_rejects_missing_field_separator(self) -> None:
        with pytest.raises(SecretResolverError, match="missing #field"):
            resolve("vault:secret/myapp")

    def test_rejects_empty_path(self) -> None:
        with pytest.raises(SecretResolverError, match="Empty path or field"):
            resolve("vault:#field")

    def test_rejects_empty_field(self) -> None:
        with pytest.raises(SecretResolverError, match="Empty path or field"):
            resolve("vault:secret/myapp#")


# ---------------------------------------------------------------------------
# resolve() — env checks
# ---------------------------------------------------------------------------

class TestResolveEnvChecks:
    def test_raises_when_vault_addr_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        with pytest.raises(SecretResolverError, match="VAULT_ADDR"):
            resolve("vault:secret/myapp#password")

    def test_raises_when_vault_token_missing(self, monkeypatch) -> None:
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        with pytest.raises(SecretResolverError, match="VAULT_TOKEN"):
            resolve("vault:secret/myapp#password")


# ---------------------------------------------------------------------------
# resolve() — hvac unavailable
# ---------------------------------------------------------------------------

class TestResolveHvacUnavailable:
    def test_raises_when_hvac_not_installed(self, monkeypatch) -> None:
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        monkeypatch.setenv("VAULT_TOKEN", "s.test")
        import tools.databridge.resolvers.vault_resolver as mod
        monkeypatch.setattr(mod, "_HVAC_AVAILABLE", False)
        with pytest.raises(SecretResolverError, match="hvac package"):
            resolve("vault:secret/myapp#password")


# ---------------------------------------------------------------------------
# resolve() — successful fetch (KV v1 and v2)
# ---------------------------------------------------------------------------

class TestResolveSuccess:
    def _setup(self, monkeypatch, response: dict) -> None:
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        monkeypatch.setenv("VAULT_TOKEN", "s.test")
        import tools.databridge.resolvers.vault_resolver as mod
        monkeypatch.setattr(mod, "_HVAC_AVAILABLE", True)
        mock_client = MagicMock()
        mock_client.read.return_value = response
        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_client
        monkeypatch.setattr(mod, "_hvac", mock_hvac)
        return mock_client

    def test_kv_v1_returns_plaintext(self, monkeypatch) -> None:
        self._setup(monkeypatch, _make_vault_response({"password": "s3cr3t"}))
        result = resolve("vault:secret/myapp#password")
        assert result == "s3cr3t"

    def test_kv_v2_unwraps_nested_data(self, monkeypatch) -> None:
        self._setup(monkeypatch, _make_vault_response_v2({"api_key": "abc123"}))
        result = resolve("vault:secret/data/myapp#api_key")
        assert result == "abc123"

    def test_path_with_multiple_segments(self, monkeypatch) -> None:
        self._setup(monkeypatch, _make_vault_response({"db_pass": "hunter2"}))
        result = resolve("vault:prod/services/postgres#db_pass")
        assert result == "hunter2"

    def test_returns_string_for_non_string_value(self, monkeypatch) -> None:
        """Numeric Vault values should be coerced to str."""
        self._setup(monkeypatch, _make_vault_response({"port": 5432}))
        result = resolve("vault:infra/db#port")
        assert result == "5432"


# ---------------------------------------------------------------------------
# resolve() — error paths during fetch
# ---------------------------------------------------------------------------

class TestResolveFetchErrors:
    def _patch_hvac(self, monkeypatch, side_effect=None, return_value=None):
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        monkeypatch.setenv("VAULT_TOKEN", "s.test")
        import tools.databridge.resolvers.vault_resolver as mod
        monkeypatch.setattr(mod, "_HVAC_AVAILABLE", True)
        mock_client = MagicMock()
        if side_effect is not None:
            mock_client.read.side_effect = side_effect
        else:
            mock_client.read.return_value = return_value
        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_client
        monkeypatch.setattr(mod, "_hvac", mock_hvac)

    def test_connection_error_raises_resolver_error(self, monkeypatch) -> None:
        self._patch_hvac(monkeypatch, side_effect=ConnectionError("refused"))
        with pytest.raises(SecretResolverError, match="connection error"):
            resolve(f"vault:secret/conn_err_{time.monotonic_ns()}#pw")

    def test_path_not_found_raises_resolver_error(self, monkeypatch) -> None:
        self._patch_hvac(monkeypatch, return_value=None)
        with pytest.raises(SecretResolverError, match="path not found"):
            resolve(f"vault:secret/missing_{time.monotonic_ns()}#field")

    def test_field_missing_raises_resolver_error(self, monkeypatch) -> None:
        self._patch_hvac(monkeypatch, return_value=_make_vault_response({"other": "val"}))
        with pytest.raises(SecretResolverError, match="not present"):
            resolve(f"vault:secret/field_miss_{time.monotonic_ns()}#password")

    def test_empty_value_raises_resolver_error(self, monkeypatch) -> None:
        self._patch_hvac(monkeypatch, return_value=_make_vault_response({"password": ""}))
        with pytest.raises(SecretResolverError, match="empty value"):
            resolve(f"vault:secret/empty_val_{time.monotonic_ns()}#password")

    def test_never_returns_empty_string(self, monkeypatch) -> None:
        """Contract: resolve() must never return an empty string."""
        self._patch_hvac(monkeypatch, return_value=_make_vault_response({"password": ""}))
        try:
            result = resolve(f"vault:secret/never_empty_{time.monotonic_ns()}#password")
            assert result != "", "resolve() must never return empty string"
        except SecretResolverError:
            pass  # correct behaviour


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

class TestCacheBehaviour:
    def test_cache_miss_then_hit(self) -> None:
        key = f"__test_cache_{time.monotonic()}__"
        hit, val = _cache_get(key)
        assert hit is False
        assert val is None
        _cache_set(key, "cached_value")
        hit, val = _cache_get(key)
        assert hit is True
        assert val == "cached_value"

    def test_second_call_uses_cache(self, monkeypatch) -> None:
        """Vault should only be called once for the same ref within TTL."""
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        monkeypatch.setenv("VAULT_TOKEN", "s.test")
        import tools.databridge.resolvers.vault_resolver as mod
        monkeypatch.setattr(mod, "_HVAC_AVAILABLE", True)
        mock_client = MagicMock()
        mock_client.read.return_value = _make_vault_response({"pw": "secret"})
        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_client
        monkeypatch.setattr(mod, "_hvac", mock_hvac)

        unique_ref = f"vault:secret/cache_test_{time.monotonic_ns()}#pw"
        resolve(unique_ref)
        resolve(unique_ref)
        assert mock_client.read.call_count == 1  # fetched once, cached second time


# ---------------------------------------------------------------------------
# Negative cache — regression tests for "over 100 attempts" bug
# ---------------------------------------------------------------------------

class TestNegativeCacheBehaviour:
    """Verify that failed Vault fetches are back-off cached for 30 s.

    Without negative caching a single unreachable/misconfigured Vault ref
    creates a new hvac.Client on every resolve() call, which allowed 100+
    connection attempts in a single health-check cycle.
    """

    def _patch_failing_hvac(self, monkeypatch, side_effect=ConnectionError("refused")):
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        monkeypatch.setenv("VAULT_TOKEN", "s.test")
        import tools.databridge.resolvers.vault_resolver as mod
        monkeypatch.setattr(mod, "_HVAC_AVAILABLE", True)
        mock_client = MagicMock()
        mock_client.read.side_effect = side_effect
        mock_hvac = MagicMock()
        mock_hvac.Client.return_value = mock_client
        monkeypatch.setattr(mod, "_hvac", mock_hvac)
        return mock_hvac, mock_client

    def test_second_call_does_not_hit_vault(self, monkeypatch) -> None:
        """Regression: repeated calls on a failing ref must NOT create a new client each time."""
        mock_hvac, mock_client = self._patch_failing_hvac(monkeypatch)
        ref = f"vault:secret/broken_{time.monotonic_ns()}#pw"

        with pytest.raises(SecretResolverError):
            resolve(ref)
        with pytest.raises(SecretResolverError):
            resolve(ref)

        # hvac.Client should have been instantiated exactly once — second call
        # was absorbed by the negative cache without touching Vault.
        assert mock_hvac.Client.call_count == 1

    def test_negative_cache_raises_with_backoff_message(self, monkeypatch) -> None:
        """Second failure within 30 s must mention back-off in the error."""
        self._patch_failing_hvac(monkeypatch)
        ref = f"vault:secret/backoff_{time.monotonic_ns()}#pw"

        with pytest.raises(SecretResolverError):
            resolve(ref)
        with pytest.raises(SecretResolverError, match="back-off"):
            resolve(ref)

    def test_clear_caches_resets_negative_cache(self, monkeypatch) -> None:
        """After _clear_caches() the resolver retries Vault instead of short-circuiting."""
        mock_hvac, mock_client = self._patch_failing_hvac(monkeypatch)
        ref = f"vault:secret/clear_{time.monotonic_ns()}#pw"

        with pytest.raises(SecretResolverError):
            resolve(ref)
        assert mock_hvac.Client.call_count == 1

        from tools.databridge.resolvers.vault_resolver import _clear_caches
        _clear_caches()

        with pytest.raises(SecretResolverError):
            resolve(ref)
        # After clearing the cache, Vault was hit a second time.
        assert mock_hvac.Client.call_count == 2

    def test_validation_errors_are_not_cached(self, monkeypatch) -> None:
        """Format errors (bad prefix / missing #) must not populate the negative cache.

        These are raised before any Vault I/O, so negative-caching them would
        incorrectly suppress later calls with a *valid* ref that shares no key.
        (Each ref is independent, but this guards against accidental key reuse.)
        """
        mock_hvac, mock_client = self._patch_failing_hvac(monkeypatch)

        # Bad format — should raise but NOT call Vault at all
        with pytest.raises(SecretResolverError, match="missing #field"):
            resolve("vault:secret/no-field-sep")
        assert mock_hvac.Client.call_count == 0

        # A subsequent well-formed ref for the same path must still reach Vault
        mock_client.read.side_effect = None
        mock_client.read.return_value = {"data": {"pw": "ok"}}
        result = resolve("vault:secret/no-field-sep#pw")
        assert result == "ok"
        assert mock_hvac.Client.call_count == 1

    def test_multiple_callers_on_failing_ref_hit_vault_once(self, monkeypatch) -> None:
        """Simulates concurrent health-check workers: only the first call reaches Vault."""
        mock_hvac, mock_client = self._patch_failing_hvac(monkeypatch)
        ref = f"vault:secret/concurrent_{time.monotonic_ns()}#pw"

        errors = []
        for _ in range(10):
            try:
                resolve(ref)
            except SecretResolverError as exc:
                errors.append(str(exc))

        assert len(errors) == 10, "all 10 calls must raise"
        assert mock_hvac.Client.call_count == 1, (
            "Vault was contacted more than once — negative cache is not working"
        )
        # Exactly one real error; the rest are back-off messages
        backoff = sum(1 for e in errors if "back-off" in e)
        assert backoff == 9


# ---------------------------------------------------------------------------
# SECRET_RESOLVERS registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_vault_registered_in_connection_manager(self) -> None:
        from tools.databridge.connection_manager import SECRET_RESOLVERS
        assert "vault" in SECRET_RESOLVERS
        assert callable(SECRET_RESOLVERS["vault"])
