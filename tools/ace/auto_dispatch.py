# CUI // SP-CTI
"""ACE auto-dispatch grants: when an event may launch a role without a person (icx-ace-01).

By default every ACE event is a RECOMMENDATION: the dispatcher records a pending
step of ``decl-wft-ace-run-launch`` and a person decides it at ``/decisions``.
A role/topic runs on auto (a person on the loop, not in it) only under a LIVE
grant, and a grant needs all of:

* **Eligibility, in reviewed config.** The role's YAML declares
  ``auto_dispatch: {eligible_topics: [...], reason: "..."}``; ``role_loader``
  refuses an eligible topic the role does not listen to. Absent means never.
* **Trust.** The role's NOVA band (``trust_calibrator``) is at least
  ``min_trust_band`` in ``args/ace/auto_dispatch.yaml`` (trusted).
* **A person's step.** One holder of ``decl-wft-ace-auto-dispatch`` takes it,
  with a reason, an expiry no later than ``max_grant_days`` and a
  ``max_per_day`` at most ``max_per_day_ceiling``. Agents and service keys cannot
  (``declared`` refuses a service actor); no MCP tool grants anything.

A grant stops being live when it is revoked, expires, reaches today's
``max_per_day``, or stop-all is set; and it is SUSPENDED by itself, for good,
when the role's trust falls below the band (a person grants again once trust
has recovered). Revoke and stop-all take effect at once, with a stated reason
and no approval: they are stops. Lifting stop-all is the declared step.

The grants log ``ace_dispatch_grants`` is append-only. A grant's state is
derived from its events here, never stored.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.auto_dispatch")

_DB_ENV = "ICDEV_ACE_DB_URL"
TEMPLATE_ID = "decl-wft-ace-auto-dispatch"
GRANT_SURFACE = "ace_api.api_auto_dispatch_grant"
RESUME_SURFACE = "ace_api.api_auto_dispatch_resume_all"
#: The stop-all switch's grant id in the log.
ALL = "*"
#: NOVA bands, lowest first (trust_calibrator.get_trust_band).
BANDS = ("probationary", "supervised", "trusted", "autonomous")

_DEFAULTS: dict[str, Any] = {
    "dispatcher_enabled": True,
    "poll_seconds": 5,
    "max_roles_per_event": 3,
    "dedup_window_seconds": 30,
    "recommendation_expiry_hours": 72,
    "min_trust_band": "trusted",
    "max_grant_days": 30,
    "max_per_day_ceiling": 50,
}


class GrantRefused(Exception):
    """A grant, revoke or resume that may not happen. Carries an HTTP status."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def to_dict(self) -> dict:
        # The shape of declared.StepRefused, so a page reads every refusal one way.
        return {"error": self.message, "code": self.code}


def config_path() -> Path:
    from icdev._paths import get_data_path

    return get_data_path("args") / "ace" / "auto_dispatch.yaml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """``args/ace/auto_dispatch.yaml`` over the defaults."""
    out = dict(_DEFAULTS)
    try:
        data = yaml.safe_load((path or config_path()).read_text(encoding="utf-8")) or {}
        out.update({k: v for k, v in data.items() if k in _DEFAULTS})
    except (OSError, ValueError) as exc:
        logger.warning("auto_dispatch config unreadable, using defaults: %s", exc)
    if out["min_trust_band"] not in BANDS:
        out["min_trust_band"] = "trusted"
    return out


def _conn():
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection(_DB_ENV)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _stamp(dt: datetime) -> str:
    """A log row's time, to the microsecond: the log is ordered by it, and a grant
    and its revoke can land in the same second."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _parse_when(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise GrantRefused(400, "expiry", "an expiry is required")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise GrantRefused(400, "expiry", f"expiry {value!r} is not a date or date-time")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if len(text) == 10:  # a bare date means the end of that day
        dt = dt + timedelta(hours=23, minutes=59, seconds=59)
    return dt


# ── eligibility and trust ────────────────────────────────────────────────────


