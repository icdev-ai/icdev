#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Resilience — Reusable Retry Utility.

D147: Extracted from tools/agent/bedrock_client.py. Provides a decorator and
a callable wrapper with exponential backoff + full jitter.

Usage:
    from tools.resilience.retry import retry, backoff_delay

    @retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError))
    def call_service():
        ...

    # Or use imperatively:
    result = retry(max_retries=3)(my_function)(args)
"""

import functools
import logging
import random
import time
from typing import Callable, Optional, Sequence, Type

logger = logging.getLogger("icdev.resilience.retry")


def backoff_delay(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> float:
    """Exponential backoff with full jitter.

    Algorithm: min(cap, base * 2^attempt) * random(0.5, 1.0)
    Prevents thundering herd when multiple callers retry simultaneously.
    """
    delay = min(max_delay, base_delay * (2 ** attempt))
    return delay * random.uniform(0.5, 1.0)


def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: Sequence[Type[Exception]] = (Exception,),
    on_retry: Optional[Callable] = None,
):
    """Decorator that retries a function on transient failures.

    Args:
        max_retries: Maximum number of retry attempts (total calls = max_retries + 1).
        base_delay: Base delay in seconds for exponential backoff.
        max_delay: Maximum delay cap in seconds.
        retryable_exceptions: Tuple of exception types that trigger a retry.
        on_retry: Optional callback(attempt, exc, delay) called before each retry.

    Returns:
        Decorated function with retry logic.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            retryable = tuple(retryable_exceptions)

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        delay = backoff_delay(attempt, base_delay, max_delay)
                        logger.warning(
                            "Retry %d/%d for %s (%s: %s) — waiting %.1fs",
                            attempt + 1,
                            max_retries,
                            func.__name__,
                            type(exc).__name__,
                            exc,
                            delay,
                        )
                        if on_retry:
                            on_retry(attempt, exc, delay)
                        time.sleep(delay)
                    else:
                        raise

            # Should not reach here
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator
