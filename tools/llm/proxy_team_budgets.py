# CUI // SP-CTI
"""Per-team spend budgets for /gameday, built on the lpx-keys-02 engine (lpx-teams-02).

A team's budget IS its virtual key's budget. This module is the gameday-facing
wrapper that binds a ``ttx_teams`` team (scoped to its ``ttx_sessions`` session)
to an exercise-windowed virtual key and gives facilitators a clear, structured
allow/warn/block decision instead of a generic 500 when a budget is exhausted.

Why no new budget table: the three non-uniform grouping units already live on the
SAME two tables from lpx-keys-01/02 — ``llm_proxy_keys`` (the key carries
``scope_type`` ∈ {team, guild, user} + ``scope_ref`` + ``session_id``) and
``llm_proxy_spend`` (the ledger). Adding a parallel team-budget table would be
exactly the "parallel notion" the cards warn against and would duplicate the cost
ledger (the same anti-pattern lpx-teams-03 avoids). So this card adds behaviour,
not schema.

BUDGET SHAPE — per EXERCISE, not per month. ``ttx_sessions.duration_minutes``
defaults to 120, so a gameday budget is scoped to one bounded session
(``budget_window='exercise'`` → ``window_key='exercise:<session_id>'``) and resets
for the next exercise. The strategy doc's "$X per student per month" shape is
wrong for gameday and is deliberately not used here.

ACADEMY is a DIFFERENT unit and is decided separately: academy budgets are
**per-guild** (``scope_type='guild'``) and may be **month-shaped**
(``budget_window='month'``) — not inherited from gameday's answer. Academy simply
issues keys with those scope/window values via lpx-keys-01/02; this gameday
wrapper does not force academy into the exercise window.

ATTRIBUTION LIMIT (accepted, not a gap): a team's 3-5 members share ONE team key,
so spend is attributable to the TEAM, never to the member — you will know Team
Blue spent $40, not that one player spent $18 of it. Per-member attribution would
require giving gameday players real ``dashboard_users`` accounts and an FK from
``ttx_team_members`` to them, a far larger change explicitly out of scope. This is
a property of the join-by-code model, not a bug to close in this card.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection

_TEAM_SCOPE = "team"
_EXERCISE_WINDOW = "exercise"


def provision_team_budget(
    session_id: int,
    team_id: int,
    budget_usd: float,
    *,
    alias: Optional[str] = None,
    tenant_id: Optional[str] = None,
    classification: Optional[str] = None,
    actor: Optional[str] = None,
    conn=None,
) -> Dict[str, Any]:
    """Ensure the team has an exercise-scoped virtual key with ``budget_usd``.

    If the team already has an active key for this session, its budget is updated
    in place (no duplicate key). Otherwise a new key is issued (the plaintext
    ``virtual_key`` is returned ONCE, only when freshly issued). The team's budget
    is its key's budget.
    """
    from tools.llm.proxy_budgets import resolve_active_key
    from tools.llm.proxy_keys import issue_key

    own = conn is None
    c = conn or get_connection()
    existing = resolve_active_key(_TEAM_SCOPE, str(team_id), session_id=str(session_id), conn=c)
    if existing:
        c.execute(
            "UPDATE llm_proxy_keys SET max_budget_usd = %s, budget_window = %s "
            "WHERE key_id = %s",
            (float(budget_usd), _EXERCISE_WINDOW, existing["key_id"]),
        )
        c.commit()
        result = {
            "key_id": existing["key_id"],
            "session_id": session_id,
            "team_id": team_id,
            "max_budget_usd": float(budget_usd),
            "budget_window": _EXERCISE_WINDOW,
            "reused": True,
        }
        if own:
            _safe_close(c)
        return result

    issued = issue_key(
        alias=alias or f"gameday-s{session_id}-t{team_id}",
        scope_type=_TEAM_SCOPE,
        scope_ref=str(team_id),
        session_id=str(session_id),
        max_budget_usd=float(budget_usd),
        budget_window=_EXERCISE_WINDOW,
        tenant_id=tenant_id,
        classification=classification,
        created_by=actor,
        conn=c,
    )
    issued["session_id"] = session_id
    issued["team_id"] = team_id
    issued["reused"] = False
    if own:
        _safe_close(c)
    return issued


def _resolve(session_id, team_id, conn):
    from tools.llm.proxy_budgets import resolve_active_key

    return resolve_active_key(_TEAM_SCOPE, str(team_id), session_id=str(session_id), conn=conn)


def check_team_budget(
    session_id: int,
    team_id: int,
    *,
    projected_cost_usd: float = 0.0,
    conn=None,
) -> Dict[str, Any]:
    """Facilitator-facing allow/warn/block for a team's exercise budget.

    Returns a structured result with a ``facilitator_message`` so the caller can
    surface a clear reason rather than a generic 500. When no team budget is
    provisioned, fails OPEN with an explanatory message (budgets are opt-in per
    exercise).
    """
    from tools.llm.proxy_budgets import check_budget

    own = conn is None
    c = conn or get_connection()
    key = _resolve(session_id, team_id, c)
    if key is None:
        if own:
            _safe_close(c)
        return {
            "allowed": True,
            "action": "allow",
            "session_id": session_id,
            "team_id": team_id,
            "budget_usd": None,
            "spent_usd": 0.0,
            "facilitator_message": (
                f"Team {team_id} has no exercise budget configured — allowing. "
                f"Provision one with provision_team_budget(session_id={session_id}, "
                f"team_id={team_id}, budget_usd=...)."
            ),
        }
    decision = check_budget(key["key_id"], projected_cost_usd=projected_cost_usd, conn=c)
    if own:
        _safe_close(c)

    action = decision["action"]
    if action == "block":
        msg = (
            f"Team {team_id} has exhausted its exercise budget "
            f"(${decision['spent_usd']:.2f} of ${decision['budget_usd']:.2f}). "
            f"AI calls are paused for this team until the facilitator raises the budget."
        )
    elif action == "warn":
        msg = (
            f"Team {team_id} is near its exercise budget "
            f"(${decision['spent_usd']:.2f} of ${decision['budget_usd']:.2f})."
        )
    else:
        msg = f"Team {team_id} within budget."
    return {
        "allowed": decision["action"] != "block",
        "action": action,
        "session_id": session_id,
        "team_id": team_id,
        "key_id": key["key_id"],
        "budget_usd": decision["budget_usd"],
        "spent_usd": decision["spent_usd"],
        "remaining_usd": decision["remaining_usd"],
        "window_key": decision["window_key"],
        "facilitator_message": msg,
    }


def record_team_spend(
    session_id: int,
    team_id: int,
    *,
    cost_usd: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    conn=None,
) -> Dict[str, Any]:
    """Record spend against a team's key. Raises ValueError if the team has no key."""
    from tools.llm.proxy_budgets import record_spend

    own = conn is None
    c = conn or get_connection()
    key = _resolve(session_id, team_id, c)
    if key is None:
        if own:
            _safe_close(c)
        raise ValueError(f"team {team_id} in session {session_id} has no provisioned budget key")
    out = record_spend(
        key["key_id"], cost_usd=cost_usd,
        input_tokens=input_tokens, output_tokens=output_tokens, conn=c,
    )
    out["session_id"] = session_id
    out["team_id"] = team_id
    if own:
        _safe_close(c)
    return out


