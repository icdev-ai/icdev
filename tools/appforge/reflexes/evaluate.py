"""AppForge Evaluate Reflex — score and select the best challenge to build next.

GREEN tier. Picks the highest-scored 'discovered' challenge and promotes to 'selected'.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def run(config: Dict[str, Any], trust) -> Dict[str, Any]:
    """Select the top-scored 'discovered' challenge for building."""
    conn = get_connection()
    try:
        # Check if there's already a challenge in progress
        in_progress = conn.execute(
            "SELECT challenge_id, title FROM appforge_challenges WHERE status IN ('selected', 'building') LIMIT 1"
        ).fetchone()
        if in_progress:
            return {
                "success": True,
                "metric_value": 0.0,
                "details": {
                    "action": "skipped",
                    "reason": f"Challenge already in progress: {in_progress['title']}",
                },
            }

        # Select top challenge
        top = conn.execute(
            "SELECT * FROM appforge_challenges WHERE status = 'discovered' ORDER BY score DESC LIMIT 1"
        ).fetchone()
        if not top:
            return {
                "success": True,
                "metric_value": 0.0,
                "details": {"action": "none", "reason": "No discovered challenges available"},
            }

        # Promote to 'selected'
        conn.execute(
            "UPDATE appforge_challenges SET status = 'selected' WHERE challenge_id = %s",
            (top["challenge_id"],),
        )
        conn.commit()

        return {
            "success": True,
            "metric_value": top["score"],
            "details": {
                "action": "selected",
                "challenge_id": top["challenge_id"],
                "title": top["title"],
                "vertical": top["vertical"],
                "score": top["score"],
            },
        }
    finally:
        conn.close()
