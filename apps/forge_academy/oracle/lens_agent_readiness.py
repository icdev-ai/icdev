# CUI // SP-CTI
"""Oracle Lens: Agent Readiness Score — surface org-level pillar gaps from checker runs."""
from __future__ import annotations

import json
import logging
from typing import Any

from tools.db.storage import get_connection

from .base_lens import BaseLens, OraclePrediction

_log = logging.getLogger(__name__)

LENS_ID = "agent_readiness_score"
LENS_NAME = "Agent Readiness Score"

_ORG_THRESHOLD = 60.0  # pillars below this % are flagged

# fa_step_progress has no mission_slug / step_type / metadata_json columns; those
# live on fa_missions.slug, fa_mission_steps.step_type, and the step submission
# (fa_step_progress.submission) respectively. Join through the steps→missions
# chain and read the pillar-score JSON out of the submission. (penta-fix-02)
_READINESS_STEPS_SQL = """
    SELECT fsp.user_id, fsp.submission
    FROM fa_step_progress fsp
    JOIN fa_mission_steps fms ON fms.id = fsp.step_id
    JOIN fa_missions fm ON fm.id = fms.mission_id
    WHERE fm.slug LIKE 'm-readiness-%'
      AND fms.step_type = 'agent_readiness'
      AND fsp.status = 'completed'
    ORDER BY fsp.completed_at DESC
"""

_ORG_READINESS_UPSERT = """
    INSERT INTO fa_org_readiness (metric_name, metric_value, details_json, computed_at)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (metric_name) DO UPDATE SET
        metric_value = excluded.metric_value,
        details_json = excluded.details_json,
        computed_at  = excluded.computed_at
"""


class AgentReadinessLens(BaseLens):
    """Detect org-level agent readiness pillar gaps and update fa_org_readiness."""

    name = LENS_ID
    description = "Aggregates pillar scores from readiness-check steps; surfaces weakest org pillars."

    def analyze(self) -> dict[str, Any]:
        conn = get_connection()
        rows = conn.execute(_READINESS_STEPS_SQL).fetchall()
        if not rows:
            return {"pillar_averages": {}, "sample_size": 0, "overall_avg": 0.0}

        pillar_scores: dict[str, list[float]] = {}
        for row in rows:
            meta = json.loads(row[1] or "{}")
            for pillar_id, score_data in meta.get("pillar_scores", {}).items():
                pct = float(score_data.get("percentage", 0))
                pillar_scores.setdefault(pillar_id, []).append(pct)

        if not pillar_scores:
            return {"pillar_averages": {}, "sample_size": len(rows), "overall_avg": 0.0}

        avg_scores = {pid: sum(v) / len(v) for pid, v in pillar_scores.items()}
        overall_avg = sum(avg_scores.values()) / len(avg_scores)

        # Persist to org_readiness table (best-effort)
        try:
            from datetime import datetime, timezone
            conn.execute(
                _ORG_READINESS_UPSERT,
                (
                    "agent_readiness_avg",
                    round(overall_avg, 1),
                    json.dumps({"pillar_averages": avg_scores, "sample_size": len(rows)}),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        except Exception as exc:
            _log.debug("org_readiness upsert skipped: %s", exc)

        return {
            "pillar_averages": avg_scores,
            "sample_size": len(rows),
            "overall_avg": overall_avg,
        }

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        predictions: list[OraclePrediction] = []
        avg_scores: dict[str, float] = analysis.get("pillar_averages", {})
        sample_size: int = analysis.get("sample_size", 0)
        if not avg_scores or sample_size == 0:
            return predictions

        weakest = sorted(avg_scores.items(), key=lambda x: x[1])[:3]
        for pillar_id, avg_pct in weakest:
            if avg_pct >= _ORG_THRESHOLD:
                continue
            confidence = min(0.95, 0.70 + (sample_size * 0.01))
            severity = "critical" if avg_pct < 40 else "warning"
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Org readiness gap: pillar '{pillar_id}' at {avg_pct:.1f}%",
                description=(
                    f"Org-wide agent readiness pillar '{pillar_id}' averages {avg_pct:.1f}% "
                    f"across {sample_size} learner check(s) — below the {_ORG_THRESHOLD}% threshold."
                ),
                confidence=round(confidence, 3),
                severity=severity,
                category="agent_readiness",
                data={
                    "pillar_id": pillar_id,
                    "avg_score": round(avg_pct, 1),
                    "sample_size": sample_size,
                    "subject_type": "org",
                    "subject_id": "org",
                    "prediction_type": "readiness_pillar_gap",
                    "horizon_days": 30,
                },
                recommendations=[
                    f"Promote readiness remediation missions for pillar '{pillar_id}' to all engineers.",
                    "Schedule a focused sprint on agent readiness gap closure.",
                ],
            ))
        return predictions

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        return predictions
