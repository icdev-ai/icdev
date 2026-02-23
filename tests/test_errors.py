# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools.resilience.errors — Structured exception hierarchy.

Validates ICDevError base class, transient/permanent subclasses,
ServiceUnavailableError, RateLimitedError, ConfigurationError,
and inheritance/retryable behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.resilience.errors import (
    ConfigurationError,
    ICDevError,
    ICDevPermanentError,
    ICDevTransientError,
    RateLimitedError,
    ServiceUnavailableError,
)


class TestICDevError:
    """Tests for the ICDevError base exception."""

    def test_has_message_attribute(self):
        """ICDevError must expose the message via str()."""
        err = ICDevError("something broke")
        assert str(err) == "something broke"

    def test_has_service_attribute(self):
        """ICDevError must store the service name."""
        err = ICDevError("fail", service="bedrock")
        assert err.service == "bedrock"

    def test_has_retryable_attribute(self):
        """ICDevError must expose a retryable flag."""
        err = ICDevError("fail")
        assert hasattr(err, "retryable")

    def test_default_retryable_is_false(self):
        """ICDevError default retryable must be False."""
        err = ICDevError("fail")
        assert err.retryable is False


class TestICDevTransientError:
    """Tests for ICDevTransientError."""

    def test_default_retryable_is_true(self):
        """ICDevTransientError default retryable must be True."""
        err = ICDevTransientError("timeout")
        assert err.retryable is True

    def test_inherits_from_icdev_error(self):
        """ICDevTransientError must be catchable as ICDevError."""
        err = ICDevTransientError("timeout")
        assert isinstance(err, ICDevError)


class TestICDevPermanentError:
    """Tests for ICDevPermanentError."""

    def test_default_retryable_is_false(self):
        """ICDevPermanentError default retryable must be False."""
        err = ICDevPermanentError("invalid config")
        assert err.retryable is False

    def test_inherits_from_icdev_error(self):
        """ICDevPermanentError must be catchable as ICDevError."""
        err = ICDevPermanentError("invalid config")
        assert isinstance(err, ICDevError)


class TestServiceUnavailableError:
    """Tests for ServiceUnavailableError (circuit breaker open)."""

    def test_inherits_from_transient(self):
        """ServiceUnavailableError must inherit from ICDevTransientError."""
        err = ServiceUnavailableError("down", service="redis")
        assert isinstance(err, ICDevTransientError)

    def test_is_retryable(self):
        """ServiceUnavailableError must be retryable."""
        err = ServiceUnavailableError("down", service="redis")
        assert err.retryable is True


class TestRateLimitedError:
    """Tests for RateLimitedError."""

    def test_has_retry_after_attribute(self):
        """RateLimitedError must expose a retry_after value."""
        err = RateLimitedError("slow down", service="api", retry_after=30)
        assert err.retry_after == 30

    def test_default_retry_after_is_zero(self):
        """RateLimitedError default retry_after must be 0."""
        err = RateLimitedError("slow down")
        assert err.retry_after == 0

    def test_is_retryable(self):
        """RateLimitedError must be retryable."""
        err = RateLimitedError("throttled")
        assert err.retryable is True


class TestConfigurationError:
    """Tests for ConfigurationError."""

    def test_has_config_key_attribute(self):
        """ConfigurationError must expose a config_key value."""
        err = ConfigurationError("missing key", config_key="bedrock.model_id")
        assert err.config_key == "bedrock.model_id"

    def test_is_not_retryable(self):
        """ConfigurationError must not be retryable."""
        err = ConfigurationError("bad config")
        assert err.retryable is False

    def test_inherits_from_permanent(self):
        """ConfigurationError must inherit from ICDevPermanentError."""
        err = ConfigurationError("bad config")
        assert isinstance(err, ICDevPermanentError)


class TestCatchAllAsICDevError:
    """Tests that all error types can be caught as ICDevError."""

    @pytest.mark.parametrize("error_class,kwargs", [
        (ICDevTransientError, {"message": "transient"}),
        (ICDevPermanentError, {"message": "permanent"}),
        (ServiceUnavailableError, {"message": "unavailable", "service": "x"}),
        (RateLimitedError, {"message": "limited"}),
        (ConfigurationError, {"message": "config"}),
    ])
    def test_all_catchable_as_icdev_error(self, error_class, kwargs):
        """All ICDEV errors must be catchable via except ICDevError."""
        err = error_class(**kwargs)
        with pytest.raises(ICDevError):
            raise err

    def test_error_str_returns_message(self):
        """str() on any ICDevError must return the message."""
        msg = "Something went terribly wrong"
        err = ICDevError(msg)
        assert str(err) == msg
