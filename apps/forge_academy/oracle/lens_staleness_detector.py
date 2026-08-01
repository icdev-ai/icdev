# CUI // SP-CTI
"""Oracle Lens — Curriculum Staleness Detector.

Flags Academy missions that may have outdated content based on:
  - Age since creation (>180 days with no updated_at touch → stale_candidate)
  - Stale + low engagement: created >180 days ago AND <3 completions in last 90 days
  - Draft missions that have sat in draft status >30 days without being activated

Produces fa_oracle_predictions with prediction_type='stale_content' or 'draft_limbo'.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from tools.db.storage import get_connection

from .base_lens import BaseLens, OraclePrediction

LENS_ID = "staleness_detector"
LENS_NAME = "Staleness Detector"

_STALE_DAYS = 180
_RECENT_COMPLETION_DAYS = 90
_RECENT_COMPLETION_MIN = 3
_DRAFT_LIMBO_DAYS = 30


def _days_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


class LensStalenesssDetector(BaseLens):
    name = LENS_ID
    description = "Detects missions with potentially outdated content or draft-limbo state"

    def analyze(self) -> dict[str, Any]:
        conn = get_connection()
        cutoff_stale = _days_ago(_STALE_DAYS)
        cutoff_recent = _days_ago(_RECENT_COMPLETION_DAYS)
        cutoff_draft  = _days_ago(_DRAFT_LIMBO_DAYS)

        # Missions older than STALE_DAYS with fewer than MIN completions in the last 90 days
        stale_low_engagement = conn.execute(
            """
            SELECT m.id, m.slug, m.title, m.tier, m.topic,
                   m.created_at, m.updated_at, m.status,
                   COUNT(mp.id) AS recent_completions
            FROM fa_missions m
            LEFT JOIN fa_mission_progress mp
              ON mp.mission_id = m.id
             AND mp.status = 'completed'
             AND mp.completed_at >= %s
            WHERE m.created_at <= %s
              AND m.is_active = 1
              AND (m.status IS NULL OR m.status = 'active')
            GROUP BY m.id
            HAVING recent_completions < %s
            ORDER BY m.created_at ASC
            LIMIT 30
            """,
            (cutoff_recent, cutoff_stale, _RECENT_COMPLETION_MIN),
        ).fetchall()

        # Draft missions that have sat without activation
        draft_limbo = conn.execute(
            """
            SELECT id, slug, title, tier, topic, created_at, updated_at
            FROM fa_missions
            WHERE status = 'draft'
              AND created_at <= %s
            ORDER BY created_at ASC
            LIMIT 20
            """,
            (cutoff_draft,),
        ).fetchall()

        return {
            "stale_low_engagement": [dict(r) for r in stale_low_engagement],
            "draft_limbo": [dict(r) for r in draft_limbo],
        }

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        predictions: list[OraclePrediction] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for m in analysis.get("stale_low_engagement", []):
            created = m.get("created_at") or now_iso
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - created_dt).days
            except Exception:
                age_days = _STALE_DAYS

            recent_c = m.get("recent_completions", 0)
            # Confidence increases with age and decreases if there's any engagement
            conf = min(0.92, 0.55 + min(age_days - _STALE_DAYS, 365) / 600.0)
            conf *= max(0.5, 1.0 - recent_c * 0.1)

            severity = "critical" if age_days > 365 else "warning"
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Stale content: {m['title']}",
                description=(
                    f"Mission '{m['title']}' (Tier {m['tier']}, {m.get('topic','')}) "
                    f"was created {age_days} days ago and has had only {recent_c} completion(s) "
                    f"in the last {_RECENT_COMPLETION_DAYS} days. Content may reference outdated APIs or tools."
                ),
                confidence=round(conf, 3),
                severity=severity,
                category="stale_content",
                data={
                    "mission_id": m["id"],
                    "mission_slug": m["slug"],
                    "age_days": age_days,
                    "recent_completions": recent_c,
                    "subject_type": "mission",
                    "subject_id": str(m["id"]),
                    "prediction_type": "stale_content",
                    "horizon_days": 30,
                },
                recommendations=[
                    f"Review '{m['slug']}' step content for outdated SDK versions or deprecated tool APIs.",
                    "Update content files and set fa_missions.updated_at to today's date.",
                    "Consider adding a Kanban task to the content-review backlog.",
                ],
            ))

        for m in analysis.get("draft_limbo", []):
            created = m.get("created_at") or now_iso
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - created_dt).days
            except Exception:
                age_days = _DRAFT_LIMBO_DAYS

            conf = min(0.88, 0.65 + min(age_days, 120) / 400.0)
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Draft limbo: {m['title']}",
                description=(
                    f"Mission '{m['title']}' (Tier {m['tier']}) has been in draft status "
                    f"for {age_days} days without being activated. Auto-generated drafts need human review."
                ),
                confidence=round(conf, 3),
                severity="warning",
                category="draft_limbo",
                data={
                    "mission_id": m["id"],
                    "mission_slug": m["slug"],
                    "age_days": age_days,
                    "subject_type": "mission",
                    "subject_id": str(m["id"]),
                    "prediction_type": "draft_limbo",
                    "horizon_days": 7,
                },
                recommendations=[
                    f"Review draft mission '{m['slug']}' and either activate or delete it.",
                    "If the draft was auto-generated by academy_reflex, verify its accuracy before activating.",
                ],
            ))

        return predictions

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        return predictions