def team_budget_status(session_id: int, *, conn=None) -> List[Dict[str, Any]]:
    """Facilitator view: each team's exercise spend vs budget (attribution is
    per-TEAM, never per-member — see module docstring)."""
    from tools.llm.proxy_budgets import get_spend

    own = conn is None
    c = conn or get_connection()
    rows = c.execute(
        "SELECT key_id, scope_ref, max_budget_usd FROM llm_proxy_keys "
        "WHERE scope_type = %s AND session_id = %s AND status = 'active' "
        "ORDER BY scope_ref",
        (_TEAM_SCOPE, str(session_id)),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        spend = get_spend(d["key_id"], conn=c)
        budget = d["max_budget_usd"]
        out.append({
            "session_id": session_id,
            "team_id": int(d["scope_ref"]) if str(d["scope_ref"]).isdigit() else d["scope_ref"],
            "key_id": d["key_id"],
            "budget_usd": budget,
            "spent_usd": spend["spent_usd"],
            "remaining_usd": None if budget is None else round(float(budget) - spend["spent_usd"], 6),
            "exhausted": budget is not None and spend["spent_usd"] >= float(budget),
        })
    if own:
        _safe_close(c)
    return out


def _safe_close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


# --- CLI --------------------------------------------------------------------

def _print(obj: Any, as_json: bool) -> None:
    print(json.dumps(obj, indent=2, default=str) if as_json else obj)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Per-team gameday budgets (lpx-teams-02)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prov = sub.add_parser("provision", help="Provision/update a team's exercise budget", parents=[common])
    p_prov.add_argument("session_id", type=int)
    p_prov.add_argument("team_id", type=int)
    p_prov.add_argument("--budget", type=float, required=True)
    p_prov.add_argument("--tenant-id")
    p_prov.add_argument("--actor")

    p_chk = sub.add_parser("check", help="Check a team's budget", parents=[common])
    p_chk.add_argument("session_id", type=int)
    p_chk.add_argument("team_id", type=int)
    p_chk.add_argument("--projected", type=float, default=0.0)

    p_st = sub.add_parser("status", help="Facilitator per-team budget status", parents=[common])
    p_st.add_argument("session_id", type=int)

    args = parser.parse_args(argv)
    if args.cmd == "provision":
        _print(provision_team_budget(args.session_id, args.team_id, args.budget,
                                     tenant_id=args.tenant_id, actor=args.actor), args.json)
        return 0
    if args.cmd == "check":
        _print(check_team_budget(args.session_id, args.team_id, projected_cost_usd=args.projected), args.json)
        return 0
    if args.cmd == "status":
        _print(team_budget_status(args.session_id), args.json)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
