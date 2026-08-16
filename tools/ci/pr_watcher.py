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
from typing import Any, Dict, List, Optional, Tuple

import yaml

from tools.ci import error_classifier as ec
from tools.kanban.state_machine import KanbanState


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


@dataclass
class WatcherAction:
    task_id: str
    pr_url: str
    classification: str
    action: str  # 'resume' | 'merge' | 'wait' | 'escalate' | 'dry_run'
    reason: str = ""
    resume_cycle: int = 0
    context_preview: str = ""


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
    task depends on — `depends_on_task_id`, which is what
    promote_backlog_to_scheduled actually checks, not the held row alone. So a
    task whose dependency is unsatisfied must keep its draft: the draft is that
    card's brake, and taking it off would ship work a human deliberately held.

    Errs toward HELD. A lookup that fails answers True, because the cost of a
    false "held" is one PR a human marks ready, and the cost of a false "free"
    is auto-merging gated work.
    """
    try:
        row = conn.execute(
            "SELECT d.status AS dep_status "
            "FROM kanban_tasks t "
            "LEFT JOIN kanban_tasks d ON d.id = t.depends_on_task_id "
            "WHERE t.id = %s AND t.depends_on_task_id IS NOT NULL",
            (task_id,),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pr_watcher: dependency lookup failed for %s: %s", task_id, exc)
        return True
    if row is None:
        return False  # no dependency declared — nothing holding it
    data = dict(row) if not isinstance(row, dict) else row
    return (data.get("dep_status") or "") not in ("done", "decomposed")


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
_NO_AUTOMERGE_LABELS = frozenset({"hold", "do-not-merge", "do not merge", "wip",
                                  "no-automerge", "blocked"})

_GH_JSON_FIELDS = (
    "state,statusCheckRollup,reviews,mergeable,isDraft,"
    "headRefName,baseRefName,updatedAt,createdAt,number,url"
)


def _pr_number(url: str) -> int:
    """The PR number, or a very large number when it cannot be read.

    Unreadable sorts LAST so it never wins the tie-break by accident.
    """
    m = re.search(r"/pull/(\d+)", url or "")
    return int(m.group(1)) if m else 1 << 30


def _wins_sibling_tiebreak(pr_url: str, siblings) -> bool:
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
    """
    mine = _pr_number(pr_url)
    return all(mine < _pr_number(other) for other in (siblings or {}))


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
        self._default_branch_resolver = (
            default_branch_resolver or repo_default_branch
        )
        self._default_branch_cache: Optional[str] = None
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

    def _open_pr_files(self) -> Dict[str, set]:
        """Map every open PR's url -> set of changed file paths (single gh call).

        Best-effort: returns {} if gh is unavailable / errors, so the sibling
        check degrades to a no-op rather than blocking the watcher.
        """
        try:
            proc = self._pr_list_runner(
                ["gh", "pr", "list", "--state", "open", "--json", "url,files",
                 "--limit", "200"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60,
            )
            if getattr(proc, "returncode", 1) != 0:
                return {}
            data = json.loads(proc.stdout or "[]")
        except Exception as exc:  # noqa: BLE001
            logger.debug("pr_watcher: open-PR file listing failed: %s", exc)
            return {}
        out: Dict[str, set] = {}
        for pr in data:
            url = pr.get("url")
            if not url:
                continue
            out[url] = {f.get("path", "") for f in (pr.get("files") or []) if f.get("path")}
        return out

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

    def _conflict_is_real(self, state: dict, runner=None) -> bool:
        """Confirm a CONFLICTING verdict against git before acting on it.

        GitHub computes `mergeable` ASYNCHRONOUSLY and caches it. When the base
        moves quickly the cached answer goes stale and stays stale, because
        nothing about the PR changed to invalidate it. On 2026-08-09 THIRTEEN
        PRs were reported CONFLICTING while `git merge-tree` merged every one of
        them cleanly — same base sha, same head sha, exit 0, a single tree hash
        and no conflict output.

        The cost of believing it was not cosmetic. Each of those PRs burned two
        rebase attempts and five resume cycles fighting a conflict that did not
        exist, then raised a HITL alert. A rebase cannot clear the flag either: a
        branch that is already current has nothing to rebase, so it succeeds,
        changes nothing, and the stale verdict survives.

        `git merge-tree --write-tree` performs the real merge in memory and exits
        non-zero on a genuine conflict, so it is the authority here and the forge
        is the cache.

        Errs toward TRUSTING THE FORGE: any failure to verify returns True, so an
        unreachable git or an unfetchable ref leaves today's behaviour unchanged
        rather than declaring a real conflict resolved.
        """
        head = (state.get("headRefName") or "").strip()
        base = (state.get("baseRefName") or "").strip() or self._default_branch()
        if not head:
            return True
        root = str(pathlib.Path(__file__).resolve().parents[2])
        run = runner or subprocess.run
        try:
            fetch = run(  # nosec B603 — fixed argv, shell=False
                ["git", "fetch", "--quiet", "origin", base, head],
                cwd=root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120, shell=False,
            )
            if getattr(fetch, "returncode", 1) != 0:
                return True
            merged = run(  # nosec B603
                ["git", "merge-tree", "--write-tree",
                 f"origin/{base}", f"origin/{head}"],
                cwd=root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120, shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("pr_watcher: conflict verification failed: %s", exc)
            return True
        if getattr(merged, "returncode", 1) == 0:
            return False
        return True

    def _mark_ready(self, pr_url: str, task_id: str, get_conn) -> bool:
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
        """
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
        return True

    def _auto_merge(self, pr_url: str) -> bool:
        if self.dry_run:
            return True
        if not self.config.get("auto_merge_enabled", False):
            return False
        try:
            proc = self._auto_merge_runner(
                ["gh", "pr", "merge", pr_url, "--squash", "--auto"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
            if getattr(proc, "returncode", 1) != 0:
                # Previously this returned False with NO log line, so a forge
                # that refused every merge looked identical to a board with
                # nothing to merge. That is how 11 PRs sat "awaiting merge" while
                # the watcher decided "merge" on each pass and was refused.
                logger.warning(
                    "pr_watcher: gh refused to merge %s: %s",
                    pr_url, (getattr(proc, "stderr", "") or "").strip()[:200])
                return False
            return True
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
        """Prior pr_watcher resume events for this task, on THIS PR."""
        return self._count_audit_actions(
            task_id, ("pr_watcher.resume",), pr_url=pr_url)

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

    def _rebase_attempts(self, task_id: str, pr_url: Optional[str] = None) -> int:
        """Prior auto-rebase attempts for this task on THIS PR, net of refunds.

        Attempts spent on a conflict the forge only IMAGINED are refunded, so a
        PR is not permanently locked out of the one action that can clear a stale
        verdict. Floored at zero: a refund can restore a budget, never grant one.
        """
        spent = self._count_audit_actions(
            task_id, ("pr_watcher.rebase", "pr_watcher.rebase_failed"),
            pr_url=pr_url,
        )
        refunded = self._count_audit_actions(
            task_id, ("pr_watcher.rebase_refund",), pr_url=pr_url)
        return max(0, spent - refunded)

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

        cap = int(self.config.get("max_rebase_attempts_per_task", 2))
        attempts = self._rebase_attempts(
            task_id, pr_url=(state.get("url") or "").strip() or None)
        if attempts >= cap:
            return {"attempted": False, "pushed": False,
                    "reason": f"rebase attempts exhausted ({attempts}/{cap})"}

        branch = (state.get("headRefName") or "").strip()
        base = (state.get("baseRefName") or "").strip() or self._default_branch()

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
            return rebase(task_id, branch, base=base)
        except Exception as exc:  # noqa: BLE001 — must never stop the poll
            logger.warning("pr_watcher: auto-rebase errored for %s: %s", task_id, exc)
            return {"attempted": True, "pushed": False,
                    "reason": f"rebase errored: {exc}"}

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

    def _hitl_recovered(self, state: dict, cycle: int, max_cycles: int) -> bool:
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

        The two raise sites are the resume cap (`cycle >= max_cycles`) and CI
        that never fired, so both are negated here. Anything that adds a third
        raise site must negate it here too, or the alert flaps again.
        """
        if (state.get("mergeable") or "").upper() != "MERGEABLE":
            return False
        if cycle >= max_cycles:
            return False
        return not self._ci_never_fired(state)

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
        # Sibling-file-conflict map (kph): fetch every open PR's changed files ONCE
        # per cycle so the DONE path can flag a merge candidate that races another
        # open PR on the same source file (the "two different blueprint.py" class).
        sibling_map = (
            self._open_pr_files()
            if self.config.get("sibling_conflict_check", True)
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
            )

            # WAKE EVENTS (agov-wake-03). This is the one place in ICDEV that
            # observes "PR #N just went green", so it is where
            # `wake_on_event("pr:1342:ci_green")` gets satisfied. Emitted right
            # after classification and BEFORE any of the holds below — a wake
            # subscriber is waiting on the CI verdict, not on whether the
            # watcher went on to merge. Re-emitting the same key every cycle is
            # harmless: fire_event only promotes wakes that are still pending.
            self._emit_wake_events(pr_url, classification, state)
            if classification == KanbanState.MERGE_CONFLICT and not self._conflict_is_real(state):
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
                if self._count_audit_actions(
                        task["id"], ("pr_watcher.rebase_refund",), pr_url=pr_url) == 0:
                    self._refund_rebase_budget(task["id"], pr_url)
                logger.warning(
                    "pr_watcher: %s is reported CONFLICTING but merges cleanly — "
                    "rebasing to force the forge to recompute", pr_url)

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
            if self._hitl_recovered(state, cycle, max_cycles):
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
                        self._mark_ready(pr_url, task["id"], self._connection())

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
                                and not _wins_sibling_tiebreak(pr_url, sib)):
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
                    if approved_ok and state.get("isDraft"):
                        approved_ok = self._mark_ready(
                            pr_url, task["id"], self._connection())
                    if approved_ok and self._auto_merge(pr_url):
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done",
                            action="merge",
                            reason="auto-merge ok",
                            resume_cycle=cycle,
                        )
                    else:
                        action = WatcherAction(
                            task_id=task["id"], pr_url=pr_url,
                            classification="done",
                            action="wait",
                            reason="approval required or merge blocked",
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
                    ))
                else:
                    logger.debug(
                        "pr_watcher: no auto-rebase for %s — %s",
                        task["id"], verdict.get("reason"),
                    )

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

        report.finished_at = datetime.now(timezone.utc).isoformat()
        # Liveness proof, written only once the poll has actually completed.
        self._record_heartbeat(report)
        return report

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
        """
        if not self.config.get("merge_unlinked_prs", True):
            return
        if self.dry_run:
            return
        try:
            proc = self._pr_list_runner(
                ["gh", "pr", "list", "--state", "open", "--limit", "100", "--json",
                 "number,url,headRefName,baseRefName,isDraft,mergeable,labels,"
                 "statusCheckRollup,reviews,state"],
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
        for pr in prs:
            url = (pr.get("url") or "").strip()
            if not url or url in linked:
                continue
            if pr.get("isDraft"):
                continue
            labels = {
                (lbl.get("name") or "").strip().lower()
                for lbl in (pr.get("labels") or [])
            }
            if labels & _NO_AUTOMERGE_LABELS:
                logger.info("pr_watcher: %s carries a hold label — leaving it", url)
                continue
            if (pr.get("baseRefName") or "") != default_branch:
                continue
            if (pr.get("mergeable") or "").upper() != "MERGEABLE":
                continue
            state = dict(pr)
            if not ec.is_passing(state):
                continue
            if ec.is_changes_requested(state):
                # A reviewer asked for changes. Merging over that is the one
                # thing an automation must never do.
                continue
            if self._auto_merge(url):
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

        started_at = time.time()
        baseline = code_reload.snapshot()
        watch = bool(self.config.get("restart_on_code_change", True))

        iteration = 0
        while True:
            iteration += 1
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
