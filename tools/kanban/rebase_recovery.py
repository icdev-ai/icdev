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

WHICH INTEGRATION (kpr-watch-15). A default `git rebase` does NOT preserve
merges, so a branch whose head is a ``merge -s ours`` supersede is FLATTENED and
the commit that supersede deliberately discarded is REPLAYED alongside its own
replacement. When ``branch_carries_merge`` measures a merge commit on the
branch, the base is MERGED IN instead — the repair a human performs, which
integrates 10 of the 62 recorded merge-carrying failures against 0 for the plain
rebase. Everything below is unchanged on both paths, and a LINEAR branch can
never take the merge path, so it can never gain a merge commit from this module.
See docs/audits/kpr-watch-15-merge-carrying-branch-rebase-survey.md.

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

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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


def branch_carries_merge(
    repo_root: str,
    base_ref: str,
    head_ref: str,
    runner: Optional[Callable] = None,
) -> Optional[List[str]]:
    """The merge commits ``head_ref`` carries that ``base_ref`` does not (kpr-watch-15).

    ``[]`` means MEASURED linear. ``None`` means git could not answer, and the
    two are never merged: an unreadable history must fall through to the
    unchanged rebase rather than be read as "linear", which is the direction
    that force-pushes a flattened branch.
    """
    log = _git(
        ["log", "--merges", "--format=%H", f"{base_ref}..{head_ref}"],
        cwd=repo_root, runner=runner,
    )
    if getattr(log, "returncode", 1) != 0:
        return None
    return [line for line in _out(log).split() if line]


#: The git verbs each integration strategy uses. `rebase` is unchanged and is
#: what every LINEAR branch still takes; `merge` exists because a default
#: `git rebase` does NOT preserve merges, so a branch whose head is a
#: `merge -s ours` supersede is FLATTENED and the commit that supersede
#: deliberately discarded is REPLAYED alongside its own replacement.
_STRATEGIES: Dict[str, Dict[str, Any]] = {
    "rebase": {
        "start": lambda base, branch: ["rebase", f"origin/{base}"],
        "cont": ["-c", "core.editor=true", "rebase", "--continue"],
        "abort": ["rebase", "--abort"],
        "noun": "rebase onto origin/%s",
        "done": "rebased onto origin/%s and force-pushed",
    },
    "merge": {
        # An explicit message: on a DETACHED head git would otherwise write
        # "into HEAD", which names nothing a reader of the branch can use.
        "start": lambda base, branch: [
            "merge", "--no-edit", "-m",
            f"Merge origin/{base} into {branch}", f"origin/{base}"],
        "cont": ["-c", "core.editor=true", "merge", "--continue"],
        "abort": ["merge", "--abort"],
        "noun": "merge of origin/%s into the branch",
        "done": "merged origin/%s into the branch and pushed",
    },
}


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


#: How many times one rebase may stop on a conflict and be resolved before we
#: give up. Every replayed commit can stop once; a kanban branch carries a
#: handful of commits, so this is a runaway guard, not a budget.
MAX_CONFLICT_STOPS = 25


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
        # The union rung's verdict (mfx-sib-03): None when it never ran, else
        # {outcome, files, rules_used, verifiers, tests, reason}.
        "union": None,
        # How the base was integrated (kpr-watch-15): "rebase" | "merge", or
        # None when the attempt was refused before the branch could be read.
        "strategy": None,
        # The merge commits the branch carried, which is WHY `strategy` is
        # `merge`. [] is measured-linear and None is unmeasurable.
        "carried_merges": None,
        # True when the branch ALREADY contained the base and there was nothing
        # to integrate -- never folded into `pushed`, which would claim an act
        # that did not happen.
        "already_current": False,
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


def _unmerged_files(cwd, runner) -> list:
    """Paths the index still holds in an unmerged state (empty on any error)."""
    status = _git(["diff", "--name-only", "--diff-filter=U"], cwd=cwd, runner=runner)
    if getattr(status, "returncode", 1) != 0:
        return []
    return [f.strip() for f in (_out(status) or "").splitlines() if f.strip()]


