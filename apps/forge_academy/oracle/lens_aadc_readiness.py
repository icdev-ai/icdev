# CUI // SP-CTI
"""Oracle Lens — AADC Readiness.

Surfaces two prediction types:
  - aadc_gap:       Tier 1 graduate whose role has an AADC mission but who has never
                    opened an AADC canvas. Suggests the appropriate AADC mission.
  - aadc_design_stale: User started an AADC mission but hasn't touched their design
                    in >3 days (design exists but assessment score is 0 or missing).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from tools.db.storage import get_connection

from .base_lens import BaseLens, OraclePrediction

LENS_ID = "aadc_readiness"
LENS_NAME = "AADC Readiness"

_STALE_DAYS = 3
_MIN_TIER1_XP = 600   # proxy for Tier 1 completion

# Maps role → AADC mission slug that best fits that role
_ROLE_AADC_MISSION = {
    "secops_eng": "m-secops-05-aadc-threat-model",
    "isso":       "m-secops-05-aadc-threat-model",
    "issm":       "m-issm-02-aadc-ato-design",
    "ciso":       "m-ciso-02-aadc-governance",
}


class LensAADCReadiness(BaseLens):
    name = LENS_ID
    description = "Detects Tier-1 graduates who haven't explored AADC and stale canvas designs"

    def analyze(self) -> dict[str, Any]:
        conn = get_connection()
        now = datetime.now(timezone.utc)
        stale_cutoff = (now - timedelta(days=_STALE_DAYS)).isoformat()

        # 1. Users with relevant roles who have enough XP but no AADC mission started
        gap_users = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role, u.xp
            FROM fa_users u
            WHERE u.role IN ('secops_eng','isso','issm','ciso')
              AND u.xp >= %s
              AND NOT EXISTS (
                  SELECT 1 FROM fa_mission_progress mp
                  JOIN fa_missions m ON m.id = mp.mission_id
                  WHERE mp.user_id = u.id
                    AND m.slug IN ('m-secops-05-aadc-threat-model',
                                   'm-issm-02-aadc-ato-design',
                                   'm-ciso-02-aadc-governance')
              )
            """,
            (_MIN_TIER1_XP,),
        ).fetchall()

        # 2. Users with a started AADC mission whose design hasn't been touched recently
        stale_designs: list[dict] = []
        try:
            stale_designs = conn.execute(
                """
                SELECT u.id AS user_id, u.username, u.display_name,
                       d.design_id, d.name AS design_name, d.updated_at
                FROM aadc_designs d
                JOIN fa_users u ON u.username = d.created_by
                WHERE d.updated_at < %s
                  AND (d.last_score IS NULL OR d.last_score < 50)
                """,
                (stale_cutoff,),
            ).fetchall()
        except Exception:
            pass  # aadc_designs may not have last_score column in all envs

        return {
            "gap_users": [dict(r) for r in gap_users],
            "stale_designs": [dict(r) for r in stale_designs],
        }

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        """BaseLens phase 2 — this lens produces final predictions directly."""
        return self.generate_predictions(analysis)

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        """BaseLens phase 3 — recommendations already inline in each prediction."""
        return predictions

    def generate_predictions(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        predictions: list[OraclePrediction] = []

        for user in analysis.get("gap_users", []):
            role = user.get("role", "")
            mission_slug = _ROLE_AADC_MISSION.get(role, "m-secops-05-aadc-threat-model")
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                category="aadc_gap",
                severity="medium",
                confidence=0.82,
                description=(
                    f"{user['display_name']} ({role}) has completed Tier 1 "
                    f"({user['xp']} XP) but hasn't started any AADC design missions. "
                    f"Recommend: {mission_slug}."
                ),
                data={
                    "subject_type": "user",
                    "subject_id": str(user["id"]),
                    "prediction_type": "aadc_gap",
                    "recommended_mission": mission_slug,
                    "user_role": role,
                    "horizon_days": 7,
                },
            ))

        for design in analysis.get("stale_designs", []):
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                category="aadc_design_stale",
                severity="low",
                confidence=0.75,
                description=(
                    f"{design['display_name']} has a stale AADC design "
                    f"'{design['design_name']}' (last updated {design['updated_at']}) "
                    f"with a low compliance score. A nudge may re-engage them."
                ),
                data={
                    "subject_type": "user",
                    "subject_id": str(design["user_id"]),
                    "prediction_type": "aadc_design_stale",
                    "design_id": design["design_id"],
                    "horizon_days": 3,
                },
            ))

        return predictions
