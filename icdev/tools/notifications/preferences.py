# CUI // SP-CTI
"""Per-user notification preferences (crx-not-01).

Stores each user's preferred channels, quiet hours, and digest opt-in so that
send-time routing can be narrowed to what the recipient actually wants. State
lives in ``notification_preferences`` (mutable; users update their own row),
which carries ``tenant_id`` + ``classification`` for row-level security.

Public surface (small + stable):

    get_preferences(user_id, tenant_id=None) -> dict
    set_preferences(user_id, tenant_id=None, classification="CUI", **fields) -> dict
    in_quiet_hours(prefs, now=None) -> bool
    resolve_user_channels(user_id, candidate_channels, tenant_id=None,
                          severity=None, now=None) -> list[str]
    wants_digest(user_id, tenant_id=None) -> bool

Quiet hours are wall-clock LOCAL hours ``[start, end)`` interpreted in the
user's ``timezone`` (IANA name; falls back to UTC when unavailable). Critical
alerts bypass quiet-hours suppression by default (config-controlled).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.db.storage import get_connection
from .routing_rules import load_rules

_TABLE = "notification_preferences"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    user_id TEXT NOT NULL,
    tenant_id TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    channels TEXT DEFAULT '[]',
    quiet_hours_start INTEGER,
    quiet_hours_end INTEGER,
    timezone TEXT DEFAULT 'UTC',
    digest_opt_in INTEGER DEFAULT 0,
    digest_frequency TEXT DEFAULT 'daily',
    updated_at TEXT,
    PRIMARY KEY (user_id, tenant_id)
)
"""

_PREF_FIELDS = (
    "channels", "quiet_hours_start", "quiet_hours_end",
    "timezone", "digest_opt_in", "digest_frequency",
)


def _defaults() -> dict:
    cfg = load_rules().get("preferences") or {}
    return {
        "channels": list(cfg.get("default_channels") or []),
        "quiet_hours_start": cfg.get("quiet_hours_start"),
        "quiet_hours_end": cfg.get("quiet_hours_end"),
        "timezone": cfg.get("timezone", "UTC"),
        "digest_opt_in": bool(cfg.get("digest_opt_in", False)),
        "digest_frequency": cfg.get("digest_frequency", "daily"),
        "critical_bypasses_quiet_hours": bool(cfg.get("critical_bypasses_quiet_hours", True)),
    }


def _ensure_schema(conn) -> None:
    try:
        conn.execute(_DDL)
        conn.commit()
    except Exception:
        pass


def _row_to_prefs(row: dict) -> dict:
    try:
        channels = json.loads(row.get("channels") or "[]")
    except Exception:
        channels = []
    return {
        "user_id": row.get("user_id"),
        "tenant_id": row.get("tenant_id") or "",
        "classification": row.get("classification") or "CUI",
        "channels": channels,
        "quiet_hours_start": row.get("quiet_hours_start"),
        "quiet_hours_end": row.get("quiet_hours_end"),
        "timezone": row.get("timezone") or "UTC",
        "digest_opt_in": bool(row.get("digest_opt_in")),
        "digest_frequency": row.get("digest_frequency") or "daily",
    }


def get_preferences(user_id: str, tenant_id: str | None = None) -> dict:
    """Return a user's stored preferences, or config-derived defaults.

    Always returns a fully-populated dict (``exists`` marks whether a stored row
    was found), so callers never have to special-case first-time users.
    """
    defaults = _defaults()
    base = {
        "user_id": user_id,
        "tenant_id": tenant_id or "",
        "classification": "CUI",
        "channels": defaults["channels"],
        "quiet_hours_start": defaults["quiet_hours_start"],
        "quiet_hours_end": defaults["quiet_hours_end"],
        "timezone": defaults["timezone"],
        "digest_opt_in": defaults["digest_opt_in"],
        "digest_frequency": defaults["digest_frequency"],
        "exists": False,
    }
    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE user_id = %s AND tenant_id = %s",
            (user_id, tenant_id or ""),
        ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()
    if row:
        merged = _row_to_prefs(dict(row))
        merged["exists"] = True
        return merged
    return base


