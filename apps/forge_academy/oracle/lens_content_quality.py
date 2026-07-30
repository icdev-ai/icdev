# CUI // SP-CTI
"""Oracle Lens — Content Quality.

Signals that a mission or step may need curriculum revision:
  - hint_overuse:         step where >50% of completions used hints
  - step_high_attempts:   step where avg attempts > 3.0 across users
  - mission_low_completion: mission with < 40% completion rate (min 5 starters)
"""

from __future__ import annotations

from typing import Any

from tools.db.storage import get_connection

from .base_lens import BaseLens, OraclePrediction

LENS_ID = "content_quality"
LENS_NAME = "Content Quality"

_HINT_OVERUSE_PCT = 0.50
_HIGH_ATTEMPTS_AVG = 3.0
_LOW_COMPLETION_PCT = 0.40
_MIN_SAMPLE = 5


class LensContentQuality(BaseLens):
    name = LENS_ID
    description = "Detects steps/missions with hint overuse, high attempts, or low completion"

    def analyze(self) -> dict[str, Any]:
        conn = get_connection()

        # Steps with high hint usage
        hint_steps = conn.execute(
            """
            SELECT ms.id AS step_id, ms.title AS step_title, ms.mission_id,
                   COUNT(*) AS total_attempts,
                   SUM(CASE WHEN sp.hints_used > 0 THEN 1 ELSE 0 END) AS hint_count,
                   AVG(sp.hints_used) AS avg_hints,
                   AVG(sp.score) AS avg_score,
                   m.slug AS mission_slug, m.title AS mission_title
            FROM fa_step_progress sp
            JOIN fa_mission_steps ms ON ms.id = sp.step_id
            JOIN fa_missions m ON m.id = ms.mission_id
            WHERE sp.status = 'completed'
            GROUP BY ms.id
            HAVING total_attempts >= %s AND CAST(hint_count AS REAL)/total_attempts > %s
            ORDER BY CAST(hint_count AS REAL)/total_attempts DESC
            LIMIT 20
            """,
            (_MIN_SAMPLE, _HINT_OVERUSE_PCT),
        ).fetchall()

        # Steps with high retry count
        retry_steps = conn.execute(
            """
            SELECT ms.id AS step_id, ms.title AS step_title, ms.mission_id,
                   COUNT(DISTINCT sp.user_id) AS unique_users,
                   AVG(CAST(
                       (SELECT COUNT(*) FROM fa_step_progress sp2
                        WHERE sp2.user_id=sp.user_id AND sp2.step_id=sp.step_id)
                       AS REAL)) AS avg_attempts,
                   m.slug AS mission_slug, m.title AS mission_title
            FROM fa_step_progress sp
            JOIN fa_mission_steps ms ON ms.id = sp.step_id
            JOIN fa_missions m ON m.id = ms.mission_id
            GROUP BY ms.id
            HAVING unique_users >= %s AND avg_attempts > %s
            ORDER BY avg_attempts DESC
            LIMIT 20
            """,
            (_MIN_SAMPLE, _HIGH_ATTEMPTS_AVG),
        ).fetchall()

        # Missions with low completion rate
        low_completion = conn.execute(
            """
            SELECT m.id, m.slug, m.title, m.tier,
                   COUNT(*) AS total_starters,
                   SUM(CASE WHEN mp.status='completed' THEN 1 ELSE 0 END) AS completed_count
            FROM fa_mission_progress mp
            JOIN fa_missions m ON m.id = mp.mission_id
            WHERE mp.status IN ('in_progress','completed')
            GROUP BY m.id
            HAVING total_starters >= %s
               AND CAST(completed_count AS REAL)/total_starters < %s
            ORDER BY CAST(completed_count AS REAL)/total_starters ASC
            LIMIT 20
            """,
            (_MIN_SAMPLE, _LOW_COMPLETION_PCT),
        ).fetchall()

        return {
            "hint_steps": [dict(r) for r in hint_steps],
            "retry_steps": [dict(r) for r in retry_steps],
            "low_completion": [dict(r) for r in low_completion],
        }

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        predictions: list[OraclePrediction] = []

        for s in analysis.get("hint_steps", []):
            hint_pct = s["hint_count"] / max(s["total_attempts"], 1)
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Hint overuse: {s['step_title']}",
                description=(
                    f"{hint_pct:.0%} of completions on '{s['step_title']}' used hints "
                    f"(avg {s['avg_hints']:.1f} hints/attempt). Step may be unclear or too hard."
                ),
                confidence=min(0.90, 0.55 + hint_pct * 0.40),
                severity="warning" if hint_pct < 0.75 else "critical",
                category="hint_overuse",
                data={
                    "step_id": s["step_id"], "mission_slug": s["mission_slug"],
                    "hint_pct": round(hint_pct, 3), "avg_hints": round(s["avg_hints"], 2),
                    "subject_type": "step", "subject_id": str(s["step_id"]),
                    "prediction_type": "hint_overuse",
                    "horizon_days": 14,
                },
                recommendations=[
                    "Rewrite the step instructions for clarity.",
                    "Add an inline hint scaffold before requiring a full solution.",
                ],
            ))

        for s in analysis.get("retry_steps", []):
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"High retries: {s['step_title']}",
                description=(
                    f"'{s['step_title']}' averages {s['avg_attempts']:.1f} attempts across "
                    f"{s['unique_users']} learners. Possible difficulty spike."
                ),
                confidence=min(0.85, 0.50 + min(s["avg_attempts"] - _HIGH_ATTEMPTS_AVG, 3) * 0.12),
                severity="warning",
                category="step_high_attempts",
                data={
                    "step_id": s["step_id"], "mission_slug": s["mission_slug"],
                    "avg_attempts": round(s["avg_attempts"], 2), "unique_users": s["unique_users"],
                    "subject_type": "step", "subject_id": str(s["step_id"]),
                    "prediction_type": "step_high_attempts",
                    "horizon_days": 14,
                },
                recommendations=[
                    "Lower the cognitive load: split into sub-steps.",
                    "Add a worked example before the challenge prompt.",
                ],
            ))

        for m in analysis.get("low_completion", []):
            completion_pct = m["completed_count"] / max(m["total_starters"], 1)
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Low completion: {m['title']} ({completion_pct:.0%})",
                description=(
                    f"Only {completion_pct:.0%} of {m['total_starters']} learners who started "
                    f"'{m['title']}' (Tier {m['tier']}) have completed it."
                ),
                confidence=min(0.88, 0.60 + (1 - completion_pct) * 0.35),
                severity="critical" if completion_pct < 0.20 else "warning",
                category="mission_low_completion",
                data={
                    "mission_id": m["id"], "mission_slug": m["slug"],
                    "completion_pct": round(completion_pct, 3),
                    "total_starters": m["total_starters"],
                    "subject_type": "mission", "subject_id": str(m["id"]),
                    "prediction_type": "mission_low_completion",
                    "horizon_days": 30,
                },
                recommendations=[
                    "Review mission step count — consider splitting into two shorter missions.",
                    "Add a mid-mission checkpoint with XP reward.",
                ],
            ))

        return predictions

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        return predictions
