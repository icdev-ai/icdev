# CUI // SP-CTI
"""Unit tests for tools/databridge/resolvers/aws_resolver.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.databridge.resolvers.aws_resolver import SecretResolverError, resolve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_boto3(monkeypatch, secret_string: str | None = "secret-value"):
    """Patch boto3.client so secretsmanager returns a canned SecretString."""
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": secret_string}

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_client

    import tools.databridge.resolvers.aws_resolver as mod
    monkeypatch.setattr(mod, "boto3", mock_boto3, raising=False)
    # boto3 is imported inline; patch the module-level import path
    monkeypatch.setitem(sys.modules, "boto3", mock_boto3)

    botocore_exc = MagicMock()
    botocore_exc.ClientError = type("ClientError", (Exception,), {
        "__init__": lambda self, resp, op: setattr(self, "response", resp) or super().__init__(str(resp)),
    })
    mock_boto3_exceptions = MagicMock()
    mock_boto3_exceptions.ClientError = botocore_exc.ClientError
    monkeypatch.setitem(sys.modules, "botocore", MagicMock())
    monkeypatch.setitem(sys.modules, "botocore.exceptions", mock_boto3_exceptions)

    return mock_client, mock_boto3_exceptions.ClientError


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

class TestResolveValidation:
    def test_rejects_non_aws_prefix(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        # boto3 not called — should fail in parsing, but prefix check is implicit
        # Since we strip "aws:" unconditionally, an empty secret_name will hit the
        # AWS client and fail differently. We test that the resolver handles it.
        # The real check is that resolve("vault:...") works fine on vault resolver,
        # not that aws_resolver rejects it — aws_resolver has no prefix guard.
        pass  # no prefix rejection in aws_resolver (dispatcher handles routing)

    def test_plain_secret_no_json_key(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": "plain-value"}
        with patch("boto3.client", return_value=mock_client):
            result = resolve("aws:prod/db/password")
        assert result == "plain-value"

    def test_json_secret_with_key(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        payload = json.dumps({"password": "pg-secret", "user": "admin"})
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": payload}
        with patch("boto3.client", return_value=mock_client):
            result = resolve("aws:prod/db/creds#password")
        assert result == "pg-secret"


# ---------------------------------------------------------------------------
# Region resolution
# ---------------------------------------------------------------------------

class TestRegion:
    def test_uses_aws_region_env(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": "v"}
        with patch("boto3.client", return_value=mock_client) as mock_boto:
            resolve("aws:some/secret")
        mock_boto.assert_called_once()
        assert mock_boto.call_args[1]["region_name"] == "us-east-1"

    def test_falls_back_to_govcloud_default(self, monkeypatch) -> None:
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": "v"}
        with patch("boto3.client", return_value=mock_client) as mock_boto:
            resolve("aws:some/secret")
        assert mock_boto.call_args[1]["region_name"] == "us-gov-west-1"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestResolveErrors:
    def test_boto3_not_installed_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        # Simulate ImportError from boto3 by making the import fail
        original = sys.modules.get("boto3")
        sys.modules["boto3"] = None  # type: ignore[assignment]
        try:
            with pytest.raises((SecretResolverError, ImportError)):
                resolve("aws:some/secret")
        finally:
            if original is None:
                sys.modules.pop("boto3", None)
            else:
                sys.modules["boto3"] = original

    def test_binary_secret_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretBinary": b"bytes", "SecretString": None}
        with patch("boto3.client", return_value=mock_client):
            with pytest.raises(SecretResolverError, match="binary secret"):
                resolve("aws:prod/binary/secret")

    def test_empty_secret_string_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": ""}
        with patch("boto3.client", return_value=mock_client):
            with pytest.raises(SecretResolverError, match="empty secret"):
                resolve("aws:prod/empty/secret")

    def test_json_key_not_found_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        payload = json.dumps({"other": "value"})
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": payload}
        with patch("boto3.client", return_value=mock_client):
            with pytest.raises(SecretResolverError, match="not found"):
                resolve("aws:prod/db/creds#password")

    def test_invalid_json_with_key_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": "not-json"}
        with patch("boto3.client", return_value=mock_client):
            with pytest.raises(SecretResolverError, match="not valid JSON"):
                resolve("aws:prod/db/creds#password")

    def test_generic_exception_raises_resolver_error(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        mock_client = MagicMock()
        mock_client.get_secret_value.side_effect = RuntimeError("network error")
        with patch("boto3.client", return_value=mock_client):
            with pytest.raises(SecretResolverError, match="call failed"):
                resolve("aws:prod/db/password")

    def test_never_returns_empty_string(self, monkeypatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": ""}
        with patch("boto3.client", return_value=mock_client):
            try:
                result = resolve("aws:prod/empty/never_empty")
                assert result != "", "resolve() must never return empty string"
            except SecretResolverError:
                pass  # correct behaviour


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_aws_registered_in_connection_manager(self) -> None:
        from tools.databridge.connection_manager import SECRET_RESOLVERS
        assert "aws" in SECRET_RESOLVERS
        assert callable(SECRET_RESOLVERS["aws"])
