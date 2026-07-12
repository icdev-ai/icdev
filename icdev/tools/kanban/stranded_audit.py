#!/usr/bin/env python3
# CUI // SP-CTI
"""Stranded-branch auditor (kph-A).

Retroactively reconciles kanban_tasks rows marked ``done`` or ``validating``
against ``origin/<default>``: a task whose ``kanban/<id>`` branch still has
commits NOT on origin is **stranded** — built-but-never-merged (the "42/42 done
LIED" class that hid the whole Cortex surface on 9 unmerged branches).

Why this is needed: the transition-time merge-verify gate
(``kanban._move_task`` -> ``_branch_has_unmerged_commits``) only fires at the
instant a task is written to ``done`` and is fail-open; nothing audits rows that
are ALREADY terminal, and the ``validating`` status is otherwise dead code
(nothing sets it, nothing detects "stuck"). This auditor closes that gap.

Surface, don't auto-act: findings are written to a JSON report and filed as HITL
``suggested`` remediation cards (quarantine state — never fed straight to the
enforcing runner). GREEN tier: reads git + kanban_tasks, writes suggested cards.

Headless:  python -m tools.kanban.stranded_audit --json
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — git plumbing only, no user input in argv
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("kanban.stranded_audit")

_TERMINAL_STATES = ("done", "validating")
_REPORT_PATH = BASE_DIR / ".tmp" / "kanban" / "stranded_audit.json"


def _default_branch() -> str:
    """Resolve the default branch name (origin/HEAD -> main), best-effort."""
    try:
        r = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().rsplit("/", 1)[-1]
    except Exception:  # noqa: BLE001
        pass
    return "main"


def _branch_unmerged_count(task_id: str, default_branch: str) -> Tuple[bool, int]:
    """Return (branch_exists, unmerged_commit_count) for ``kanban/<task_id>``.

    Checks BOTH the local branch and ``origin/kanban/<task_id>`` — a stranded
    branch may exist only on origin in a fresh checkout. Compares
    ``origin/<default>..<ref>``. FAIL-OPEN: any git error yields (False, 0) so an
    unreachable git never fabricates strandedness.
    """
    branch = f"kanban/{task_id}"
    exists_any = False
    max_unmerged = 0
    for ref in (branch, f"origin/{branch}"):
        try:
            chk = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", ref],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10,
            )
            if chk.returncode != 0:
                continue
            exists_any = True
            log = subprocess.run(
                ["git", "log", f"origin/{default_branch}..{ref}", "--oneline"],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10,
            )
            if log.returncode != 0:
                continue
            n = len([ln for ln in log.stdout.splitlines() if ln.strip()])
            max_unmerged = max(max_unmerged, n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stranded_audit: git check %s errored (fail-open): %s", ref, exc)
    return exists_any, max_unmerged


def audit_stranded_tasks(
    conn=None,
    git_check: Optional[Callable[[str], Tuple[bool, int]]] = None,
    fetch: bool = True,
) -> Dict:
    """Classify every terminal (done/validating) kanban task vs origin/main.

    Returns {default_branch, total, stranded[], orphan_validating[], clean_count}.
    ``git_check`` (task_id -> (exists, unmerged)) is injectable for tests.
    """
    default_branch = _default_branch()
    if fetch and git_check is None:
        try:
            subprocess.run(
                ["git", "fetch", "origin", default_branch, "--quiet"],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=20,
            )
        except Exception:  # noqa: BLE001
            pass
    check = git_check or (lambda tid: _branch_unmerged_count(tid, default_branch))

    own = conn is None
    if own:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stranded_audit: DB unavailable (%s)", exc)
            return {"default_branch": default_branch, "total": 0, "stranded": [],
                    "orphan_validating": [], "clean_count": 0, "error": str(exc)}
    try:
        try:
            rows = conn.execute(
                "SELECT id, status, title FROM kanban_tasks WHERE status IN (%s, %s)",
                _TERMINAL_STATES,
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 — table may not exist yet
            logger.warning("stranded_audit: kanban_tasks query failed (%s)", exc)
            rows = []
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    stranded: List[dict] = []
    orphan_validating: List[dict] = []
    clean = 0
    for row in rows:
        tid = row["id"] if not isinstance(row, (tuple, list)) else row[0]
        status = row["status"] if not isinstance(row, (tuple, list)) else row[1]
        title = (row["title"] if not isinstance(row, (tuple, list)) else row[2]) or ""
        exists, unmerged = check(tid)
        if exists and unmerged > 0:
            stranded.append({"id": tid, "status": status, "title": title,
                             "unmerged_commits": unmerged, "branch": f"kanban/{tid}"})
        elif status == "validating" and not exists:
            # validating is dead code: a validating row with no branch is stuck.
            orphan_validating.append({"id": tid, "status": status, "title": title})
        else:
            clean += 1

    return {
        "default_branch": default_branch,
        "total": len(rows),
        "stranded": stranded,
        "orphan_validating": orphan_validating,
        "clean_count": clean,
    }


def _file_suggested_cards(findings: Dict) -> List[str]:
    """File HITL 'suggested' remediation cards for stranded/orphan tasks (best-effort).

    Uses the canonical task_factory.create_tasks (never raw INSERT); ids are stable
    so daily re-runs dedupe rather than pile up.
    """
    default_branch = findings.get("default_branch", "main")
    specs: List[dict] = []
    for f in findings.get("stranded", []):
        specs.append({
            "id": f"kph-stranded-{f['id']}",
            "title": f"[STRANDED] {f['id']}: {f['unmerged_commits']} commit(s) not on origin/{default_branch}",
            "description": (
                f"Task {f['id']} (status={f['status']}) has branch {f['branch']} with "
                f"{f['unmerged_commits']} commit(s) not merged to origin/{default_branch}. "
                f"Its artifacts are NOT on main — the card lies about being complete. "
                f"HITL: cherry-pick/reconcile onto origin/main via a fresh worktree, or re-open the task."
            ),
            "task_type": "bug",
            "priority": "high",
            "status": "suggested",
            "idempotency_key": f"stranded-audit-{f['id']}",
        })
    for f in findings.get("orphan_validating", []):
        specs.append({
            "id": f"kph-orphan-{f['id']}",
            "title": f"[STRANDED] {f['id']}: stuck in 'validating' with no branch",
            "description": (
                f"Task {f['id']} is in 'validating' but has no kanban/{f['id']} branch "
                f"(local or origin). 'validating' is otherwise dead in the runner, so this "
                f"row is stuck. HITL: verify whether the work merged and re-classify."
            ),
            "task_type": "chore",
            "priority": "medium",
            "status": "suggested",
            "idempotency_key": f"stranded-audit-{f['id']}",
        })
    if not specs:
        return []
    try:
        from tools.kanban.task_factory import create_tasks
        return create_tasks(specs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stranded_audit: could not file suggested cards (%s)", exc)
        return []


def _record_lessons(findings: Dict) -> int:
    """Record an UNMERGED_STRANDED lesson per stranded/orphan task (best-effort).

    Feeds the Lessons-Learned engine so persistent stranding builds a recurrence
    score and escalates to remediation — complementary to the suggested cards,
    which are the direct surface. De-dup + remediation gating live in the lessons
    engine (write_lesson / maybe_create_remediation_card).
    """
    ids = [f["id"] for f in findings.get("stranded", [])] + \
          [f["id"] for f in findings.get("orphan_validating", [])]
    if not ids:
        return 0
    n = 0
    try:
        from tools.workflow.lesson_learned import analyze_task, write_lesson
    except Exception as exc:  # noqa: BLE001
        logger.warning("stranded_audit: lessons engine unavailable (%s)", exc)
        return 0
    for tid in ids:
        try:
            write_lesson(analyze_task(tid, outcome="unmerged_stranded"))
            n += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("stranded_audit: lesson for %s failed (%s)", tid, exc)
    return n


def run(config: dict, state: object) -> dict:
    """Genesis reflex entry point."""
    try:
        findings = audit_stranded_tasks()
        created = _file_suggested_cards(findings)
        _record_lessons(findings)
        try:
            _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            _REPORT_PATH.write_text(json.dumps({**findings, "cards_filed": created}, indent=2),
                                    encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        n_stranded = len(findings.get("stranded", [])) + len(findings.get("orphan_validating", []))
        logger.info("stranded_audit: %d stranded/orphan of %d terminal task(s); %d card(s) filed",
                    n_stranded, findings.get("total", 0), len(created))
        return {"success": True, "metric_value": float(n_stranded),
                "details": {**findings, "cards_filed": created}}
    except Exception as exc:  # noqa: BLE001
        logger.exception("stranded_audit failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0}


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit kanban tasks stranded off origin/main.")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    ap.add_argument("--no-cards", action="store_true", help="Detect only; do not file suggested cards")
    ap.add_argument("--no-fetch", action="store_true", help="Skip git fetch")
    args = ap.parse_args()
    findings = audit_stranded_tasks(fetch=not args.no_fetch)
    created = [] if args.no_cards else _file_suggested_cards(findings)
    out = {**findings, "cards_filed": created}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        s = len(findings["stranded"]) + len(findings["orphan_validating"])
        print(f"Terminal tasks: {findings['total']} | stranded/orphan: {s} | clean: {findings['clean_count']}")
        for f in findings["stranded"]:
            print(f"  STRANDED  {f['id']}: {f['unmerged_commits']} commit(s) off origin/{findings['default_branch']}")
        for f in findings["orphan_validating"]:
            print(f"  ORPHAN    {f['id']}: validating, no branch")
        if created:
            print(f"Filed {len(created)} suggested card(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
