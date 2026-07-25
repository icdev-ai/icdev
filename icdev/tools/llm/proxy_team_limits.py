# CUI // SP-CTI
"""Per-team RPM/TPM rate ceilings for /gameday competition fairness (lpx-teams-01).

THE FAIRNESS REQUIREMENT. A gameday exercise runs 4-5 teams competing on ONE
shared server against one org-level provider rate limit. Teams are adversaries,
so a team that exhausts the shared rate limit degrades every opponent's exercise
— even while staying under budget. That is a competition-integrity bug, and the
existing guards do not address it:

* ``tools/llm/gateway.py``'s cost cap is process-global — it throttles EVERYONE
  when one team overruns (the opposite of fair).
* ``tools/llm/rate_gate.py`` is a process/cluster concurrency gate with no team
  dimension.
* ``tools/llm/proxy_budgets.py`` (lpx-keys-02) caps DOLLARS per scope, not the
  request/token RATE that starves opponents.

This module adds a per-team RPM/TPM ceiling keyed on ``ttx_teams.team_id`` and
SCOPED to the active ``ttx_sessions.session_id`` (team_id is an autoincrement PK,
but a team only means something inside its session). Exceeding a team's own
ceiling degrades ONLY that team — the deny is asserted in the tests, because the
deny case is the whole point.

Ceiling sizing (confirmed scale: 4-5 teams, 3-5 members each) is CONFIGURABLE
PER SESSION and never hardcoded:

* The base share is ``org_limit / N`` where **N is the session's ACTUAL team
  count** (``COUNT(ttx_teams)`` for the session), falling back to
  ``ttx_sessions.max_teams`` (DEFAULT 8) only when no teams exist yet. Sizing off
  the default would hand 8 teams' worth of headroom to a 5-team exercise; sizing
  off a hardcoded 5 would break a 3- or 7-team run.
* A ``burst_factor`` (>= 1.0) lets a team briefly exceed its 1/N share so idle
  capacity is not stranded when only two teams are mid-response. The ceilings can
  therefore sum to more than the org limit — that is the intended burst
  allowance, safe because teams rarely peak simultaneously.

A team's 3-5 members share the team's ceiling. That is correct and forced —
gameday members join by ``join_code`` and have no ``dashboard_users`` row, so
per-member ceilings are impossible (see lpx-teams-02).

Storage note: like its sibling ``ttx_api_log``, these tables carry no
``tenant_id`` / ``classification`` columns — the gameday enforcement path uses
``get_connection()`` without a security context, so adding those columns (unset)
would only risk an RLS predicate mismatch. Rate windows are minute buckets
(integer epoch/60), which is PG-portable; no SQLite-dialect JSON SQL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

# Org-level provider ceilings the teams share. Operator-tunable; defaults are
# deliberately modest so an unconfigured install still enforces fairness.
ENV_ORG_RPM = "ICDEV_LLM_ORG_RPM"
ENV_ORG_TPM = "ICDEV_LLM_ORG_TPM"
ENV_BURST_FACTOR = "ICDEV_LLM_TEAM_BURST_FACTOR"
_DEFAULT_ORG_RPM = 60
_DEFAULT_ORG_TPM = 100_000
_DEFAULT_BURST_FACTOR = 1.5

_DDL = """
CREATE TABLE IF NOT EXISTS llm_proxy_team_limits (
    session_id   INTEGER NOT NULL,
    team_id      INTEGER NOT NULL,
    rpm_limit    INTEGER NOT NULL,
    tpm_limit    INTEGER NOT NULL,
    team_count   INTEGER,
    burst_factor REAL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, team_id)
);
CREATE TABLE IF NOT EXISTS llm_proxy_team_usage (
    session_id     INTEGER NOT NULL,
    team_id        INTEGER NOT NULL,
    window_minute  INTEGER NOT NULL,
    request_count  INTEGER NOT NULL DEFAULT 0,
    token_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, team_id, window_minute)
);
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_llm_proxy_team_usage_win ON llm_proxy_team_usage(session_id, window_minute)",
)

_migrated = False


def ensure_schema(conn=None) -> None:
    global _migrated
    if _migrated and conn is None:
        return
    own = conn is None
    c = conn or get_connection()
    for stmt in _DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            c.execute(stmt)
    for stmt in _INDEXES:
        try:
            c.execute(stmt)
        except Exception:
            pass
    c.commit()
    if own:
        _migrated = True


# --- Config -----------------------------------------------------------------

def _org_rpm() -> int:
    return _int_env(ENV_ORG_RPM, _DEFAULT_ORG_RPM)


def _org_tpm() -> int:
    return _int_env(ENV_ORG_TPM, _DEFAULT_ORG_TPM)


