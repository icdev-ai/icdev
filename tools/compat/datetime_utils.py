"""Timezone-aware datetime utilities for ICDEV.

Replaces deprecated datetime.utcnow() with timezone-aware alternatives.
ADR D186: Compatible with all installation profiles (CUI and non-CUI).
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
