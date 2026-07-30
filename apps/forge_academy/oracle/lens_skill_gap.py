# CUI // SP-CTI
"""Oracle Lens — Skill Gap.

Identifies cohort-wide skill acquisition deficits:
  - cohort_skill_gap:       skill node unlocked by < 30% of eligible users
  - prerequisite_blocker:   skill node where its prereq is barely unlocked, blocking the whole tree
"""

from __future__ import annotations

from typing import Any

from tools.db.storage import get_connection

from .base_lens import BaseLens, OraclePrediction

LENS_ID = "skill_gap"
LENS_NAME = "Skill Gap"

_LOW_UNLOCK_PCT = 0.30
_BLOCKER_DELTA = 0.25   # prereq unlock rate - child unlock rate > this → prereq is the blocker
_MIN_USERS = 3


class LensSkillGap(BaseLens):
    name = LENS_ID
    description = "Detects cohort-wide skill unlock deficits and prerequisite blockers"

    def analyze(self) -> dict[str, Any]:
        conn = get_connection()

        total_users = conn.execute("SELECT COUNT(*) FROM fa_users WHERE role != 'unset'").fetchone()[0]
        if total_users < _MIN_USERS:
            return {"skill_gaps": [], "blocker_nodes": [], "total_users": total_users}

        # Skill nodes with low unlock rate among all activated users
        skill_gaps = conn.execute(
            """
            SELECT sn.id, sn.slug, sn.title, sn.tier, sn.prereq_ids_json,
                   COUNT(us.user_id) AS unlock_count
            FROM fa_skill_nodes sn
            LEFT JOIN fa_user_skills us ON us.skill_id = sn.id
            GROUP BY sn.id
            HAVING CAST(unlock_count AS REAL) / %s < %s
            ORDER BY CAST(unlock_count AS REAL) / %s ASC
            LIMIT 15
            """,
            (total_users, _LOW_UNLOCK_PCT, total_users),
        ).fetchall()

        # Find potential blocker prereqs: prereq has decent unlock rate but child is far lower
        blocker_nodes: list[dict] = []
        for node in skill_gaps:
            import json as _json
            prereq_ids = _json.loads(node["prereq_ids_json"] or "[]")
            for prereq_id in prereq_ids:
                prereq_row = conn.execute(
                    "SELECT sn.id, sn.slug, sn.title, COUNT(us.user_id) AS unlock_count "
                    "FROM fa_skill_nodes sn "
                    "LEFT JOIN fa_user_skills us ON us.skill_id = sn.id "
                    "WHERE sn.id = %s GROUP BY sn.id",
                    (prereq_id,),
                ).fetchone()
                if not prereq_row:
                    continue
                prereq_pct = prereq_row["unlock_count"] / total_users
                child_pct = node["unlock_count"] / total_users
                if prereq_pct - child_pct > _BLOCKER_DELTA:
                    blocker_nodes.append({
                        "child_id": node["id"],
                        "child_slug": node["slug"],
                        "child_title": node["title"],
                        "child_unlock_pct": child_pct,
                        "prereq_id": prereq_row["id"],
                        "prereq_slug": prereq_row["slug"],
                        "prereq_title": prereq_row["title"],
                        "prereq_unlock_pct": prereq_pct,
                        "delta": prereq_pct - child_pct,
                    })

        return {
            "skill_gaps": [dict(r) for r in skill_gaps],
            "blocker_nodes": blocker_nodes,
            "total_users": total_users,
        }

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        predictions: list[OraclePrediction] = []
        total = analysis.get("total_users", 0)
        if total < _MIN_USERS:
            return predictions

        for node in analysis.get("skill_gaps", []):
            unlock_pct = node["unlock_count"] / total
            severity = "critical" if unlock_pct < 0.10 else "warning"
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Skill gap: {node['title']} ({unlock_pct:.0%} unlocked)",
                description=(
                    f"Only {unlock_pct:.0%} of active learners ({node['unlock_count']}/{total}) "
                    f"have unlocked '{node['title']}' (Tier {node['tier']}). "
                    f"Cohort-wide skill gap detected."
                ),
                confidence=min(0.88, 0.60 + (1 - unlock_pct) * 0.30),
                severity=severity,
                category="cohort_skill_gap",
                data={
                    "skill_id": node["id"], "skill_slug": node["slug"],
                    "unlock_pct": round(unlock_pct, 3),
                    "unlock_count": node["unlock_count"], "total_users": total,
                    "subject_type": "skill_node", "subject_id": str(node["id"]),
                    "prediction_type": "cohort_skill_gap",
                    "horizon_days": 30,
                },
                recommendations=[
                    f"Add a dedicated Tier {node['tier']} warm-up mission targeting '{node['title']}'.",
                    "Review whether prerequisite missions are too gating.",
                ],
            ))

        for b in analysis.get("blocker_nodes", []):
            predictions.append(OraclePrediction(
                lens=LENS_ID,
                title=f"Prereq blocker: {b['prereq_title']} blocking {b['child_title']}",
                description=(
                    f"'{b['prereq_title']}' has {b['prereq_unlock_pct']:.0%} unlock rate but "
                    f"'{b['child_title']}' is only at {b['child_unlock_pct']:.0%} — "
                    f"{b['delta']:.0%} gap suggests the prerequisite is a progression bottleneck."
                ),
                confidence=min(0.82, 0.55 + b["delta"] * 0.50),
                severity="warning",
                category="prerequisite_blocker",
                data={
                    "prereq_id": b["prereq_id"], "prereq_slug": b["prereq_slug"],
                    "child_id": b["child_id"], "child_slug": b["child_slug"],
                    "delta": round(b["delta"], 3),
                    "subject_type": "skill_node", "subject_id": str(b["prereq_id"]),
                    "prediction_type": "prerequisite_blocker",
                    "horizon_days": 21,
                },
                recommendations=[
                    f"Create a bridging step in the '{b['prereq_title']}' mission.",
                    "Consider making the prerequisite optional for experienced roles.",
                ],
            ))

        return predictions

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        return predictions
