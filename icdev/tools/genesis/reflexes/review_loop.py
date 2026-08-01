# CUI // SP-CTI
"""Genesis reflex: working-tree review_loop drift probe (REPORT-ONLY).

Runs the diff-scoped review loop over the daemon's working tree without
autofixing — it never edits or commits, so it cannot collide with the many
concurrent kanban/CLI sessions sharing the main checkout. On a clean checkout
(no uncommitted .py changes) every gate skips → a cheap green no-op. When the
working tree has uncommitted .py drift, it surfaces the gate findings via the
audit trail + the returned summary (for the dashboard / monitoring) but leaves
the fixing to the pre-PR preflight (tools/ci/modules/git_ops.py) and the
pre-commit hook, which are correctly scoped to a single task's changes.

Never raises — a probe failure must not break the reflex loop. The daemon's
per-reflex watchdog bounds any hung subprocess (coherence / SIPA).
"""
from __future__ import annotations

from typing import Any, Dict

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Reflex entrypoint — one report-only review_loop pass. Soft-fails."""
    try:
        from tools.quality.review_loop import preflight
    except Exception as exc:
        logger.warning("review_loop reflex: import failed: %s", exc)
        return {"ok": False, "reason": f"import failed: {exc}"}

    try:
        report = preflight(
            base=None,            # working-tree mode (uncommitted + untracked)
            autofix=False,        # REPORT ONLY — never edit the shared checkout
            coherence_scope="changed",
            audit=True,
            max_iterations=1,
        )
        return {
            "ok": True,
            "green": report.green,
            "changed_files": len(report.changed_files),
            "open_findings": len(report.fix_brief),
            "reason": report.reason,
        }
    except Exception as exc:
        logger.warning("review_loop reflex failed: %s", exc)
        return {"ok": False, "reason": str(exc)}