def _burst_factor() -> float:
    raw = os.environ.get(ENV_BURST_FACTOR, "").strip()
    if not raw:
        return _DEFAULT_BURST_FACTOR
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_BURST_FACTOR


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _actual_team_count(conn, session_id: int) -> int:
    """N = actual teams in the session; fall back to max_teams (DEFAULT 8)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ttx_teams WHERE session_id = %s", (session_id,)
    ).fetchone()
    n = int(dict(row)["n"]) if row else 0
    if n > 0:
        return n
    mt = conn.execute(
        "SELECT max_teams FROM ttx_sessions WHERE session_id = %s", (session_id,)
    ).fetchone()
    if mt:
        try:
            return max(1, int(dict(mt)["max_teams"]))
        except (TypeError, ValueError, KeyError):
            pass
    return 8


def compute_ceiling(org_limit: int, team_count: int, burst_factor: float) -> int:
    """Per-team ceiling = ceil(org/N * burst), at least 1."""
    n = max(1, int(team_count))
    base = org_limit / n
    return max(1, int(base * float(burst_factor) + 0.999))


def configure_session_ceilings(
    session_id: int,
    *,
    team_count: Optional[int] = None,
    org_rpm: Optional[int] = None,
    org_tpm: Optional[int] = None,
    burst_factor: Optional[float] = None,
    conn=None,
) -> Dict[str, Any]:
    """(Re)compute and persist per-team RPM/TPM ceilings for every team in a session.

    ``team_count`` defaults to the session's ACTUAL team count. Ceilings are
    written per (session_id, team_id) so a per-team override is possible later.
    """
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    n = team_count if team_count is not None else _actual_team_count(c, session_id)
    rpm_org = org_rpm if org_rpm is not None else _org_rpm()
    tpm_org = org_tpm if org_tpm is not None else _org_tpm()
    bf = burst_factor if burst_factor is not None else _burst_factor()
    rpm_ceiling = compute_ceiling(rpm_org, n, bf)
    tpm_ceiling = compute_ceiling(tpm_org, n, bf)

    teams = c.execute(
        "SELECT team_id FROM ttx_teams WHERE session_id = %s", (session_id,)
    ).fetchall()
    team_ids = [int(dict(t)["team_id"]) for t in teams]
    now = _now()
    for tid in team_ids:
        _upsert_limit(c, session_id, tid, rpm_ceiling, tpm_ceiling, n, bf, now)
    c.commit()
    if own:
        _safe_close(c)
    return {
        "session_id": session_id,
        "team_count": n,
        "rpm_ceiling": rpm_ceiling,
        "tpm_ceiling": tpm_ceiling,
        "burst_factor": bf,
        "org_rpm": rpm_org,
        "org_tpm": tpm_org,
        "teams_configured": team_ids,
    }


def _upsert_limit(conn, session_id, team_id, rpm, tpm, n, bf, now) -> None:
    existing = conn.execute(
        "SELECT 1 FROM llm_proxy_team_limits WHERE session_id = %s AND team_id = %s",
        (session_id, team_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE llm_proxy_team_limits SET rpm_limit = %s, tpm_limit = %s, "
            "team_count = %s, burst_factor = %s, updated_at = %s "
            "WHERE session_id = %s AND team_id = %s",
            (rpm, tpm, n, bf, now, session_id, team_id),
        )
    else:
        conn.execute(
            "INSERT INTO llm_proxy_team_limits (session_id, team_id, rpm_limit, "
            "tpm_limit, team_count, burst_factor, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (session_id, team_id, rpm, tpm, n, bf, now),
        )


def _get_limit(conn, session_id: int, team_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT rpm_limit, tpm_limit, team_count, burst_factor FROM llm_proxy_team_limits "
        "WHERE session_id = %s AND team_id = %s",
        (session_id, team_id),
    ).fetchone()
    return dict(row) if row else None


# --- Enforcement ------------------------------------------------------------

def _current_minute(now: Optional[float] = None) -> int:
    return int((now if now is not None else time.time()) // 60)


def _window_usage(conn, session_id, team_id, minute) -> Dict[str, int]:
    row = conn.execute(
        "SELECT request_count, token_count FROM llm_proxy_team_usage "
        "WHERE session_id = %s AND team_id = %s AND window_minute = %s",
        (session_id, team_id, minute),
    ).fetchone()
    if not row:
        return {"request_count": 0, "token_count": 0}
    d = dict(row)
    return {"request_count": int(d["request_count"]), "token_count": int(d["token_count"])}


def check_team_rate(
    session_id: int,
    team_id: int,
    *,
    tokens: int = 0,
    now: Optional[float] = None,
    conn=None,
) -> Dict[str, Any]:
    """Decide allow/deny for one team's next call against its own RPM/TPM ceiling.

    Only THIS team's window counters are consulted, so exceeding a ceiling
    degrades only this team — never its opponents. If no ceiling is configured for
    the session yet, the call is allowed (fail-open: fairness enforcement is
    opt-in per session via ``configure_session_ceilings``). Does NOT record the
    call — call :func:`record_team_call` on an allowed call.
    """
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    minute = _current_minute(now)
    limit = _get_limit(c, session_id, team_id)
    usage = _window_usage(c, session_id, team_id, minute)
    if own:
        _safe_close(c)

    if limit is None:
        return {
            "allowed": True,
            "action": "allow",
            "session_id": session_id,
            "team_id": team_id,
            "rpm_used": usage["request_count"],
            "tpm_used": usage["token_count"],
            "rpm_limit": None,
            "tpm_limit": None,
            "reason": "No per-team ceiling configured for this session",
        }

    rpm_used = usage["request_count"]
    tpm_used = usage["token_count"]
    rpm_limit = int(limit["rpm_limit"])
    tpm_limit = int(limit["tpm_limit"])
    over_rpm = rpm_used >= rpm_limit
    over_tpm = (tpm_used + max(0, int(tokens))) > tpm_limit
    allowed = not (over_rpm or over_tpm)
    if allowed:
        reason = "Within team ceiling"
    elif over_rpm:
        reason = (
            f"Team {team_id} hit its RPM ceiling ({rpm_used}/{rpm_limit} this minute) "
            f"— throttled to protect opponents' fair share"
        )
    else:
        reason = (
            f"Team {team_id} would exceed its TPM ceiling "
            f"({tpm_used}+{tokens}/{tpm_limit} this minute)"
        )
    return {
        "allowed": allowed,
        "action": "allow" if allowed else "deny",
        "session_id": session_id,
        "team_id": team_id,
        "rpm_used": rpm_used,
        "tpm_used": tpm_used,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "reason": reason,
    }


def record_team_call(
    session_id: int,
    team_id: int,
    *,
    tokens: int = 0,
    now: Optional[float] = None,
    conn=None,
) -> Dict[str, Any]:
    """Record one call in the current minute window for a team."""
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    minute = _current_minute(now)
    existing = c.execute(
        "SELECT request_count, token_count FROM llm_proxy_team_usage "
        "WHERE session_id = %s AND team_id = %s AND window_minute = %s",
        (session_id, team_id, minute),
    ).fetchone()
    if existing:
        c.execute(
            "UPDATE llm_proxy_team_usage SET request_count = request_count + 1, "
            "token_count = token_count + %s "
            "WHERE session_id = %s AND team_id = %s AND window_minute = %s",
            (max(0, int(tokens)), session_id, team_id, minute),
        )
    else:
        c.execute(
            "INSERT INTO llm_proxy_team_usage (session_id, team_id, window_minute, "
            "request_count, token_count) VALUES (%s, %s, %s, %s, %s)",
            (session_id, team_id, minute, 1, max(0, int(tokens))),
        )
    c.commit()
    usage = _window_usage(c, session_id, team_id, minute)
    if own:
        _safe_close(c)
    return {"session_id": session_id, "team_id": team_id, "window_minute": minute, **usage}


def team_rate_status(session_id: int, *, now: Optional[float] = None, conn=None) -> List[Dict[str, Any]]:
    """Facilitator view: per-team usage vs ceiling for the current minute, with an
    ``at_ceiling`` flag so a throttled team is observable, not a silent stall."""
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    minute = _current_minute(now)
    limits = c.execute(
        "SELECT team_id, rpm_limit, tpm_limit FROM llm_proxy_team_limits "
        "WHERE session_id = %s ORDER BY team_id",
        (session_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for lrow in limits:
        lim = dict(lrow)
        tid = int(lim["team_id"])
        usage = _window_usage(c, session_id, tid, minute)
        rpm_limit = int(lim["rpm_limit"])
        tpm_limit = int(lim["tpm_limit"])
        out.append({
            "session_id": session_id,
            "team_id": tid,
            "rpm_used": usage["request_count"],
            "rpm_limit": rpm_limit,
            "tpm_used": usage["token_count"],
            "tpm_limit": tpm_limit,
            "at_ceiling": usage["request_count"] >= rpm_limit or usage["token_count"] >= tpm_limit,
        })
    if own:
        _safe_close(c)
    return out


# --- helpers ----------------------------------------------------------------

def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _safe_close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


# --- CLI --------------------------------------------------------------------

def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Per-team RPM/TPM ceilings (lpx-teams-01)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cfg = sub.add_parser("configure", help="Compute + persist per-team ceilings", parents=[common])
    p_cfg.add_argument("session_id", type=int)
    p_cfg.add_argument("--team-count", type=int)
    p_cfg.add_argument("--org-rpm", type=int)
    p_cfg.add_argument("--org-tpm", type=int)
    p_cfg.add_argument("--burst-factor", type=float)

    p_chk = sub.add_parser("check", help="Check a team's rate", parents=[common])
    p_chk.add_argument("session_id", type=int)
    p_chk.add_argument("team_id", type=int)
    p_chk.add_argument("--tokens", type=int, default=0)

    p_st = sub.add_parser("status", help="Facilitator per-team status", parents=[common])
    p_st.add_argument("session_id", type=int)

    args = parser.parse_args(argv)
    if args.cmd == "configure":
        _print(configure_session_ceilings(
            args.session_id, team_count=args.team_count, org_rpm=args.org_rpm,
            org_tpm=args.org_tpm, burst_factor=args.burst_factor), args.json)
        return 0
    if args.cmd == "check":
        _print(check_team_rate(args.session_id, args.team_id, tokens=args.tokens), args.json)
        return 0
    if args.cmd == "status":
        _print(team_rate_status(args.session_id), args.json)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
