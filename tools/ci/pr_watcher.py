# CUI // SP-CTI
"""OPT-70: tools/ci/pr_watcher.py — autonomous PR→resume feedback loop.

OPT-70 autonomous PR watcher + feedback loop pattern adapted from
jonwiggins/optio (MIT). See https://github.com/jonwiggins/optio
ICDEV implementation is independent — no runtime dependency on optio
or its Kubernetes/Fastify/BullMQ stack.

The watcher polls the kanban board for tasks whose tracked PR is
open, calls `gh pr view --json ...`, classifies the PR state via
tools.ci.error_classifier (OPT-72), and either:

    * Injects a resume-context message into the kanban executor queue
      (OPT-62 primitive) for CI failures, merge conflicts, or review
      changes-requested.
    * Auto-merges the PR when CI is green and review is approved
      (opt-in via args/pr_watcher_config.yaml).

Reads tasks where `executor_url` matches a github.com PR URL and the
most recent audit-trail workflow_state is `pr_opened` (or a resumable
state). Writes audit_trail rows for every action.

CLI:
    python tools/ci/pr_watcher.py --once --dry-run --json
    python tools/ci/pr_watcher.py --daemon --interval 30
    python tools/ci/pr_watcher.py --once --task task-xyz
    # Bounded run — one heartbeat, then exit (kax-obs-01):
    python tools/ci/pr_watcher.py --daemon --interval 1 --max-iterations 1

Non-goals:
    * GitLab / glab backend (deferred)
    * Linear/Jira ticket intake
    * Kubernetes or BullMQ
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import yaml

from tools.ci import error_classifier as ec
from tools.ci.merge_readiness import (
    BEHIND_MAIN,
    DEFAULT_MAX_BEHIND_COMMITS,
    NO_AUTOMERGE_LABELS,
    PROTECTED_PATH,
    READY,
    HELD_LABEL,
    classify_merge_readiness,
    fetch_required_checks,
    held_label_reason,
    hold_labels,
    measure_behind_by,
    protected_hits,
)
from tools.kanban.state_machine import KanbanState
from tools.kanban import deps as kanban_deps


logger = get_logger(__name__)
ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "args" / "pr_watcher_config.yaml"

# Max characters of CI log text we inject back into a resume message.
#: Minimum gap between resume injections for the SAME PR.
#:
#: max_resume_cycles_per_task is a budget of ATTEMPTS; this is what makes
#: each one an attempt. Without it the cap was spent at POLL speed —
#: measured 2026-08-16, PRs #1742 and #1744 each burned all five cycles in
#: about three minutes (one per ~45 s poll) and escalated to "manual
#: intervention required" while both were fully green and merged cleanly
#: under `git merge-tree`. No agent can read a message and push a fix in
#: 45 seconds, so those were not five attempts; they were one attempt and
#: four wasted budget entries.
#:
#: 600s gives five real attempts across ~50 minutes. Override per
#: deployment with `resume_cooldown_seconds` in the watcher config.
RESUME_COOLDOWN_SECONDS = 600

DEFAULT_CI_LOG_MAX = 4000

# Source prefix for the HITL alerts this watcher raises. Spelled once: the
# dashboard, tools/kanban/cli.py and tools/kanban/hitl_notify.py all key off the
# same string, and a sweep that parses it loosely can invent a task id.
_HITL_ALERT_PREFIX = "pr_watcher:hitl:"

# Liveness heartbeat (kax-obs-02). Every COMPLETED poll appends a row to the
# existing `heartbeat_checks` table — the same table tools/scout/daemon.py
# already uses to prove a daemon is alive — so "is the watcher polling?" can be
# answered without the log file landing anywhere. Deliberately NOT a new daemon
# and NOT a new log: a process-exists check (which the launcher already does at
# tools/genesis/launcher.py) cannot tell a live-but-wedged watcher from a
# healthy one, and the log is exactly the surface that went missing.
WATCHER_HEARTBEAT_CHECK_TYPE = "pr_watcher_poll"

_PR_URL_RE = re.compile(
    r"https?://github\.com/[^/\s]+/[^/\s]+/pull/\d+",
    re.IGNORECASE,
)


# ────────────────────────────────────────────────────────────────────────────
# Data types
# ────────────────────────────────────────────────────────────────────────────


# Which KIND of conflict the forge is reporting (kpr-watch-07). Three answers,
# because they need three different remedies — see `PRWatcher.classify_conflict`.
CONFLICT_REAL = "real"
CONFLICT_UNION_ONLY = "union_only"
CONFLICT_PHANTOM = "phantom"


@dataclass
class WatcherAction:
    task_id: str
    pr_url: str
    classification: str
    action: str  # 'resume' | 'merge' | 'wait' | 'escalate' | 'dry_run'
    reason: str = ""
    resume_cycle: int = 0
    context_preview: str = ""
    # The base sha a rebase attempt was spent against. A rebase resolves the
    # collision with the base AS IT WAS; when the base moves and collides again
    # the old attempt describes a world that no longer exists, so the budget
    # counts attempts per base era rather than per PR lifetime. Absent on every
    # row written before kpr-watch-07, which is why "no recorded sha" reads as
    # "another era" rather than as "this one".
    base_sha: str = ""


@dataclass
class WatcherReport:
    started_at: str
    finished_at: str
    tasks_checked: int
    actions: List[WatcherAction] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "tasks_checked": self.tasks_checked,
            "actions": [asdict(a) for a in self.actions],
        }


# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────


def load_config(path: Optional[pathlib.Path] = None) -> dict:
    p = path or DEFAULT_CONFIG
    if not p.exists():
        return {
            "poll_interval_seconds": 30,
            "max_resume_cycles_per_task": 5,
            "resume_cooldown_seconds": RESUME_COOLDOWN_SECONDS,
            "auto_merge_enabled": False,
            "auto_merge_require_approval": True,
            "ci_log_max_chars": DEFAULT_CI_LOG_MAX,
            "refuse_merge_when_behind": True,
            "max_behind_commits": DEFAULT_MAX_BEHIND_COMMITS,
        }
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ────────────────────────────────────────────────────────────────────────────
# Task + PR lookups
# ────────────────────────────────────────────────────────────────────────────


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp, tolerating a trailing 'Z' and a naive value."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_pr_url(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = _PR_URL_RE.search(text)
    return match.group(0) if match else None



def _is_gate_task(conn, task_id: str) -> bool:
    """True when `task_id` is a manual-gate sentinel.

    Reads the title so the predicate sees both halves it matches on (the id
    shape AND the title marker). A lookup failure answers False: this guard
    must never be the reason the watch loop stalls.
    """
    try:
        from tools.kanban.gates import is_manual_gate
    except Exception:  # noqa: BLE001 — gates module unavailable
        return False
    title = ""
    try:
        row = conn.execute(
            "SELECT title FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if row is not None:
            title = (row[0] if not isinstance(row, dict) else row.get("title")) or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("pr_watcher: gate title lookup failed for %s: %s", task_id, exc)
    return bool(is_manual_gate(task_id, title))


def _held_by_a_gate(conn, task_id: str) -> bool:
    """True when this task's own dependency is not satisfied.

    This is the predicate that keeps auto-ready honest. A MANUAL-ONLY card (AGOV)
    expresses "a human decides when this ships" by holding a gate row that every
    task depends on. So a task whose dependency is unsatisfied must keep its
    draft: the draft is that card's brake, and taking it off would ship work a
    human deliberately held.

    WHICH dependency is asked of ``tools.kanban.deps``, the same predicate
    ``promote_backlog_to_scheduled`` and the dispatcher use — holding a PR on a
    scalar the junction graph superseded would leave a released task's PR in
    draft forever, which is the same stall one step downstream (kpr-fix-02).

    Errs toward HELD. A lookup that fails answers True, because the cost of a
    false "held" is one PR a human marks ready, and the cost of a false "free"
    is auto-merging gated work.
    """
    try:
        return bool(kanban_deps.blocking_deps(task_id, conn))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pr_watcher: dependency lookup failed for %s: %s", task_id, exc)
        return True


def _record_gate_refusal(conn, task_id: str, reason: str) -> None:
    """Append-only audit row for a refused gate completion. Best-effort."""
    try:
        conn.execute(
            "INSERT INTO kanban_status_transitions "
            "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("kst-" + uuid.uuid4().hex[:12], task_id, "pr_opened", "refused",
             "pr_watcher",
             ("manual gate not completed by merge; "
              f"release deliberately. {reason}")[:200],
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("pr_watcher: gate-refusal audit row failed: %s", exc)


def _set_task_status(get_conn, task_id: str, status: str, reason: str = "") -> bool:
    """Write the task's status. The watcher is the ONLY component that knows when
    a PR actually merged, so it is the right owner of the pr_opened -> done edge.

    Previously it recorded 'done' in the audit trail and never touched
    kanban_tasks — the board could not tell an open PR from a finished one.
    Never raises: a status-write failure must not stall the watch loop.

    Refuses to complete a manual gate — see the comment at the guard below.
    """
    try:
        conn = get_conn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pr_watcher: no DB connection to set %s -> %s: %s",
                       task_id, status, exc)
        return False
    try:
        # A MANUAL GATE IS NEVER COMPLETED BY A MERGE.
        #
        # A gate is a sentinel, not work: other tasks depend on it precisely so
        # they do NOT dispatch. On 2026-08-08 a dispatched session used the gate
        # task `hgx-gate-01` to carry a fix and opened a PR against it. When that
        # PR merged, this function set the gate `done` — releasing six slices that
        # had an agent edit its own guardrails — and then re-applied `done` every
        # 30s cycle, so every manual reset was undone within seconds.
        #
        # Guarding here rather than at the merge call site covers every caller.
        # Only `done` is refused; the gate can still be parked or failed.
        if status == "done" and _is_gate_task(conn, task_id):
            logger.warning(
                "pr_watcher: REFUSING to complete manual gate %s (%s). A gate is a "
                "sentinel, not work — a PR should never be attached to one. Release "
                "it deliberately: python -m tools.kanban.cli --set-status %s done",
                task_id, reason or "no reason given", task_id,
            )
            _record_gate_refusal(conn, task_id, reason)
            return False

        conn.execute(
            "UPDATE kanban_tasks SET status = %s, updated_at = %s WHERE id = %s",
            (status, datetime.now(timezone.utc).isoformat(), task_id),
        )
        try:
            from tools.kanban.transition_reason import resolve_transition_reason
            _reason = resolve_transition_reason(
                reason, from_status="pr_opened", to_status=status,
                actor="pr_watcher",
            )[:200]
            conn.execute(
                "INSERT INTO kanban_status_transitions "
                "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("kst-" + uuid.uuid4().hex[:12], task_id, "pr_opened", status,
                 "pr_watcher", _reason,
                 datetime.now(timezone.utc).isoformat()),
            )
        except Exception as _exc:  # noqa: BLE001 — audit row is best-effort
            logger.warning(
                "_set_task_status: best-effort INSERT into kanban_status_transitions failed (non-blocking): %s",
                _exc,
            )
        conn.commit()
        logger.info("pr_watcher: %s -> %s (%s)", task_id, status, reason)

        # WAKE EVENTS (agov-wake-03): `wake_on_event("task:kax-merge-02:done")`.
        # The watcher owns the pr_opened -> done edge, so a task completing
        # through the PR flow is only ever observed here — state_machine's
        # emitter never sees it. Best-effort; a wake must never undo a committed
        # status change.
        try:
            from tools.agent_runtime.wake_signals import emit_task_status

            emit_task_status(task_id, status, conn=conn)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pr_watcher: wake event emit skipped for %s: %s", task_id, exc)

        # Harness co-learning: this is a TERMINAL transition, so the harness
        # needs the outcome here. _move_task() in the kanban reflex carries the
        # same hook, but under the PR flow it deliberately does NOT mark the
        # task done — the work is not finished until the PR merges, and this
        # watcher owns that edge. The result was that every task completing
        # through the PR flow (the primary build path) recorded a codegen
        # decision at dispatch and then never an outcome: harness_eval filled
        # with rows whose actual_outcome stayed NULL, and compute_metrics
        # derived precision/recall/ECE from the small minority of tasks that
        # failed outright. Best-effort — a telemetry write must never stall the
        # watch loop or undo a committed status change.
        if status in ("done", "token_exhausted", "failed"):
            try:
                from tools.genesis.harness.eval_harness import record_outcome

                record_outcome(task_id, "resolved" if status == "done" else "failed")
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "pr_watcher: harness record_outcome skipped for %s: %s",
                    task_id, exc,
                )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("pr_watcher: failed to set %s -> %s: %s", task_id, status, exc)
        return False
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

def _enforced_done_ok(get_connection, task_id: str) -> Tuple[bool, str]:
    """Enforced done-gate for auto-merge (Governed Delivery Pipeline).

    Under ``KANBAN_PIPELINE_ENFORCE``, a kanban PR may auto-merge only after the
    task's ICDEV done-verification (conformance + code-quality/coherence/tests,
    recorded in ``kanban_verifications``) has PASSED — CI green alone is not
    enough. This closes the gap where a conformance failure could still be
    merged (a PR can go CI-green while ``review_passed`` is false).

    Returns ``(ok, reason)``. Enforcement OFF → always ``ok`` (no new blocker,
    behavior unchanged). **Fail-closed** under enforcement: a missing, failed, or
    unreadable verification holds the merge (the watcher retries next cycle).
    """
    if os.environ.get("KANBAN_PIPELINE_ENFORCE", "0").strip().lower() not in ("1", "true", "yes"):
        return True, "enforcement off"
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT result, review_passed FROM kanban_verifications "
            "WHERE task_id = %s ORDER BY verified_at DESC LIMIT 1",
            (task_id,),
        )
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — hold the merge, don't merge blind
        return False, f"enforced gate: verification unreadable, holding ({exc})"
    if not row:
        return False, "enforced gate: awaiting ICDEV done-verification"
    result = str((row["result"] if isinstance(row, dict) else row[0]) or "").lower()
    review_passed = row["review_passed"] if isinstance(row, dict) else row[1]
    if result == "failed":
        return False, "enforced gate: ICDEV verification result=failed (e.g. conformance)"
    if review_passed == 0:  # int 0 or bool False — conformance failed (None = not judged, allowed)
        return False, "enforced gate: conformance review_passed=false"
    if result in ("pass", "passed", "bypassed"):
        return True, f"enforced gate passed (result={result})"
    return False, f"enforced gate: verification not yet passed (result={result or 'pending'})"


def _latest_verification(get_connection, task_id: str) -> Optional[dict]:
    """The row `_enforced_done_ok` will read, or None. Never raises."""
    try:
        cur = get_connection().cursor()
        cur.execute(
            "SELECT result, review_passed, reason FROM kanban_verifications "
            "WHERE task_id = %s ORDER BY verified_at DESC LIMIT 1",
            (task_id,),
        )
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.debug("pr_watcher: could not read verification for %s: %s", task_id, exc)
        return None
    if not row:
        return None
    if isinstance(row, dict):
        return row
    return {"result": row[0], "review_passed": row[1], "reason": row[2]}


def reverify_is_allowed(latest: Optional[dict], *, allow_when_missing: bool) -> Tuple[bool, str]:
    """Whether re-verifying this task could only refresh a verdict, never launder one.

    Re-verification recomputes "does this branch carry work" and writes a row with
    ``review_passed`` NULL. `_enforced_done_ok` reads NULL as "not judged, allowed",
    so appending one **clears** whatever the previous row said. That makes two cases
    unsafe:

    * ``review_passed == 0`` — conformance genuinely failed. A fresh NULL row would
      overwrite a real judgment with no judgment and merge the PR. Never do this.
    * no row at all — the task has never been verified. Manufacturing a passing
      verdict from git state would let a PR merge with no conformance review at all,
      which is precisely the gate this whole mechanism exists to enforce. Off by
      default; ``reverify_when_missing`` opts in for operators who accept that.

    The case this IS for: a row that FAILED for a reason unrelated to conformance —
    the dispatch-time verifier reads process-local dicts and reports "No git commits
    found on task branch" whenever the daemon restarted mid-flight. That verdict is
    an artifact of process lifetime, and refreshing it is exactly right.
    """
    if latest is None:
        return (allow_when_missing,
                "no prior verification — task never judged"
                if not allow_when_missing else "no prior verification (opted in)")
    if latest.get("review_passed") == 0:
        return False, "conformance review_passed=false — a refresh would launder it"
    result = str(latest.get("result") or "").lower()
    if result in ("pass", "passed", "bypassed"):
        return False, "already passing — nothing to refresh"
    return True, f"prior verification result={result or 'unknown'}, not a conformance failure"


def list_pr_tasks(
    get_connection,
    task_id: Optional[str] = None,
) -> List[dict]:
    """Find kanban tasks that have a github PR url.

    Uses `executor_url` (the column OPT-31 added to link tasks to their
    executor artifacts) or scrapes it out of the description text.
    Returns a list of dicts: {id, title, pr_url, executor_url}.
    """
    conn = get_connection()
    try:
        if task_id:
            rows = conn.execute(
                "SELECT id, title, description, status, executor_url "
                "FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, description, status, executor_url "
                "FROM kanban_tasks WHERE status IN "
                # 'pr_opened' is the state a task sits in from the moment its PR
                # is opened until it merges — it is THE state this watcher exists
                # to service. Omitting it meant the watcher lost sight of a task
                # the instant it had a PR.
                "('in_progress', 'scheduled', 'pr_opened', "
                " 'ci_failed', 'merge_conflict', 'changes_requested')"
            ).fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    out: List[dict] = []
    for row in rows:
        # psycopg2 rows behave like both tuples and dicts depending on
        # the factory. Normalize to dict access by column name.
        data = {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"] or "",
            "status": row["status"],
            "executor_url": row["executor_url"] or "",
        }
        pr_url = _parse_pr_url(data["executor_url"]) or _parse_pr_url(
            data["description"]
        )
        if pr_url:
            data["pr_url"] = pr_url
            out.append(data)
    return out


# ────────────────────────────────────────────────────────────────────────────
# gh CLI wrappers
# ────────────────────────────────────────────────────────────────────────────


# The coordination / generated path lists moved to tools/git/coordination_paths.py
# (rem-hyg-07) so the seed-time sibling check in tools/kanban/lane_conflicts.py
# asks the SAME question this merge-time guard does. A second divergent copy is
# worse than none: the two checks would report different collisions and a reader
# could not tell which list was current. The curation, and the two deadlocks that
# produced it, are documented there.
#
# These private aliases stay because they are the names this module's guard and
# its tests use; they are re-exports, not a copy.
from tools.git.coordination_paths import (  # noqa: E402,F401
    COORDINATION_PATH_MARKERS as _ADDITIVE_PATH_MARKERS,  # noqa: F401
    GENERATED_PATH_MARKERS as _GENERATED_PATH_MARKERS,  # noqa: F401
    is_coordination_path as _is_additive_path,
    is_generated_path as _is_generated_path,  # noqa: F401
)


#: `isDraft` is not cosmetic. GitHub refuses `gh pr merge` on a draft with
#: "Pull Request is still a draft", and without this field the watcher could not
#: SEE that — so it re-attempted the same refused merge every cycle, forever.
#: Five of ten open PRs on 2026-08-09 were green drafts sitting untouched for
#: exactly that reason, each waiting on a human to click "Ready for review".
#: Any of these on a PR stops the unlinked sweep. A human may open a PR to
#: discuss rather than to land, and the cost of guessing wrong is a merge nobody
#: asked for — so the escape hatch is cheap, obvious, and checked first.
#: kpr-watch-01 moved the list itself to `tools/ci/merge_readiness.py` so the
#: merger and the report cannot hold two different lists. Alias kept for
#: existing importers.
_NO_AUTOMERGE_LABELS = NO_AUTOMERGE_LABELS

#: `mergeStateStatus` and `headRefOid` are kpr-stale-02. `mergeable` answers
#: only "does this collide TEXTUALLY", and reports MERGEABLE for a branch
#: arbitrarily far behind main — so neither this watcher nor the report could
#: SEE a stale branch, and the CONFLICTING interlock caught only the subset that
#: happened to collide. `mergeStateStatus` is the forge's own verdict (BEHIND
#: only where the base branch has `strict` protection, which this repo does NOT
#: — measured 2026-08-18), and `headRefOid` is what `measure_behind_by` compares
#: against the base tip to get the count the check actually runs on.
#: `labels` is kpr-watch-04. `_NO_AUTOMERGE_LABELS` was referenced at exactly
#: ONE site — the unlinked sweep — and the task-linked path could not have
#: honoured a hold label even if it wanted to, because this field list never
#: asked for one. So the documented escape hatch did not cover the path that
#: does most of the merging: a human labelling a `kanban/<id>` PR `do-not-merge`
#: got no warning and no effect, and the PR merged itself. A label has to mean
#: the same thing on both paths, and that starts with fetching it.
_GH_JSON_FIELDS = (
    "state,statusCheckRollup,reviews,mergeable,mergeStateStatus,isDraft,labels,"
    "headRefName,headRefOid,baseRefName,updatedAt,createdAt,number,url"
)


def _pr_number(url: str) -> int:
    """The PR number, or a very large number when it cannot be read.

    Unreadable sorts LAST so it never wins the tie-break by accident.
    """
    m = re.search(r"/pull/(\d+)", url or "")
    return int(m.group(1)) if m else 1 << 30


def _wins_sibling_tiebreak(pr_url: str, siblings, blocked=None) -> bool:
    """True when THIS PR is the one that should merge first among its siblings.

    THE DEADLOCK THIS BREAKS. hold_on_sibling_conflict exists to SERIALISE merges
    that touch the same source file — merge one, let the rest rebase. It held
    every one of them instead. If A shares a file with B, then B also shares one
    with A, so both are held and nothing breaks the tie: with 14 AGOV PRs over
    the same new modules on 2026-08-09, every PR was a sibling of several others
    and the entire board sat at "awaiting merge" with zero active tasks.
    Serialising requires choosing who goes first; refusing to choose is not
    serialisation, it is a stall.

    Lowest PR number wins, which is deterministic and stable: every watcher
    iteration and every process reaches the same verdict without coordination, so
    two watchers cannot both decide they are first. It also means the OLDEST PR
    goes first, which is the fair reading of a queue.

    The guard itself is unchanged for everyone else — the losers still wait, and
    still rebase afterwards.

    `blocked` — urls of siblings that CANNOT merge right now (draft, or the forge
    reports CONFLICTING) — are dropped from the tie-break, because the same
    sentence above applies one level up. A PR held behind a sibling that cannot
    merge is not being serialised behind it; it is waiting for a queue position
    that will never come free, and the hold is re-evaluated every poll so the
    wait never expires. MEASURED 2026-08-17 by tools/ci/sibling_hold_survey.py:
    of six open PRs, #1769 was held behind #1744 (a draft with a real CLAUDE.md
    conflict, open over a day) and #1781 behind #1773 — under the CURRENT
    posture, not a hypothetical widened one.

    Dropping them cannot let two PRs that share a file merge together, which is
    the invariant the guard exists for: a sibling excluded here is one the forge
    would refuse to merge anyway. And it is not permanent — the exclusion is
    recomputed every poll, so a blocker that gets rebased back to MERGEABLE
    rejoins the queue and wins it.
    """
    mine = _pr_number(pr_url)
    skip = set(blocked or ())
    return all(
        mine < _pr_number(other)
        for other in (siblings or {})
        if other not in skip
    )


def _pr_can_merge(state: dict) -> bool:
    """Whether an open PR could merge at all right now.

    Unknown counts as MERGEABLE: erring the other way would drop a sibling from
    the tie-break on no evidence, and letting a PR past a hold is the direction
    with consequences.
    """
    if (state or {}).get("draft"):
        return False
    mergeable = (state or {}).get("mergeable")
    if mergeable is None:
        return True
    return str(mergeable).upper() != "CONFLICTING"


def repo_default_branch(*, runner=None, gh_bin: str = "gh") -> str:
    """Resolve the repository's default branch name.

    Tries `gh repo view --json defaultBranchRef`, then
    `git symbolic-ref refs/remotes/origin/HEAD`, then falls back to
    "main". Pass `runner` in tests to avoid hitting the real CLIs.
    """
    if runner is None:
        runner = subprocess.run
    try:
        proc = runner(
            [gh_bin, "repo", "view", "--json", "defaultBranchRef"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if proc.returncode == 0:
            ref = json.loads(proc.stdout or "{}").get("defaultBranchRef") or {}
            name = (ref.get("name") or "").strip()
            if name:
                return name
    except Exception:
        pass
    try:
        proc = runner(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().removeprefix("origin/")
    except Exception:
        pass
    return "main"


def fetch_pr_state(
    pr_url: str,
    *,
    runner=None,
    gh_bin: str = "gh",
) -> dict:
    """Return the parsed JSON from `gh pr view <url> --json <fields>`.

    Raises RuntimeError on any gh failure. Pass `runner` in tests to
    avoid hitting the real gh CLI.
    """
    cmd = [gh_bin, "pr", "view", pr_url, "--json", _GH_JSON_FIELDS]
    if runner is None:
        runner = subprocess.run
    try:
        proc = runner(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"gh CLI not on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gh pr view timed out: {exc}") from exc

    if proc.returncode != 0:
        raise RuntimeError(
            f"gh pr view failed: exit={proc.returncode} "
            f"stderr={(proc.stderr or '').strip()[:200]}"
        )
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"gh pr view returned non-JSON: {exc}"
        ) from exc


def fetch_ci_logs(
    pr_url: str,
    *,
    runner=None,
    gh_bin: str = "gh",
    max_chars: int = DEFAULT_CI_LOG_MAX,
) -> str:
    """Best-effort pull of recent CI log text for the PR's failing run.

    If gh can't be reached or no logs exist, returns an empty string.
    """
    if runner is None:
        runner = subprocess.run
    # gh run list/view is the cleanest path; return a friendly empty
    # string on any failure so the caller can still proceed.
    try:
        proc = runner(
            [gh_bin, "pr", "checks", pr_url],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        if proc.returncode != 0:
            return ""
        return (proc.stdout or "")[:max_chars]
    except Exception:
        return ""


# ────────────────────────────────────────────────────────────────────────────
# Resume context builder
# ────────────────────────────────────────────────────────────────────────────


def prepare_resume_context(
    task_id: str,
    classification: KanbanState,
    pr_state: dict,
    ci_logs: str,
    *,
    max_chars: int = DEFAULT_CI_LOG_MAX,
) -> str:
    """Build a compact resume-context message to inject via OPT-62."""
    lines: List[str] = []
    lines.append(
        f"[pr_watcher resume] task={task_id} PR#{pr_state.get('number','?')} "
        f"branch={pr_state.get('headRefName','?')}"
    )
    lines.append(f"Current state: {classification.value}")

    if classification == KanbanState.CI_FAILED:
        failed = [
            c for c in pr_state.get("statusCheckRollup") or []
            if (c.get("conclusion") or "").upper() == "FAILURE"
        ]
        if failed:
            names = ", ".join(c.get("name", "?") for c in failed[:5])
            lines.append(f"Failing checks: {names}")
        category = ec.classify_ci_log_failure(ci_logs)
        if category:
            lines.append(f"Failure category: {category}")
        if ci_logs:
            lines.append("Last CI log excerpt:")
            lines.append(ci_logs[-max_chars:])

    elif classification == KanbanState.MERGE_CONFLICT:
        lines.append(
            "A merge conflict is blocking this PR. Pull the latest "
            "base branch, resolve conflicts, force-push (WITHIN the "
            "task worktree — never against main)."
        )

    elif classification == KanbanState.CHANGES_REQUESTED:
        comments: List[str] = []
        for review in pr_state.get("reviews") or []:
            if (review.get("state") or "").upper() == "CHANGES_REQUESTED":
                body = (review.get("body") or "").strip()
                author = review.get("author", {}).get("login") \
                    if isinstance(review.get("author"), dict) \
                    else review.get("author", "reviewer")
                if body:
                    comments.append(f"- {author}: {body[:600]}")
        if comments:
            lines.append("Reviewer feedback:")
            lines.extend(comments)
        else:
            lines.append("A reviewer requested changes but left no body.")

    lines.append("")
    lines.append(
        "Please address the issue above and update the PR. The watcher "
        "will re-poll and auto-merge once CI is green and review is "
        "approved."
    )
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Core watcher
# ────────────────────────────────────────────────────────────────────────────



def repo_of(pr_url: str | None) -> str | None:
    """``owner/repo`` from a GitHub PR url, or None.

    Module-level and pure on purpose: an external-repo task's PR lives in
    ANOTHER repository, and `gh pr list` without --repo lists whichever repo the
    process happens to be standing in. Kept off PRWatcher so a caller can ask
    without holding a watcher -- and so the existing test doubles that stub the
    watcher keep working.
    """
    if not pr_url:
        return None
    parts = [p for p in str(pr_url).split("/") if p]
    try:
        i = parts.index("pull")
    except ValueError:
        return None
    return "/".join(parts[i - 2:i]) if i >= 2 else None


class PRWatcher:
    def __init__(
        self,
        *,
        config: Optional[dict] = None,
        get_connection=None,
        queue_message=None,
        fetch_state=None,
        fetch_logs=None,
        auto_merge_runner=None,
        default_branch_resolver=None,
        pr_list_runner=None,
        rebase_fn=None,
        behind_probe=None,
        gh_runner=None,
        dry_run: bool = False,
    ):
        self.config = config or load_config()
        self.dry_run = dry_run
        # Injected dependencies for testability
        self._get_connection = get_connection
        self._queue_message = queue_message
        self._fetch_state = fetch_state or fetch_pr_state
        self._fetch_logs = fetch_logs or fetch_ci_logs
        self._auto_merge_runner = auto_merge_runner or subprocess.run
        self._pr_list_runner = pr_list_runner or subprocess.run
        self._rebase_fn = rebase_fn
        # kpr-stale-02: (base, head sha) -> commits behind, or None for
        # UNMEASURED. Injectable so tests never touch a live forge.
        self._behind_probe = behind_probe or measure_behind_by
        self._behind_cache: Dict[tuple, Optional[int]] = {}
        self._default_branch_resolver = (
            default_branch_resolver or repo_default_branch
        )
        self._default_branch_cache: Optional[str] = None
        # task-det-295a9bb95e: the branch-protection REQUIRED set, read through
        # this runner and cached (see `required_checks`). Injectable so tests
        # never touch a live forge.
        # None defers to merge_readiness's default runner -- the seam a test
        # session stubs so an un-injected watcher never reads the live forge.
        self._gh_runner = gh_runner
        self._required_checks_cache: Optional[tuple] = None   # (monotonic, set|None)
        # Re-verification attempts per task, for the life of this watcher.
        # Bounded so a task whose verification genuinely fails cannot spin: it is
        # re-checked once, and if it still fails the PR stays held until a human
        # or a dispatch changes something.
        self._reverify_attempts: Dict[str, int] = {}

    # ── helpers ─────────────────────────────────────────────────────

    def _connection(self):
        if self._get_connection is not None:
            return self._get_connection
        # Lazy import to avoid pulling psycopg2 during pure tests
        from tools.db.storage import get_connection  # type: ignore
        return get_connection

    def _send_resume(self, task_id: str, text: str) -> bool:
        if self.dry_run:
            return True
        if self._queue_message is not None:
            try:
                self._queue_message(task_id, text, sender="pr_watcher")
                return True
            except Exception as exc:
                logger.warning(
                    "pr_watcher: queue_message failed: %s", exc
                )
                return False
        # Real runtime path
        try:
            from tools.airgap.hook_compat import queue_message
            queue_message(task_id, text, sender="pr_watcher")
            return True
        except Exception as exc:
            logger.warning("pr_watcher: queue_message import failed: %s", exc)
            return False

    def _emit_wake_events(
        self, pr_url: str, classification: Any, state: dict,
    ) -> Dict[str, Any]:
        """Fire this PR's wake event keys. Best-effort — never raises.

        Kept as a method purely so a test can observe (or replace) it without
        reaching into the wake store. `dry_run` emits nothing: a --dry-run poll
        must not resume a real agent.
        """
        if self.dry_run:
            return {"keys": [], "promoted": []}
        try:
            from tools.agent_runtime.wake_signals import emit_pr_state

            return emit_pr_state(
                pr_url,
                classification=classification,
                pr_state=(state or {}).get("state"),
            )
        except Exception as exc:  # noqa: BLE001 — a wake must never break the watch loop
            logger.debug("pr_watcher: wake event emit failed for %s: %s", pr_url, exc)
            return {"keys": [], "promoted": [], "error": str(exc)}

    def _open_pr_index(self, repo: str | None = None) -> Dict[str, dict]:
        """url -> {files, mergeable, draft} for every open PR (single gh call).

        `mergeable`/`draft` are what let the tie-break skip a sibling that cannot
        merge — see `_wins_sibling_tiebreak`. Fetched in the SAME call that
        already lists the files, so the guard gained a second input without a
        second API round-trip per cycle.

        Best-effort: returns {} if gh is unavailable / errors, so the sibling
        check degrades to a no-op rather than blocking the watcher.
        """
        try:
            # --repo when the caller names one: an EXTERNAL-repo task's PR is not
            # in this checkout's listing, and its own absence is then read as
            # "the listing failed" -- which made land.py refuse every ICDEV[FT]
            # task on no_sibling_conflict, whatever the PR looked like
            # (measured 2026-08-30 on icdev_ft#320).
            proc = self._pr_list_runner(
                ["gh", "pr", "list", "--state", "open", "--json",
                 "url,files,mergeable,isDraft", "--limit", "200",
                 *(["--repo", repo] if repo else [])],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60,
            )
            if getattr(proc, "returncode", 1) != 0:
                return {}
            data = json.loads(proc.stdout or "[]")
        except Exception as exc:  # noqa: BLE001
            logger.debug("pr_watcher: open-PR file listing failed: %s", exc)
            return {}
        out: Dict[str, dict] = {}
        for pr in data:
            url = pr.get("url")
            if not url:
                continue
            out[url] = {
                "files": {
                    f.get("path", "")
                    for f in (pr.get("files") or []) if f.get("path")
                },
                "mergeable": pr.get("mergeable"),
                "draft": bool(pr.get("isDraft")),
            }
        return out

    def _protected_paths(self) -> List[str]:
        """Paths no automation may merge a change to. Empty disables the guard."""
        raw = self.config.get("protected_paths") or []
        return [str(x).strip() for x in raw if str(x or "").strip()]

    def _protected_hits(self, pr_url: str) -> List[str]:
        """Protected paths this PR touches — [] when clean or unguarded.

        Nearly free: `_open_pr_index` already fetches `url,files` for every open
        PR in ONE gh call, every poll, to build the sibling-conflict map. This is
        an intersection over a set already in memory.

        FAIL-CLOSED, and deliberately the OPPOSITE default from that sibling map,
        which degrades to a warning when the listing is unavailable. The
        asymmetry is the point and it is not a bug: a missed sibling conflict
        costs a retry, whereas a missed protected path costs the control itself —
        this is the one guard standing between a defective merge ladder and its
        own unreviewed merge. So a PR absent from the index is treated as
        protected, not as clean.
        """
        paths = self._protected_paths()
        if not paths:
            return []
        entry = self._open_pr_index().get(pr_url)
        files = entry.get("files") if entry else None
        return protected_hits(files, paths) or []

    def _protected_already_held(self, pr_url: str) -> bool:
        """Has this PR's hold already been recorded?

        `audit_trail` is APPEND-ONLY, and the watcher polls every few seconds.
        Auditing a standing hold each cycle wrote 161 rows in 59 minutes for two
        PRs — a signal buried in its own repetition, and unbounded for a PR left
        held over a weekend. A hold is an EVENT; it is recorded when it starts.

        Read from the audit rather than an in-memory set so the dedupe survives
        a daemon restart, which is precisely when a re-audit storm would start
        again. Best-effort: `_count_audit_actions` already returns 0 on any
        error, and a duplicate audit row is a far smaller harm than a missed
        refusal, so an unreadable audit re-records rather than going quiet.
        """
        return self._count_audit_actions(
            "", ("pr_watcher.protected_path_hold",), pr_url=pr_url) > 0

    def _refuse_protected(self, pr_url: str, task_id: str = "") -> List[str]:
        """Report and audit a protected-path refusal. Returns the hits (or [])."""
        hits = self._protected_hits(pr_url)
        if not hits:
            return []
        if self._protected_already_held(pr_url):
            # Still SAID every cycle, at debug: the refusal must never become
            # invisible, and a held PR that stops appearing anywhere is how the
            # AWAITING MERGE column went quiet in the first place.
            logger.debug(
                "pr_watcher: still refusing %s — protected path(s) %s (already "
                "audited)", pr_url, ", ".join(hits))
            return hits
        logger.warning(
            "pr_watcher: REFUSING to merge %s — it touches protected path(s) %s. "
            "A human must review and merge this by hand.",
            pr_url, ", ".join(hits))
        self._audit(WatcherAction(
            task_id=task_id, pr_url=pr_url, classification="blocked",
            action="protected_path_hold",
            reason=("touches protected path(s): " + ", ".join(hits))[:500],
            resume_cycle=0,
        ))
        return hits

    def _refuse_held_label(
        self, pr_url: str, state: dict, task_id: str = "",
    ) -> List[str]:
        """Report and audit a hold-label refusal. Returns the labels (or []).

        kpr-watch-04. Shaped exactly like `_refuse_protected` above, for the
        same reason it is: a refusal nobody can see is how work goes quiet. The
        label case was the ONE refusal in the unlinked sweep that already said
        something out loud, and the task-linked path said nothing because it
        never looked. Both now report the `held_label` state through the shared
        classifier's own vocabulary.

        The membership test itself is `merge_readiness.hold_labels`, not a
        second `.get("labels")` here — see that function for why a shared LIST
        was not enough.
        """
        held = hold_labels(state or {})
        if not held:
            return []
        logger.warning(
            "pr_watcher: REFUSING to merge %s — it %s. A human put that label "
            "there; remove it to let the pipeline land this.",
            pr_url, held_label_reason(held))
        self._audit(WatcherAction(
            task_id=task_id, pr_url=pr_url, classification="blocked",
            action="held_label_hold",
            reason=held_label_reason(held)[:500],
            resume_cycle=0,
        ))
        return held

    def _open_pr_files(self, repo: str | None = None) -> Dict[str, set]:
        """Map every open PR's url -> set of changed file paths.

        ``repo`` ("owner/name") lists THAT repository instead of the one this
        process is standing in -- required for an external-repo task."""
        return {url: e["files"] for url, e in self._open_pr_index(repo).items()}

    def _landed_map(self, tasks: List[dict]) -> Dict[str, dict]:
        """task_id -> landed-check report for every task with an open PR.

        `tools.kanban.landed_check` already answers "is this task id ALREADY on
        the default branch", and it is already consulted at SEED time
        (`task_factory`) and at DISPATCH time (`reflexes/kanban.py`). Neither
        covers the window this closes: a PR is opened, the same work lands under
        a DIFFERENT PR number while it sits, and the stale PR can then only
        merge as a REVERT — #1651 was -38/+26 on rest_v1.py, i.e. it would have
        deleted 38 lines main currently has, with every gate on the board green
        because every gate on the board asks about the PR.

        One `git log --grep` answers for the whole batch (git ORs repeated
        --grep), so this costs one subprocess per cycle, not one per PR.

        FAIL-OPEN: any failure returns {}, and a report with `checked: False` is
        never treated as a finding. An unreachable git must not wedge merging.
        """
        ids = [str(t.get("id")) for t in tasks if t.get("id")]
        if not ids:
            return {}
        try:
            from tools.kanban import landed_check

            return landed_check.check_landed_bulk(ids)
        except Exception as exc:  # noqa: BLE001 — advisory check, never fatal
            logger.debug("pr_watcher: landed-check batch failed: %s", exc)
            return {}

    def _sibling_conflicts(self, candidate_url: str, file_map: Dict[str, set]) -> Dict[str, set]:
        """Return {other_pr_url: shared_integrity_files} for OPEN PRs that touch a
        non-additive file the candidate PR also touches.

        Additive/coordination files (manifest shards, APPEND_ONLY_TABLES,
        component_registry, etc. — see _ADDITIVE_PATH_MARKERS) are union-merged and
        excluded, so only genuine same-source-file collisions (two branches editing
        the same blueprint.py / module / migration) are flagged.
        """
        cand = {f for f in file_map.get(candidate_url, set()) if not _is_additive_path(f)}
        if not cand:
            return {}
        conflicts: Dict[str, set] = {}
        for url, files in file_map.items():
            if url == candidate_url:
                continue
            shared = cand & {f for f in files if not _is_additive_path(f)}
            if shared:
                conflicts[url] = shared
        return conflicts

    # ── staleness (kpr-stale-02) ────────────────────────────────────

    def _max_behind(self) -> int:
        return int(self.config.get(
            "max_behind_commits", DEFAULT_MAX_BEHIND_COMMITS))

    def _behind_by(self, state: dict) -> Optional[int]:
        """Commits this PR's branch is behind its base. ``None`` = UNMEASURED.

        Cached per (base, head sha) for the life of the watcher, because the
        answer cannot change without the head sha or the base tip changing, and
        this is called from a loop that runs every `poll_interval_seconds`.
        Keyed on the BASE NAME rather than the base sha on purpose: the cache
        would otherwise never hit, since the base tip moves constantly — and a
        count that is one or two commits stale still separates a routine branch
        from a `#1651` (36 behind) by an order of magnitude. The cache is
        dropped when the process restarts, which `restart_on_code_change`
        already does on every merged change to this file.
        """
        base = (state.get("baseRefName") or "").strip() or self._default_branch()
        head = (state.get("headRefOid") or "").strip()
        if not head:
            return None
        key = (base, head)
        if key not in self._behind_cache:
            self._behind_cache[key] = self._behind_probe(base, head)
        return self._behind_cache[key]

    def _stale_verdict(self, state: dict) -> Optional[Tuple[int, str]]:
        """``(behind_by, reason)`` when this PR is TOO FAR BEHIND to merge.

        ``None`` means "do not hold": within the limit, unmeasured, or the check
        is switched off. Unmeasured is FAIL-OPEN and the callers say so — a
        forge that cannot answer must not freeze the whole pipeline, and that is
        the posture `landed_check` already takes for the same class of question.
        """
        if not self.config.get("refuse_merge_when_behind", True):
            return None
        merge_state = (state.get("mergeStateStatus") or "").strip().upper()
        limit = self._max_behind()
        behind = self._behind_by(state)
        if merge_state == "BEHIND":
            # The forge will refuse this merge itself; the count is a detail.
            return (behind if behind is not None else -1,
                    "the forge reports mergeStateStatus=BEHIND")
        if behind is not None and behind > limit:
            return (behind,
                    "%d commits behind %s (limit %d) -- it merges CLEANLY and "
                    "would re-apply its diff over a tree that has moved on"
                    % (behind, (state.get("baseRefName") or "the base branch"),
                       limit))
        return None

    def required_checks(self) -> Optional[FrozenSet[str]]:
        """The checks branch protection REQUIRES, or None to count every check.

        THE DEFECT (task-det-295a9bb95e). `Test (Windows)` is non-required on
        purpose -- icdev-ci.yml: "a Windows-only flake cannot block a merge" --
        and the forge merges on Lint / Test / Security Scan / Helm Lint alone.
        This watcher read EVERY check, so one red Windows run classified PR
        #1859 `ci_failed` with its required set fully green, injected five
        resume contexts into a branch with nothing wrong in it, escalated to
        "manual intervention required", and filed a NEEDED-A-HUMAN card (#1841
        before it, same shape: 2 of the 11 `ci_failed` escalations since
        2026-08-14). The forge then merged both itself.

        Read from branch protection -- the ONE place the set is declared --
        through `merge_readiness.fetch_required_checks`, never from a list in
        code that drifts the day a check is promoted. Cached for
        `required_checks_cache_seconds` (default 300) INCLUDING an unresolved
        answer, so an unprotected branch or a token that may not read the rule
        costs one call per window, not one per PR per poll.

        `required_checks_only: false` in args/pr_watcher_config.yaml turns it
        off: None, no forge call, and every predicate reads every check -- the
        pre-existing behaviour, one flag flip away.
        """
        if not self.config.get("required_checks_only", True):
            return None
        ttl = float(self.config.get("required_checks_cache_seconds", 300) or 0)
        now = time.monotonic()
        cached = self._required_checks_cache
        if cached is not None and now - cached[0] < ttl:
            return cached[1]
        resolved = None
        try:
            resolved = fetch_required_checks(
                self._default_branch(), runner=self._gh_runner)
        except Exception as exc:  # noqa: BLE001 -- unresolved, never a crash
            logger.warning("pr_watcher: required-check resolution failed: %s", exc)
        if resolved is None:
            logger.info(
                "pr_watcher: required checks UNRESOLVED for %s -- every check "
                "counts until the next attempt", self._default_branch())
        self._required_checks_cache = (now, resolved)
        return resolved

    def _default_branch(self) -> str:
        if self._default_branch_cache is None:
            try:
                self._default_branch_cache = self._default_branch_resolver()
            except Exception as exc:
                logger.warning(
                    "pr_watcher: default-branch resolution failed: %s", exc
                )
                self._default_branch_cache = "main"
        return self._default_branch_cache

    def _maybe_reverify(self, get_conn, task_id: str) -> bool:
        """Refresh a stale verification once. Returns True if a new row was written.

        Exists because the enforced done-gate has no way to be *un*-blocked: it
        reads only the latest kanban_verifications row and nothing writes one
        except a dispatch, so a green PR whose verification went stale waits
        forever, then ages into is_stale -> FAILED and gets re-dispatched — which
        opens a second PR instead of merging the first.

        Deliberately narrow. See `reverify_is_allowed`: a conformance failure is
        never refreshed, and a task with no verification at all stays held unless
        an operator opts in, because inventing a first verdict from git state
        would merge PRs that no conformance review ever saw.
        """
        if not self.config.get("reverify_on_hold", True):
            return False
        cap = int(self.config.get("reverify_max_attempts_per_task", 1))
        if self._reverify_attempts.get(task_id, 0) >= cap:
            return False

        latest = _latest_verification(get_conn, task_id)
        allowed, why = reverify_is_allowed(
            latest,
            allow_when_missing=bool(self.config.get("reverify_when_missing", False)),
        )
        if not allowed:
            logger.debug("pr_watcher: not re-verifying %s — %s", task_id, why)
            return False

        self._reverify_attempts[task_id] = self._reverify_attempts.get(task_id, 0) + 1
        if self.dry_run:
            logger.info("pr_watcher: would re-verify %s (%s)", task_id, why)
            return False
        try:
            from tools.kanban.reverify import reverify

            verdict = reverify(task_id, get_conn)
        except Exception as exc:  # noqa: BLE001 — must never stop the poll
            logger.warning("pr_watcher: re-verify failed for %s: %s", task_id, exc)
            return False
        logger.info(
            "pr_watcher: re-verified %s -> %s (%s)",
            task_id, verdict.get("result"), str(verdict.get("reason"))[:120],
        )
        return bool(verdict.get("written"))

    def _ci_retrigger_attempts(self, task_id: str, pr_url: str) -> int:
        """Prior CI re-trigger attempts for THIS PR (its own ledger)."""
        return self._count_audit_actions(
            task_id, ("pr_watcher.ci_retrigger",), pr_url=pr_url)

    def _ci_never_fired(self, state: dict) -> bool:
        """True when a PR has NO checks at all and is old enough that it should.

        A workflow that never fires leaves a PR that can never go green and can
        never be recovered: every other repair path assumes there is a CI result
        to react to. #1462 sat with zero checks in its rollup — not failing, not
        running, simply absent — and no code in this loop had an opinion about
        it, so it waited for a person.

        Age is measured from createdAt, not updatedAt: a comment or a label moves
        updatedAt, so a chatty PR would never look old enough to have missed its
        run. The grace period exists because a PR opened seconds ago legitimately
        has an empty rollup while GitHub queues the workflow.
        """
        if state.get("statusCheckRollup"):
            return False
        created = (state.get("createdAt") or "").strip()
        if not created:
            return False  # cannot age it, so do not act on it
        try:
            stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            return False
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        grace = int(self.config.get("ci_missing_grace_minutes", 15))
        age_min = (datetime.now(timezone.utc) - stamp).total_seconds() / 60.0
        return age_min >= grace

    def _retrigger_ci(self, task_id: str, pr_url: str) -> Dict[str, Any]:
        """Close and reopen the PR so `pull_request` workflows fire again.

        Chosen over an empty commit deliberately: a commit changes the branch and
        lands in history forever to work around an infrastructure hiccup, while a
        close/reopen leaves the diff, the reviews and the branch untouched. It is
        also reversible in the only sense that matters — if the reopen fails, the
        PR is closed and that is loud, so the reopen is NOT conditional on the
        close succeeding cleanly.
        """
        if self.dry_run:
            return {"attempted": False, "reason": "dry-run: would close/reopen"}
        cap = int(self.config.get("max_ci_retriggers_per_pr", 1))
        attempts = self._ci_retrigger_attempts(task_id, pr_url)
        if attempts >= cap:
            return {"attempted": False,
                    "reason": f"ci re-trigger exhausted ({attempts}/{cap})"}
        try:
            close = self._auto_merge_runner(
                ["gh", "pr", "close", pr_url], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
            reopen = self._auto_merge_runner(
                ["gh", "pr", "reopen", pr_url], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
        except Exception as exc:  # noqa: BLE001 — must never stop the poll
            logger.warning("pr_watcher: ci re-trigger failed for %s: %s", task_id, exc)
            return {"attempted": True, "ok": False, "reason": str(exc)[:200]}
        ok = getattr(reopen, "returncode", 1) == 0
        if not ok:
            # The PR may now be CLOSED. Say so at ERROR: this is the one outcome
            # here that is worse than doing nothing.
            logger.error(
                "pr_watcher: %s reopen FAILED after close — PR may be left closed: %s",
                pr_url, (getattr(reopen, "stderr", "") or "")[:200])
        else:
            logger.info("pr_watcher: re-triggered CI on %s (close/reopen)", pr_url)
        return {"attempted": True, "ok": ok,
                "reason": "closed and reopened to re-fire pull_request workflows",
                "close_rc": getattr(close, "returncode", None)}

    def _git_probe(self, argv: List[str], runner=None, stdin: Optional[str] = None):
        """One read-only git call against the repo root. Never raises."""
        root = str(pathlib.Path(__file__).resolve().parents[2])
        run = runner or subprocess.run
        kwargs: Dict[str, Any] = dict(
            cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, shell=False,
        )
        if stdin is not None:
            kwargs["input"] = stdin
        return run(argv, **kwargs)  # nosec B603 — fixed argv, shell=False

    def _no_union_attr_tree(self, base: str, runner=None) -> Optional[str]:
        """A tree holding the base's `.gitattributes` MINUS its union rules.

        Used to re-run the merge the way the forge runs it. Deliberately not
        "disable all attributes": dropping `* text=auto eol=lf` too would let a
        line-ending difference surface as a conflict, and the answer would then
        be attributed to `merge=union` — a label that has to be true, because a
        rebase is spent on it.

        None means union is not in play at all (no `.gitattributes`, or no union
        rule in it), so union cannot be the explanation for anything.
        """
        show = self._git_probe(
            ["git", "cat-file", "-p", f"origin/{base}:.gitattributes"], runner)
        if getattr(show, "returncode", 1) != 0:
            return None
        lines = (getattr(show, "stdout", "") or "").splitlines()
        kept = [ln for ln in lines if "merge=union" not in ln]
        if len(kept) == len(lines):
            return None
        blob = self._git_probe(
            ["git", "hash-object", "-w", "--stdin"], runner,
            stdin="\n".join(kept) + "\n")
        if getattr(blob, "returncode", 1) != 0:
            return None
        sha = (getattr(blob, "stdout", "") or "").strip()
        if not sha:
            return None
        tree = self._git_probe(
            ["git", "mktree"], runner,
            stdin=f"100644 blob {sha}\t.gitattributes\n")
        if getattr(tree, "returncode", 1) != 0:
            return None
        return (getattr(tree, "stdout", "") or "").strip() or None

    def classify_conflict(self, state: dict, runner=None) -> str:
        """Which KIND of conflict a CONFLICTING verdict is. Three, not two.

        The old question was "is the forge lying?", and it had the wrong shape,
        because `git merge-tree` reads `.gitattributes` and GitHub does not.
        Anything `merge=union` resolves therefore merges clean here and conflicts
        there, and the two disagree while both are correct about their own merge.

        MEASURED 2026-08-17. Nine of ten open PRs were DIRTY; re-running the
        three-way merge with the union rules stripped reproduced the forge's
        verdict on ten of ten, negative control included — #1730 also appended to
        `args/ci_test_files/core.txt`, did not collide there, and was the one PR
        the forge called MERGEABLE. CLAUDE.md requires every PR that adds a test
        file to append to that list, so this is the common case, not an edge.

          real        git conflicts too. Nothing local resolves it; escalate.
          union_only  clean here, conflicting there, and only because of union.
                      A rebase APPLIES the union rule and writes the resolution
                      into the branch, after which the forge has nothing left to
                      object to — until main appends to the same file again.
          phantom     clean both ways. The forge's cached verdict really is
                      stale (measured on #1473, 2026-08-09: same base sha, same
                      head sha, exit 0). Only a new head sha clears it.

        The last two both want a rebase; they differ in whether the cause
        RECURS, which is what the rebase budget has to know.

        Errs toward TRUSTING THE FORGE: every failure to verify returns `real`,
        so an unreachable git or an unfetchable ref leaves behaviour unchanged
        rather than declaring a genuine conflict resolved.
        """
        head = (state.get("headRefName") or "").strip()
        base = (state.get("baseRefName") or "").strip() or self._default_branch()
        if not head:
            return CONFLICT_REAL
        try:
            fetch = self._git_probe(
                ["git", "fetch", "--quiet", "origin", base, head], runner)
            if getattr(fetch, "returncode", 1) != 0:
                return CONFLICT_REAL
            with_union = self._git_probe(
                ["git", "merge-tree", "--write-tree",
                 f"origin/{base}", f"origin/{head}"], runner)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("pr_watcher: conflict verification failed: %s", exc)
            return CONFLICT_REAL
        if getattr(with_union, "returncode", 1) != 0:
            # git and the forge agree. The second probe cannot change that, and
            # a PR is polled every 30s — do not pay for it.
            return CONFLICT_REAL

        try:
            attr_tree = self._no_union_attr_tree(base, runner)
            if not attr_tree:
                return CONFLICT_PHANTOM
            without_union = self._git_probe(
                ["git", "-c", f"attr.tree={attr_tree}",
                 "merge-tree", "--write-tree",
                 f"origin/{base}", f"origin/{head}"], runner)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("pr_watcher: union probe failed: %s", exc)
            return CONFLICT_PHANTOM
        # merge-tree exits 1 on a conflict and 128+ on an error. The first
        # version of this probe passed `--attr-source`, which merge-tree does
        # not accept, and read the resulting exit 129 as a clean merge. Only 1
        # means conflict; anything else that is not 0 means the probe did not
        # run, which is not evidence of anything.
        rc = getattr(without_union, "returncode", 0)
        if rc == 1:
            return CONFLICT_UNION_ONLY
        return CONFLICT_PHANTOM

    def _conflict_is_real(self, state: dict, runner=None) -> bool:
        """Whether a CONFLICTING verdict survives verification against git.

        Kept as the boolean its callers ask for. `union_only` answers False
        here: git merges the branch, so nothing in the tree needs fixing — but
        see `classify_conflict`, because the forge is not wrong either and the
        remedy differs.
        """
        return self.classify_conflict(state, runner=runner) == CONFLICT_REAL

    def _base_sha(self, base: str, runner=None) -> str:
        """The sha of `origin/<base>`, or "" if it cannot be read."""
        try:
            p = self._git_probe(
                ["git", "rev-parse", f"refs/remotes/origin/{base}"], runner)
        except (OSError, subprocess.SubprocessError):
            return ""
        if getattr(p, "returncode", 1) != 0:
            return ""
        return (getattr(p, "stdout", "") or "").strip()

    def _mark_ready(
        self, pr_url: str, task_id: str, get_conn, state=None,
    ) -> bool:
        """Take a green PR out of draft so the merge below can actually run.

        The pipeline is autonomous up to this exact point and then stops: a
        dispatched session opens its PR as a draft, CI goes green, and
        `gh pr merge` is refused with "Pull Request is still a draft" on every
        subsequent cycle. Nothing in the loop could clear that, so finished work
        accumulated until a human clicked a button — 5 of 10 open PRs on
        2026-08-09, and the same jam twice before that.

        Two things must remain true after this, and both are checked by the
        caller, not assumed here: the task is not a gate sentinel, and its
        dependency is satisfied. A held card keeps its draft, because for a
        MANUAL-ONLY card the draft IS the brake.

        DECISION (kpr-watch-04): A HOLD LABEL SUPPRESSES AUTO-READY TOO, not
        only auto-merge. The card asked for this to be decided explicitly, so
        the reasoning lives here next to the code.

        AGAINST: drafts and labels are separate controls, and conflating them
        surprises people in the other direction — somebody who marks a PR `wip`
        while still expecting the "Ready for review" button to work is not wrong
        about what those two widgets normally mean.

        FOR, and why it wins: un-drafting HERE is not a human clicking that
        button, it is one step of this automation's own merge sequence. The
        comment at the call site says exactly that, and says un-drafting must
        never happen "for a PR that was not about to merge anyway" — and a PR
        carrying `do-not-merge` is by definition not about to merge, so
        un-drafting it destroys state and enables nothing. The asymmetry decides
        it: the merge this would eventually permit is irreversible and the
        un-draft is "visible and hard to walk back", while the cost of erring
        the other way is that a human removes a label they applied on purpose.
        `wip` and `hold` are in `NO_AUTOMERGE_LABELS` because they ARE the
        not-finished signal, which is the same thing the draft is saying.

        NOTHING HUMAN IS SUPPRESSED. This stops `gh pr ready` issued by the
        watcher. A person may still un-draft the PR by hand at any moment, and
        the merge is still refused afterwards — so the two controls stay
        independent in the direction where independence matters.

        Placement mirrors the protected-path guard: ahead of `dry_run`, or a dry
        run reports a transition the real run would refuse.
        """
        held = hold_labels(state or {})
        if held:
            logger.info(
                "pr_watcher: NOT un-drafting %s — it %s (task %s). A hold label "
                "stops the whole automated merge sequence, not just its last "
                "step; remove the label to let the pipeline land this.",
                pr_url, held_label_reason(held), task_id)
            return False
        if self.dry_run:
            return True
        if not self.config.get("auto_ready_draft_prs", True):
            logger.info("pr_watcher: %s is a draft and auto_ready_draft_prs is "
                        "off — leaving it for a human", pr_url)
            return False
        try:
            conn = get_conn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("pr_watcher: cannot check gates for %s: %s", task_id, exc)
            return False
        try:
            if _is_gate_task(conn, task_id):
                logger.warning("pr_watcher: REFUSING to un-draft manual gate %s", task_id)
                return False
            if _held_by_a_gate(conn, task_id):
                logger.info("pr_watcher: %s is held by an unsatisfied dependency — "
                            "its draft stays", task_id)
                return False
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc = self._auto_merge_runner(
                ["gh", "pr", "ready", pr_url],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("pr_watcher: gh pr ready failed for %s: %s", pr_url, exc)
            return False
        if proc.returncode != 0:
            logger.warning("pr_watcher: gh pr ready refused %s: %s",
                           pr_url, (proc.stderr or "")[:200])
            return False
        logger.info("pr_watcher: marked %s ready for review (task %s)", pr_url, task_id)
        # RECORD THE PROMOTION (kpr-watch-06). Every kanban PR now opens as a
        # draft, so this call is the pipeline's single promotion point — and
        # until now it left no trace anywhere but a log line, which means a
        # promotion regression would have shown up as a quiet backlog of drafts
        # rather than as a number. One row per PR, not per poll: after this
        # succeeds `isDraft` is false, so the caller never asks again. A REFUSAL
        # is deliberately not audited here — it repeats every 30s poll for as
        # long as the hold stands, and the `wait` action the caller already
        # writes records it once per cycle without a second flood.
        self._audit(WatcherAction(
            task_id=task_id, pr_url=pr_url, classification="done",
            action="auto_ready",
            reason=("draft promoted: CI green, not a manual gate, "
                    "dependency satisfied"),
        ))
        return True

    def _auto_merge(self, pr_url: str, state: Optional[dict] = None) -> bool:
        # LAST LINE, not the only one. Both merge paths call this, so the guard
        # here cannot be routed around by a future caller — but the linked path
        # also checks BEFORE it un-drafts, because un-drafting a PR that was
        # never going to merge burns the one brake a human still has.
        if self._refuse_protected(pr_url):
            return False
        # HOLD LABEL (kpr-watch-04), the same belt-and-braces shape. `state` is
        # optional because each caller has already asked the same question by
        # the time it gets here; passing the record makes the chokepoint answer
        # for both of them, so a future caller cannot route around the label the
        # way the task-linked path routed around it for its whole existence.
        # LOGGED, NOT AUDITED: whoever decided to stop already wrote the audit
        # row, and one refusal must not produce two — the same rule the unlinked
        # sweep states next to its `protected_path` branch.
        held = hold_labels(state or {})
        if held:
            logger.warning(
                "pr_watcher: REFUSING to merge %s — it %s.",
                pr_url, held_label_reason(held))
            return False
        if self.dry_run:
            return True
        if not self.config.get("auto_merge_enabled", False):
            return False
        try:
            # --auto ASKS GITHUB TO MERGE WHEN CHECKS PASS, and it requires the
            # repository to have auto-merge ENABLED. Measured 2026-08-30:
            # `allow_auto_merge` is false on BOTH icdev and icdev_ft (it needs
            # branch protection, which this plan does not offer on a private
            # repo -- the protection API answers 403). So this call could never
            # succeed on either parent, `merge_requested` failed on every land,
            # and the sanctioned door -- twelve gates green, one to go -- was
            # structurally incapable of merging ANYTHING. That is why every
            # agent, and every human, fell back to a raw `gh pr merge` that runs
            # none of the thirteen checks. A door that cannot open is not a door.
            #
            # Falling back to an immediate merge is safe HERE and only here:
            # both callers have already established the PR is mergeable and its
            # checks are green -- land.py through its own ci_green gate,
            # pr_watcher through classify_merge_readiness returning READY. --auto
            # would only re-wait for a verdict the caller already holds. Where a
            # repo DOES allow auto-merge the first call still wins, so nothing
            # changes for a deployment that has it.
            attempts = (
                ["gh", "pr", "merge", pr_url, "--squash", "--auto"],
                ["gh", "pr", "merge", pr_url, "--squash"],
            )
            last_err = ""
            for i, cmd in enumerate(attempts):
                proc = self._auto_merge_runner(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=60,
                )
                if getattr(proc, "returncode", 1) == 0:
                    if i:
                        logger.info(
                            "pr_watcher: merged %s without --auto "
                            "(auto-merge is not enabled on this repository)", pr_url)
                    return True
                last_err = (getattr(proc, "stderr", "") or "").strip()[:200]
            # Previously this returned False with NO log line, so a forge that
            # refused every merge looked identical to a board with nothing to
            # merge. That is how 11 PRs sat "awaiting merge" while the watcher
            # decided "merge" on each pass and was refused.
            logger.warning("pr_watcher: gh refused to merge %s: %s", pr_url, last_err)
            return False
        except Exception as exc:
            logger.warning("pr_watcher: auto-merge failed: %s", exc)
            return False

    def reclaim_worktree(self, task_id: str) -> dict:
        """Remove a task's worktree once its PR is merged.

        Creation was bounded; reclamation was not. Measured 2026-08-02: 122
        registered worktrees, recursively nested, several locked — the leak
        undercuts the delivery pipeline, which is otherwise the platform's
        strongest differentiator.

        SAFETY, in order. Each check exists because a worktree can hold the only
        copy of a session's work, and a removed commit that is not on a branch
        is not recoverable by any ordinary means:

          * merged-ness is the caller's precondition, but it is re-checked here
            against origin rather than trusted;
          * a worktree with uncommitted changes is left alone — someone may be
            mid-edit even after the PR merged;
          * commits not reachable from the default branch hold it;
          * ``--force`` is NEVER used, so git's own refusal is a final backstop.

        Returns a verdict dict rather than a bool so the reason is auditable.
        """
        import subprocess
        from pathlib import Path

        def _git(*args, cwd=None):
            proc = subprocess.run(
                ["git", *(["-C", cwd] if cwd else []), *args],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120,
            )
            return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()

        try:
            from tools.genesis.reflexes.kanban import _task_worktree_path
            path = Path(_task_worktree_path(task_id))
        except Exception as exc:  # noqa: BLE001
            return {"reclaimed": False, "reason": f"path resolution failed: {exc}"}

        if not path.exists():
            _git("worktree", "prune")
            return {"reclaimed": False, "reason": "already gone", "pruned": True}

        code, dirty, _ = _git("status", "--porcelain", cwd=str(path))
        if code != 0:
            return {"reclaimed": False, "reason": "status unreadable"}
        if dirty:
            return {"reclaimed": False, "reason": "uncommitted changes present"}

        base = self._default_branch()
        code, ahead, _ = _git("rev-list", "--count", f"origin/{base}..HEAD", cwd=str(path))
        if code != 0:
            return {"reclaimed": False, "reason": "ahead-count unreadable"}
        if ahead != "0":
            return {"reclaimed": False, "reason": f"{ahead} commits not on origin/{base}"}

        code, _, err = _git("worktree", "remove", str(path))
        if code != 0:
            return {"reclaimed": False, "reason": f"git refused: {err.splitlines()[-1][:120] if err else '?'}"}

        _git("worktree", "prune")
        logger.info("pr_watcher: reclaimed worktree for %s at %s", task_id, path)
        return {"reclaimed": True, "path": str(path)}

    def _audit_payloads(
        self,
        task_id: str,
        actions: Tuple[str, ...],
        pr_url: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """The `details` payloads `_count_audit_actions` counts, unaggregated.

        A count answers "how many attempts", which was the whole question until
        an attempt gained a base era to belong to. Same filters, same
        fail-quiet: an unreadable audit trail yields [], never a partial ledger
        dressed as a complete one.
        """
        get_conn = self._connection()
        try:
            conn = get_conn()
        except Exception:  # noqa: BLE001
            return []
        try:
            placeholders = ", ".join(["%s"] * len(actions))
            _pg = getattr(conn, "_backend", "sqlite") == "postgresql"
            details_col = "details::text" if _pg else "details"
            rows = conn.execute(
                f"SELECT {details_col} AS d FROM audit_trail "  # nosec B608
                f"WHERE action IN ({placeholders}) "
                f"AND {details_col} LIKE %s",
                (*actions, f"%{task_id}%"),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                blob = (dict(r) if not isinstance(r, dict) else r).get("d")
                if not blob:
                    continue
                try:
                    payload = json.loads(blob)
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("task_id") != task_id:
                    continue
                if pr_url is not None and (payload.get("pr_url") or "") != pr_url:
                    continue
                out.append(payload)
            return out
        except Exception:  # noqa: BLE001
            return []
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _count_audit_actions(
        self,
        task_id: str,
        actions: Tuple[str, ...],
        pr_url: Optional[str] = None,
    ) -> int:
        """Best-effort count of prior pr_watcher audit rows for this task.

        Reads audit_trail details JSON. The `action` filter is what keeps the
        resume budget and the rebase budget separate ledgers: a rebase attempt
        writes `pr_watcher.rebase*` and is therefore invisible to
        `_resume_cycle`, which is exactly the "a rebase must not spend a resume"
        requirement, enforced by the storage layout rather than by convention.
        """
        get_conn = self._connection()
        try:
            conn = get_conn()
        except Exception:
            return 0
        try:
            placeholders = ", ".join(["%s"] * len(actions))
            _pg = getattr(conn, "_backend", "sqlite") == "postgresql"
            details_col = "details::text" if _pg else "details"
            rows = conn.execute(
                f"SELECT {details_col} AS d FROM audit_trail "  # nosec B608
                f"WHERE action IN ({placeholders}) "
                f"AND {details_col} LIKE %s",
                (*actions, f"%{task_id}%"),
            ).fetchall()
            if pr_url is None:
                return len(rows)
            # PER-PR BUDGET. Counting a task's whole audit history made these
            # budgets permanent: a task that burned 5 resumes on an abandoned PR
            # inherited 5/5 on its NEXT one and could never be auto-recovered
            # again. Measured 2026-08-09 — sbx-fld-05 was at 5/5 and 2/2 while
            # holding a clean, green PR the watcher would have refused to help.
            # A new PR is a new attempt, so the ledger is scoped to it.
            #
            # Parse `details` as the JSON it is rather than scanning the blob:
            # the payload embeds reasons naming OTHER PRs, so a substring test
            # over-counts (it matched six tasks where one had escalated).
            n = 0
            for r in rows:
                blob = (dict(r) if not isinstance(r, dict) else r).get("d")
                if not blob:
                    continue
                try:
                    payload = json.loads(blob)
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("task_id") != task_id:
                    continue
                if (payload.get("pr_url") or "") == pr_url:
                    n += 1
            return n
        except Exception:
            return 0
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _resume_cycle(self, task_id: str, pr_url: Optional[str] = None) -> int:
        """Prior resume events for this task on THIS PR, net of refunds.

        Cycles spent on a conflict the forge only IMAGINED are refunded, for the
        same reason the rebase budget is: a PR that merges cleanly must not be
        locked out of recovery by a verdict we have disproved. Without this a
        phantom drove the PR to the cap and it escalated to "manual intervention
        required" permanently, because nothing ever gave a resume back.

        ONE refund restores ONE FULL budget, not one cycle. A single extra poll
        against a stale verdict achieves nothing; a full budget is a genuine
        second run at the problem. Floored at zero — a refund can restore a
        budget, never grant one — and issued at most once per PR, so a forge
        that keeps lying cannot buy unlimited attempts.
        """
        spent = self._count_audit_actions(
            task_id, ("pr_watcher.resume",), pr_url=pr_url)
        refunds = self._count_audit_actions(
            task_id, ("pr_watcher.resume_refund",), pr_url=pr_url)
        if not refunds:
            return spent
        per_refund = int(self.config.get("max_resume_cycles_per_task", 5))
        return max(0, spent - refunds * per_refund)

    def _refund_resume_budget(self, task_id: str, pr_url: str,
                              classification: str = "") -> None:
        """Cancel the resume cycles a phantom conflict consumed.

        Written through _audit like every other action: the audit trail is
        append-only (NIST AU), so a refund is a row the counter subtracts, never
        a mutation of the rows it refunds.
        """
        self._audit(WatcherAction(
            task_id=task_id, pr_url=pr_url, classification=classification,
            action="resume_refund",
            reason="resume budget was spent on a conflict that does not exist",
        ))
        logger.info(
            "pr_watcher: refunded resume budget for %s (stale conflict)", pr_url)

    def _seconds_since_last_resume(
        self, task_id: str, pr_url: Optional[str] = None,
    ) -> Optional[float]:
        """Age of the most recent resume injection for THIS PR, or None.

        The budget counts attempts; this is what makes an attempt an attempt.
        Without it the cap is spent at POLL speed rather than at agent speed —
        measured 2026-08-16, #1742 and #1744 each burned all five cycles in
        about three minutes (one per ~45 s poll) and then escalated to "manual
        intervention required" while both PRs were fully green and merged
        cleanly under `git merge-tree`.

        None when there is no prior resume or the age cannot be read, so the
        caller proceeds — an unreadable clock must not block recovery.
        """
        get_conn = self._connection()
        try:
            conn = get_conn()
        except Exception:  # noqa: BLE001
            return None
        try:
            _pg = getattr(conn, "_backend", "sqlite") == "postgresql"
            details_col = "details::text" if _pg else "details"
            rows = conn.execute(
                f"SELECT {details_col} AS d, created_at FROM audit_trail "  # nosec B608
                f"WHERE action = %s AND {details_col} LIKE %s "
                "ORDER BY created_at DESC LIMIT 25",
                ("pr_watcher.resume", f"%{task_id}%"),
            ).fetchall()
            for r in rows:
                row = dict(r) if not isinstance(r, dict) else r
                try:
                    payload = json.loads(row.get("d") or "")
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("task_id") != task_id:
                    continue
                if pr_url is not None and (payload.get("pr_url") or "") != pr_url:
                    continue
                stamp = row.get("created_at")
                if isinstance(stamp, str):
                    stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if stamp is None:
                    return None
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                return (datetime.now(timezone.utc) - stamp).total_seconds()
            return None
        except Exception:  # noqa: BLE001
            return None
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _attempts_against_another_base(
        self, task_id: str, pr_url: Optional[str], base_sha: str,
    ) -> int:
        """Rebase attempts on this PR that were spent against a DIFFERENT base.

        A rebase resolves the collision with the base as it was. When the base
        moves and collides again, the earlier attempt describes a world that no
        longer exists — counting it caps the PR on work already done.

        A row with no recorded `base_sha` predates kpr-watch-07 and belongs to
        an unknown era, so it is discounted too. That is the deliberate half of
        the unstick: every PR deadlocked by the old ledger gets its budget back
        once, and only once, because every attempt from here on records its sha.
        """
        return sum(
            1
            for payload in self._audit_payloads(
                task_id, ("pr_watcher.rebase", "pr_watcher.rebase_failed"),
                pr_url=pr_url,
            )
            if (payload.get("base_sha") or "") != base_sha
        )

    def _rebase_attempts(
        self,
        task_id: str,
        pr_url: Optional[str] = None,
        base_sha: str = "",
    ) -> int:
        """Prior auto-rebase attempts for this task on THIS PR and THIS base.

        Attempts spent on a conflict the forge only IMAGINED are refunded, so a
        PR is not permanently locked out of the one action that can clear a stale
        verdict. Floored at zero: a refund can restore a budget, never grant one.

        `base_sha` narrows the ledger to the current base era. Without it the
        cap was a one-way ratchet against a cause that RECURS: two attempts is
        the right budget for a verdict that goes stale once and the wrong one
        for a union collision that returns every time main appends to the same
        file. hcx-evt-03 spent its three attempts by 15:46 on 2026-08-16 and
        could never rebase again, while the rebase was the only thing that
        would have worked — proved when one, run by hand on 2026-08-17, flipped
        it to MERGEABLE in seconds.
        """
        spent = self._count_audit_actions(
            task_id, ("pr_watcher.rebase", "pr_watcher.rebase_failed"),
            pr_url=pr_url,
        )
        refunded = self._count_audit_actions(
            task_id, ("pr_watcher.rebase_refund",), pr_url=pr_url)
        superseded = (
            self._attempts_against_another_base(task_id, pr_url, base_sha)
            if base_sha else 0
        )
        return max(0, spent - refunded - superseded)

    def _refund_rebase_budget(self, task_id: str, pr_url: str,
                              classification: str = "") -> None:
        """Cancel the rebase attempts a phantom conflict consumed.

        Written through _audit like every other action: the audit trail is
        append-only (NIST AU), so a refund is a row the counter subtracts, never
        a mutation of the rows it refunds. _audit also honours dry_run and uses
        the one event_type the live CHECK constraint already accepts.
        """
        self._audit(WatcherAction(
            task_id=task_id, pr_url=pr_url, classification=classification,
            action="rebase_refund",
            reason="forge reported CONFLICTING but the merge is clean",
        ))
        logger.info("pr_watcher: refunded rebase budget for %s (stale conflict)",
                    pr_url)

    def _maybe_rebase(self, task: dict, state: dict) -> Dict[str, Any]:
        """Try the cheap recovery for a DIRTY PR: rebase the branch onto its base.

        A DIRTY PR is a resume class today, so a branch that is merely stale
        burns all five LLM resumes on a conflict a plain rebase would have
        cleared, and then lands in a permanent human queue. Rebase first; only
        fall through to the resume/escalate path when the rebase cannot help.

        Returns a verdict dict (never raises). ``attempted`` False means the
        rebase was declined before any git ran, so no attempt is consumed.
        """
        task_id = task["id"]
        if not self.config.get("auto_rebase_on_conflict", True):
            return {"attempted": False, "pushed": False,
                    "reason": "auto_rebase_on_conflict=false"}

        branch = (state.get("headRefName") or "").strip()
        base = (state.get("baseRefName") or "").strip() or self._default_branch()
        base_sha = self._base_sha(base)

        cap = int(self.config.get("max_rebase_attempts_per_task", 2))
        attempts = self._rebase_attempts(
            task_id, pr_url=(state.get("url") or "").strip() or None,
            base_sha=base_sha)
        if attempts >= cap:
            return {"attempted": False, "pushed": False, "base_sha": base_sha,
                    "reason": (f"rebase attempts exhausted ({attempts}/{cap}) "
                               f"against base {base_sha[:7] or '?'}")}

        from tools.kanban.rebase_recovery import branch_is_task_owned

        owned, why = branch_is_task_owned(branch, task_id)
        if not owned:
            # Not this task's branch — never force-push it. Declining (rather
            # than "attempting and failing") keeps the budget for a branch that
            # could actually be recovered.
            logger.warning(
                "pr_watcher: refusing auto-rebase for %s — %s", task_id, why,
            )
            return {"attempted": False, "pushed": False, "reason": f"refused: {why}"}

        if self._rebase_fn is None and self.dry_run:
            return {"attempted": False, "pushed": False,
                    "reason": f"dry-run: would rebase {branch} onto origin/{base}"}

        rebase = self._rebase_fn
        if rebase is None:
            from tools.kanban.rebase_recovery import rebase_and_push

            rebase = rebase_and_push

        try:
            verdict = dict(rebase(task_id, branch, base=base))
        except Exception as exc:  # noqa: BLE001 — must never stop the poll
            logger.warning("pr_watcher: auto-rebase errored for %s: %s", task_id, exc)
            verdict = {"attempted": True, "pushed": False,
                       "reason": f"rebase errored: {exc}"}
        # The era this attempt belongs to. Recorded on the audit row by the
        # caller, and read back by _attempts_against_another_base.
        verdict["base_sha"] = base_sha
        return verdict

    def _hitl_alert(self, task_id: str, pr_url: str, reason: str) -> None:
        """Raise a FIRING alert when a task genuinely needs a human.

        The pipeline is meant to run unattended; the honest exception is a task
        whose automatic recovery is spent. Until now that parked SILENTLY — the
        watcher logged an escalation and moved on, the scheduler reported a
        different reason entirely, and the task waited until somebody happened to
        look. On 2026-08-09 three tasks sat that way at once.

        Writes to `alerts`, which the dashboard already lists and counts, so the
        notification appears there with no new surface. `tools/kanban/cli.py
        --needs-human` reads the same rows for the terminal.

        Deduped on source: one firing alert per task, not one per poll (the
        watcher polls every 30s, which would be 2880 rows a day). Best-effort —
        a notification failure must never stop the loop.
        """
        source = f"pr_watcher:hitl:{task_id}"
        try:
            conn = self._connection()()
        except Exception:  # noqa: BLE001
            return
        try:
            existing = conn.execute(
                "SELECT id FROM alerts WHERE source = %s AND status = 'firing'",
                (source,),
            ).fetchone()
            if existing:
                return
            conn.execute(
                "INSERT INTO alerts "
                "(project_id, severity, source, title, description, status, "
                " auto_healed, created_at) "
                "VALUES (%s, %s, %s, %s, %s, 'firing', %s, %s)",
                (None, "warning", source,
                 f"{task_id} needs a human",
                 f"{reason} PR: {pr_url}", False,
                 datetime.now(timezone.utc).isoformat()),
            )
            try:
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
            logger.warning("pr_watcher: HITL alert raised for %s — %s", task_id, reason)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pr_watcher: HITL alert failed for %s: %s", task_id, exc)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _hitl_recovered(self, state: dict, cycle: int, max_cycles: int,
                        landed: Optional[dict] = None) -> bool:
        """True when the PREMISE of a HITL alert is false — not merely that the
        forge is happy.

        Recovery has to be the negation of the raise conditions, because the
        resolve runs EARLIER in the same pass than both raise sites. Clearing on
        `mergeable` alone therefore did not clear anything: a PR that is
        MERGEABLE with red CI and its resume budget spent got resolved here and
        re-raised ~330 lines below, on every single pass.

        Measured 2026-08-10, the night the resolve shipped: agov-det-02 and
        sbx-sig-02 — both MERGEABLE, both `ci_failed` at 5/5 — cycled about once
        a minute; ~180 alert rows in a day. The panel refilled as fast as it
        drained, which is the same "list nobody reads" failure the resolve was
        written to prevent.

        The raise sites are the resume cap (`cycle >= max_cycles`), CI that
        never fired, a task whose work is ALREADY on the default branch, and
        (kpr-stale-02) a branch too far BEHIND the default branch to merge
        safely whose automatic rebase was declined — so all four are negated
        here. Anything that adds a fifth must negate it here too, or the alert
        flaps again.

        The staleness check is asked LAST, and that ordering is a cost decision
        as much as a correctness one: it is the only condition here that can
        reach the forge, so it is only ever asked about a PR every cheaper
        condition has already called recovered.

        The already-landed case is the one most able to flap: such a PR is green
        AND MERGEABLE by construction — that is exactly why it is dangerous —
        so every condition above it says "recovered". Nothing about main ever
        un-lands the commit, so the premise stays true until a human closes or
        rebases the PR, at which point the task leaves `list_pr_tasks` and
        `_sweep_stale_hitl_alerts` retires the alert.
        """
        if (state.get("mergeable") or "").upper() != "MERGEABLE":
            return False
        if cycle >= max_cycles:
            return False
        if landed and landed.get("checked") and landed.get("landed"):
            return False
        if self._ci_never_fired(state):
            return False
        # Same flap shape as the already-landed case above, for the same
        # reason: a stale branch is green AND MERGEABLE by construction — that
        # is exactly what makes it dangerous — so every condition above says
        # "recovered". It stops being true when the branch is rebased, which
        # changes the head sha and so is re-measured rather than cached.
        return self._stale_verdict(state) is None

    #: Task states after which nothing will poll the task again, so nothing will
    #: ever revisit its alert. `list_pr_tasks` selects only the live states.
    TERMINAL_TASK_STATES = ("done", "dismissed", "cancelled", "archived")

    def _sweep_stale_hitl_alerts(self) -> int:
        """Resolve firing HITL alerts that NOTHING will ever revisit.

        Every other resolve path runs inside the per-task loop, which iterates
        `list_pr_tasks` — and that selects only live states ('in_progress',
        'scheduled', 'pr_opened', 'ci_failed', 'merge_conflict',
        'changes_requested'). The moment a task reaches `done`, it drops out of
        the query, so its alert is never looked at again and fires forever.

        Measured 2026-08-10: agov-inbox-01 and agov-inbox-02 had zero unlanded
        content — every file byte-identical to main via #1497 — so their PRs were
        closed and their tasks force-done. Both alerts stayed FIRING and had to be
        cleared by hand from a `python -c`. No code path existed that would ever
        have cleared them.

        That is the same failure #1511 was written to fix: an alert list that can
        only grow is one people stop reading, which is the state in which a real
        escalation gets missed. #1511 fixed the recovery case; this fixes the
        case where the work is genuinely over.

        Deliberately a DB-only sweep with no forge calls: it runs every poll, and
        one `gh` call per firing alert would add seconds to a 30s cycle. The
        closed-PR case is handled in the loop, where the state is already fetched.

        Best-effort — a sweep failure must never stop the poll. Returns the number
        resolved so the caller can log it.
        """
        try:
            conn = self._connection()()
        except Exception:  # noqa: BLE001
            return 0
        resolved = 0
        try:
            rows = conn.execute(
                "SELECT source FROM alerts "
                "WHERE status = 'firing' AND source LIKE 'pr_watcher:hitl:%'"
            ).fetchall()
            for row in rows:
                source = (row[0] if not isinstance(row, dict) else row.get("source")) or ""
                # Require the prefix rather than splitting on it: `split()` on a
                # missing delimiter returns the WHOLE string, so a foreign source
                # would parse to a task id that matches nothing, look like a
                # deleted task, and get "resolved". The SQL above filters too —
                # this is the parse refusing to invent a task id regardless.
                if not source.startswith(_HITL_ALERT_PREFIX):
                    continue
                task_id = source[len(_HITL_ALERT_PREFIX):].strip()
                if not task_id:
                    continue
                task = conn.execute(
                    "SELECT status FROM kanban_tasks WHERE id = %s", (task_id,)
                ).fetchone()
                if task is None:
                    # The task was deleted. Nothing can act on the alert, and
                    # leaving it firing asks a human to chase a row that is gone.
                    reason = "task no longer exists"
                else:
                    status = (task[0] if not isinstance(task, dict) else task.get("status")) or ""
                    if status.strip().lower() not in self.TERMINAL_TASK_STATES:
                        continue
                    reason = f"task is {status}"
                self._resolve_hitl_alert(task_id)
                resolved += 1
                logger.info(
                    "pr_watcher: cleared stale HITL alert for %s — %s", task_id, reason)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pr_watcher: stale HITL sweep failed: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return resolved

    def _resolve_hitl_alert(self, task_id: str) -> None:
        """Clear the alert once the task moves — the queue must drain itself."""
        source = f"pr_watcher:hitl:{task_id}"
        try:
            conn = self._connection()()
        except Exception:  # noqa: BLE001
            return
        try:
            conn.execute(
                "UPDATE alerts SET status = 'resolved', resolved_at = %s "
                "WHERE source = %s AND status = 'firing'",
                (datetime.now(timezone.utc).isoformat(), source),
            )
            try:
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("pr_watcher: HITL resolve failed for %s: %s", task_id, exc)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _audit(self, action: WatcherAction) -> None:
        if self.dry_run:
            return
        try:
            get_conn = self._connection()
            conn = get_conn()
        except Exception:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO audit_trail "
                "(created_at, event_type, actor, action, details) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    now,
                    "hook_event_logged",
                    "pr_watcher",
                    f"pr_watcher.{action.action}",
                    json.dumps(asdict(action)),
                ),
            )
            conn.commit() if hasattr(conn, "commit") else None
        except Exception as exc:
            logger.debug("pr_watcher: audit write failed: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _record_merge_eligibility(self) -> dict:
        """Append a merge-eligibility observation for every open PR (kpr-watch-02).

        WHY THE WATCHER AND NOT THE REPORT. `merge_stall` can answer "is this PR
        eligible" from one `gh pr list`, but it cannot answer "and for HOW LONG"
        without somebody having been watching. Nothing was: the forge's
        `updatedAt` moves on a comment or a label, and `_audit` records the
        ACTIONS this watcher takes, never the moment a refusal stopped applying.
        So the alarm that says "this should have merged 40 minutes ago" needs an
        observer on the same cadence as the merger, which is this loop.

        Rows are written only when a PR's (state, head_sha) CHANGES, so a PR
        sitting `ready` for an hour has exactly one row whose `observed_at` IS
        its first-seen-ready. A 30s poll therefore costs a handful of rows a day,
        not ~29,000.

        Best-effort, like the heartbeat beside it: an observation is evidence for
        a report, and no report is worth stopping the thing that actually merges.
        `dry_run` writes nothing, for the same reason `_audit` does not.
        """
        if self.dry_run:
            return {"ok": None, "written": 0, "error": "dry run"}
        try:
            from tools.ci import merge_stall

            cfg = merge_stall.load_config()
            if not (cfg["record_observations"] and cfg["record_from_pr_watcher"]):
                return {"ok": None, "written": 0, "error": "disabled by config"}
            prs = merge_stall.list_prs("open", runner=self._pr_list_runner)
            rows = merge_stall.eligibility_rows(
                prs,
                default_branch=self._default_branch(),
                # Ownership is carried as `door`, never fed to the ladder — see
                # merge_stall's docstring. A task-linked PR must be judged on the
                # same merits as any other or every stall on the task path, which
                # is where three of the four known causes live, stays invisible.
                linked_urls={(t.get("pr_url") or "").strip()
                             for t in list_pr_tasks(self._connection())},
                protected_paths=self._protected_paths(),
                max_behind_commits=self._max_behind(),
            )
            return merge_stall.record_transitions(rows, recorded_by="pr_watcher")
        except Exception as exc:  # noqa: BLE001 — never break the watch loop
            logger.debug("pr_watcher: merge-eligibility recording failed: %s", exc)
            return {"ok": False, "written": 0, "error": str(exc)[:200]}

    def _record_heartbeat(self, report: "WatcherReport") -> bool:
        """Append this poll's liveness row to `heartbeat_checks`.

        Written at the END of the poll, so the timestamp means "a poll ran to
        completion", not "a poll started". `items_found` / `details` carry the
        counts that separate the two states an operator has to tell apart:

          * watcher stale   — last_run is hours old, nothing is polling
          * watcher polling — last_run is fresh, actions_taken == 0, i.e.
                              the board simply has nothing mergeable

        Best-effort: a heartbeat failure must never stop the watch loop, and an
        install whose DB predates `heartbeat_checks` just gets no row.
        """
        if self.dry_run:
            return False
        try:
            get_conn = self._connection()
            conn = get_conn()
        except Exception as exc:  # noqa: BLE001
            logger.debug("pr_watcher: no DB connection for heartbeat: %s", exc)
            return False
        try:
            started = _parse_iso(report.started_at)
            finished = _parse_iso(report.finished_at)
            duration_ms = 0
            if started is not None and finished is not None:
                duration_ms = max(0, int((finished - started).total_seconds() * 1000))

            interval = int(self.config.get("poll_interval_seconds", 30) or 30)
            next_run = (
                (finished + timedelta(seconds=interval)).isoformat()
                if finished is not None
                else report.finished_at
            )

            actions_by_type: Dict[str, int] = {}
            for a in report.actions:
                actions_by_type[a.action] = actions_by_type.get(a.action, 0) + 1

            # `result_summary` carries the JSON payload, NOT `details`: the live
            # PostgreSQL `heartbeat_checks` has no `details` column even though
            # init_icdev_db.py's SQLite DDL declares one. Naming it here would
            # raise, be swallowed by the best-effort except below, and the
            # heartbeat would silently never land on the primary backend —
            # precisely the "reports success while persisting nothing" failure.
            # tools/scout/daemon.py already writes its payload the same way.
            conn.execute(
                "INSERT INTO heartbeat_checks "
                "(check_type, last_run, next_run, status, result_summary, "
                " items_found, duration_ms) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    WATCHER_HEARTBEAT_CHECK_TYPE,
                    report.finished_at,
                    next_run,
                    # 'ok', not 'healthy': the live PG status CHECK allows only
                    # pending/ok/warning/critical/error. SQLite's DDL also lists
                    # 'healthy', so this would have passed every local test and
                    # silently violated the constraint on the primary backend.
                    "ok",
                    json.dumps({
                        "tasks_checked": report.tasks_checked,
                        "actions_taken": len(report.actions),
                        "actions_by_type": actions_by_type,
                        "started_at": report.started_at,
                        "finished_at": report.finished_at,
                        "poll_interval_seconds": interval,
                    }),
                    report.tasks_checked,
                    duration_ms,
                ),
            )
            if hasattr(conn, "commit"):
                conn.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("pr_watcher: heartbeat write failed: %s", exc)
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ── main loop ───────────────────────────────────────────────────

    def poll_once(
        self,
        task_id: Optional[str] = None,
    ) -> WatcherReport:
        started_at = datetime.now(timezone.utc).isoformat()
        report = WatcherReport(
            started_at=started_at,
            finished_at="",
            tasks_checked=0,
        )

        get_conn = self._connection()

        # Reconcile task -> PR links before listing. `list_pr_tasks` can only
        # see a PR via `executor_url`, which is written by exactly one path
        # (kanban.py::_push_branch_and_open_pr) that requires a local
        # `kanban/<id>` ref and the un-suffixed branch name. PRs opened any
        # other way — notably retry branches like `kanban/<id>-r2` — were
        # invisible here permanently. Best-effort: a linker failure must never
        # stop the poll.
        if self.config.get("link_prs_on_poll", True) and not task_id:
            try:
                from tools.kanban.pr_linker import link_open_prs

                linked = link_open_prs(get_conn, dry_run=self.dry_run)
                if linked["linked"]:
                    logger.info(
                        "pr_watcher: linked %d task(s) to their open PR: %s",
                        len(linked["linked"]),
                        ", ".join(e["task_id"] for e in linked["linked"]),
                    )
                if linked.get("relinked"):
                    logger.info(
                        "pr_watcher: repaired %d stale link(s) to a closed PR: %s",
                        len(linked["relinked"]),
                        ", ".join(f"{e['task_id']} {e['was']}->{e['url']}"
                                  for e in linked["relinked"]),
                    )
                if linked.get("stale_ambiguous"):
                    logger.warning(
                        "pr_watcher: %d task(s) have a stale link AND several open "
                        "PRs — a human must pick: %s",
                        len(linked["stale_ambiguous"]),
                        ", ".join(e["task_id"] for e in linked["stale_ambiguous"]),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("pr_watcher: PR link reconcile failed: %s", exc)

        try:
            tasks = list_pr_tasks(get_conn, task_id=task_id)
        except Exception as exc:
            logger.warning("pr_watcher: list_pr_tasks failed: %s", exc)
            tasks = []

        max_cycles = int(
            self.config.get("max_resume_cycles_per_task", 5)
        )
        ci_log_max = int(
            self.config.get("ci_log_max_chars", DEFAULT_CI_LOG_MAX)
        )
        require_approval = bool(
            self.config.get("auto_merge_require_approval", True)
        )
        # The REQUIRED check set, once per poll (task-det-295a9bb95e).
        required = self.required_checks()
        # Sibling-file-conflict map (kph): fetch every open PR's changed files ONCE
        # per cycle so the DONE path can flag a merge candidate that races another
        # open PR on the same source file (the "two different blueprint.py" class).
        sibling_index = (
            self._open_pr_index()
            if self.config.get("sibling_conflict_check", True)
            else {}
        )
        sibling_map = {url: e["files"] for url, e in sibling_index.items()}
        # Siblings that cannot merge at all right now. They are dropped from the
        # tie-break below: waiting behind a PR the forge would refuse is not
        # serialisation, it is a queue position that never comes free.
        blocked_siblings = {
            url for url, e in sibling_index.items() if not _pr_can_merge(e)
        }
        # Already-landed map (trust-disc-05, extended): one `git log --grep` for
        # the whole batch, so the DONE path can refuse to merge a PR whose work
        # is already on main under a different number. Same once-per-cycle shape
        # as sibling_map above.
        landed_map = (
            self._landed_map(tasks)
            if self.config.get("landed_check_on_poll", True)
            else {}
        )

        for task in tasks:
            report.tasks_checked += 1
            pr_url = task["pr_url"]
            try:
                state = self._fetch_state(pr_url)
            except Exception as exc:
                action = WatcherAction(
                    task_id=task["id"],
                    pr_url=pr_url,
                    classification="error",
                    action="wait",
                    reason=f"fetch failed: {exc}",
                )
                report.actions.append(action)
                self._audit(action)
                continue

            ci_logs = ""
            if state.get("statusCheckRollup"):
                ci_logs = self._fetch_logs(pr_url, max_chars=ci_log_max)

            classification = ec.classify_pr_state(
                state, ci_logs=ci_logs, require_approval=require_approval,
                required=required,
            )
            # A failing check the required set set aside is NAMED on the audit
            # row, never dropped -- a red check nobody can see afterwards is the
            # `awk '{print $2}'` defect in a new coat.
            ignored = ec.ignored_failures(state, required=required)
            ignored_note = (
                "; ignored non-required failing check(s): " + ", ".join(ignored)
                if ignored else "")
            if ignored:
                logger.info(
                    "pr_watcher: %s -- %s failing but not required; classified %s",
                    pr_url, ", ".join(ignored), classification)

            # WAKE EVENTS (agov-wake-03). This is the one place in ICDEV that
            # observes "PR #N just went green", so it is where
            # `wake_on_event("pr:1342:ci_green")` gets satisfied. Emitted right
            # after classification and BEFORE any of the holds below — a wake
            # subscriber is waiting on the CI verdict, not on whether the
            # watcher went on to merge. Re-emitting the same key every cycle is
            # harmless: fire_event only promotes wakes that are still pending.
            self._emit_wake_events(pr_url, classification, state)
            # WHICH KIND of conflict (kpr-watch-07). Computed once per task per
            # poll and reused by the rebase and resume paths below — a second
            # call would re-run two merges against a live forge every 30s.
            conflict_kind = (
                self.classify_conflict(state)
                if classification == KanbanState.MERGE_CONFLICT
                else ""
            )
            if conflict_kind and conflict_kind != CONFLICT_REAL:
                # The forge's cached verdict disagrees with git. git wins — but
                # knowing that is not a remedy, and the obvious remedy is wrong.
                #
                # Measured on #1473 (2026-08-09), 18 commits behind main. git
                # merge-tree, a real git rebase and a real git merge ALL merged it
                # clean while the API held mergeable=false/dirty. What that rules
                # out matters more than what it shows:
                #   * merging anyway fails — GitHub refuses a PR it believes is
                #     conflicting, whatever we conclude locally;
                #   * close + reopen does NOT clear it — tried against the live PR;
                #   * nothing that leaves the ref untouched clears it.
                # The verdict is cached against the head sha, so the ONLY lever is
                # a new sha. Pushing a merge of the base flipped #1473 to
                # mergeable=true within seconds.
                #
                # So this must NOT reclassify to MERGEABLE. Doing that routes the
                # PR to _auto_merge — the one action the forge is guaranteed to
                # refuse — and away from _maybe_rebase below, which is gated on
                # MERGE_CONFLICT and is the thing that actually moves the ref.
                # Leaving the classification alone is the fix.
                #
                # The rebase budget is the other half: those 2 attempts get spent
                # fighting the phantom, and once spent the PR can never be rebased
                # again — stuck at exactly the moment we can prove it is fine.
                # Refund ONCE per PR: enough to act on a verdict we have disproved,
                # bounded so a forge that keeps lying cannot buy unlimited pushes.
                #
                # The base-era rule in _rebase_attempts is what makes this
                # survivable for the RECURRING half (union_only): the refund is
                # a one-shot, and a collision that returns on every push to main
                # needs more than one. The refund stays because a phantom really
                # does only need one.
                if self._count_audit_actions(
                        task["id"], ("pr_watcher.rebase_refund",), pr_url=pr_url) == 0:
                    self._refund_rebase_budget(task["id"], pr_url)
                if conflict_kind == CONFLICT_UNION_ONLY:
                    logger.warning(
                        "pr_watcher: %s conflicts for the FORGE but not for git — "
                        "a .gitattributes merge=union rule resolves it and GitHub "
                        "does not apply one. Rebasing writes that resolution into "
                        "the branch.", pr_url)
                else:
                    logger.warning(
                        "pr_watcher: %s is reported CONFLICTING but merges cleanly — "
                        "rebasing to force the forge to recompute", pr_url)

                # THE RESUME BUDGET NEEDS THE SAME PROTECTION THE REBASE BUDGET
                # ALREADY HAS. A phantom conflict does not only cost rebases: the
                # PR stays classified MERGE_CONFLICT, so it takes the resume path
                # too, and every cycle spent there was spent on a conflict we can
                # prove does not exist. Once the cap is reached the PR escalates
                # to "manual intervention required" — permanently, because
                # nothing gives a resume back — while merging cleanly under
                # `git merge-tree`. Measured 2026-08-16: #1742 and #1744 both sat
                # there, fully green, 0 of 10 checks failing.
                #
                # Refunded only when the budget is actually EXHAUSTED. Refunding
                # a PR that still has cycles left would spend the one-shot on a
                # PR that did not need it, and this is a one-shot on purpose:
                # bounded so a forge that keeps lying cannot buy unlimited
                # attempts. One refund restores one full budget, so the PR gets
                # a genuine second run at the problem rather than a single extra
                # poll.
                spent = self._resume_cycle(task["id"], pr_url=pr_url)
                if spent >= int(self.config.get(
                        "max_resume_cycles_per_task", 5)) and self._count_audit_actions(
                        task["id"], ("pr_watcher.resume_refund",), pr_url=pr_url) == 0:
                    self._refund_resume_budget(
                        task["id"], pr_url, classification=classification.value)

            cycle = self._resume_cycle(task["id"], pr_url=pr_url)

            # An alert says "needs a human". The moment that stops being true,
            # clear it here — not only on DONE, which was the bug: a branch whose
            # conflict got resolved goes back to MERGEABLE without ever passing
            # through DONE, so its alert stayed firing forever. Measured
            # 2026-08-10: 14 firing HITL alerts, of which at least 2 named
            # branches that had already been fixed and were sitting green.
            #
            # But MERGEABLE alone is NOT recovery, and clearing on it alone made
            # the alert FLAP. A PR can be mergeable and still need a human: red
            # CI with the resume budget spent is exactly that, and it gets
            # resolved here and re-raised ~330 lines below in the SAME pass, on
            # every pass. Measured the same night, after the resolve shipped:
            # agov-det-02 and sbx-sig-02 (both MERGEABLE, both ci_failed at 5/5)
            # cycled roughly once a minute, ~180 alert rows in a day. The panel
            # refilled as fast as it drained, which is the same "list nobody
            # reads" failure the resolve was written to prevent.
            #
            # So recovery means the alert's PREMISE is false, not merely that the
            # forge is happy: the resume budget is no longer spent, and there is
            # CI to be green. Both raise sites (resume cap, CI-never-fired) are
            # covered, so no pass can resolve and re-raise the same alert.
            #
            # The resolve is deduped on `source` and is a no-op when nothing is
            # firing, so calling it on every healthy pass costs nothing.
            if self._hitl_recovered(state, cycle, max_cycles,
                                    landed=landed_map.get(task["id"])):
                self._resolve_hitl_alert(task["id"])

            # A CLOSED PR cannot be rebased, resumed or merged, so an alert
            # saying "the resume budget is spent" is describing a branch nobody
            # can act on. Closing a PR is a decision; the alert about it is spent
            # with it. MERGED is handled by the DONE branch below, and the
            # terminal-task case by _sweep_stale_hitl_alerts after the loop —
            # this is the third door, for a PR closed while its task is still
            # live and therefore still polled here.
            if (state.get("state") or "").upper() == "CLOSED":
                self._resolve_hitl_alert(task["id"])

            if classification == KanbanState.DONE:
                merged = (
                    (state.get("state") or "").upper() == "MERGED"
                )
                # The task moved, so any HITL alert against it is stale. Clearing
                # here keeps the queue self-draining: an alert list nobody can
                # empty is one people stop reading.
                self._resolve_hitl_alert(task["id"])
                if merged:
                    # Reclaim here rather than at task-done: this is the point
                    # where the watcher OBSERVES the merge, and it is the only
                    # place that knows the PR actually landed. Best-effort —
                    # a failed reclaim must never hold up the done transition.
                    try:
                        verdict = self.reclaim_worktree(task["id"])
                        if not verdict.get("reclaimed"):
                            logger.debug(
                                "pr_watcher: worktree for %s not reclaimed (%s)",
                                task["id"], verdict.get("reason"),
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("pr_watcher: worktree reclaim errored: %s", exc)

                if merged or not self.config.get("auto_merge_enabled", False):
                    action_label = "merge" if merged else "wait"
                    reason = (
                        "PR already merged"
                        if merged
                        else "CI green but auto_merge_enabled=false"
                    )
                    action = WatcherAction(
                        task_id=task["id"], pr_url=pr_url,
                        classification="done",
                        action=action_label, reason=reason,
                        resume_cycle=cycle,
                    )
                    # THE MERGE IS THE COMPLETION. Only this watcher knows the PR
                    # actually landed, so only it can close the loop. Until now it
                    # recorded 'done' in the audit trail and left kanban_tasks
                    # untouched, so the board could not tell an open PR from a
                    # finished one. CI-green-but-not-merged stays in pr_opened.
                    if merged and task.get("status") in (
                        "pr_opened", "in_progress", "ci_failed",
                        "merge_conflict", "changes_requested",
                    ):
                        _set_task_status(
                            get_conn, task["id"], "done",
                            reason=f"PR merged: {pr_url}",
                        )
                else:
                    # Enforced done-gate (Governed Delivery Pipeline): under
                    # KANBAN_PIPELINE_ENFORCE, hold the merge until the task's
                    # ICDEV verification (conformance + gates) has PASSED — CI
                    # green alone is not enough.
                    gate_ok, gate_reason = _enforced_done_ok(get_conn, task["id"])
                    if not gate_ok and self._maybe_reverify(get_conn, task["id"]):
                        # A fresh verdict landed — ask the gate again rather than
                        # waiting a whole cycle to notice.
                        gate_ok, gate_reason = _enforced_done_ok(get_conn, task["id"])
                    if not gate_ok:
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done",
                            action="wait",
                            reason=gate_reason,
                            resume_cycle=cycle,
                        )
                        report.actions.append(action)
                        self._audit(action)
                        continue
                    # Base-branch guard (incident 2026-07-08, PR #114):
                    # never auto-merge a PR whose base is not the repo
                    # default branch — merging into a feature branch
                    # strands the change off-main. Unknown base is
                    # treated as unsafe.
                    base_ref = (state.get("baseRefName") or "").strip()
                    default_branch = self._default_branch()
                    if base_ref != default_branch:
                        logger.warning(
                            "pr_watcher: refusing auto-merge for %s — "
                            "PR base '%s' is not the default branch '%s'",
                            pr_url, base_ref or "<unknown>", default_branch,
                        )
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done",
                            action="wait",
                            reason=(
                                f"refusing auto-merge: PR base "
                                f"'{base_ref or '<unknown>'}' is not the "
                                f"default branch '{default_branch}'"
                            ),
                            resume_cycle=cycle,
                        )
                        report.actions.append(action)
                        self._audit(action)
                        continue
                    # HOLD LABEL (kpr-watch-04). THE ESCAPE HATCH DID NOT
                    # COVER THE DOOR THAT DOES MOST OF THE MERGING.
                    # `_NO_AUTOMERGE_LABELS` was referenced at exactly one site,
                    # inside `_sweep_unlinked_prs`, and its own comment said so.
                    # This path never saw a label and could not have: the
                    # `gh pr view` field list did not request `labels` at all.
                    # So for a `kanban/<task-id>` PR there was no label-shaped
                    # brake of any kind, and the asymmetry ran in the dangerous
                    # direction — a human labelling one `do-not-merge` got no
                    # warning and no effect, and would reasonably believe the PR
                    # was held while the watcher merged it.
                    #
                    # It matters most at exactly the moment the other brakes
                    # come off. The remaining holds on this path are the draft
                    # (which `_mark_ready` clears itself the moment a dependency
                    # is satisfied), an unsatisfied dependency, a manual gate row
                    # and a reviewer requesting changes — so releasing a
                    # MANUAL-ONLY card's gate removes every one of them at once.
                    #
                    # BEFORE THE UN-DRAFT, for the reason kpr-watch-05 put the
                    # protected-path guard there: un-drafting is visible, hard to
                    # walk back, and burns the one brake a human still has. The
                    # refusal is REPORTED as the shared classifier's `held_label`
                    # state, never a silent skip — that was the other half of the
                    # complaint, and `_refuse_held_label` writes the audit row.
                    held = self._refuse_held_label(pr_url, state, task["id"])
                    if held:
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification=HELD_LABEL, action="wait",
                            reason=("held: " + held_label_reason(held))[:500],
                            resume_cycle=cycle,
                        )
                        report.actions.append(action)
                        self._audit(action)
                        continue

                    # UN-DRAFT FIRST, before any hold can `continue` past this.
                    #
                    # Auto-merge must work regardless of who opened the PR — a
                    # kanban session, a CLI session, or a person. It did not: the
                    # sibling-conflict hold below returns early, so a green PR
                    # held behind a sibling was never taken out of draft, and
                    # when the sibling finally merged the PR sat there STILL a
                    # draft with nothing left to trigger it. Three AGOV PRs were
                    # in exactly that state — CLEAN, green, and invisible to
                    # auto-merge.
                    #
                    # Safe to do early because un-drafting merges nothing. It
                    # only removes the one blocker GitHub will not let the
                    # watcher clear later, and _mark_ready still refuses for a
                    # manual gate or an unsatisfied dependency.
                    if state.get("isDraft"):
                        self._mark_ready(pr_url, task["id"], self._connection(),
                                         state=state)

                    # Sibling-file-conflict guard (kph): another open PR edits the
                    # same source file(s) — merging both races on one path (the
                    # "two different blueprint.py" collision that stranded Cortex).
                    # Union-merged coordination files are excluded. Warn + audit
                    # always; HOLD (serialize the merge) only when
                    # hold_on_sibling_conflict is set, so a legitimate shared edit
                    # is not blocked by default.
                    sib = self._sibling_conflicts(pr_url, sibling_map) if sibling_map else {}
                    if sib:
                        detail = "; ".join(
                            f"{u} [{', '.join(sorted(fs))}]" for u, fs in sib.items()
                        )
                        logger.warning(
                            "pr_watcher: %s shares source file(s) with %d open PR(s): %s",
                            pr_url, len(sib), detail,
                        )
                        self._audit(WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done",
                            action="sibling_conflict_warn",
                            reason=f"shares source file(s) with open PR(s): {detail}",
                            resume_cycle=cycle,
                        ))
                        # Lessons-Learned (kph): a sibling source-file race is a
                        # systemic pipeline signal — record it so recurrence +
                        # remediation fire (classified as SIBLING_FILE_CONFLICT).
                        # A "wait"/"warn" action never reaches the task-failure
                        # lesson hook, so emit it explicitly here.
                        try:
                            from tools.workflow.lesson_learned import (
                                analyze_task, write_lesson,
                            )
                            write_lesson(analyze_task(
                                task["id"], outcome="sibling_file_conflict"))
                        except Exception as _ll_exc:  # noqa: BLE001
                            logger.debug(
                                "pr_watcher: sibling lesson hook failed: %s", _ll_exc)
                        if (self.config.get("hold_on_sibling_conflict", False)
                                and not _wins_sibling_tiebreak(
                                    pr_url, sib, blocked=blocked_siblings)):
                            action = WatcherAction(
                                task_id=task["id"], pr_url=pr_url,
                                classification="done", action="wait",
                                reason=(f"held: sibling file conflict with {len(sib)} "
                                        "open PR(s); a lower-numbered sibling goes first"),
                                resume_cycle=cycle,
                            )
                            report.actions.append(action)
                            self._audit(action)
                            continue

                    # ALREADY ON MAIN. The board tracks task -> PR; nothing
                    # checked task -> main, so a PR whose work merged under a
                    # different number stays green and mergeable, and merging it
                    # applies a diff against a branch that has moved on — a
                    # revert wearing a feature's clothes. This must run BEFORE
                    # _auto_merge; after it, the damage is a commit on main.
                    #
                    # Advisory by default and audited either way. `enforce`
                    # (KANBAN_LANDED_CHECK) holds the merge for a human, which
                    # is the same posture landed_check uses at seed and dispatch
                    # time — one switch, not a second one invented here.
                    #
                    # SURVEYED at THIS call site before wiring, because the
                    # existing survey measured the dispatch population and does
                    # not transfer: 2026-08-16, the 6 task-linked open PRs the
                    # merge path actually sees produced 0 fires (1 body-only
                    # reference, which never blocks). Against the three known
                    # true positives — rem-hyg-02 (#1738, closed by hand that
                    # day), ctx-perf-02 (#1646) and ctx-trust-02 (#1651) — it
                    # fires on 3 of 3 with blocking-tier `subject` evidence.
                    landed = landed_map.get(task["id"]) or {}
                    if landed.get("checked") and landed.get("landed"):
                        try:
                            from tools.kanban.landed_check import (
                                format_warning, mode as _landed_mode,
                            )
                            detail = format_warning(landed).strip()
                            enforcing = _landed_mode() == "enforce"
                        except Exception as _lc_exc:  # noqa: BLE001
                            detail = f"task {task['id']} already on the default branch"
                            enforcing = False
                            logger.debug(
                                "pr_watcher: landed-check formatting failed: %s", _lc_exc)
                        logger.warning(
                            "pr_watcher: %s — %s is ALREADY on the default branch "
                            "(evidence: %s); merging this PR may REVERT it",
                            pr_url, task["id"], landed.get("confidence"),
                        )
                        self._audit(WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done",
                            action="already_landed_hold" if enforcing
                                   else "already_landed_warn",
                            reason=detail[:500],
                            resume_cycle=cycle,
                        ))
                        if enforcing:
                            self._hitl_alert(
                                task["id"], pr_url,
                                "the work for this task is already on the default "
                                "branch under a different PR; merging this one may "
                                "revert it. Verify, then close or rebase.",
                            )
                            action = WatcherAction(
                                task_id=task["id"], pr_url=pr_url,
                                classification="done", action="wait",
                                reason=(f"held: {task['id']} already on the default "
                                        f"branch (evidence: {landed.get('confidence')})"),
                                resume_cycle=cycle,
                            )
                            report.actions.append(action)
                            self._audit(action)
                            continue

                    # BEHIND MAIN (kpr-stale-02). THE SAFETY HOLE, and the
                    # last gate before the merge because it is the only one
                    # that costs a forge round-trip — every cheaper refusal
                    # above has already passed by the time we ask.
                    #
                    # `mergeable` is MERGEABLE for a branch arbitrarily far
                    # behind main so long as nothing collides TEXTUALLY, so the
                    # CONFLICTING interlock only ever caught the colliding
                    # subset. The rest merged cleanly and re-applied their diff
                    # over a tree that had moved on. #1651 was -38/+26 on
                    # rest_v1.py and 36 commits behind main; a human closed it
                    # by hand, because nothing here could see it.
                    # `auto_rebase_on_conflict` is not the answer on its own —
                    # it repairs a branch that has ALREADY gone DIRTY, which is
                    # precisely the subset that never had this problem.
                    #
                    # THE REPAIR IS THE ONE THAT ALREADY EXISTS. A stale branch
                    # needs exactly what a conflicted one needs — a rebase onto
                    # its base — so this reuses `_maybe_rebase` rather than
                    # inventing a second push path: same ownership refusal
                    # (kanban/<task-id> only), same per-base-era budget, same
                    # audit. A branch it declines to touch is held and a human
                    # is told, which is the honest end state for a PR nobody
                    # can safely rebase automatically.
                    stale = self._stale_verdict(state)
                    if stale is not None:
                        behind_n, stale_why = stale
                        logger.warning(
                            "pr_watcher: refusing auto-merge for %s — %s",
                            pr_url, stale_why)
                        self._audit(WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done", action="behind_main_hold",
                            reason=stale_why[:500], resume_cycle=cycle,
                        ))
                        rebase = self._maybe_rebase(task, state)
                        if rebase.get("pushed"):
                            # The branch moved, so the head sha changed and CI
                            # will re-run against it. Next poll re-measures.
                            self._audit(WatcherAction(
                                task_id=task["id"], pr_url=pr_url,
                                classification="done", action="rebase",
                                reason=("rebased a stale branch onto %s: %s"
                                        % (base_ref, rebase.get("reason", "")))[:500],
                                resume_cycle=cycle,
                            ))
                        elif not rebase.get("attempted"):
                            # Nothing automatic can move this branch. A hold
                            # nobody can see is how work goes quiet, so say so.
                            self._hitl_alert(
                                task["id"], pr_url,
                                "this PR is %s behind %s and merges CLEANLY, so "
                                "merging it would revert whatever it touches. "
                                "Automatic rebase declined (%s) — rebase it by "
                                "hand, or close it."
                                % ("%d commits" % behind_n if behind_n >= 0
                                   else "reported BEHIND", base_ref,
                                   rebase.get("reason", "no reason given")),
                            )
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done", action="wait",
                            reason=("held: %s" % stale_why)[:500],
                            resume_cycle=cycle,
                        )
                        report.actions.append(action)
                        self._audit(action)
                        continue

                    approved_ok = (
                        not require_approval
                        or ec.is_approved_and_passing(state)
                    )
                    # A draft cannot be merged, only refused — so clear it here,
                    # AFTER every other gate has passed (green CI, no sibling
                    # conflict, approval satisfied). Ordering is the safety
                    # property: un-drafting is visible and hard to walk back, so
                    # it must never happen for a PR that was not about to merge
                    # anyway. _mark_ready refuses for a gate task or a held
                    # dependency; if it declines, fall through to the same "wait"
                    # branch a blocked merge takes.
                    # Belt-and-braces: normally the un-draft above already ran,
                    # but a PR can be converted back to a draft between polls.
                    # AHEAD OF THE UN-DRAFT (kpr-watch-05). `_auto_merge`
                    # refuses a protected PR too, but by then `_mark_ready` has
                    # already cleared the draft — and the comment above says why
                    # that must not happen for a PR that was not about to merge:
                    # un-drafting is visible and hard to walk back, and the draft
                    # is exactly the brake the per-episode manual gates relied on.
                    if approved_ok and self._refuse_protected(pr_url, task["id"]):
                        approved_ok = False
                    if approved_ok and state.get("isDraft"):
                        approved_ok = self._mark_ready(
                            pr_url, task["id"], self._connection(), state=state)
                    if approved_ok and self._auto_merge(pr_url, state=state):
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done",
                            action="merge",
                            reason="auto-merge ok" + ignored_note,
                            resume_cycle=cycle,
                        )
                    else:
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done",
                            action="wait",
                            reason="approval required or merge blocked"
                            + ignored_note,
                            resume_cycle=cycle,
                        )
                report.actions.append(action)
                self._audit(action)
                continue

            if classification == KanbanState.PR_OPENED:
                # CI THAT NEVER FIRED. Every other repair path in this loop
                # assumes there is a CI result to react to; with an empty rollup
                # there is nothing to fail, nothing to rebase against, and the
                # PR waits in PR_OPENED forever. Try the cheap fix once, then
                # hand it to a human rather than waiting silently.
                if self._ci_never_fired(state):
                    verdict = self._retrigger_ci(task["id"], pr_url)
                    if verdict.get("attempted"):
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification=classification.value,
                            action="ci_retrigger",
                            reason=verdict.get("reason", "")[:200],
                            resume_cycle=cycle,
                        )
                        report.actions.append(action)
                        self._audit(action)
                        continue
                    if "exhausted" in (verdict.get("reason") or ""):
                        # One re-trigger did not bring CI back: this is
                        # infrastructure, not something the loop can fix.
                        self._hitl_alert(
                            task["id"], pr_url,
                            "no CI checks ever ran, and a re-trigger did not "
                            "start them.")
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification=classification.value,
                            action="escalate",
                            reason="CI never fired; re-trigger exhausted",
                            resume_cycle=cycle,
                        )
                        report.actions.append(action)
                        self._audit(action)
                        continue

                # Report why we are actually waiting. PR_OPENED is also the
                # default fall-through, so a flat "CI still running" here
                # misreports a green-but-unapproved PR as mid-CI and hides the
                # real blocker.
                if ec.is_in_progress(state):
                    wait_reason = "CI still running"
                elif ec.is_passing(state):
                    wait_reason = "CI green; awaiting approving review"
                else:
                    wait_reason = "awaiting CI results"
                action = WatcherAction(
                    task_id=task["id"], pr_url=pr_url,
                    classification=classification.value,
                    action="wait", reason=wait_reason,
                    resume_cycle=cycle,
                )
                report.actions.append(action)
                self._audit(action)
                continue

            if classification == KanbanState.FAILED:
                action = WatcherAction(
                    task_id=task["id"], pr_url=pr_url,
                    classification=classification.value,
                    action="escalate",
                    reason="PR is stale — exceeded max age",
                    resume_cycle=cycle,
                )
                report.actions.append(action)
                self._audit(action)
                continue

            # A DIRTY PR gets the cheap recovery BEFORE any resume is spent:
            # rebase the branch onto its base and let CI re-run. Most drifted
            # branches merge fine afterwards, and the five resumes they would
            # otherwise burn end in a permanent human queue (xbm-wake-01 sat
            # 95.7h). A rebase that hits a REAL conflict is aborted, nothing is
            # pushed, and we fall straight through to the escalation below —
            # i.e. genuinely conflicting branches behave exactly as before.
            if classification == KanbanState.MERGE_CONFLICT:
                verdict = self._maybe_rebase(task, state)
                if verdict.get("pushed"):
                    action = WatcherAction(
                        task_id=task["id"], pr_url=pr_url,
                        classification=classification.value,
                        action="rebase",
                        reason=verdict.get("reason", "rebased onto base"),
                        # Deliberately the UNCHANGED resume count: a rebase is
                        # not a resume and must not consume that budget.
                        resume_cycle=cycle,
                        base_sha=verdict.get("base_sha", ""),
                    )
                    report.actions.append(action)
                    self._audit(action)
                    continue
                if verdict.get("attempted"):
                    # The attempt is spent — audit it so the rebase cap is
                    # durable across restarts — then escalate/resume as today.
                    self._audit(WatcherAction(
                        task_id=task["id"], pr_url=pr_url,
                        classification=classification.value,
                        action="rebase_failed",
                        reason=verdict.get("reason", "rebase failed"),
                        resume_cycle=cycle,
                        base_sha=verdict.get("base_sha", ""),
                    ))
                else:
                    logger.debug(
                        "pr_watcher: no auto-rebase for %s — %s",
                        task["id"], verdict.get("reason"),
                    )

                # NO AGENT CAN FIX A CONFLICT THAT IS NOT IN THE TREE.
                #
                # For union_only and phantom the branch merges clean under git,
                # so a resumed session opens the files, finds no conflict
                # markers and nothing to resolve, and pushes nothing. Ten
                # resumes went into hcx-evt-03 that way and fifteen into
                # kpr-dup-03; all they bought was the escalation that follows a
                # spent budget, after which pr_watcher goes quiet for good and
                # the board shows AWAITING MERGE with no explanation.
                #
                # The rebase above IS the remedy, so this sits after it: when a
                # rebase was possible it already ran and we never get here.
                # Reported as a `wait` rather than skipped in silence — a poll
                # that decides to do nothing has to say what it decided, which
                # is the complaint that opened this card.
                if conflict_kind in (CONFLICT_UNION_ONLY, CONFLICT_PHANTOM):
                    why = (
                        "only a .gitattributes merge=union rule the forge does "
                        "not apply" if conflict_kind == CONFLICT_UNION_ONLY
                        else "a stale cached verdict"
                    )
                    report.actions.append(WatcherAction(
                        task_id=task["id"], pr_url=pr_url,
                        classification=classification.value,
                        action="wait",
                        reason=(
                            f"{conflict_kind}: git merges this branch cleanly — "
                            f"the forge reports CONFLICTING because of {why}. "
                            f"A resume cannot help; {verdict.get('reason', 'no rebase')}"
                        ),
                        resume_cycle=cycle,
                        base_sha=verdict.get("base_sha", ""),
                    ))
                    continue

            # Resume classes: CI_FAILED / MERGE_CONFLICT / CHANGES_REQUESTED
            if cycle >= max_cycles:
                # ESCALATE ONCE PER PR, not once per poll. This branch re-fired
                # on every cycle: #1742 and #1744 were re-escalated every ~42 s
                # for hours, and pr_watcher.escalate stood at 42,902 rows —
                # nearly all of it a handful of PRs re-announcing. That floods
                # the audit trail and, worse, re-sends the HITL alert, which is
                # how a "manual intervention required" notification stops being
                # read. The task is already parked; saying so again changes
                # nothing.
                already = self._count_audit_actions(
                    task["id"], ("pr_watcher.escalate",), pr_url=pr_url)
                if already:
                    logger.debug(
                        "pr_watcher: %s already escalated (%d/%d) — staying quiet",
                        pr_url, cycle, max_cycles)
                    continue
                action = WatcherAction(
                    task_id=task["id"], pr_url=pr_url,
                    classification=classification.value,
                    action="escalate",
                    reason=(
                        f"resume cap reached ({cycle}/{max_cycles}) — "
                        "manual intervention required"
                    ),
                    resume_cycle=cycle,
                )
                # This is the legitimate HITL case: every automatic recovery is
                # spent. Notify rather than only logging.
                self._hitl_alert(
                    task["id"], pr_url,
                    f"resume cap reached ({cycle}/{max_cycles}) after "
                    f"{classification.value}.")
                report.actions.append(action)
                self._audit(action)
                continue

            # A RESUME MUST BE GIVEN TIME TO WORK BEFORE THE NEXT ONE IS SPENT.
            #
            # The budget is five ATTEMPTS at getting an agent to fix the PR.
            # Nothing stopped it being spent at POLL speed: the watcher injected
            # context, the next poll 45 s later saw the same classification and
            # injected again, and the whole budget was gone in about three
            # minutes — before any agent could plausibly have read the first
            # message, let alone pushed a commit. Measured 2026-08-16: #1742
            # burned 17:17:43 -> 17:20:56, #1744 17:54:20 -> 17:57:29, both then
            # escalating to "manual intervention required" while fully green and
            # merging cleanly under `git merge-tree`.
            #
            # This does not change the budget, only the pacing: five attempts
            # still, now spaced far enough apart to be attempts. A PR waiting out
            # the cooldown is recorded as `wait`, which is what the state
            # actually is, rather than silently consuming a cycle.
            since = self._seconds_since_last_resume(task["id"], pr_url=pr_url)
            cooldown = float(self.config.get(
                "resume_cooldown_seconds", RESUME_COOLDOWN_SECONDS))
            if since is not None and since < cooldown:
                report.actions.append(WatcherAction(
                    task_id=task["id"], pr_url=pr_url,
                    classification=classification.value,
                    action="wait",
                    reason=(
                        f"resume cooldown: {since:.0f}s of {cooldown:.0f}s since "
                        f"the last injection (cycle {cycle}/{max_cycles})"
                    ),
                    resume_cycle=cycle,
                ))
                continue

            context = prepare_resume_context(
                task["id"],
                classification,
                state,
                ci_logs,
                max_chars=ci_log_max,
            )
            queued = self._send_resume(task["id"], context)
            action = WatcherAction(
                task_id=task["id"], pr_url=pr_url,
                classification=classification.value,
                action="resume" if queued else "wait",
                reason="injected resume context"
                if queued
                else "queue_message failed",
                resume_cycle=cycle + 1,
                context_preview=context[:400],
            )
            report.actions.append(action)
            self._audit(action)

        # After the loop, because a task that reached a terminal state is not IN
        # the loop — that is precisely why its alert was stranded.
        self._sweep_stale_hitl_alerts()

        # A task whose status drifted OUT of the polled set never reaches the
        # loop above, so the merge it is waiting on is never observed. This is
        # the only place that looks from the PR side (kpr-watch-09).
        try:
            self.reconcile_stranded_tasks()
        except Exception as exc:  # noqa: BLE001 — never break the poll
            logger.debug("pr_watcher: stranded reconcile failed: %s", exc)

        report.finished_at = datetime.now(timezone.utc).isoformat()
        # Merge-eligibility observation (kpr-watch-02), beside the heartbeat and
        # for the same reason: the heartbeat proves the WATCHER ran, and this
        # proves what it was looking at while it ran. Together they separate "the
        # merger is down" from "the merger is up and a green PR is still sitting
        # there", which is the distinction nothing in this pipeline could make.
        self._record_merge_eligibility()
        # Liveness proof, written only once the poll has actually completed.
        self._record_heartbeat(report)
        return report


    #: Statuses `list_pr_tasks` polls. A task outside this set is invisible to
    #: the main loop — which is the whole reason `reconcile_stranded_tasks`
    #: exists. Kept next to the sweep that compensates for it so the two cannot
    #: drift apart silently.
    _POLLED_STATUSES = (
        "in_progress", "scheduled", "pr_opened",
        "ci_failed", "merge_conflict", "changes_requested",
    )
    #: Nothing to reconcile — the task is already finished or abandoned.
    _TERMINAL_STATUSES = ("done", "cancelled", "decomposed", "superseded")

    def _pr_state(self, number, runner=None):
        """`MERGED` / `CLOSED` / `OPEN` for one PR, or None if unknowable.

        None is deliberately distinct from CLOSED. An unanswerable query is not
        evidence, and the one thing this sweep must never do is mark a task done
        because `gh` timed out.
        """
        run = runner or subprocess.run
        try:
            proc = run(  # nosec B603 — fixed argv, shell=False
                ["gh", "pr", "view", str(number), "--json", "state"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60, shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("pr_watcher: state lookup for #%s failed: %s", number, exc)
            return None
        if getattr(proc, "returncode", 1) != 0:
            return None
        try:
            payload = json.loads(getattr(proc, "stdout", "") or "{}")
        except ValueError:
            return None
        return payload.get("state") if isinstance(payload, dict) else None

    def reconcile_stranded_tasks(self) -> Dict[str, Any]:
        """Mark done any task whose PR MERGED while nothing was watching it.

        `list_pr_tasks` polls BY STATUS, but the thing that has to be observed —
        a merged PR — is a property of the PR. So a task whose status drifts out
        of the polled set becomes permanently invisible to the only component
        that closes the loop, and the board can never self-correct.

        MEASURED 2026-08-18: kpr-watch-01 was dispatched at 16:09, opened PR
        #1744 at 16:27, and was reaped to `backlog` at 16:35 — eight minutes
        AFTER its PR existed. The PR merged two days later with nothing
        watching; the task still read `backlog` while its work sat on main, and
        five kpr-watch-* tasks were queued behind it.

        Rare — one case out of 424 PR-carrying tasks — but permanent and silent,
        and it took a human noticing. The entry paths are many (the stale
        reaper, the PR-flow rollback, auto-revive, the orphan sweep, a manual
        move); the trap is ONE, so it is closed here rather than at each writer.
        Reconciling from the PR side is what makes it writer-agnostic.

        SCOPED TO COST NOTHING ON A HEALTHY BOARD: only tasks that carry a PR
        url AND sit outside both the polled and the terminal sets are looked up,
        so a board with no stranded tasks makes no forge calls at all.
        """
        out: Dict[str, Any] = {
            "reconciled": [], "unknown": [], "checked": 0, "written": False,
        }
        try:
            get_conn = self._connection()
            conn = get_conn()
        except Exception:  # noqa: BLE001 — a reconcile must never break the poll
            return out
        try:
            rows = conn.execute(
                "SELECT id, status, executor_url FROM kanban_tasks"
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.debug("pr_watcher: stranded reconcile query failed: %s", exc)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return out

        skip = set(self._POLLED_STATUSES) | set(self._TERMINAL_STATUSES)
        try:
            for raw in rows:
                row = dict(raw) if not isinstance(raw, dict) else raw
                status = (row.get("status") or "").strip()
                url = (row.get("executor_url") or "").strip()
                if status in skip or "/pull/" not in url:
                    continue
                number = _pr_number(url)
                out["checked"] += 1
                # Injection point, matching this file's existing convention
                # (`_rebase_fn`, `_pr_list_runner`, `_auto_merge_runner`).
                lookup = getattr(self, "_pr_state_runner", None)
                state = ((lookup(number) if lookup else self._pr_state(number))
                         or "").upper()
                if state != "MERGED":
                    if not state:
                        out["unknown"].append(row.get("id"))
                    continue
                reason = (
                    f"reconciled: PR {url} is MERGED but the task was left in "
                    f"{status!r} — nothing polls that status, so the merge was "
                    f"never observed"
                )
                out["reconciled"].append({"task_id": row.get("id"), "reason": reason})
                logger.warning("pr_watcher: %s — %s", row.get("id"), reason)
                if not self.dry_run:
                    _set_task_status(
                        self._connection(), row.get("id"), "done", reason=reason)
                    out["written"] = True
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return out

    def _sweep_unlinked_prs(self, report: "WatcherReport") -> None:
        """Auto-merge green PRs that no kanban task points at.

        THE GAP THIS CLOSES. Every repair path here starts from list_pr_tasks,
        which selects kanban_tasks — so a PR with no task row is invisible to the
        entire pipeline: no auto-ready, no auto-merge, no rebase, no escalation.
        On 2026-08-09 roughly a dozen PRs opened from CLI sessions (fix/*, feat/*)
        were each merged BY HAND for exactly that reason, while kanban's own PRs
        merged themselves. Auto-merge should not depend on which door the work
        came through.

        DELIBERATELY THE NARROW SUBSET. A task-linked PR gets resumes, rebases and
        status transitions because there is a task to carry that state. Here there
        is none, so this does one thing: merge a PR that is already finished and
        already passing. It never pushes, never closes, never edits a branch.

        THE OPT-OUT IS A LABEL, because a human may open a PR to discuss rather
        than to land. Any of hold/do-not-merge/wip/no-automerge stops it, and a
        draft stops it too — an unlinked PR is NOT un-drafted, since for a human
        the draft IS the "not ready" signal (a kanban task has a gate and a
        dependency to say that instead).

        THE LADDER ITSELF LIVES IN `tools/ci/merge_readiness.py` (kpr-watch-01).
        It used to be a run of bare `continue` statements here, which meant the
        pipeline could merge a PR but could not answer "why is this one not
        merging" — and the read-only report that answers it must not be a second
        transcription of this policy. Same table, two consumers: this one merges
        on `ready`, `python -m tools.ci.merge_readiness` only prints.
        """
        if not self.config.get("merge_unlinked_prs", True):
            return
        if self.dry_run:
            return
        try:
            proc = self._pr_list_runner(
                ["gh", "pr", "list", "--state", "open", "--limit", "100", "--json",
                 "number,url,headRefName,headRefOid,baseRefName,isDraft,mergeable,"
                 "mergeStateStatus,labels,statusCheckRollup,reviews,state"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60,
            )
            if getattr(proc, "returncode", 1) != 0:
                return
            prs = json.loads(proc.stdout or "[]")
        except Exception as exc:  # noqa: BLE001 — a sweep must never stop the poll
            logger.debug("pr_watcher: unlinked sweep listing failed: %s", exc)
            return

        try:
            linked = {
                (t.get("pr_url") or "").strip()
                for t in list_pr_tasks(self._connection())
            }
        except Exception as exc:  # noqa: BLE001
            # Without the linked set every task-linked PR would be treated as
            # unlinked and merged without its task ever being updated. Refuse.
            logger.warning("pr_watcher: cannot list linked PRs, skipping sweep: %s", exc)
            return

        default_branch = self._default_branch()
        required = self.required_checks()
        for pr in prs:
            url = (pr.get("url") or "").strip()
            verdict = classify_merge_readiness(
                pr, default_branch=default_branch, linked_urls=linked,
                changed_files=self._open_pr_index().get(url, {}).get("files")
                if url in self._open_pr_index() else None,
                protected_paths=self._protected_paths(),
                required_checks=required)
            if verdict.state == READY:
                # STALENESS IS MEASURED LAST, and only here (kpr-stale-02).
                # It is the one rung that costs a forge round-trip, so it is
                # paid for exactly the PRs that were otherwise about to merge —
                # never for the drafts, the red ones or the task-linked ones.
                verdict = classify_merge_readiness(
                    pr, default_branch=default_branch, linked_urls=linked,
                    behind_by=self._behind_by(pr),
                    max_behind_commits=self._max_behind(),
                    changed_files=self._open_pr_index().get(url, {}).get("files")
                    if url in self._open_pr_index() else None,
                    protected_paths=self._protected_paths(),
                    required_checks=required,
                ) if self.config.get("refuse_merge_when_behind", True) else verdict
            if verdict.state != READY:
                # The refusals used to be bare `continue`s and so were entirely
                # invisible; the hold label was the only one that said anything.
                if verdict.state == HELD_LABEL:
                    logger.info(
                        "pr_watcher: %s carries a hold label — leaving it", url)
                elif verdict.state == PROTECTED_PATH:
                    # Named rather than dropped into the debug-level `else`:
                    # the whole point is that a human can see what is waiting
                    # and why. `_refuse_protected` is not called here — the
                    # classifier already decided, and calling it would emit a
                    # second audit row for one refusal.
                    #
                    # Audited ONCE per PR (kpr-watch-10). This branch is reached
                    # every poll for as long as the PR sits there, and it was
                    # the louder of the two writers: 161 rows in 59 minutes.
                    if self._protected_already_held(url):
                        logger.debug("pr_watcher: %s still held — %s",
                                     url, verdict.reason)
                    else:
                        logger.warning(
                            "pr_watcher: %s is green and MERGEABLE but %s",
                            url, verdict.reason)
                        self._audit(WatcherAction(
                            task_id="", pr_url=url, classification="blocked",
                            action="protected_path_hold",
                            reason=verdict.reason[:500], resume_cycle=0,
                        ))
                elif verdict.state == BEHIND_MAIN:
                    # AND NOTHING ELSE. The unlinked sweep deliberately never
                    # pushes — it has no task to carry the state and no claim on
                    # the branch, so rebasing somebody's PR here would be the
                    # sweep exceeding its own charter. Report it, audit it,
                    # leave it for whoever opened it. (A kanban/* branch takes
                    # the linked path below, which DOES rebase.)
                    logger.warning(
                        "pr_watcher: %s is green and MERGEABLE but %s — "
                        "refusing to merge it; a human should rebase it",
                        url, verdict.reason)
                    self._audit(WatcherAction(
                        task_id="", pr_url=url, classification="done",
                        action="behind_main_hold", reason=verdict.reason[:500],
                        resume_cycle=0,
                    ))
                else:
                    logger.debug("pr_watcher: not merging %s — %s: %s",
                                 url or "<no url>", verdict.state, verdict.reason)
                continue
            if self._auto_merge(url, state=pr):
                logger.info("pr_watcher: auto-merged unlinked PR %s (%s)",
                            url, pr.get("headRefName"))
                action = WatcherAction(
                    task_id="", pr_url=url, classification="done",
                    action="merge", reason="unlinked PR, green and mergeable",
                    resume_cycle=0,
                )
                report.actions.append(action)
                self._audit(action)

        report.finished_at = datetime.now(timezone.utc).isoformat()

    def run_daemon(
        self, interval: int = 30, max_iterations: int = 0
    ) -> None:
        """Poll forever (or up to `max_iterations` ticks).

        Picks up its own code changes between polls: this daemon runs for days,
        so without that every merged fix stays inert until a human restarts it.
        On 2026-08-09 that was four hand restarts, and twice the board looked
        broken when the only fault was this process serving hours-old code.
        """
        from tools.genesis import code_reload

        # Record which code this process is running (autonomy-id-01). This
        # daemon DOES self-update — see the docstring above — which is exactly
        # why the record matters: `restart_if_code_changed` re-execs through
        # `os.execv`, and a re-exec that fails leaves a long-lived process
        # serving old code while looking healthy. The row is the difference
        # between "it self-updates" as a design claim and as an observation.
        try:
            # DISTINCT PER PROCESS (autonomy-sid-01). Two pr_watchers race
            # on auto-merge, which /start's own notes warn about, and a
            # shared id hid that from every coordination surface.
            from tools.coordination.service_identity import (
                claim_service_identity,
            )

            claim_service_identity("pr-watcher", "pr_watcher")
            from tools.coordination import session_registry as _reg

            _reg.register(intent="pr watcher — merging eligible kanban PRs")
        except Exception:  # noqa: BLE001 — observability must not stop the poll
            pass

        started_at = time.time()
        baseline = code_reload.snapshot()
        watch = bool(self.config.get("restart_on_code_change", True))

        iteration = 0
        while True:
            iteration += 1
            # Keep the session row fresh — see tools/daemon/base.py for why a
            # boot-only registration makes a long-running process disappear.
            try:
                from tools.coordination import session_registry as _sreg

                _sreg.heartbeat()
            except Exception:  # noqa: BLE001 — liveness reporting is not a dep
                pass
            try:
                report = self.poll_once()
                logger.info(
                    "pr_watcher: iteration=%d checked=%d actions=%d",
                    iteration, report.tasks_checked, len(report.actions),
                )
            except Exception as exc:  # defensive — keep the daemon alive
                logger.warning("pr_watcher iteration failed: %s", exc)
            # Periodic housekeeping, NOT part of a task-focused poll. It lives
            # here rather than in poll_once because poll_once is what unit tests
            # call: inside it, the sweep shelled out to a real `gh pr list` and
            # operated on live PRs during the suite — only a stubbed _auto_merge
            # stood between that and merging someone's open PR from a test run.
            try:
                self._sweep_unlinked_prs(
                    WatcherReport(started_at="", finished_at="", tasks_checked=0))
            except Exception as exc:  # noqa: BLE001 — a sweep must not stop the loop
                logger.warning("pr_watcher: unlinked sweep failed: %s", exc)
            if max_iterations and iteration >= max_iterations:
                return
            # AFTER a completed poll and before the sleep: never mid-work, and
            # never while a merge is in flight. Does not return if it re-execs.
            try:
                code_reload.restart_if_code_changed(
                    baseline, started_at=started_at, enabled=watch)
            except Exception as exc:  # noqa: BLE001 — watching must not kill it
                logger.warning("pr_watcher: code-change check failed: %s", exc)
            time.sleep(max(1, int(interval)))


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    # Load .env so KANBAN_PIPELINE_ENFORCE (and API keys) are set even when the
    # daemon is started outside a shell that exported them — mirrors
    # kanban_scheduler. Without this the enforced done-gate goes inert on a bare
    # restart, silently reverting to CI-only auto-merge.
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    ap = argparse.ArgumentParser(
        description="OPT-70 autonomous PR watcher"
    )
    ap.add_argument("--once", action="store_true",
                    help="Run a single poll and exit")
    ap.add_argument("--daemon", action="store_true",
                    help="Poll forever at --interval")
    ap.add_argument("--interval", type=int, default=30,
                    help="Seconds between polls in daemon mode")
    ap.add_argument("--max-iterations", type=int, default=0,
                    help="Stop after N daemon ticks (0 = forever). Makes the "
                         "iteration= heartbeat observable in a bounded run.")
    ap.add_argument("--task", default=None,
                    help="Limit the poll to a single task id")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't queue messages, merge, or write audit rows")
    ap.add_argument("--json", action="store_true",
                    help="Emit report as JSON to stdout")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)

    if not args.once and not args.daemon:
        args.once = True

    config = load_config(
        pathlib.Path(args.config) if args.config else None
    )
    watcher = PRWatcher(config=config, dry_run=args.dry_run)

    if args.daemon:
        watcher.run_daemon(
            interval=args.interval, max_iterations=args.max_iterations
        )
        return 0

    report = watcher.poll_once(task_id=args.task)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"pr_watcher: checked {report.tasks_checked} task(s), "
            f"{len(report.actions)} action(s)"
        )
        for a in report.actions:
            print(f"  {a.task_id} | {a.classification} | {a.action}  {a.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