def _union_resolve(cwd, union_rules, runner) -> Optional[Dict[str, Any]]:
    """The union rung (mfx-sib-03): rules chosen BY FILE from the declared table.

    Runs only after `_auto_resolve_conflicts` declined. Returns the outcome
    dict, or None when the rung is switched off (``union_rules is False``).
    A rung that errors is reported as `refused`, never raised: a recovery
    attempt must not stall the watcher, and an unexplained abort is the exact
    silence this card exists to remove.
    """
    if union_rules is False:
        return None
    try:
        from tools.kanban import union_resolver

        outcome = union_resolver.resolve_index_conflicts(
            cwd, rules_cfg=union_rules if isinstance(union_rules, dict) else None,
            runner=runner)
    except Exception as exc:  # noqa: BLE001 -- the rung must never stall the watcher
        logger.warning("rebase_recovery: union rung errored: %s", exc)
        return {"outcome": "refused", "files": [], "rules_used": [], "verifiers": [],
                "tests": [], "reason": f"union rung errored: {exc}"}
    return outcome.to_dict()


def _fold_union(summary: Optional[Dict[str, Any]], outcome: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """One summary across every conflict stop of a single rebase."""
    if outcome is None:
        return summary
    if summary is None:
        return dict(outcome)
    for key in ("files", "rules_used", "verifiers", "tests"):
        merged = list(summary.get(key) or [])
        merged.extend(x for x in (outcome.get(key) or []) if x not in merged)
        summary[key] = merged
    summary["outcome"] = outcome.get("outcome", summary.get("outcome"))
    summary["reason"] = outcome.get("reason", "")
    return summary


def _auto_resolve_conflicts(cwd, runner) -> list:
    """Resolve provably-additive conflicts in place; return the notes.

    Empty list means nothing was resolved — either there was nothing safe to do,
    or a file the resolver does not understand is conflicted, in which case the
    caller falls through to the union rung, and from there to the abort.
    """
    from tools.kanban.conflict_resolvers import is_resolvable_path, resolve_file

    files = _unmerged_files(cwd, runner)
    if not files:
        return []
    # ALL of them must be resolvable. Resolving some and aborting on the rest
    # would leave the worktree half-edited, and a partial resolution is harder to
    # read than none.
    if not all(is_resolvable_path(f) for f in files):
        return []

    notes: list = []
    for rel in files:
        got = resolve_file(pathlib.Path(cwd) / rel)
        if not got:
            return []          # could not prove it safe — leave everything alone
        notes.extend(got)
        add = _git(["add", "--", rel], cwd=cwd, runner=runner)
        if getattr(add, "returncode", 1) != 0:
            return []
    return notes


def rebase_and_push(
    task_id: str,
    branch: str,
    *,
    base: str = "main",
    repo_root: Optional[str] = None,
    runner: Optional[Callable] = None,
    dry_run: bool = False,
    union_rules: Any = None,
) -> Dict[str, Any]:
    """Rebase `branch` onto ``origin/<base>`` in a scratch worktree and push it.

    Returns a verdict dict; never raises. ``pushed`` is True only when the
    rebase applied cleanly AND the force-with-lease push was accepted.
    ``conflict`` is True when the rebase hit a real textual conflict — the
    caller should escalate that exactly as it would have without this module.

    ``dry_run`` performs the rebase probe (which touches only a temp directory
    and the local object store) and stops before the push.

    ``union_rules`` is the `union_resolver` config block (mfx-sib-03): None
    reads it from args/pr_watcher_config.yaml, a dict is used as given, and
    False switches the rung off. The verdict's ``union`` key reports what the
    rung did.
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

    # WHICH INTEGRATION (kpr-watch-15). A default `git rebase` does not preserve
    # merges, so a branch whose head is a `merge -s ours` supersede -- the
    # documented recipe for "keep the rebased tree, discard the pre-rebase
    # commit" -- is FLATTENED and the discarded commit is replayed alongside its
    # own replacement. `dwr-ev-03` (PR #2179) offered TWO commits where the
    # branch intended one and drew ten `rebase_failed` rows for it.
    #
    # MEASURED over all 649 lifetime `pr_watcher.rebase_failed` rows, each
    # replayed against the branch head and base it actually had at that instant
    # (`tools/ci/rebase_merge_survey.py`): 62 carried a merge, 475 were linear,
    # 112 unmeasurable. Over those 62 -- where a plain rebase reproduces 62 of
    # 62 failures -- merging the base IN integrates 10 and diagnoses 5 more as a
    # branch that already contains its base, against 3 and 5 for
    # `--rebase-merges`. Over the 111 measurable `pr_watcher.rebase` SUCCESSES,
    # ZERO were on a merge-carrying branch, so this refuses no rebase that has
    # ever worked, and the 475 linear rows are untouched by construction.
    carried = branch_carries_merge(root, f"origin/{base}",
                                   f"refs/remotes/origin/{branch}", runner)
    strategy = "merge" if carried else "rebase"
    verb = _STRATEGIES[strategy]

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
        noun = verb["noun"] % base
        common = {"strategy": strategy, "carried_merges": carried}
        reb = _git([*ident, *verb["start"](base, branch)], cwd=tmp, runner=runner)
        union_summary: Optional[Dict[str, Any]] = None
        stops = 0
        while getattr(reb, "returncode", 1) != 0:
            stops += 1
            if stops > MAX_CONFLICT_STOPS:
                _git(verb["abort"], cwd=tmp, runner=runner)
                return _verdict(
                    attempted=True, conflict=True, branch=branch, base=base,
                    old_sha=old_sha, union=union_summary, **common,
                    reason=(f"{noun} stopped on conflicts more "
                            f"than {MAX_CONFLICT_STOPS} times; giving up"),
                )
            # Before giving up: some conflicts are not disagreements. Two
            # branches each appending an independent block to a shared reference
            # file, or each allocating "the next free" section number, resolve
            # the same way every time — keep both. Six of roughly ten hand
            # resolutions on 2026-08-09 were exactly that, and each cost a human
            # a worktree round-trip.
            #
            # resolve_conflicts refuses anything it cannot prove is additive, and
            # never touches code, so an unresolved file falls through to the
            # union rung, and from there to the abort path below unchanged.
            resolved_notes = _auto_resolve_conflicts(tmp, runner)
            if resolved_notes:
                how = "auto-resolved %d additive conflict(s): %s" % (
                    len(resolved_notes), "; ".join(resolved_notes[:3]))
            else:
                # THE UNION RUNG (mfx-sib-03). A REAL conflict on a DECLARED
                # sibling-append file -- a canvas blueprint gaining one route
                # block per card, the `request.path in [...]` list gaining one
                # token per card, a coverage table gaining one row -- has one
                # correct resolution, and an operator applied it ten times by
                # hand on 2026-09-03/04. Rules are chosen by FILE from
                # args/pr_watcher_config.yaml, never guessed from content; the
                # result is verified (ast + ruff, typescript, Jinja, git diff
                # --check, declared page tests) before anything is pushed; an
                # undeclared file or a failed verifier refuses, and the abort
                # below leaves the branch untouched.
                outcome = _union_resolve(tmp, union_rules, runner)
                union_summary = _fold_union(union_summary, outcome)
                if outcome is None or outcome.get("outcome") != "resolved":
                    detail = (_err(reb) or _out(reb)).splitlines()
                    why = detail[-1][:200] if detail else "no detail"
                    if outcome is not None and outcome.get("reason"):
                        why += "; union rung: " + str(outcome["reason"])[:300]
                    _git(verb["abort"], cwd=tmp, runner=runner)
                    return _verdict(
                        attempted=True, conflict=True, branch=branch, base=base,
                        old_sha=old_sha, union=union_summary, **common,
                        reason="%s hit conflicts: %s" % (noun, why),
                    )
                how = "union-resolved %s" % ", ".join(
                    outcome.get("rules_used") or outcome.get("files") or [])
            cont = _git([*ident, *verb["cont"]], cwd=tmp, runner=runner)
            if getattr(cont, "returncode", 1) == 0:
                logger.info("rebase_recovery: %s on %s; %s continued",
                            how, branch, strategy)
                reb = cont
                continue
            if _unmerged_files(tmp, runner):
                # The NEXT replayed commit stopped on a conflict of its own.
                # Round again: each stop is resolved and verified on its own.
                # A merge has exactly one stop, so this only ever loops for a
                # rebase -- it is left shared rather than special-cased.
                reb = cont
                continue
            _git(verb["abort"], cwd=tmp, runner=runner)
            return _verdict(
                attempted=True, conflict=True, branch=branch, base=base,
                old_sha=old_sha, union=union_summary, **common,
                reason=(how + f" but `git {strategy} --continue` still failed: "
                        + (_err(cont) or "")[:160]),
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
                new_sha=new_sha, commits_ahead=0, union=union_summary, **common,
                reason=(
                    f"{noun} left no commits ahead of origin/{base} — the branch's "
                    "work is already on the base; not pushing an empty branch"
                ),
            )

        if strategy == "merge" and new_sha == old_sha:
            # "Already up to date": the branch ALREADY contains the base and
            # there is nothing to integrate. 5 of the 62 measured rows are this
            # -- the forge reporting CONFLICTING over a branch git merges clean,
            # the `phantom` class the watcher already refunds. Pushing the same
            # sha and reporting `pushed` would claim an integration that did not
            # happen, so it is reported as what it is.
            return _verdict(
                attempted=True, branch=branch, base=base, old_sha=old_sha,
                new_sha=new_sha, commits_ahead=ahead, union=union_summary,
                already_current=True, **common,
                reason=(
                    f"branch already contains origin/{base} — nothing to "
                    "integrate; the conflict the forge reports is not in the tree"
                ),
            )

        if union_summary and union_summary.get("tests"):
            # The declared page tests run ONCE, on the COMPLETED tree, before
            # the push. Per conflict stop the tree is mid-replay and a later
            # commit could still change the file; the tree about to be pushed
            # is the one that has to pass. A failure pushes nothing.
            from tools.kanban import union_resolver

            timeout = union_resolver.DEFAULT_PYTEST_TIMEOUT
            if isinstance(union_rules, dict):
                try:
                    timeout = int((union_rules.get("verify") or {}).get(
                        "pytest_timeout_seconds", timeout))
                except (TypeError, ValueError):
                    pass
            ok, detail = union_resolver.run_declared_tests(
                tmp, union_summary["tests"], timeout=timeout)
            union_summary.setdefault("verifiers", []).append("pytest")
            if not ok:
                union_summary["outcome"] = "refused"
                union_summary["reason"] = detail
                return _verdict(
                    attempted=True, conflict=True, branch=branch, base=base,
                    old_sha=old_sha, new_sha=new_sha, commits_ahead=ahead,
                    union=union_summary, **common,
                    reason=("union-resolved %s failed its declared tests; "
                            "not pushing: " % noun + detail[:300]),
                )

        if dry_run:
            return _verdict(
                attempted=True, branch=branch, base=base, old_sha=old_sha,
                new_sha=new_sha, commits_ahead=ahead, union=union_summary, **common,
                reason=f"dry-run: {noun} clean ({ahead} commit(s)), push skipped",
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
                new_sha=new_sha, commits_ahead=ahead, union=union_summary, **common,
                reason=(
                    "force-with-lease push rejected (branch moved under us?): "
                    f"{_err(push)[:200]}"
                ),
            )
        logger.info(
            "rebase_recovery: %s %s (%s -> %s)",
            branch, verb["done"] % base, old_sha[:8], new_sha[:8],
        )
        return _verdict(
            attempted=True, pushed=True, branch=branch, base=base,
            old_sha=old_sha, new_sha=new_sha, commits_ahead=ahead,
            union=union_summary, **common,
            reason=(verb["done"] % base) + f" ({ahead} commit(s))",
        )
    except Exception as exc:  # noqa: BLE001 — a recovery attempt must never stall the watcher
        return _verdict(
            attempted=True, branch=branch, base=base, old_sha=old_sha,
            strategy=strategy, carried_merges=carried,
            reason=f"{strategy} errored: {exc}",
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
