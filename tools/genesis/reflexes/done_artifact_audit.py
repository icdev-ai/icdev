# CUI // SP-CTI
"""Genesis SUPPORT reflex — done-artifact audit over projects-in-flight.

Runs the done-artifact auditor on a cadence so every batch / project-in-flight
is continuously checked: do tasks flagged `done` actually have their claimed
artifacts on the working tree? Born from the ACE incident (42/42 "done" but
mostly unbuilt). Reports only — never mutates task status (the live scheduler
would re-dispatch and recreate the divergence).

Contract: run(config, trust) -> {"success", "metric_value", "details"} where
metric_value is the count of done-tasks flagged with missing artifacts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_BASE = Path(__file__).resolve().parents[3]
_PROJECTS_YAML = _BASE / "args" / "projects.yaml"


def _project_keys() -> list[str]:
    """Project keys currently in flight (registered in args/projects.yaml)."""
    try:
        data = yaml.safe_load(_PROJECTS_YAML.read_text(encoding="utf-8")) or {}
        return [p["key"] for p in data.get("projects", []) if p.get("key")]
    except Exception as exc:  # pragma: no cover - config read guard
        logger.warning("done_artifact_audit: could not read projects.yaml: %s", exc)
        return []


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Audit done tasks for every project-in-flight; flag missing artifacts."""
    from tools.db.storage import get_connection
    from tools.kanban.done_artifact_auditor import audit_project, summarize

    # Disk-existence only by default — `git ls-files` per path is too slow under
    # the daemon watchdog. Opt in via config for a deeper, slower sweep.
    use_git = bool(config.get("use_git", False))

    keys = _project_keys()
    conn = get_connection()
    flagged: list[dict] = []
    per_project: dict[str, dict] = {}
    total_audited = 0
    try:
        for key in keys:
            try:
                results = audit_project(key, conn, _BASE, use_git=use_git)
            except Exception as exc:
                logger.warning("done_artifact_audit: project '%s' failed: %s", key, exc)
                continue
            summary = summarize(results)
            per_project[key] = summary
            total_audited += summary["total"]
            for r in results:
                if r["verdict"] == "missing_artifacts":
                    flagged.append(
                        {"task_id": r["task_id"], "project_id": key, "missing": r["missing"]}
                    )
    finally:
        try:
            conn.close()
        except Exception:
            pass

    flagged_count = len(flagged)
    if flagged_count:
        logger.warning(
            "done_artifact_audit: %d 'done' task(s) missing claimed artifacts across "
            "%d project(s)-in-flight: %s",
            flagged_count,
            len(keys),
            ", ".join(sorted({f["task_id"] for f in flagged}))[:500],
        )
    else:
        logger.info(
            "done_artifact_audit: %d done tasks across %d projects all have their artifacts.",
            total_audited,
            len(keys),
        )

    return {
        "success": True,
        "metric_value": float(flagged_count),
        "details": {
            "projects_in_flight": len(keys),
            "done_tasks_audited": total_audited,
            "flagged_count": flagged_count,
            "flagged": flagged[:50],
            "per_project": per_project,
            "use_git": use_git,
        },
    }
