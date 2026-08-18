# CUI // SP-CTI
"""Common helper functions shared across canvas blueprints."""

import json
from datetime import datetime, timezone


def now_isoformat() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# Alias used by rename branch code
now_iso = now_isoformat


def parse_utc_timestamp(raw) -> "datetime | None":
    """A UTC-aware datetime from whatever a driver or a log handed back, or None.

    STDLIB ONLY. This exists because `python-dateutil` was imported by two
    runtime modules and declared in neither requirements.txt nor pyproject.toml,
    at both sites inside a bare `except Exception` that returned a
    benign-looking value. On any install without it — the CI runner, and any
    air-gapped deployment, which is what this project targets — the stale reaper
    skipped every task and every notification duration rendered "unknown", with
    nothing anywhere to say why.

    Handles what this codebase actually writes: a driver-native datetime
    (PostgreSQL), and `datetime.now(timezone.utc).isoformat()` strings (SQLite,
    logs, JSON payloads). A trailing `Z` is normalised because
    `datetime.fromisoformat` did not accept it before 3.11 and stored rows
    outlive interpreters. Naive values are read as UTC, which is what every
    writer here means.

    Returns None rather than raising: a stamp that cannot be read is one row's
    problem, and the caller is the only thing that knows whether that is fatal.
    """
    if raw is None:
        return None
    if hasattr(raw, "tzinfo"):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    return {}


def _decode_json_value(value):
    """Decode a JSON-encoded TEXT column value, or return it untouched.

    Only object/array payloads are decoded. A scalar (``"12"``, ``"null"``) or
    anything that is not valid JSON is returned exactly as it came out of the
    row, so this is always safe to apply to a column of unknown content.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return value
    return parsed if isinstance(parsed, (dict, list)) else value


def row_to_dict_json(row, json_fields=None) -> dict:
    """Convert a row to a dict, decoding its JSON-encoded TEXT columns.

    The schema stores structured values in plain ``TEXT`` columns
    (``agents.capabilities``, ``interconnection_agreements.ports_protocols``,
    ...), so a bare :func:`row_to_dict` hands callers a JSON *string* where
    they expect a dict or list.

    Args:
        row: A ``sqlite3.Row``, psycopg row, or any mapping. ``None`` -> ``{}``.
        json_fields: Column names to decode. When ``None`` (the default) every
            string value that parses as a JSON object or array is decoded --
            callers that do not know their column list still get structured
            values back.

    Returns:
        A plain dict. Values that are not JSON objects/arrays are unchanged.
    """
    data = row_to_dict(row)
    if json_fields is None:
        return {key: _decode_json_value(val) for key, val in data.items()}
    for name in json_fields:
        if name in data:
            data[name] = _decode_json_value(data[name])
    return data
