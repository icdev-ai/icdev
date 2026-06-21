# CUI // SP-CTI
"""NOVA SELA — Skill Evolution Genesis Reflex (weekly cadence).

Runs the GEPA-style text mutation loop across all icdev-* skills and
the top-5 most-used hardprompts.  Winners are proposed as oracle_predictions
(kanban suggested cards) for human review — NEVER auto-merged.

Risk tier: YELLOW (reads skill files, writes oracle_predictions).
Schedule: weekly (Saturday 03:00 UTC).
"""
from __future__ import annotations

from typing import Any

from tools.logging.icdev_logger import get_logger

LOG = get_logger(__name__)


def run(config: dict[str, Any], trust: Any) -> dict[str, Any]:
    """Execute the Skill Evolution Reflex."""
    from tools.evolution.artifact_evolver import evolve_all_skills

    dry_run: bool = config.get("dry_run", False)
    skill_limit: int = int(config.get("skill_limit", 10))

    LOG.info("[evolution] starting SELA skill evolution (dry_run=%s limit=%d)", dry_run, skill_limit)

    try:
        result = evolve_all_skills(dry_run=dry_run, limit=skill_limit)
    except Exception as exc:
        LOG.warning("[evolution] evolve_all_skills failed: %s", exc)
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": str(exc)},
        }

    promoted = sum(
        1 for r in result.get("results", {}).values()
        if isinstance(r, dict) and r.get("promoted")
    )
    processed = result.get("skills_processed", 0)

    LOG.info(
        "[evolution] processed %d skills; %d promoted for human review",
        processed, promoted,
    )

    return {
        "success": True,
        "metric_value": float(promoted),
        "details": {
            "skills_processed": processed,
            "skills_promoted": promoted,
            "dry_run": dry_run,
            "results": {
                k: {kk: vv for kk, vv in v.items() if kk != "winner_text"}
                for k, v in result.get("results", {}).items()
                if isinstance(v, dict)
            },
        },
    }
