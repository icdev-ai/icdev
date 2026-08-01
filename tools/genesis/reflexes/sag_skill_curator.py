# CUI // SP-CTI
"""Genesis Reflex — SAG Skill Curator (sag-skl-01).

Curates the auto-generated skills the standalone agent has promoted into
``.agents/skills/icdev-auto-*/``. Each cadence it runs
:func:`tools.agent_runtime.skills_lifecycle.curate`, which **archives (never
deletes)** idle, unpinned auto-skills after N days — the SKILL.md is moved to
``.agents/skills/_archive/`` and the ``sag_skill_registry`` status flips to
``archived``. Pinned skills are retained indefinitely.

dry_run defaults TRUE (report only) — flip via the reflex config
``args/genesis_config.yaml`` (or ctx) after reviewing what it would archive. This
reflex neither generates nor promotes skills; promotion stays strictly HITL.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

from typing import Any, Dict

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.genesis.reflexes.sag_skill_curator")

CADENCE_HOURS: int = 24
_DEFAULT_ARCHIVE_DAYS = 30


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Archive idle, unpinned auto-skills. Reflex contract: success/metric/details."""
    result: Dict[str, Any] = {"cadence_hours": CADENCE_HOURS, "status": "ok"}
    try:
        from tools.agent_runtime.skills_lifecycle import curate

        days = int(ctx.get("archive_after_days", _DEFAULT_ARCHIVE_DAYS))
        dry_run = bool(ctx.get("dry_run", True))
        report = curate(archive_after_days=days, dry_run=dry_run, conn=conn)
        result["success"] = True
        result["metric_value"] = float(len(report.get("archived", [])))
        result["details"] = {
            "dry_run": dry_run,
            "checked": report.get("checked", 0),
            "archived": report.get("archived", []),
            "retained_pinned": report.get("retained_pinned", []),
        }
        return result
    except Exception as exc:  # noqa: BLE001 — never wedge the daemon
        logger.warning("sag_skill_curator reflex error: %s", exc)
        result["status"] = "error"
        result["success"] = False
        result["metric_value"] = 0.0
        result["details"] = {"error": str(exc)}
        return result


if __name__ == "__main__":
    import json as _json

    print(_json.dumps(run({}), indent=2))
