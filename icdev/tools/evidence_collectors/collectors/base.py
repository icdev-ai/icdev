# CUI // SP-CTI
"""What every collector returns: OBSERVATIONS of named facts (icx-ent-02).

An observation is one fact a collector read from one system's source, with:

* ``value``        True or False when the source said so, None when it did not (unreachable, a credential that could not
                   be read, a rule not deployed, a device that has not reported, nothing to judge). None is UNVERIFIED,
                   never a pass and never a failure;
* ``observed_at``  RFC 3339 with its zone: when the SOURCE last established it (an AWS Config rule's last successful
                   evaluation, the oldest device report) or when the collector read it live;
* ``collector``, ``system`` and ``max_age_h`` (the freshness budget, the configuration's): past it, the observation
                   counts for nothing;
* ``source``       the operation or path read (never a credential), and ``detail``: counts only, never a user's or a
                   device's name.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


def rfc3339(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_RFC3339 = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})")


def instant(v: Any) -> datetime | None:
    """An RFC 3339 date-time WITH its zone as an aware datetime; None for anything else.

    Exactly ICDEV[OS]'s pattern (``T`` and ``Z`` upper case, seconds present, an offset with its colon), an hour of
    0-23 (no ``24:00``), and fractions of any length truncated to microseconds (Graph sends seven digits). Times are
    parsed and compared as instants, never as strings.
    """
    m = _RFC3339.fullmatch(v.strip()) if isinstance(v, str) else None
    if m is None or int(m.group(2)) > 23:
        return None
    day, hh, mm, ss, frac, zone = m.groups()
    text = f"{day}T{hh}:{mm}:{ss}" + (f".{frac[:6]}" if frac else "") + ("+00:00" if zone == "Z" else zone)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def observation(*, system: str, collector: str, kind: str, fact: str, value: bool | None, why: str,
                observed_at: str | None, max_age_h: float, source: str, detail: dict | None = None) -> dict:
    if value not in (True, False, None):
        raise ValueError(f"{fact}: a fact is True, False or None")
    return {"system": system, "collector": collector, "kind": kind, "fact": fact, "value": value, "why": why,
            "observed_at": observed_at, "max_age_h": float(max_age_h), "source": source, "detail": detail or {}}


def unread(*, system: str, collector: str, kind: str, facts, why: str, max_age_h: float, source: str) -> list[dict]:
    """Every fact a collector declares, UNVERIFIED with the same reason (the source could not be read)."""
    return [observation(system=system, collector=collector, kind=kind, fact=f, value=None, why=why, observed_at=None,
                        max_age_h=max_age_h, source=source) for f in facts]
