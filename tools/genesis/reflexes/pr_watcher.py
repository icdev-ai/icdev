# CUI // SP-CTI
"""Genesis reflex: drive the OPT-70 PR watcher each cycle.

Thin wrapper around tools.ci.pr_watcher.PRWatcher.poll_once so the
autonomous PR feedback loop runs as part of the Genesis daemon cadence.

Each tick the watcher polls open kanban PRs (tasks whose executor_url is a
GitHub PR) and either:
  * injects a resume-context message for CI failures / merge conflicts /
    changes-requested (the autofix loop), or
  * auto-merges when CI is green and review approved — gated by the hybrid
    per-task-type policy in args/pr_watcher_config.yaml (chore/test/fix merge;
    feature/bug wait for a human).

Never raises — a watcher failure must not break the reflex loop. The daemon's
per-reflex watchdog (default 300s) bounds any hung gh/network call.
"""
from __future__ import annotations

from typing import Any, Dict

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Reflex entrypoint — runs one PR-watcher poll. Soft-fails."""
    try:
        from tools.ci.pr_watcher import PRWatcher, load_config
    except Exception as exc:
        logger.warning("pr_watcher reflex: import failed: %s", exc)
        return {"ok": False, "reason": f"import failed: {exc}"}

    try:
        watcher = PRWatcher(config=load_config())
        report = watcher.poll_once()
        merges = sum(1 for a in report.actions if a.action == "merge")
        resumes = sum(1 for a in report.actions if a.action == "resume")
        return {
            "ok": True,
            "tasks_checked": report.tasks_checked,
            "actions": len(report.actions),
            "merges": merges,
            "resumes": resumes,
        }
    except Exception as exc:
        logger.warning("pr_watcher reflex failed: %s", exc)
        return {"ok": False, "reason": str(exc)}
