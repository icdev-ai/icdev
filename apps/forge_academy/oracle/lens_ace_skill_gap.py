# CUI // SP-CTI
"""Oracle Lens: ACE Skill Gap — detect Tier-2 learners with no ACE co-worker engagement."""
from __future__ import annotations

from typing import Any

from tools.db.storage import get_connection

from .base_lens import BaseLens, OraclePrediction

LENS_ID = "ace_skill_gap"
LENS_NAME = "ACE Skill Gap"

ACE_MISSION_SLUGS = (
    "m-ace-01-roles-delegation",
    "m-ace-02-creator-verifier",
    "m-ace-03-multi-role-pipeline",
    "m-ace-capstone",
)

# fa_mission_progress keys missions by mission_id (there is no mission_slug
# column); join through fa_missions to resolve tier / slug. (penta-fix-02)
_TIER2_COMPLETE_SQL = """
    SELECT DISTINCT fp.user_id, u.username, u.display_name
    FROM fa_mission_progress fp
    JOIN fa_missions fm ON fm.id = fp.mission_id
    JOIN fa_users u ON u.id = fp.user_id
    WHERE fm.tier = 2
      AND fp.status = 'completed'
    GROUP BY fp.user_id, u.username, u.display_name
    HAVING COUNT(DISTINCT fp.mission_id) >= 3
"""

_ACE_STARTED_SQL = """
    SELECT DISTINCT fp.user_id
    FROM fa_mission_progress fp
    JOIN fa_missions fm ON fm.id = fp.mission_id
    WHERE fm.slug IN ({placeholders})
      AND fp.status IN ('in_progress', 'completed')
"""


class ACESkillGapLens(BaseLens):
    """Identify Tier 2 learners with no ACE mission engagement."""

    name = LENS_ID
    description = "Flags learners who completed 3+ Tier 2 missions but never started an ACE co-worker mission."

    def analyze(self) -> dict[str, Any]:
        conn = get_connection()
        tier2_rows = conn.execute(_TIER2_COMPLETE_SQL).fetchall()
        if not tier2_rows:
            return {"gap_users": []}

        placeholders = ",".join("%s" for _ in ACE_MISSION_SLUGS)
        ace_started = {
            row[0]
            for row in conn.execute(
                _ACE_STARTED_SQL.format(placeholders=placeholders),
                list(ACE_MISSION_SLUGS),
            ).fetchall()
        }

        gap_users = [
            {"user_id": row[0], "username": row[1], "display_name": row[2]}
            for row in tier2_rows
            if row[0] not in ace_started
        ]
        return {"gap_users": gap_users}

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        predictions: list[OraclePrediction] = []
        for u in analysis.get("gap_users", []):
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"ACE skill gap: {u['display_name']}",
                description=(
                    f"{u['display_name']} has completed 3+ Tier 2 missions but has not started "
                    "any ACE co-worker mission. Agentic team skills are a critical gap."
                ),
                confidence=0.82,
                severity="warning",
                category="skill_gap",
                data={
                    "user_id": u["user_id"],
                    "username": u["username"],
                    "subject_type": "user",
                    "subject_id": str(u["user_id"]),
                    "prediction_type": "ace_skill_gap",
                    "horizon_days": 14,
                },
                recommendations=[
                    "Recommend m-ace-01-roles-delegation as the next mission.",
                    "Highlight ACE co-worker track in the learner's dashboard.",
                ],
            ))
        return predictions

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        return predictions
