# CUI // SP-CTI
"""Mission Canvas — Conflict Detection & Resolution wrapper.

Wraps tools.filesync.conflict_resolver to detect and surface
conflicts across mission data, policies, or configurations.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from typing import Optional

logger = get_logger("icdev.mission_canvas.conflict_resolver")


def detect_conflicts(
    mission_id: str,
    sources: list[dict],
    rules: Optional[list[str]] = None,
) -> dict:
    """Detect conflicts between mission data sources or policy documents.

    Each source dict should have keys: id, content, content_type.
    Returns conflicts with severity and suggested resolution.
    """
    try:
        from tools.filesync.conflict_resolver import resolve_conflicts

        actions = [
            {
                "source_id": s.get("id"),
                "target_id": s.get("id"),
                "content_type": s.get("content_type", "text"),
                "strategy": rules[0] if rules else "source_wins",
            }
            for s in sources
        ]
        conflicts = resolve_conflicts(actions=actions)
        return {
            "mission_id": mission_id,
            "conflicts": conflicts,
            "count": len(conflicts),
            "status": "ok",
        }
    except Exception as exc:
        logger.warning("Conflict detection failed: %s", exc)
        return {
            "mission_id": mission_id,
            "conflicts": [],
            "count": 0,
            "status": "error",
            "error": str(exc),
        }


def resolve_conflict(mission_id: str, conflict_id: str, strategy: str = "source_wins") -> dict:
    """Apply a resolution strategy to a detected conflict."""
    try:
        from tools.filesync.conflict_resolver import resolve_conflicts

        result = resolve_conflicts(
            actions=[{"source_id": conflict_id, "target_id": conflict_id, "strategy": strategy}],
            strategy=strategy,
        )
        return {
            "mission_id": mission_id,
            "conflict_id": conflict_id,
            "result": result,
            "status": "resolved",
        }
    except Exception as exc:
        logger.warning("Conflict resolution failed: %s", exc)
        return {
            "mission_id": mission_id,
            "conflict_id": conflict_id,
            "status": "error",
            "error": str(exc),
        }
