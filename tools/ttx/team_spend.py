# CUI // SP-CTI
"""Per-team spend attribution from ttx_api_log (lpx-teams-03).

``ttx_api_log(log_id, session_id, team_id, tool_slug, endpoint, call_id,
result_hash, token_count, cost_usd, called_at)`` is the natural attribution hook:
it already logs every AI tool call per team and is indexed on
``(team_id, session_id)`` (``idx_ttx_api_log_team``).

Design choice — OPTION (a): add ``token_count`` / ``cost_usd`` columns to
``ttx_api_log`` (via the gameday ``db.py`` migrate + guarded ALTER) and attribute
spend on this single table. We did NOT take option (b) — joining to
``token_tracker`` / ``llm_gateway_audit`` by ``call_id`` — because those stores
are keyed by agent/project, not by the gameday ``call_id``, so the join would be
fragile and frequently empty for gameday tool calls. Option (a) keeps attribution
a single-store query against the table that already carries the right key + index,
and populates it at the existing ``log_api_receipt`` insert hook. We do not write
both (no duplicate cost ledger).

``ttx_api_log`` is append-only in fact (only ``engine.log_api_receipt`` inserts;
every other reference is a SELECT), so it is declared in ``APPEND_ONLY_TABLES``.

This answers "what did each team spend this exercise?" from a CLI (``--json``) or
the gameday UI. Attribution is per-TEAM, never per-member — a team's members share
one team key/identity (join-by-code, no ``dashboard_users`` row); see lpx-teams-02.
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


def team_spend_report(session_id: int, *, conn=None) -> List[Dict[str, Any]]:
    """Per-team spend for one exercise: calls, tokens, and cost from ttx_api_log.

    Includes teams with zero logged calls (LEFT JOIN from ttx_teams) so the
    facilitator sees every team, not only those that made calls.
    """
    own = conn is None
    c = conn or get_connection()
    rows = c.execute(
        """
        SELECT t.team_id AS team_id,
               t.team_name AS team_name,
               COUNT(a.log_id) AS call_count,
               COALESCE(SUM(a.token_count), 0) AS total_tokens,
               COALESCE(SUM(a.cost_usd), 0.0) AS total_cost_usd
        FROM ttx_teams t
        LEFT JOIN ttx_api_log a
          ON a.team_id = t.team_id AND a.session_id = t.session_id
        WHERE t.session_id = %s
        GROUP BY t.team_id, t.team_name
        ORDER BY total_cost_usd DESC, t.team_id
        """,
        (session_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        out.append({
            "session_id": session_id,
            "team_id": int(d["team_id"]),
            "team_name": d["team_name"],
            "call_count": int(d["call_count"]),
            "total_tokens": int(d["total_tokens"]),
            "total_cost_usd": round(float(d["total_cost_usd"]), 6),
        })
    if own:
        _safe_close(c)
    return out


def session_spend_total(session_id: int, *, conn=None) -> Dict[str, Any]:
    """Roll-up across all teams for one exercise."""
    report = team_spend_report(session_id, conn=conn)
    return {
        "session_id": session_id,
        "teams": len(report),
        "total_calls": sum(t["call_count"] for t in report),
        "total_tokens": sum(t["total_tokens"] for t in report),
        "total_cost_usd": round(sum(t["total_cost_usd"] for t in report), 6),
        "per_team": report,
    }


def _safe_close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Per-team spend attribution (lpx-teams-03)")
    parser.add_argument("session_id", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--total", action="store_true", help="Show session roll-up")
    args = parser.parse_args(argv)

    result: Any = session_spend_total(args.session_id) if args.total else team_spend_report(args.session_id)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if args.total:
            print(f"Session {args.session_id}: {result['total_calls']} calls, "
                  f"{result['total_tokens']} tokens, ${result['total_cost_usd']:.4f}")
            result = result["per_team"]
        for t in result:
            print(f"  team {t['team_id']:>4} {t['team_name']:<20} "
                  f"calls={t['call_count']:<5} tokens={t['total_tokens']:<8} "
                  f"${t['total_cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
