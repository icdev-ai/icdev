# CUI // SP-CTI
"""AI GameDay League — database helpers for gd_ai_* tables."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from datetime import datetime, timezone

from tools.db.storage import get_connection

log = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Tournaments ────────────────────────────────────────────────────────────────

def create_tournament(
    name: str,
    round_count: int = 5,
    round_duration_minutes: int = 60,
    scenario_pack: str = "cyber_adversarial",
    config: dict | None = None,
) -> dict:
    conn = get_connection()
    row = conn.execute(
        """INSERT INTO gd_ai_tournaments
           (name, scenario_pack, status, round_count, round_duration_minutes,
            config_json, created_at)
           VALUES (%s, %s, 'pending', %s, %s, %s, %s)
           RETURNING *""",
        (name, scenario_pack, round_count, round_duration_minutes,
         json.dumps(config or {}), _now()),
    ).fetchone()
    conn.commit()
    return dict(row)


def get_tournament(tournament_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM gd_ai_tournaments WHERE id = %s", (tournament_id,)
    ).fetchone()
    return dict(row) if row else None


def update_tournament(tournament_id: int, **fields) -> None:
    allowed = {"status", "current_round", "started_at", "completed_at", "config_json"}
    cols = {k: v for k, v in fields.items() if k in allowed}
    if not cols:
        return
    set_clause = ", ".join(f"{k} = ?" for k in cols)
    conn = get_connection()
    conn.execute(
        f"UPDATE gd_ai_tournaments SET {set_clause} WHERE id = %s",
        (*cols.values(), tournament_id),
    )
    conn.commit()


def list_tournaments(limit: int = 20) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM gd_ai_tournaments ORDER BY created_at DESC LIMIT %s", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── Teams ──────────────────────────────────────────────────────────────────────

def seed_teams(tournament_id: int, team_defs: list[dict]) -> list[dict]:
    """Insert team rows from team registry definitions."""
    conn = get_connection()
    teams = []
    for td in team_defs:
        row = conn.execute(
            """INSERT INTO gd_ai_teams
               (tournament_id, team_key, name, domain, color)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (tournament_id, team_key) DO UPDATE
               SET name=EXCLUDED.name
               RETURNING *""",
            (tournament_id, td["key"], td["name"], td["domain"], td.get("color", "#6c6c80")),
        ).fetchone()
        teams.append(dict(row))
    conn.commit()
    return teams


def get_team(tournament_id: int, team_key: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM gd_ai_teams WHERE tournament_id = %s AND team_key = %s",
        (tournament_id, team_key),
    ).fetchone()
    return dict(row) if row else None


def update_team_scores(
    tournament_id: int,
    team_key: str,
    score_delta: int = 0,
    rounds_won_delta: int = 0,
    artifacts_suggested_delta: int = 0,
    training_pairs_delta: int = 0,
) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE gd_ai_teams
           SET total_score = total_score + %s,
               rounds_won  = rounds_won  + %s,
               artifacts_suggested = artifacts_suggested + %s,
               training_pairs_contributed = training_pairs_contributed + %s
           WHERE tournament_id = %s AND team_key = %s""",
        (score_delta, rounds_won_delta, artifacts_suggested_delta,
         training_pairs_delta, tournament_id, team_key),
    )
    conn.commit()


# ── Rounds ─────────────────────────────────────────────────────────────────────

def create_round(tournament_id: int, round_num: int, scenario: dict) -> dict:
    conn = get_connection()
    row = conn.execute(
        """INSERT INTO gd_ai_rounds
           (tournament_id, round_num, scenario_json, status, created_at)
           VALUES (%s, %s, %s, 'pending', %s)
           ON CONFLICT (tournament_id, round_num) DO UPDATE
           SET scenario_json = EXCLUDED.scenario_json
           RETURNING *""",
        (tournament_id, round_num, json.dumps(scenario), _now()),
    ).fetchone()
    conn.commit()
    return dict(row)


