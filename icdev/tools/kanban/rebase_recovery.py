#!/usr/bin/env python3
# CUI // SP-CTI
"""Rebase a DIRTY kanban PR branch onto its base before spending a resume (kax-conflict-01).

A kanban PR whose branch has drifted behind main goes DIRTY (``mergeable ==
CONFLICTING``) and nothing tries to recover it. `tools/ci/pr_watcher.py` treats
that as a resume class: it injects a "resolve the conflict" message into the
executor queue, once per poll, until the resume cap is hit — then escalates to a
human queue. Measured 2026-08-07: 2 of 5 open kanban PRs were DIRTY and
``xbm-wake-01`` sat parked 95.7 hours before escalating "resume cap reached
(5/5)".

The escalation is right. What was missing is that the CHEAP recovery is never
tried first. Most of these branches carry no textual conflict at all — they are
simply stale, or their content already landed on main by another route — and a
plain ``git rebase origin/main`` clears the DIRTY flag outright. Spending five
LLM resumes on that is expensive and ends in a permanent human queue.

So: on DIRTY, rebase in an ISOLATED scratch worktree, and push only when the
rebase is clean.

Safety, in order — each of these exists because a force-push is not undoable
from the board's side:

  * **Branch ownership.** Only ``kanban/<task-id>`` (or its ``-rN`` retry
    sibling, which `kanban.py` creates for the same task) may ever be
    force-pushed. Anything else — ``main``, another task's branch, a
    hand-authored ``feat/…`` — is refused before git is touched.
  * **Isolation.** The rebase runs in a throwaway detached worktree under the
    system temp dir, never in the task's own worktree (a session may be live in
    it) and never in the shared checkout.
  * **Clean-only.** A rebase that hits a real conflict is aborted and reported;
    nothing is pushed and the caller escalates exactly as it does today.
  * **--force-with-lease pinned to the observed sha.** If anything pushed to the
    branch between the read and the push, the push is rejected rather than
    clobbering it.

CLI:
    python tools/kanban/rebase_recovery.py --task kax-conflict-01 --dry-run --json
    python tools/kanban/rebase_recovery.py --task kax-conflict-01 --base main --json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

def _repo_root() -> pathlib.Path:
    """The git repo this module lives in.

    Walks up looking for a ``.git`` entry rather than counting ``parents[N]``:
    the count is off by one in the ``icdev/tools/kanban/`` mirror, and in a git
    WORKTREE ``.git`` is a file, not a directory — both are exactly the
    environments this module runs in. Falls back to the package parent.
    """
    here = pathlib.Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / ".git").exists():
            return candidate
    return here.parents[2]


ROOT = _repo_root()

BRANCH_PREFIX = "kanban/"
# `kanban.py` opens retry PRs on `kanban/<id>-r2`, `-r3`, … . Those are still the
# same task's branch and are the ones `pr_linker` most often links, so refusing
# them would leave exactly the class of PR this module exists to recover
# unrecoverable. Nothing else is accepted.
_RETRY_SUFFIX_RE = re.compile(r"^-r\d+$")

DEFAULT_TIMEOUT = 180

# Committer identity used ONLY when the environment has none configured.
FALLBACK_IDENTITY_NAME = "icdev-rebase-recovery"
FALLBACK_IDENTITY_EMAIL = "icdev-rebase-recovery@localhost"


def _git(
    args: List[str],
    *,
    cwd: Optional[str] = None,
    runner: Optional[Callable] = None,
    timeout: int = DEFAULT_TIMEOUT,
):
    return (runner or subprocess.run)(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        timeout=timeout,
    )


def _out(proc) -> str:
    return (getattr(proc, "stdout", "") or "").strip()


def _err(proc) -> str:
    return (getattr(proc, "stderr", "") or "").strip()


def branch_is_task_owned(branch: str, task_id: str) -> Tuple[bool, str]:
    """Whether `branch` is the kanban branch belonging to `task_id`.

    This is the gate on force-pushing. It is deliberately a whole-string match
    against a derived name rather than a prefix test: ``kanban/foo-2`` starts
    with ``kanban/foo`` but belongs to a different task, and a prefix test would
    happily clobber it.
    """
    branch = (branch or "").strip()
    task_id = (task_id or "").strip()
    if not branch or not task_id:
        return False, "empty branch or task id"
    if not branch.startswith(BRANCH_PREFIX):
        return False, f"branch '{branch}' is not under {BRANCH_PREFIX}"
    tail = branch[len(BRANCH_PREFIX):]
    if tail == task_id:
        return True, "exact task branch"
    if tail.startswith(task_id) and _RETRY_SUFFIX_RE.match(tail[len(task_id):]):
        return True, "retry branch for this task"
    return False, f"branch '{branch}' does not belong to task '{task_id}'"


def _identity_args(repo_root: str, runner: Optional[Callable]) -> List[str]:
    """`git -c` overrides supplying a committer identity, or [] if one exists.

    A rebase RE-COMMITS, so it needs a committer identity, and a recovery tool
    that only works where someone happened to run `git config --global user.email`
    is not a recovery tool. A bare CI runner has none and the rebase dies with
    ``fatal: empty ident name`` — which surfaces as "conflict" and escalates a
    PR that had no conflict at all.

    The fallback is applied only when git cannot resolve an identity, so a
    configured environment is never overridden. It affects the COMMITTER only:
    rebase preserves each replayed commit's original author, and recording this
    tool as the committer is simply true.
    """
    probe = _git(["config", "--get", "user.email"], cwd=repo_root, runner=runner)
    if getattr(probe, "returncode", 1) == 0 and _out(probe):
        return []
    logger.debug("rebase_recovery: no git identity configured — using fallback")
    return [
        "-c", f"user.name={FALLBACK_IDENTITY_NAME}",
        "-c", f"user.email={FALLBACK_IDENTITY_EMAIL}",
    ]


def _verdict(**kw: Any) -> Dict[str, Any]:
    base = {
        "attempted": False,
        "pushed": False,
        "conflict": False,
        "reason": "",
        "branch": "",
        "base": "",
        "commits_ahead": None,
        "old_sha": "",
        "new_sha": "",
    }
    base.update(kw)
    return base


def _cleanup(repo_root: str, path: Optional[str], runner) -> None:
    """Drop the scratch worktree. Never raises.

    ``--force`` is correct here and only here: this worktree was created by this
    function seconds ago, is detached, and holds nothing but a replay of commits
    that already exist on the remote branch. That is the opposite of
    `pr_watcher.reclaim_worktree`, which must never force because a task
    worktree can hold the only copy of a session's work.
    """
    if path:
        try:
            _git(["worktree", "remove", "--force", path], cwd=repo_root, runner=runner)
        except Exception as exc:  # noqa: BLE001
            logger.debug("rebase_recovery: worktree remove failed: %s", exc)
        try:
            if pathlib.Path(path).exists():
                shutil.rmtree(path, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
    try:
        _git(["worktree", "prune"], cwd=repo_root, runner=runner)
    except Exception:  # noqa: BLE001
        pass


def rebase_and_push(
    task_id: str,
    branch: str,
    *,
    base: str = "main",
    repo_root: Optional[str] = None,
    runner: Optional[Callable] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Rebase `branch` onto ``origin/<base>`` in a scratch worktree and push it.

    Returns a verdict dict; never raises. ``pushed`` is True only when the
    rebase applied cleanly AND the force-with-lease push was accepted.
    ``conflict`` is True when the rebase hit a real textual conflict — the
    caller should escalate that exactly as it would have without this module.

    ``dry_run`` performs the rebase probe (which touches only a temp directory
    and the local object store) and stops before the push.
    """
    root = str(repo_root or ROOT)

    owned, why = branch_is_task_owned(branch, task_id)
    if not owned:
        return _verdict(branch=branch, base=base, reason=f"refused: {why}")

    fetch = _git(["fetch", "origin", base, branch], cwd=root, runner=runner)
    if getattr(fetch, "returncode", 1) != 0:
        return _verdict(
            branch=branch, base=base,
            reason=f"fetch failed: {_err(fetch)[:200]}",
        )

    head = _git(["rev-parse", f"refs/remotes/origin/{branch}"], cwd=root, runner=runner)
    if getattr(head, "returncode", 1) != 0:
        return _verdict(
            branch=branch, base=base,
            reason=f"remote branch not found: {_err(head)[:200]}",
        )
    old_sha = _out(head)

    tmp = tempfile.mkdtemp(prefix=f"icdev-rebase-{task_id}-")
    # mkdtemp created the directory; `git worktree add` insists on making it.
    shutil.rmtree(tmp, ignore_errors=True)

    add = _git(["worktree", "add", "--detach", tmp, old_sha], cwd=root, runner=runner)
    if getattr(add, "returncode", 1) != 0:
        _cleanup(root, tmp, runner)
        return _verdict(
            branch=branch, base=base, old_sha=old_sha,
            reason=f"scratch worktree failed: {_err(add)[:200]}",
        )

    try:
        ident = _identity_args(tmp, runner)
        reb = _git([*ident, "rebase", f"origin/{base}"], cwd=tmp, runner=runner)
        if getattr(reb, "returncode", 1) != 0:
            detail = (_err(reb) or _out(reb)).splitlines()
            _git(["rebase", "--abort"], cwd=tmp, runner=runner)
            return _verdict(
                attempted=True, conflict=True, branch=branch, base=base,
                old_sha=old_sha,
                reason=(
                    "rebase onto origin/%s hit conflicts: %s"
                    % (base, detail[-1][:200] if detail else "no detail")
                ),
            )

        count = _git(
            ["rev-list", "--count", f"origin/{base}..HEAD"], cwd=tmp, runner=runner
        )
        ahead = int(_out(count) or "0") if getattr(count, "returncode", 1) == 0 else None
        new_sha = _out(_git(["rev-parse", "HEAD"], cwd=tmp, runner=runner))

        if ahead == 0:
            # Every commit replayed to nothing: the work is already on the base.
            # Pushing would empty the PR, which reads as "the change was undone".
            # Report it and let the caller escalate — a human should close it.
            return _verdict(
                attempted=True, branch=branch, base=base, old_sha=old_sha,
                new_sha=new_sha, commits_ahead=0,
                reason=(
                    f"rebase left no commits ahead of origin/{base} — the branch's "
                    "work is already on the base; not pushing an empty branch"
                ),
            )

        if dry_run:
            return _verdict(
                attempted=True, branch=branch, base=base, old_sha=old_sha,
                new_sha=new_sha, commits_ahead=ahead,
                reason=f"dry-run: rebase clean ({ahead} commit(s)), push skipped",
            )

        push = _git(
            [
                "push",
                f"--force-with-lease=refs/heads/{branch}:{old_sha}",
                "origin",
                f"HEAD:refs/heads/{branch}",
            ],
            cwd=tmp,
            runner=runner,
        )
        if getattr(push, "returncode", 1) != 0:
            return _verdict(
                attempted=True, branch=branch, base=base, old_sha=old_sha,
                new_sha=new_sha, commits_ahead=ahead,
                reason=(
                    "force-with-lease push rejected (branch moved under us?): "
                    f"{_err(push)[:200]}"
                ),
            )
        logger.info(
            "rebase_recovery: %s rebased onto origin/%s and pushed (%s -> %s)",
            branch, base, old_sha[:8], new_sha[:8],
        )
        return _verdict(
            attempted=True, pushed=True, branch=branch, base=base,
            old_sha=old_sha, new_sha=new_sha, commits_ahead=ahead,
            reason=f"rebased onto origin/{base} and force-pushed ({ahead} commit(s))",
        )
    except Exception as exc:  # noqa: BLE001 — a recovery attempt must never stall the watcher
        return _verdict(
            attempted=True, branch=branch, base=base, old_sha=old_sha,
            reason=f"rebase errored: {exc}",
        )
    finally:
        _cleanup(root, tmp, runner)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Rebase a kanban task branch onto its base")
    ap.add_argument("--task", required=True, help="Kanban task id")
    ap.add_argument("--branch", default=None,
                    help="Branch to rebase (default: kanban/<task>)")
    ap.add_argument("--base", default="main")
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="Probe the rebase locally; never push")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    verdict = rebase_and_push(
        args.task,
        args.branch or f"{BRANCH_PREFIX}{args.task}",
        base=args.base,
        repo_root=args.repo_root,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(verdict, indent=2, sort_keys=True))
    else:
        print(f"{verdict['branch']}: pushed={verdict['pushed']} {verdict['reason']}")
    return 0 if (verdict["pushed"] or verdict["attempted"]) else 1


if __name__ == "__main__":
    sys.exit(main())