def _roles(roles: list | None = None) -> list:
    if roles is not None:
        return roles
    from icdev.tools.ace.role_loader import RoleLoader

    return RoleLoader().list_roles()


def trust_of(role_id: str) -> tuple[float, str]:
    """(score, band) for a role, from NOVA."""
    from icdev.tools.ace.trust_calibrator import get_trust_band, get_trust_score

    score = float(get_trust_score(role_id))
    return score, get_trust_band(score).name


def trust_ok(band: str, cfg: dict | None = None) -> bool:
    floor = (cfg or load_config())["min_trust_band"]
    return band in BANDS and BANDS.index(band) >= BANDS.index(floor)


def eligible(roles: list | None = None, cfg: dict | None = None) -> list[dict[str, Any]]:
    """Every (role, topic) a reviewed role YAML declares auto-eligible, with its trust now."""
    cfg = cfg or load_config()
    out = []
    for role in _roles(roles):
        decl = getattr(role, "auto_dispatch", None) or {}
        topics = list(decl.get("eligible_topics") or [])
        if not topics:
            continue
        score, band = trust_of(role.role_id)
        for topic in topics:
            out.append({"role_id": role.role_id, "display_name": role.display_name,
                        "topic": topic, "reason": str(decl.get("reason") or ""),
                        "trust_score": round(score, 4), "trust_band": band,
                        "trust_ok": trust_ok(band, cfg)})
    return sorted(out, key=lambda e: (e["role_id"], e["topic"]))


def _eligibility(role_id: str, topic: str, roles: list | None) -> dict | None:
    for role in _roles(roles):
        if role.role_id == role_id:
            decl = getattr(role, "auto_dispatch", None) or {}
            if topic in (decl.get("eligible_topics") or []):
                return {"role": role, "reason": str(decl.get("reason") or "")}
            return None
    return None


# ── the log ──────────────────────────────────────────────────────────────────


def _append(event: str, grant_id: str, *, role_id: str = "", topic: str = "",
            expires_at: str = "", max_per_day: int = 0, reason: str = "",
            actor: dict | None = None, step_id: str = "", conn=None) -> str:
    own = conn is None
    conn = conn or _conn()
    event_id = f"adge-{uuid.uuid4().hex[:16]}"
    try:
        conn.execute(
            "INSERT INTO ace_dispatch_grants (event_id, grant_id, event, role_id, topic, "
            "expires_at, max_per_day, reason, actor, actor_role, step_id, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (event_id, grant_id, event, role_id, topic, expires_at, int(max_per_day), reason,
             (actor or {}).get("id") or "system", (actor or {}).get("role") or "",
             step_id, _stamp(_now())),
        )
        conn.commit()
    finally:
        if own:
            conn.close()
    return event_id


_COLS = ("event_id", "grant_id", "event", "role_id", "topic", "expires_at", "max_per_day",
         "reason", "actor", "actor_role", "step_id", "created_at")


def _events(conn=None) -> list[dict[str, Any]]:
    own = conn is None
    conn = conn or _conn()
    try:
        rows = conn.execute(
            "SELECT event_id, grant_id, event, role_id, topic, expires_at, max_per_day, reason, "
            "actor, actor_role, step_id, created_at FROM ace_dispatch_grants "
            "ORDER BY created_at ASC, event_id ASC").fetchall()
    finally:
        if own:
            conn.close()
    return [dict(zip(_COLS, tuple(r))) for r in rows]


def _launches_today(grant_ids: list[str], now: datetime) -> dict[str, int]:
    if not grant_ids:
        return {}
    start = _fmt(now.replace(hour=0, minute=0, second=0, microsecond=0))
    marks = ", ".join(["%s"] * len(grant_ids))
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT grant_id, COUNT(*) FROM ace_event_results WHERE status = 'auto_launched' "
            f"AND created_at >= %s AND grant_id IN ({marks}) GROUP BY grant_id",  # nosec B608
            (start, *grant_ids)).fetchall()
    finally:
        conn.close()
    counts = {}
    for row in rows:
        grant_id, n = tuple(row)
        counts[str(grant_id)] = int(n)
    return counts


