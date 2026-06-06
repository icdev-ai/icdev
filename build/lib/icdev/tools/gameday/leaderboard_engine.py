# CUI // SP-CTI
"""AI GameDay League — leaderboard engine.

Aggregates per-round judge evaluations into live tournament standings
and writes the gd_ai_leaderboard snapshot table.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from typing import Any

from tools.db.storage import get_connection
from .db import upsert_leaderboard

log = get_logger(__name__)


def refresh_leaderboard(tournament_id: int) -> list[dict]:
    """Recompute standings from judge evals and write leaderboard snapshot."""
    conn = get_connection()

    # Aggregate scores per team across all completed rounds
    rows = conn.execute(
        """SELECT
               t.id                 AS team_id,
               t.team_key,
               t.name,
               t.domain,
               t.color,
               t.total_score,
               t.rounds_won,
               t.artifacts_suggested,
               t.training_pairs_contributed,
               AVG(COALESCE(e.ethics_score, 1.0))     AS avg_ethics,
               AVG(COALESCE(e.innovation_score, 0.0)) AS avg_innovation
           FROM gd_ai_teams t
           LEFT JOIN gd_ai_judge_evals e ON e.team_id = t.id
           WHERE t.tournament_id = ?
           GROUP BY t.id
           ORDER BY t.total_score DESC""",
        (tournament_id,),
    ).fetchall()

    leaderboard = []
    for rank, row in enumerate(rows, start=1):
        upsert_leaderboard(
            tournament_id=tournament_id,
            team_id=row["team_id"],
            team_key=row["team_key"],
            rank=rank,
            total_score=row["total_score"],
            rounds_won=row["rounds_won"],
            artifacts_suggested=row["artifacts_suggested"],
            training_pairs_contributed=row["training_pairs_contributed"],
            avg_ethics_score=float(row["avg_ethics"] or 1.0),
            avg_innovation_score=float(row["avg_innovation"] or 0.0),
        )
        leaderboard.append({
            "rank":                       rank,
            "team_key":                   row["team_key"],
            "name":                       row["name"],
            "domain":                     row["domain"],
            "color":                      row["color"],
            "total_score":                row["total_score"],
            "rounds_won":                 row["rounds_won"],
            "artifacts_suggested":        row["artifacts_suggested"],
            "training_pairs_contributed": row["training_pairs_contributed"],
            "avg_ethics_score":           float(row["avg_ethics"] or 1.0),
            "avg_innovation_score":       float(row["avg_innovation"] or 0.0),
        })

    # Register blockchain provenance for leaderboard snapshot
    try:
        import hashlib
        import json
        from tools.provenance.registry import register_citation
        from tools.blockchain.chain_anchor import ChainAnchor

        payload = {"tournament_id": tournament_id, "leaderboard": leaderboard}
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        source_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        reg_id = register_citation(
            citation_type="canvas_ai",
            source_table="gd_ai_leaderboard",
            source_record_id=f"tournament-{tournament_id}",
            source_hash=source_hash,
            source_doc="gameday-leaderboard",
            project_id=f"tournament-{tournament_id}",
        )
        if reg_id:
            ChainAnchor().anchor_provenance([reg_id])
            log.info("[Leaderboard] Provenance registered: %s", reg_id)
    except Exception as exc:
        log.debug("[Leaderboard] Provenance registration skipped: %s", exc)

    return leaderboard


def get_round_scores(tournament_id: int) -> list[dict]:
    """Return per-round scores for all teams (sparkline data)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT r.round_num, e.team_key, e.total_score,
                  e.quality_score, e.innovation_score, e.ethics_score,
                  e.adversarial_score, e.compliance_score,
                  e.routed_to_suggested, e.ethics_blocked
           FROM gd_ai_judge_evals e
           JOIN gd_ai_rounds r ON r.id = e.round_id
           WHERE r.tournament_id = ?
           ORDER BY r.round_num, e.total_score DESC""",
        (tournament_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_team_detail(tournament_id: int, team_key: str) -> dict[str, Any]:
    """Return full team stats including per-round breakdown and recent artifacts."""
    conn = get_connection()

    # Team row
    team = conn.execute(
        "SELECT * FROM gd_ai_teams WHERE tournament_id = ? AND team_key = ?",
        (tournament_id, team_key),
    ).fetchone()
    if not team:
        return {}

    # Per-round evals
    evals = conn.execute(
        """SELECT r.round_num, e.*
           FROM gd_ai_judge_evals e
           JOIN gd_ai_rounds r ON r.id = e.round_id
           WHERE r.tournament_id = ? AND e.team_key = ?
           ORDER BY r.round_num""",
        (tournament_id, team_key),
    ).fetchall()

    # Recent artifacts (last 20)
    artifacts = conn.execute(
        """SELECT a.member_role, a.artifact_type, a.tokens_used,
                  a.model_used, a.latency_ms, a.created_at,
                  SUBSTR(a.content, 1, 300) AS content_excerpt
           FROM gd_ai_artifacts a
           JOIN gd_ai_rounds r ON r.id = a.round_id
           WHERE r.tournament_id = ? AND a.team_key = ?
           ORDER BY a.created_at DESC LIMIT 20""",
        (tournament_id, team_key),
    ).fetchall()

    return {
        "team":      dict(team),
        "evals":     [dict(e) for e in evals],
        "artifacts": [dict(a) for a in artifacts],
    }
