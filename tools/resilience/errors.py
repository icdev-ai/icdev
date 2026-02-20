#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Resilience — Structured Exception Hierarchy.

D148: Structured error hierarchy for new code. Existing bare exceptions
are left untouched to avoid mass-refactor risk. New code SHOULD use these
exceptions for categorized error handling.

Usage:
    from tools.resilience.errors import ICDevTransientError, ServiceUnavailableError

    raise ICDevTransientError("Bedrock throttled", service="bedrock", retryable=True)
"""


class ICDevError(Exception):
    """Base exception for all ICDEV errors.

    Attributes:
        service: Name of the service that caused the error (e.g. "bedrock").
        retryable: Whether the caller should retry the operation.
    """

    def __init__(self, message: str, service: str = "", retryable: bool = False):
        super().__init__(message)
        self.service = service
        self.retryable = retryable


class ICDevTransientError(ICDevError):
    """Transient error — the operation may succeed on retry.

    Examples: network timeout, rate limiting, service temporarily unavailable.
    """

    def __init__(self, message: str, service: str = "", retryable: bool = True):
        super().__init__(message, service=service, retryable=retryable)


class ICDevPermanentError(ICDevError):
    """Permanent error — retrying will not help.

    Examples: invalid credentials, validation failure, missing configuration.
    """

    def __init__(self, message: str, service: str = "", retryable: bool = False):
        super().__init__(message, service=service, retryable=retryable)


class ServiceUnavailableError(ICDevTransientError):
    """Service unavailable — circuit breaker is OPEN.

    Raised when a circuit breaker prevents calling a degraded service.
    """

    def __init__(self, message: str, service: str = ""):
        super().__init__(
            message or f"Service '{service}' is unavailable (circuit breaker open)",
            service=service,
            retryable=True,
        )


class RateLimitedError(ICDevTransientError):
    """Rate limited — caller should back off and retry later.

    Attributes:
        retry_after: Seconds to wait before retrying (if known).
    """

    def __init__(self, message: str, service: str = "", retry_after: int = 0):
        super().__init__(message, service=service, retryable=True)
        self.retry_after = retry_after


class ConfigurationError(ICDevPermanentError):
    """Configuration error — missing or invalid configuration."""

    def __init__(self, message: str, config_key: str = ""):
        super().__init__(message, service="config", retryable=False)
        self.config_key = config_key
