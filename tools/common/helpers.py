# CUI // SP-CTI
"""Common helper functions shared across canvas blueprints."""

from datetime import datetime, timezone


def now_isoformat() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# Alias used by rename branch code
now_iso = now_isoformat


def row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    return {}
