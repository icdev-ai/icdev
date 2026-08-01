#!/usr/bin/env python3
# CUI // SP-CTI
"""SAM.gov API Quota Tracker — persistent daily call counter.

Tracks API calls against SAM.gov's daily limit (default 10,000/day).
Persists to sam_gov_api_quota table in icdev.db so state survives
script restarts and daemon crashes.

Architecture:
    - One row per calendar day (UTC) — upserted on each increment
    - check_quota() called BEFORE every API request
    - increment_quota() called AFTER successful API response
    - parse_429() extracts reset time from SAM.gov 429 response body
    - Buffer configurable (default 50) — stops before hard limit
    - Append-only quota_events for audit trail (NIST AU)

Usage:
    python tools/govcon/quota_tracker.py --status --json
    python tools/govcon/quota_tracker.py --reset --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.quota_tracker")

# ── Constants ────────────────────────────────────────────────────────

DEFAULT_DAILY_LIMIT = 10000
DEFAULT_BUFFER = 50  # stop this many calls before the hard limit


# ── Table init ───────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sam_gov_api_quota (
    date TEXT PRIMARY KEY,
    requests_made INTEGER NOT NULL DEFAULT 0,
    daily_limit INTEGER NOT NULL DEFAULT 10000,
    buffer_remaining INTEGER NOT NULL DEFAULT 50,
    last_429_at TEXT,
    last_429_reset TEXT,
    last_429_body TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sam_gov_quota_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    date TEXT NOT NULL,
    requests_made INTEGER,
    daily_limit INTEGER,
    details TEXT,
    created_at TEXT NOT NULL
);
"""


def _ensure_tables():
    """Create quota tables if they don't exist."""
    with get_connection() as conn:
        for stmt in _SCHEMA_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_config() -> dict:
    """Load rate_limit config from govcon_config.yaml."""
    if not _HAS_YAML:
        return {}
    config_path = PROJECT_ROOT / "args" / "govcon_config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("sam_gov", {}).get("rate_limit", {})


