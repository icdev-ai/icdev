# CUI // SP-CTI
"""Oracle Lens — Learner Risk.

Detects users who are at risk of disengaging:
  - learner_at_risk: inactive for >7 days with incomplete missions
  - streak_break: streak previously ≥3 days, now 0 (lapsed within 48h window)
  - retry_storm: user with 3+ failed attempts on the same step today
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from tools.db.storage import get_connection

from .base_lens import BaseLens, OraclePrediction

LENS_ID = "learner_risk"
LENS_NAME = "Learner Risk"

_INACTIVE_WARN_DAYS = 7
_INACTIVE_CRIT_DAYS = 14
_RETRY_STORM_THRESHOLD = 3


class LensLearnerRisk(BaseLens):
    name = LENS_ID
    description = "Detects at-risk learners: inactivity, streak breaks, retry storms"

    def analyze(self) -> dict[str, Any]:
        conn = get_connection()
        now = datetime.now(timezone.utc)
        cutoff_warn = (now - timedelta(days=_INACTIVE_WARN_DAYS)).isoformat()
        cutoff_crit = (now - timedelta(days=_INACTIVE_CRIT_DAYS)).isoformat()

        # Users with at least one incomplete mission and no recent activity
        inactive_warn = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.last_active, u.streak_days,
                   COUNT(mp.id) AS incomplete_count
            FROM fa_users u
            LEFT JOIN fa_mission_progress mp
                   ON mp.user_id = u.id AND mp.status IN ('in_progress','not_started')
            WHERE u.last_active IS NOT NULL
              AND u.last_active < %s
              AND u.last_active >= %s
            GROUP BY u.id
            HAVING incomplete_count > 0
            """,
            (cutoff_warn, cutoff_crit),
        ).fetchall()

        inactive_crit = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.last_active, u.streak_days,
                   COUNT(mp.id) AS incomplete_count
            FROM fa_users u
            LEFT JOIN fa_mission_progress mp
                   ON mp.user_id = u.id AND mp.status IN ('in_progress','not_started')
            WHERE u.last_active IS NOT NULL
              AND u.last_active < %s
            GROUP BY u.id
            HAVING incomplete_count > 0
            """,
            (cutoff_crit,),
        ).fetchall()

        # Retry storms: same step, 3+ failed attempts today
        today = now.strftime("%Y-%m-%d")
        retry_storms = conn.execute(
            """
            SELECT sp.user_id, sp.step_id, COUNT(*) AS attempt_count,
                   u.username, u.display_name
            FROM fa_step_progress sp
            JOIN fa_users u ON u.id = sp.user_id
            WHERE sp.started_at >= %s
              AND sp.status = 'failed'
            GROUP BY sp.user_id, sp.step_id
            HAVING attempt_count >= %s
            """,
            (today, _RETRY_STORM_THRESHOLD),
        ).fetchall()

        return {
            "inactive_warn": [dict(r) for r in inactive_warn],
            "inactive_crit": [dict(r) for r in inactive_crit],
            "retry_storms": [dict(r) for r in retry_storms],
        }

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        predictions: list[OraclePrediction] = []

        for u in analysis.get("inactive_crit", []):
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Learner at risk: {u['display_name']}",
                description=(
                    f"{u['display_name']} has been inactive for over {_INACTIVE_CRIT_DAYS} days "
                    f"with {u['incomplete_count']} incomplete mission(s). High churn risk."
                ),
                confidence=min(0.95, 0.70 + u["incomplete_count"] * 0.05),
                severity="critical",
                category="learner_at_risk",
                data={
                    "user_id": u["id"], "username": u["username"],
                    "last_active": u["last_active"],
                    "incomplete_count": u["incomplete_count"],
                    "subject_type": "user", "subject_id": str(u["id"]),
                    "prediction_type": "learner_at_risk",
                    "horizon_days": 7,
                },
                recommendations=[
                    "Send a re-engagement prompt via FORGE Sensei.",
                    "Assign an easier warm-up mission in their role track.",
                ],
            ))

        for u in analysis.get("inactive_warn", []):
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Engagement drop: {u['display_name']}",
                description=(
                    f"{u['display_name']} inactive 7–{_INACTIVE_CRIT_DAYS} days with "
                    f"{u['incomplete_count']} incomplete mission(s). Streak: {u['streak_days']} days."
                ),
                confidence=0.55 + u["incomplete_count"] * 0.04,
                severity="warning",
                category="learner_at_risk",
                data={
                    "user_id": u["id"], "username": u["username"],
                    "last_active": u["last_active"],
                    "incomplete_count": u["incomplete_count"],
                    "subject_type": "user", "subject_id": str(u["id"]),
                    "prediction_type": "learner_at_risk",
                    "horizon_days": 14,
                },
                recommendations=["Nudge with a daily login bonus reminder."],
            ))

        for r in analysis.get("retry_storms", []):
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Retry storm: {r['display_name']} on step {r['step_id']}",
                description=(
                    f"{r['display_name']} has failed step {r['step_id']} "
                    f"{r['attempt_count']} times today. Possible content barrier."
                ),
                confidence=min(0.90, 0.60 + r["attempt_count"] * 0.06),
                severity="warning",
                category="retry_storm",
                data={
                    "user_id": r["user_id"], "username": r["username"],
                    "step_id": r["step_id"], "attempt_count": r["attempt_count"],
                    "subject_type": "user", "subject_id": str(r["user_id"]),
                    "prediction_type": "retry_storm",
                    "horizon_days": 1,
                },
                recommendations=[
                    "Trigger FORGE Sensei proactive hint for this step.",
                    "Flag step for content quality review.",
                ],
            ))

        return predictions

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        # Recommendations already embedded during score() — no additional enrichment needed.
        return predictions
