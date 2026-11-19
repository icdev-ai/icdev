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

from datetime import datetime, timezone
from typing import Any


def rfc3339(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def instant(v: Any) -> datetime | None:
    """An RFC 3339 date-time WITH its zone as an aware datetime; None for anything else (empty, junk, no zone).

    Times are parsed and compared as instants, never as strings.
    """
    s = str(v or "").strip()
    if len(s) < 20 or s[10] not in "Tt":
        return None
    try:
        t = datetime.fromisoformat(s[:-1] + "+00:00" if s[-1] in "Zz" else s)
    except ValueError:
        # Graph and others send 7 fractional digits; Python takes 6.
        try:
            head, _, frac = s.partition(".")
            if not frac:
                return None
            digits = "".join(ch for ch in frac if ch.isdigit())
            zone = frac[len(digits):]
            t = datetime.fromisoformat(f"{head}.{digits[:6]}" + ("+00:00" if zone in ("Z", "z") else zone))
        except ValueError:
            return None
    return t if t.tzinfo is not None else None


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