def _log_event(event_type: str, requests_made: int, daily_limit: int, details: str | None = None):
    """Append to quota events audit trail."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO sam_gov_quota_events "
                "(id, event_type, date, requests_made, daily_limit, details, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (f"qe-{uuid4().hex[:12]}", event_type, _today(), requests_made, daily_limit, details, _now()),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Non-critical — don't block API calls for audit failures
        logger.warning("_log_event: best-effort INSERT into sam_gov_quota_events failed (non-blocking): %s", exc)


# ── Core API ─────────────────────────────────────────────────────────


def check_quota(buffer: int | None = None) -> dict:
    """Check if we have remaining quota for today.

    Args:
        buffer: Override buffer count (default from config or 50).

    Returns:
        Dict with: allowed (bool), requests_made, daily_limit,
        remaining, reset_at (if quota exhausted from 429).
    """
    _ensure_tables()
    config = _load_config()
    daily_limit = config.get("daily_limit", DEFAULT_DAILY_LIMIT)
    if buffer is None:
        buffer = config.get("quota_buffer", DEFAULT_BUFFER)

    today = _today()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT requests_made, daily_limit, last_429_reset FROM sam_gov_api_quota WHERE date = %s",
            (today,),
        ).fetchone()

    if not row:
        return {
            "allowed": True,
            "requests_made": 0,
            "daily_limit": daily_limit,
            "remaining": daily_limit - buffer,
            "buffer": buffer,
        }

    requests_made = row["requests_made"]
    effective_limit = daily_limit - buffer
    remaining = max(0, effective_limit - requests_made)

    result = {
        "allowed": requests_made < effective_limit,
        "requests_made": requests_made,
        "daily_limit": daily_limit,
        "remaining": remaining,
        "buffer": buffer,
    }

    if row["last_429_reset"]:
        result["reset_at"] = row["last_429_reset"]

    return result


def increment_quota(count: int = 1) -> dict:
    """Record API call(s) made today.

    Args:
        count: Number of calls to record (default 1).

    Returns:
        Dict with updated requests_made and remaining.
    """
    _ensure_tables()
    config = _load_config()
    daily_limit = config.get("daily_limit", DEFAULT_DAILY_LIMIT)
    buffer = config.get("quota_buffer", DEFAULT_BUFFER)
    today = _today()
    now = _now()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT requests_made FROM sam_gov_api_quota WHERE date = %s",
            (today,),
        ).fetchone()

        if row:
            new_count = row["requests_made"] + count
            conn.execute(
                "UPDATE sam_gov_api_quota SET requests_made = %s, updated_at = %s WHERE date = %s",
                (new_count, now, today),
            )
        else:
            new_count = count
            conn.execute(
                "INSERT INTO sam_gov_api_quota "
                "(date, requests_made, daily_limit, buffer_remaining, updated_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (today, new_count, daily_limit, buffer, now),
            )
        conn.commit()

    effective_limit = daily_limit - buffer
    remaining = max(0, effective_limit - new_count)

    # Log warning event if approaching limit
    if remaining <= 100 and remaining > 0:
        _log_event("quota_warning", new_count, daily_limit, f"Only {remaining} requests remaining today")
    elif remaining <= 0:
        _log_event("quota_exhausted", new_count, daily_limit, "Daily quota exhausted (with buffer)")

    return {
        "requests_made": new_count,
        "daily_limit": daily_limit,
        "remaining": remaining,
    }


def record_429(response_body: str | None = None, reset_time: str | None = None) -> dict:
    """Record a 429 rate-limit response from SAM.gov.

    Parses the response body for the reset time if not provided.

    Args:
        response_body: Raw 429 response body (JSON string).
        reset_time: Explicit reset time override.

    Returns:
        Dict with parsed reset_at.
    """
    _ensure_tables()
    today = _today()
    now = _now()

    # Parse reset time from SAM.gov response body
    # Format: {"code":"900804","message":"Message throttled out",
    #          "description":"...","nextAccessTime":"2026-Mar-25 00:00:00+0000 UTC"}
    parsed_reset = reset_time
    if not parsed_reset and response_body:
        try:
            body = json.loads(response_body) if isinstance(response_body, str) else response_body
            next_access = body.get("nextAccessTime", "")
            if next_access:
                parsed_reset = next_access
        except (json.JSONDecodeError, AttributeError):
            pass

    config = _load_config()
    daily_limit = config.get("daily_limit", DEFAULT_DAILY_LIMIT)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT requests_made FROM sam_gov_api_quota WHERE date = %s",
            (today,),
        ).fetchone()

        requests_made = row["requests_made"] if row else 0

        if row:
            conn.execute(
                "UPDATE sam_gov_api_quota SET last_429_at = %s, last_429_reset = %s, "
                "last_429_body = %s, updated_at = %s WHERE date = %s",
                (now, parsed_reset, response_body, now, today),
            )
        else:
            conn.execute(
                "INSERT INTO sam_gov_api_quota "
                "(date, requests_made, daily_limit, buffer_remaining, "
                "last_429_at, last_429_reset, last_429_body, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (today, 0, daily_limit, DEFAULT_BUFFER, now, parsed_reset, response_body, now),
            )
        conn.commit()

    _log_event("rate_limited_429", requests_made, daily_limit, f"SAM.gov returned 429. Reset: {parsed_reset}")

    return {
        "status": "recorded",
        "reset_at": parsed_reset,
        "requests_today": requests_made,
    }


def get_status() -> dict:
    """Get full quota status including history."""
    _ensure_tables()
    config = _load_config()
    daily_limit = config.get("daily_limit", DEFAULT_DAILY_LIMIT)
    buffer = config.get("quota_buffer", DEFAULT_BUFFER)
    today = _today()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM sam_gov_api_quota WHERE date = %s",
            (today,),
        ).fetchone()

        # Last 7 days of usage
        history = conn.execute(
            "SELECT date, requests_made, daily_limit, last_429_at FROM sam_gov_api_quota ORDER BY date DESC LIMIT 7",
        ).fetchall()

        # Recent events
        events = conn.execute(
            "SELECT event_type, date, requests_made, details, created_at "
            "FROM sam_gov_quota_events ORDER BY created_at DESC LIMIT 10",
        ).fetchall()

    if row:
        requests_made = row["requests_made"]
        effective_limit = daily_limit - buffer
        remaining = max(0, effective_limit - requests_made)
        result = {
            "status": "ok",
            "today": {
                "date": today,
                "requests_made": requests_made,
                "daily_limit": daily_limit,
                "buffer": buffer,
                "remaining": remaining,
                "allowed": requests_made < effective_limit,
                "last_429_at": row["last_429_at"],
                "last_429_reset": row["last_429_reset"],
            },
        }
    else:
        result = {
            "status": "ok",
            "today": {
                "date": today,
                "requests_made": 0,
                "daily_limit": daily_limit,
                "buffer": buffer,
                "remaining": daily_limit - buffer,
                "allowed": True,
            },
        }

    result["history"] = [dict(r) for r in history]
    result["recent_events"] = [dict(r) for r in events]
    result["classification"] = "CUI"
    return result


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="SAM.gov API Quota Tracker (CUI // SP-CTI)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show quota status")
    group.add_argument("--check", action="store_true", help="Check if quota allows a request")
    group.add_argument("--reset", action="store_true", help="Reset today's counter (for testing)")

    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.status:
        result = get_status()
    elif args.check:
        result = check_quota()
    elif args.reset:
        _ensure_tables()
        today = _today()
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM sam_gov_api_quota WHERE date = %s",
                (today,),
            )
            conn.commit()
        _log_event("manual_reset", 0, DEFAULT_DAILY_LIMIT, "Counter reset manually")
        result = {"status": "ok", "message": f"Quota reset for {today}"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
