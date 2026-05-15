# CUI // SP-CTI
"""IQE AI GameDay League collection adapters.

Registers three collections on the module-level Executor:
  gameday.tournaments  — tournament records with status + round progress
  gameday.leaderboard  — live team standings per tournament
  gameday.artifacts    — agent-generated artifacts with eval scores
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def tournaments_adapter(conn: Any) -> list[dict]:
    try:
        from tools.db.storage import get_connection as _gc
        c = conn or _gc()
        rows = c.execute(
            """SELECT id, name, status, round_count, current_round,
                      round_duration_minutes, scenario_pack,
                      started_at, completed_at, created_at
               FROM gd_ai_tournaments ORDER BY created_at DESC LIMIT 50"""
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def leaderboard_adapter(conn: Any) -> list[dict]:
    try:
        from tools.db.storage import get_connection as _gc
        c = conn or _gc()
        rows = c.execute(
            """SELECT lb.rank, lb.team_key, t.name, t.domain, t.color,
                      lb.total_score, lb.rounds_won, lb.artifacts_suggested,
                      lb.training_pairs_contributed,
                      lb.avg_ethics_score, lb.avg_innovation_score,
                      lb.updated_at,
                      tour.name AS tournament_name
               FROM gd_ai_leaderboard lb
               JOIN gd_ai_teams t ON t.id = lb.team_id
               JOIN gd_ai_tournaments tour ON tour.id = lb.tournament_id
               ORDER BY lb.tournament_id DESC, lb.rank ASC LIMIT 100"""
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def artifacts_adapter(conn: Any) -> list[dict]:
    try:
        from tools.db.storage import get_connection as _gc
        c = conn or _gc()
        rows = c.execute(
            """SELECT a.id, a.team_key, a.member_role, a.artifact_type,
                      a.tokens_used, a.model_used, a.latency_ms,
                      a.created_at,
                      COALESCE(e.total_score, 0) AS judge_score,
                      COALESCE(e.ethics_score, 1.0) AS ethics_score,
                      COALESCE(e.ethics_blocked, 0) AS ethics_blocked
               FROM gd_ai_artifacts a
               LEFT JOIN gd_ai_judge_evals e ON e.round_id = a.round_id
                   AND e.team_id = a.team_id
               ORDER BY a.created_at DESC LIMIT 200"""
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# Register collections
register_collection(
    "gameday.tournaments",
    tournaments_adapter,
    description="AI GameDay League tournament records with status and round progress",
    schema={
        "id": "int",
        "name": "str",
        "status": "str",
        "round_count": "int",
        "current_round": "int",
        "scenario_pack": "str",
    },
    filters=["status", "scenario_pack"],
)

register_collection(
    "gameday.leaderboard",
    leaderboard_adapter,
    description="Live team standings per AI GameDay tournament",
    schema={
        "rank": "int",
        "team_key": "str",
        "name": "str",
        "total_score": "int",
        "rounds_won": "int",
        "avg_ethics_score": "float",
    },
    filters=["team_key", "tournament_name"],
)

register_collection(
    "gameday.artifacts",
    artifacts_adapter,
    description="Agent-generated artifacts with judge evaluation scores",
    schema={
        "team_key": "str",
        "member_role": "str",
        "artifact_type": "str",
        "judge_score": "int",
        "ethics_blocked": "int",
    },
    filters=["team_key", "artifact_type", "ethics_blocked"],
)
