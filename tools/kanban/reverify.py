#!/usr/bin/env python3
"""Recompute a kanban task's verification from durable git state (kpr-rvfy-01).

`pr_watcher._enforced_done_ok` reads only the LATEST `kanban_verifications` row.
Nothing writes a fresh one except a dispatch — the INSERT sites all sit on the
dispatch/completion path — so a task that verifies badly once stays blocked from
auto-merge until it is re-dispatched, and a re-dispatch opens a *new* PR on a
suffixed branch instead of reusing the old one. That loop is what left
`gdx-aud-01` holding three open PRs.

**Why the original verdict was wrong in the first place.** The dispatch-time
verifier (`kanban.py::_git_worktree_has_real_changes`) reads two module-level
dicts, `_worktrees` and `_dispatch_main_heads`, both populated at dispatch. They
are process-local: if the daemon restarts between dispatch and verification, all
three of its checks skip and it returns ``(False, "")``, which is recorded as
"No git commits found on task branch". `tsr-core-01-d5` carries exactly that
verdict while its PR is green with real changes.

So this module deliberately does **not** reuse that primitive. It reads only
durable facts — remote refs — and therefore gives the same answer regardless of
which process asks or how long after the fact:

    git log <base>..<branch> --name-only

Rows are appended, never updated: `kanban_verifications` is part of the
append-only audit surface, and the whole point is that the history of verdicts
stays readable.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
import uuid
from typing import Callable, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

DEFAULT_BASE = "origin/main"
BRANCH_PREFIX = "kanban/"
# Marks rows this module wrote, so a re-verification is distinguishable from a
# dispatch-time verdict when reading the audit trail.
DISPATCH_SOURCE = "reverify"


def _run(runner: Optional[Callable], args: List[str], cwd: Optional[str], timeout: int = 30):
    return (runner or subprocess.run)(
        args, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=cwd, timeout=timeout,
    )


def resolve_branch(task_id: str, task_row: Optional[dict] = None) -> str:
    """The branch carrying this task's work.

    Prefers the recorded `branch_name` — a retry works on a suffixed branch
    (``kanban/<id>-r2``), and assuming the canonical name would then verify the
    wrong ref. Falls back to ``kanban/<task_id>``.
    """
    if task_row:
        recorded = (task_row.get("branch_name") or "").strip()
        if recorded:
            return recorded
    return f"{BRANCH_PREFIX}{task_id}"


def _remote_ref(branch: str) -> str:
    """Map a local branch name to its origin ref."""
    if branch.startswith("origin/"):
        return branch
    return f"origin/{branch}"


def compute_verification(
    task_id: str,
    *,
    task_row: Optional[dict] = None,
    base: str = DEFAULT_BASE,
    repo_root: Optional[str] = None,
    runner: Optional[Callable] = None,
    fetch: bool = True,
) -> Dict[str, object]:
    """Return a verdict dict computed from remote git state. Writes nothing.

    ``{result, reason, branch, files_changed, commits}`` where ``result`` is
    ``passed`` or ``failed`` — the same vocabulary `_enforced_done_ok` reads.
    """
    branch = resolve_branch(task_id, task_row)
    ref = _remote_ref(branch)
    verdict: Dict[str, object] = {
        "task_id": task_id, "branch": branch, "files_changed": 0, "commits": 0,
    }

    if fetch:
        # Best-effort: a stale ref would produce a confidently wrong verdict,
        # but an offline runner should still be able to read what it has.
        try:
            _run(runner, ["git", "fetch", "origin", branch, "--quiet"], repo_root, timeout=60)
        except Exception as exc:  # noqa: BLE001
            logger.debug("reverify: fetch failed for %s (%s) — using cached refs", branch, exc)

    try:
        exists = _run(runner, ["git", "rev-parse", "--verify", ref], repo_root)
    except Exception as exc:  # noqa: BLE001
        verdict.update(result="failed", reason=f"git unavailable: {exc}")
        return verdict
    if exists.returncode != 0:
        # Deleted after merge is the common case, and it is NOT evidence of
        # missing work — say so rather than implying the agent produced nothing.
        verdict.update(
            result="failed",
            reason=(
                f"branch {ref} not found on origin (deleted after merge, or never "
                f"pushed) — cannot verify from git"
            ),
        )
        return verdict

    try:
        log = _run(
            runner,
            ["git", "log", f"{base}..{ref}", "--name-only", "--pretty=format:"],
            repo_root,
        )
        count = _run(
            runner, ["git", "rev-list", "--count", f"{base}..{ref}"], repo_root
        )
    except Exception as exc:  # noqa: BLE001
        verdict.update(result="failed", reason=f"git log failed: {exc}")
        return verdict

    if log.returncode != 0:
        verdict.update(
            result="failed",
            reason=f"git log {base}..{ref} failed: {(log.stderr or '').strip()[:120]}",
        )
        return verdict

    files = sorted({ln.strip() for ln in (log.stdout or "").splitlines() if ln.strip()})
    try:
        commits = int((count.stdout or "0").strip() or 0)
    except (ValueError, AttributeError):
        commits = 0
    verdict["files_changed"] = len(files)
    verdict["commits"] = commits

    if files:
        preview = ", ".join(files[:3])
        verdict.update(
            result="passed",
            reason=(
                f"Verified (git-first, re-verified): {len(files)} file(s) changed "
                f"on {branch} across {commits} commit(s) — e.g. {preview}"
            ),
        )
    else:
        verdict.update(
            result="failed",
            reason=(
                f"No file changes on {ref} vs {base} — the branch exists but carries "
                f"no work ahead of base"
            ),
        )
    return verdict


def reverify(
    task_id: str,
    get_connection,
    *,
    base: str = DEFAULT_BASE,
    repo_root: Optional[str] = None,
    runner: Optional[Callable] = None,
    fetch: bool = True,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Recompute the verdict and APPEND a `kanban_verifications` row.

    Returns the verdict with ``written`` set. Raises ``LookupError`` if the task
    does not exist — a silent no-op on a typo'd id is how a caller ends up
    believing a task was cleared when nothing happened.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, branch_name FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"no such task: {task_id}")
        task_row = {"branch_name": row["branch_name"]}

        verdict = compute_verification(
            task_id, task_row=task_row, base=base,
            repo_root=repo_root, runner=runner, fetch=fetch,
        )
        verdict["written"] = False
        if dry_run:
            return verdict

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO kanban_verifications "
            "(id, task_id, verified_at, result, reason, git_commits, "
            " dispatch_source, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                f"rvfy-{uuid.uuid4().hex[:12]}",
                task_id, now, verdict["result"], verdict["reason"],
                verdict["commits"], DISPATCH_SOURCE, now,
            ),
        )
        # review_passed is deliberately left NULL. _enforced_done_ok treats NULL
        # as "not judged, allowed" and 0 as a hard conformance failure; this
        # module verifies that work EXISTS, it does not judge conformance, so
        # writing 0 here would block merges it has no basis to block.
        try:
            conn.commit()
        except Exception:  # noqa: BLE001 — autocommit backends
            pass
        verdict["written"] = True
        return verdict
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Recompute a kanban task's verification from git state"
    )
    ap.add_argument("task_id")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute the verdict without writing a row")
    ap.add_argument("--no-fetch", action="store_true",
                    help="Use cached refs (offline)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from tools.db.storage import get_connection

    try:
        verdict = reverify(
            args.task_id, get_connection, base=args.base,
            fetch=not args.no_fetch, dry_run=args.dry_run,
        )
    except LookupError as exc:
        print(f"reverify: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(verdict, indent=2, sort_keys=True))
    else:
        state = "would write" if args.dry_run else (
            "wrote" if verdict.get("written") else "did not write")
        print(f"{args.task_id}: {verdict['result']} ({state})")
        print(f"  {verdict['reason']}")
    return 0 if verdict["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