def set_preferences(
    user_id: str,
    tenant_id: str | None = None,
    classification: str = "CUI",
    **fields,
) -> dict:
    """Upsert a user's preferences. Unknown kwargs are ignored.

    Accepts any subset of: ``channels`` (list), ``quiet_hours_start`` /
    ``quiet_hours_end`` (int hour 0-23 or ``None``), ``timezone`` (str),
    ``digest_opt_in`` (bool), ``digest_frequency`` (str). Fields not supplied
    keep their current stored value (or the default for a new row).
    """
    current = get_preferences(user_id, tenant_id)
    merged = {k: current.get(k) for k in _PREF_FIELDS}
    for k, v in fields.items():
        if k in _PREF_FIELDS:
            merged[k] = v

    now_iso = datetime.now(timezone.utc).isoformat()
    channels_json = json.dumps(list(merged.get("channels") or []))
    digest_flag = 1 if merged.get("digest_opt_in") else 0

    conn = get_connection()
    try:
        _ensure_schema(conn)
        exists = conn.execute(
            f"SELECT 1 FROM {_TABLE} WHERE user_id = %s AND tenant_id = %s",
            (user_id, tenant_id or ""),
        ).fetchone()
        if exists:
            conn.execute(
                f"UPDATE {_TABLE} SET classification = %s, channels = %s, "
                f"quiet_hours_start = %s, quiet_hours_end = %s, timezone = %s, "
                f"digest_opt_in = %s, digest_frequency = %s, updated_at = %s "
                f"WHERE user_id = %s AND tenant_id = %s",
                (
                    classification, channels_json,
                    merged.get("quiet_hours_start"), merged.get("quiet_hours_end"),
                    merged.get("timezone") or "UTC", digest_flag,
                    merged.get("digest_frequency") or "daily", now_iso,
                    user_id, tenant_id or "",
                ),
            )
        else:
            conn.execute(
                f"INSERT INTO {_TABLE} (user_id, tenant_id, classification, channels, "
                f"quiet_hours_start, quiet_hours_end, timezone, digest_opt_in, "
                f"digest_frequency, updated_at) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    user_id, tenant_id or "", classification, channels_json,
                    merged.get("quiet_hours_start"), merged.get("quiet_hours_end"),
                    merged.get("timezone") or "UTC", digest_flag,
                    merged.get("digest_frequency") or "daily", now_iso,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return get_preferences(user_id, tenant_id)


def _local_hour(tz_name: str | None, now: datetime | None = None) -> int:
    """Current wall-clock hour in the given IANA timezone (UTC fallback)."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(tz_name or "UTC")).hour
    except Exception:
        return dt.astimezone(timezone.utc).hour


def in_quiet_hours(prefs: dict, now: datetime | None = None) -> bool:
    """True when ``now`` falls within the user's quiet-hours window ``[start, end)``.

    Handles windows that wrap midnight (e.g. 22 -> 6). When either bound is unset
    there are no quiet hours and this returns ``False``.
    """
    start = prefs.get("quiet_hours_start")
    end = prefs.get("quiet_hours_end")
    if start is None or end is None:
        return False
    try:
        start = int(start) % 24
        end = int(end) % 24
    except (TypeError, ValueError):
        return False
    if start == end:
        return False
    hour = _local_hour(prefs.get("timezone"), now)
    if start < end:
        return start <= hour < end
    # Wrapping window (e.g. 22:00 -> 06:00)
    return hour >= start or hour < end


def dispatcher_paused() -> bool:
    """True when the autonomous kanban dispatcher is currently paused.

    Quiet hours assume the platform is working autonomously and that alerts can
    wait until morning. A paused dispatcher inverts that: nothing is being
    picked up, so an alert raised during the pause has no autonomous responder
    and must not be silently held overnight.

    The kanban import is deliberately lazy and local. Notifications must not
    take a module-level dependency on the kanban package — that would be a
    layering inversion and an import cycle risk — so this is a soft, optional
    probe of runtime state.

    Fails CLOSED toward existing behaviour: any error means "not paused", so
    quiet hours continue to suppress exactly as they do today. A notification
    subsystem must never break because a scheduler module moved.

    Patch this symbol directly to control the behaviour in tests.
    """
    try:
        from tools.kanban.scheduler_control import is_paused

        return bool(is_paused())
    except Exception:
        return False


def resolve_user_channels(
    user_id: str,
    candidate_channels: list[str],
    tenant_id: str | None = None,
    severity: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Narrow routing-resolved channels to a user's actual preferences.

    Intersects ``candidate_channels`` (typically the output of
    ``routing_rules.resolve_channels``) with the user's enabled channels,
    preserving candidate order. During quiet hours all channels are suppressed
    UNLESS either
      * the alert is critical and ``critical_bypasses_quiet_hours`` is set, or
      * the autonomous kanban dispatcher is paused (see
        :func:`dispatcher_paused`) — with no autonomous responder, a suppressed
        alert would go unanswered until the pause is noticed by hand.
    A user with no channel preferences accepts all candidates.
    """
    prefs = get_preferences(user_id, tenant_id)

    crit = {
        s.lower()
        for s in ((load_rules().get("escalation") or {}).get("critical_severities") or ["critical"])
    }
    is_critical = (severity or "").lower() in crit

    if in_quiet_hours(prefs, now):
        bypass = is_critical and _defaults()["critical_bypasses_quiet_hours"]
        # A paused dispatcher means no autonomous responder is running, so
        # holding this until the window closes would leave it unanswered.
        if not bypass and not dispatcher_paused():
            return []

    user_channels = {c.lower() for c in (prefs.get("channels") or [])}
    if not user_channels:
        # No explicit preference -> accept everything routed to them.
        return list(candidate_channels)
    return [c for c in candidate_channels if c.lower() in user_channels]


def wants_digest(user_id: str, tenant_id: str | None = None) -> bool:
    """True when the user has opted into digest-mode delivery."""
    return bool(get_preferences(user_id, tenant_id).get("digest_opt_in"))