def stop_all_state(events: list[dict] | None = None) -> dict[str, Any]:
    """``{stopped, by, reason, at}`` — the latest stop_all / resume_all wins."""
    events = _events() if events is None else events
    last = None
    for ev in events:
        if ev["grant_id"] == ALL and ev["event"] in ("stop_all", "resume_all"):
            last = ev
    if not last:
        return {"stopped": False, "by": "", "reason": "", "at": ""}
    return {"stopped": last["event"] == "stop_all", "by": last["actor"],
            "reason": last["reason"], "at": str(last["created_at"])}


def grants(*, include_closed: bool = True, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every grant with its derived state: live, revoked, suspended or expired."""
    now = now or _now()
    events = _events()
    by_id: dict[str, dict] = {}
    for ev in events:
        if ev["grant_id"] == ALL:
            continue
        if ev["event"] == "granted":
            by_id[ev["grant_id"]] = {
                "grant_id": ev["grant_id"], "role_id": ev["role_id"], "topic": ev["topic"],
                "expires_at": ev["expires_at"], "max_per_day": int(ev["max_per_day"]),
                "reason": ev["reason"], "granted_by": ev["actor"], "step_id": ev["step_id"],
                "granted_at": str(ev["created_at"]), "state": "live", "closed_by": "",
                "closed_reason": "", "closed_at": ""}
            continue
        g = by_id.get(ev["grant_id"])
        if g and g["state"] == "live" and ev["event"] in ("revoked", "suspended", "expired"):
            g.update(state=ev["event"], closed_by=ev["actor"], closed_reason=ev["reason"],
                     closed_at=str(ev["created_at"]))
    for g in by_id.values():
        if g["state"] == "live" and g["expires_at"] and g["expires_at"] <= _fmt(now):
            g["state"] = "expired"
    counts = _launches_today([g["grant_id"] for g in by_id.values()], now)
    out = []
    for g in by_id.values():
        g["launched_today"] = counts.get(g["grant_id"], 0)
        g["at_limit"] = g["state"] == "live" and g["launched_today"] >= g["max_per_day"]
        if include_closed or g["state"] == "live":
            out.append(g)
    return sorted(out, key=lambda g: g["granted_at"], reverse=True)


# ── decisions and stops ──────────────────────────────────────────────────────


def check_grant(role_id: str, topic: str, *, reason: str, expires_at: str, max_per_day: Any,
                roles: list | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The payload a grant would bind, or GrantRefused. Writes nothing.

    Run BEFORE the person's step is taken, so a refused grant leaves no decision.
    """
    cfg = load_config()
    now = now or _now()
    role_id, topic, reason = str(role_id or "").strip(), str(topic or "").strip(), \
        str(reason or "").strip()
    if not role_id or not topic:
        raise GrantRefused(400, "subject", "role_id and topic are required")
    if not reason:
        raise GrantRefused(400, "reason", "a reason is required to turn auto-dispatch on")
    if _eligibility(role_id, topic, roles) is None:
        raise GrantRefused(403, "not_eligible", (
            f"{role_id} does not declare {topic} auto-eligible in its role YAML "
            "(auto_dispatch.eligible_topics); that is a reviewed config change"))
    score, band = trust_of(role_id)
    if not trust_ok(band, cfg):
        raise GrantRefused(403, "trust", (
            f"{role_id} is {band} (trust {score:.2f}); auto-dispatch needs "
            f"{cfg['min_trust_band']} or above"))
    when = _parse_when(expires_at)
    if when <= now:
        raise GrantRefused(400, "expiry", "the expiry is in the past")
    if when > now + timedelta(days=int(cfg["max_grant_days"])):
        raise GrantRefused(400, "expiry",
                           f"the expiry may be at most {cfg['max_grant_days']} days ahead")
    try:
        per_day = int(max_per_day)
    except (TypeError, ValueError):
        raise GrantRefused(400, "max_per_day", "max_per_day must be a whole number")
    if not 1 <= per_day <= int(cfg["max_per_day_ceiling"]):
        raise GrantRefused(400, "max_per_day",
                           f"max_per_day must be 1 to {cfg['max_per_day_ceiling']}")
    if any(g["state"] == "live" and g["role_id"] == role_id and g["topic"] == topic
           for g in grants(include_closed=False, now=now)):
        raise GrantRefused(409, "already_granted", f"{role_id}/{topic} already has a live grant")
    return {"role_id": role_id, "topic": topic, "expires_at": _fmt(when),
            "max_per_day": per_day, "reason": reason}


def record_grant(payload: dict[str, Any], *, actor: dict, step_id: str) -> dict[str, Any]:
    """Append the grant the person's step decided. Returns it."""
    grant_id = f"adg-{uuid.uuid4().hex[:12]}"
    _append("granted", grant_id, role_id=payload["role_id"], topic=payload["topic"],
            expires_at=payload["expires_at"], max_per_day=payload["max_per_day"],
            reason=payload["reason"], actor=actor, step_id=step_id)
    return {"grant_id": grant_id, "state": "live", **payload, "step_id": step_id}


def revoke(grant_id: str, *, actor: dict, reason: str) -> dict[str, Any]:
    """Turn a grant off now. A stop: no approval, a stated reason."""
    if not (reason or "").strip():
        raise GrantRefused(400, "reason", "say why auto-dispatch is being turned off")
    found = next((g for g in grants() if g["grant_id"] == grant_id), None)
    if found is None:
        raise GrantRefused(404, "unknown_grant", f"no grant {grant_id}")
    if found["state"] != "live":
        raise GrantRefused(409, "not_live", f"grant {grant_id} is already {found['state']}")
    _append("revoked", grant_id, role_id=found["role_id"], topic=found["topic"],
            reason=reason.strip(), actor=actor)
    return {"grant_id": grant_id, "state": "revoked"}


def stop_all(*, actor: dict, reason: str) -> dict[str, Any]:
    """Stop every grant at once. A stop: no approval, a stated reason."""
    if not (reason or "").strip():
        raise GrantRefused(400, "reason", "say why every auto-dispatch is being stopped")
    _append("stop_all", ALL, reason=reason.strip(), actor=actor)
    return stop_all_state()


def check_resume() -> None:
    if not stop_all_state()["stopped"]:
        raise GrantRefused(409, "not_stopped", "auto-dispatch is not stopped")


def record_resume(*, actor: dict, reason: str, step_id: str) -> dict[str, Any]:
    _append("resume_all", ALL, reason=reason, actor=actor, step_id=step_id)
    return stop_all_state()


# ── the dispatcher's question ────────────────────────────────────────────────


def live_grant(role_id: str, topic: str, *, roles: list | None = None,
               now: datetime | None = None) -> tuple[dict | None, str]:
    """(grant, "") when this role/topic may launch on auto now, else (None, why).

    Side effects, each recorded once: a grant past its expiry gets ``expired``;
    a grant whose role's trust fell below the band gets ``suspended``.
    """
    now = now or _now()
    events = _events()
    if stop_all_state(events)["stopped"]:
        return None, "stop-all is set"
    mine = [g for g in grants(include_closed=True, now=now)
            if g["role_id"] == role_id and g["topic"] == topic]
    for g in mine:
        if g["state"] == "expired" and not g["closed_at"]:
            _append("expired", g["grant_id"], role_id=role_id, topic=topic,
                    reason=f"expired at {g['expires_at']}")
    live = next((g for g in mine if g["state"] == "live"), None)
    if live is None:
        return None, "no live grant"
    if _eligibility(role_id, topic, roles) is None:
        return None, "no longer declared auto-eligible"
    score, band = trust_of(role_id)
    if not trust_ok(band):
        _append("suspended", live["grant_id"], role_id=role_id, topic=topic,
                reason=f"trust fell to {band} ({score:.2f})")
        return None, f"suspended: trust fell to {band}"
    if live["launched_today"] >= live["max_per_day"]:
        return None, f"today's limit of {live['max_per_day']} is reached"
    return live, ""
