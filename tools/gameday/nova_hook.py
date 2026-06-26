# CUI // SP-CTI
"""GameDay NOVA Hook — persist winning reasoning patterns to NOVA's learning store.

After each tournament, extracts high-scoring team artifacts and submits them
to NOVA's ECHO layer as training signal for future co-worker improvement.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_log = logging.getLogger(__name__)

_SCORE_THRESHOLD = 75.0  # only persist artifacts scoring above this


def persist_tournament_learnings(tournament_data: dict) -> dict:
    """Extract and persist high-quality tournament artifacts to NOVA.

    Called after tournament completion. Submits winning reasoning patterns
    to NOVA's ECHO layer as labeled training pairs.

    Returns: {"pairs_submitted": int, "skipped": int, "nova_available": bool}
    """
    artifacts = tournament_data.get("artifacts", [])
    pairs_submitted = 0
    skipped = 0

    high_quality = [a for a in artifacts if a.get("judge_score", 0) >= _SCORE_THRESHOLD]

    if not high_quality:
        return {"pairs_submitted": 0, "skipped": len(artifacts), "nova_available": False}

    try:
        from tools.nova.echo import submit_training_pair
        nova_available = True
    except ImportError:
        _log.info("NOVA not available; falling back to ft_datasets table")
        nova_available = False
        try:
            _persist_to_ft_datasets(high_quality, tournament_data)
            pairs_submitted = len(high_quality)
        except Exception as exc:
            _log.debug("ft_datasets fallback failed: %s", exc)
        return {"pairs_submitted": pairs_submitted, "skipped": skipped, "nova_available": False}

    scenario_name = tournament_data.get("scenario_name", "unknown")

    for artifact in high_quality:
        try:
            pair = _build_training_pair(artifact, scenario_name)
            submit_training_pair(
                prompt=pair["prompt"],
                completion=pair["completion"],
                metadata={
                    "source": "gameday_tournament",
                    "tournament_id": tournament_data.get("tournament_id"),
                    "artifact_type": artifact.get("artifact_type"),
                    "judge_score": artifact.get("judge_score"),
                    "team_role": artifact.get("team_role"),
                },
                quality_score=artifact.get("judge_score", 0) / 100.0,
            )
            pairs_submitted += 1
        except Exception as exc:
            _log.warning("Failed to submit training pair: %s", exc)
            skipped += 1

    return {"pairs_submitted": pairs_submitted, "skipped": skipped, "nova_available": nova_available}


def _build_training_pair(artifact: dict, scenario_name: str) -> dict:
    """Convert a scored artifact into a training pair for NOVA."""
    team_role = artifact.get("team_role", "unknown")
    artifact_type = artifact.get("artifact_type", "unknown")
    content = artifact.get("content") or artifact.get("artifact_json") or {}

    content_str = json.dumps(content, indent=2) if isinstance(content, dict) else str(content)

    prompt = (
        f"You are a {team_role} team member in a GameDay exercise.\n"
        f"Scenario: {scenario_name}\n"
        f"Task: Produce a high-quality {artifact_type.replace('_', ' ')}.\n"
        f"Focus on correctness, completeness, and regulatory compliance."
    )

    return {"prompt": prompt, "completion": content_str[:2000]}


def _persist_to_ft_datasets(artifacts: list[dict], tournament_data: dict) -> None:
    """Fallback: write training pairs to gd_ai_training_pairs table."""
    from tools.db.storage import get_connection
    conn = get_connection()
    ts = datetime.now(timezone.utc).isoformat()
    for artifact in artifacts:
        pair = _build_training_pair(artifact, tournament_data.get("scenario_name", ""))
        conn.execute(
            """
            INSERT INTO gd_ai_training_pairs
                (tournament_id, team_key, artifact_type, prompt, completion, quality_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tournament_data.get("tournament_id", ""),
                artifact.get("team_key", ""),
                artifact.get("artifact_type", ""),
                pair["prompt"],
                pair["completion"],
                artifact.get("judge_score", 0) / 100.0,
                ts,
            ),
        )
    conn.commit()