def get_round(tournament_id: int, round_num: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM gd_ai_rounds WHERE tournament_id = %s AND round_num = %s",
        (tournament_id, round_num),
    ).fetchone()
    return dict(row) if row else None


def update_round(round_id: int, **fields) -> None:
    allowed = {"status", "started_at", "completed_at"}
    cols = {k: v for k, v in fields.items() if k in allowed}
    if not cols:
        return
    set_clause = ", ".join(f"{k} = ?" for k in cols)
    conn = get_connection()
    conn.execute(
        f"UPDATE gd_ai_rounds SET {set_clause} WHERE id = %s",
        (*cols.values(), round_id),
    )
    conn.commit()


# ── Artifacts ──────────────────────────────────────────────────────────────────

def save_artifact(
    round_id: int,
    team_id: int,
    team_key: str,
    member_role: str,
    artifact_type: str,
    content: str,
    tokens_used: int = 0,
    model_used: str = "",
    latency_ms: int = 0,
) -> dict:
    conn = get_connection()
    row = conn.execute(
        """INSERT INTO gd_ai_artifacts
           (round_id, team_id, team_key, member_role, artifact_type,
            content, tokens_used, model_used, latency_ms, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (round_id, team_id, team_key, member_role, artifact_type,
         content, tokens_used, model_used, latency_ms, _now()),
    ).fetchone()
    conn.commit()
    artifact = dict(row)

    # Register blockchain provenance
    try:
        import hashlib
        import json
        from tools.provenance.registry import register_citation
        from tools.blockchain.chain_anchor import ChainAnchor

        payload = {
            "artifact_id": artifact["id"],
            "round_id": round_id,
            "team_key": team_key,
            "artifact_type": artifact_type,
            "model_used": model_used,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        source_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        reg_id = register_citation(
            citation_type="canvas_ai",
            source_table="gd_ai_artifacts",
            source_record_id=str(artifact["id"]),
            source_hash=source_hash,
            source_doc=f"gameday-artifact-{artifact_type}",
        )
        if reg_id:
            ChainAnchor().anchor_provenance([reg_id])
            log.info("[Artifact] Provenance registered: %s", reg_id)
    except Exception as exc:
        log.debug("[Artifact] Provenance registration skipped: %s", exc)

    return artifact


def get_round_artifacts(round_id: int, team_key: str | None = None) -> list[dict]:
    conn = get_connection()
    if team_key:
        rows = conn.execute(
            "SELECT * FROM gd_ai_artifacts WHERE round_id = %s AND team_key = %s ORDER BY created_at",
            (round_id, team_key),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM gd_ai_artifacts WHERE round_id = %s ORDER BY created_at",
            (round_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Judge Evaluations ──────────────────────────────────────────────────────────

def save_judge_eval(
    round_id: int,
    team_id: int,
    team_key: str,
    quality_score: float,
    innovation_score: float,
    ethics_score: float,
    adversarial_score: float,
    compliance_score: float,
    total_score: int,
    routed_to_suggested: int = 0,
    training_pairs_extracted: int = 0,
    ethics_blocked: int = 0,
    judge_notes: str | None = None,
) -> dict:
    conn = get_connection()
    row = conn.execute(
        """INSERT INTO gd_ai_judge_evals
           (round_id, team_id, team_key, quality_score, innovation_score,
            ethics_score, adversarial_score, compliance_score, total_score,
            routed_to_suggested, training_pairs_extracted, ethics_blocked,
            judge_notes, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (round_id, team_id) DO UPDATE
           SET total_score=EXCLUDED.total_score,
               ethics_blocked=EXCLUDED.ethics_blocked,
               routed_to_suggested=EXCLUDED.routed_to_suggested,
               judge_notes=EXCLUDED.judge_notes
           RETURNING *""",
        (round_id, team_id, team_key, quality_score, innovation_score,
         ethics_score, adversarial_score, compliance_score, total_score,
         routed_to_suggested, training_pairs_extracted, ethics_blocked,
         judge_notes, _now()),
    ).fetchone()
    conn.commit()
    return dict(row)


def get_round_evals(round_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM gd_ai_judge_evals WHERE round_id = %s ORDER BY total_score DESC",
        (round_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── LLMOps Events ──────────────────────────────────────────────────────────────

def log_llmops_event(
    tournament_id: int,
    round_id: int | None,
    team_key: str,
    member_role: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    error: str | None = None,
) -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO gd_ai_llmops_events
               (tournament_id, round_id, team_key, member_role, model,
                prompt_tokens, completion_tokens, latency_ms, error, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (tournament_id, round_id, team_key, member_role, model,
             prompt_tokens, completion_tokens, latency_ms, error, _now()),
        )
        conn.commit()
    except Exception as exc:
        log.warning("llmops_event write failed: %s", exc)


def get_tournament_llmops(tournament_id: int, limit: int = 200) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM gd_ai_llmops_events
           WHERE tournament_id = %s ORDER BY created_at DESC LIMIT %s""",
        (tournament_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Training Pairs ─────────────────────────────────────────────────────────────

def save_training_pair(
    round_id: int,
    team_key: str,
    member_role: str,
    prompt: str,
    completion: str,
    quality_score: float = 0.0,
    ft_dataset_id: str | None = None,
) -> dict:
    conn = get_connection()
    row = conn.execute(
        """INSERT INTO gd_ai_training_pairs
           (round_id, team_key, member_role, prompt, completion,
            quality_score, ft_dataset_id, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (round_id, team_key, member_role, prompt, completion,
         quality_score, ft_dataset_id, _now()),
    ).fetchone()
    conn.commit()
    return dict(row)


def count_pending_training_pairs(tournament_id: int) -> int:
    conn = get_connection()
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM gd_ai_training_pairs tp
           JOIN gd_ai_rounds r ON r.id = tp.round_id
           WHERE r.tournament_id = %s AND tp.ft_dataset_id IS NULL""",
        (tournament_id,),
    ).fetchone()
    return row["cnt"] if row else 0


# ── Leaderboard ────────────────────────────────────────────────────────────────

def upsert_leaderboard(
    tournament_id: int,
    team_id: int,
    team_key: str,
    rank: int,
    total_score: int,
    rounds_won: int,
    artifacts_suggested: int,
    training_pairs_contributed: int,
    avg_ethics_score: float,
    avg_innovation_score: float,
) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO gd_ai_leaderboard
           (tournament_id, team_id, team_key, rank, total_score, rounds_won,
            artifacts_suggested, training_pairs_contributed,
            avg_ethics_score, avg_innovation_score, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tournament_id, team_id) DO UPDATE
           SET rank=EXCLUDED.rank,
               total_score=EXCLUDED.total_score,
               rounds_won=EXCLUDED.rounds_won,
               artifacts_suggested=EXCLUDED.artifacts_suggested,
               training_pairs_contributed=EXCLUDED.training_pairs_contributed,
               avg_ethics_score=EXCLUDED.avg_ethics_score,
               avg_innovation_score=EXCLUDED.avg_innovation_score,
               updated_at=EXCLUDED.updated_at""",
        (tournament_id, team_id, team_key, rank, total_score, rounds_won,
         artifacts_suggested, training_pairs_contributed,
         avg_ethics_score, avg_innovation_score, _now()),
    )
    conn.commit()


def get_leaderboard(tournament_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT lb.*, t.name, t.domain, t.color
           FROM gd_ai_leaderboard lb
           JOIN gd_ai_teams t ON t.id = lb.team_id
           WHERE lb.tournament_id = %s
           ORDER BY lb.rank ASC""",
        (tournament_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_tournament() -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM gd_ai_tournaments ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None
