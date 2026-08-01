# CUI // SP-CTI
"""TTX Engine — real-time leaderboard and end-of-session category ribbons."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection
from .constants import RIBBON_DEFS

log = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _team_unscored_count(conn, team_id: int) -> int:
    """Count a team's responses the LLM judge left UNSCORED (fail-loud marker in
    judge_rationale_json). Parsed in Python — never json_extract in SQL — so the
    leaderboard can render unscored responses distinctly instead of as fake 0/50.
    """
    rows = conn.execute(
        "SELECT judge_rationale_json FROM ttx_scores WHERE team_id = %s",
        (team_id,),
    ).fetchall()
    n = 0
    for r in rows:
        raw = (r["judge_rationale_json"] if hasattr(r, "keys") else r[0]) or "{}"
        try:
            if json.loads(raw).get("unscored"):
                n += 1
        except Exception:
            pass
    return n


def compute_leaderboard(session_id: int) -> list[dict[str, Any]]:
    """Recompute rankings for all teams in a session and persist to ttx_leaderboard."""
    conn = get_connection()

    teams = conn.execute(
        "SELECT * FROM ttx_teams WHERE session_id = %s ORDER BY total_score DESC",
        (session_id,),
    ).fetchall()

    rows = []
    for rank, team in enumerate(teams, start=1):
        team_id = team["team_id"]

        # Aggregate score breakdown
        agg = conn.execute(
            """SELECT
                 COALESCE(SUM(receipt_pts), 0) AS receipt_pts,
                 COALESCE(SUM(judge_pts), 0) AS judge_pts,
                 COALESCE(SUM(time_bonus_pts), 0) AS time_bonus_pts,
                 COALESCE(SUM(total_pts), 0) AS total_pts
               FROM ttx_scores WHERE team_id = %s""",
            (team_id,),
        ).fetchone()

        conn.execute(
            """INSERT INTO ttx_leaderboard
               (session_id, team_id, rank_pos, total_score,
                receipt_pts, judge_pts, time_bonus_pts, computed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (session_id, team_id)
               DO UPDATE SET
                 rank_pos = excluded.rank_pos,
                 total_score = excluded.total_score,
                 receipt_pts = excluded.receipt_pts,
                 judge_pts = excluded.judge_pts,
                 time_bonus_pts = excluded.time_bonus_pts,
                 computed_at = excluded.computed_at""",
            (
                session_id, team_id, rank,
                agg["total_pts"] if agg else 0,
                agg["receipt_pts"] if agg else 0,
                agg["judge_pts"] if agg else 0,
                agg["time_bonus_pts"] if agg else 0,
                _now(),
            ),
        )
        conn.execute(
            "UPDATE ttx_teams SET rank_pos = %s, total_score = %s WHERE team_id = %s",
            (rank, agg["total_pts"] if agg else 0, team_id),
        )

        row = {
            "rank": rank,
            "team_id": team_id,
            "team_name": team["team_name"],
            "total_score": agg["total_pts"] if agg else 0,
            "receipt_pts": agg["receipt_pts"] if agg else 0,
            "judge_pts": agg["judge_pts"] if agg else 0,
            "time_bonus_pts": agg["time_bonus_pts"] if agg else 0,
            "unscored_count": _team_unscored_count(conn, team_id),
        }
        rows.append(row)

    conn.commit()
    return rows


def get_leaderboard(session_id: int) -> list[dict[str, Any]]:
    """Return current cached leaderboard for a session."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT lb.rank_pos AS rank, lb.team_id, t.team_name,
                  lb.total_score, lb.receipt_pts, lb.judge_pts, lb.time_bonus_pts
           FROM ttx_leaderboard lb
           JOIN ttx_teams t ON t.team_id = lb.team_id
           WHERE lb.session_id = %s
           ORDER BY lb.rank_pos""",
        (session_id,),
    ).fetchall()
    if rows:
        out = []
        for r in rows:
            d = dict(r)
            d["unscored_count"] = _team_unscored_count(conn, d["team_id"])
            out.append(d)
        return out
    return compute_leaderboard(session_id)


# ---------------------------------------------------------------------------
# Category ribbons
# ---------------------------------------------------------------------------

def award_ribbons(session_id: int) -> dict[str, dict | None]:
    """Determine category ribbon winners for a session.

    Returns {ribbon_slug: {team_id, team_name, value} | None}.
    """
    conn = get_connection()
    ribbons: dict[str, dict | None] = {k: None for k in RIBBON_DEFS}

    teams = conn.execute(
        "SELECT team_id, team_name FROM ttx_teams WHERE session_id = %s", (session_id,)
    ).fetchall()
    if not teams:
        return ribbons

    # Speed King — lowest average time_taken_s across responses
    speed_rows = conn.execute(
        """SELECT r.team_id, AVG(r.time_taken_s) AS avg_time
           FROM ttx_responses r
           JOIN ttx_teams t ON t.team_id = r.team_id
           WHERE t.session_id = %s AND r.time_taken_s IS NOT NULL
           GROUP BY r.team_id ORDER BY avg_time ASC LIMIT 1""",
        (session_id,),
    ).fetchone()
    if speed_rows:
        team = conn.execute(
            "SELECT team_name FROM ttx_teams WHERE team_id = %s", (speed_rows["team_id"],)
        ).fetchone()
        ribbons["speed_king"] = {"team_id": speed_rows["team_id"], "team_name": team["team_name"] if team else "?", "value": round(speed_rows["avg_time"], 1)}

    # AI Innovator — highest (receipt_pts + judge_pts) aggregate
    ai_rows = conn.execute(
        """SELECT team_id, SUM(receipt_pts + judge_pts) AS ai_score
           FROM ttx_scores WHERE team_id IN (
             SELECT team_id FROM ttx_teams WHERE session_id = %s
           ) GROUP BY team_id ORDER BY ai_score DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    if ai_rows:
        team = conn.execute(
            "SELECT team_name FROM ttx_teams WHERE team_id = %s", (ai_rows["team_id"],)
        ).fetchone()
        ribbons["ai_innovator"] = {"team_id": ai_rows["team_id"], "team_name": team["team_name"] if team else "?", "value": ai_rows["ai_score"]}

    # Doctrine Scholar — highest average judge_pts
    scholar_rows = conn.execute(
        """SELECT team_id, AVG(judge_pts) AS avg_judge
           FROM ttx_scores WHERE team_id IN (
             SELECT team_id FROM ttx_teams WHERE session_id = %s
           ) GROUP BY team_id ORDER BY avg_judge DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    if scholar_rows:
        team = conn.execute(
            "SELECT team_name FROM ttx_teams WHERE team_id = %s", (scholar_rows["team_id"],)
        ).fetchone()
        ribbons["doctrine_scholar"] = {"team_id": scholar_rows["team_id"], "team_name": team["team_name"] if team else "?", "value": round(scholar_rows["avg_judge"], 1)}

    # Strategist — highest score on COA-type injects (slug contains 'coa')
    strat_rows = conn.execute(
        """SELECT s.team_id, SUM(s.total_pts) AS coa_pts
           FROM ttx_scores s
           JOIN ttx_injects i ON i.inject_id = s.inject_id
           WHERE i.session_id = %s AND LOWER(i.slug) LIKE %s
           GROUP BY s.team_id ORDER BY coa_pts DESC LIMIT 1""",
        (session_id, "%coa%"),
    ).fetchone()
    if strat_rows:
        team = conn.execute(
            "SELECT team_name FROM ttx_teams WHERE team_id = %s", (strat_rows["team_id"],)
        ).fetchone()
        ribbons["strategist"] = {"team_id": strat_rows["team_id"], "team_name": team["team_name"] if team else "?", "value": strat_rows["coa_pts"]}

    # Safety Architect — highest cumulative AADC judge_pts across design challenge injects
    safety_rows = conn.execute(
        """SELECT s.team_id, SUM(s.judge_pts) AS aadc_total
           FROM ttx_scores s
           JOIN ttx_injects i ON i.inject_id = s.inject_id
           WHERE i.session_id = %s
           GROUP BY s.team_id ORDER BY aadc_total DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    if safety_rows:
        # Only award if at least one AADC design challenge inject exists in this session
        aadc_exists = conn.execute(
            """SELECT 1 FROM ttx_injects
               WHERE session_id = %s AND config_json LIKE '%aadc_design_challenge%' LIMIT 1""",
            (session_id,),
        ).fetchone()
        if aadc_exists:
            team = conn.execute(
                "SELECT team_name FROM ttx_teams WHERE team_id = %s", (safety_rows["team_id"],)
            ).fetchone()
            ribbons["safety_architect"] = {
                "team_id": safety_rows["team_id"],
                "team_name": team["team_name"] if team else "?",
                "value": safety_rows["aadc_total"],
            }

    return ribbons
