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
DEFAULT_CI_LOG_MAX = 4000

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


# Coordination files that MANY task branches legitimately co-edit (manifest
# shards, append-only-table registry, nav/registry configs, conftest schema).
# Two PRs touching these is normal, not a collision — exclude them from the
# sibling-conflict check so it only fires on genuine same-source-file races
# (e.g. two branches each creating a different tools/cortex/blueprint.py). See
# the merge-conflict-hotspots prevention notes.
#
# "Union-merged" is only literally true for the manifest entries: `.gitattributes`
# declares `tools/manifest*` `merge=union` (kax-conflict-03), so concurrent
# appends there really do resolve without a human. The remaining paths are
# structured config/code, where union would produce duplicate keys or broken
# syntax — they are excluded from the sibling check as a heuristic about how
# they are edited, NOT because git resolves them automatically. Adding a path
# here does not make it auto-mergeable.
#
# `args/ci_test_files/` is the second entry that is literally union-merged
# (kax-conflict-07). It holds the pytest allowlists that used to be a
# line-continuation chain inside .github/workflows/icdev-ci.yml. That inlining
# is what deadlocked the board on 2026-08-09: five open PRs each appended a test
# path to the same hand-written workflow, so this guard made each a sibling of
# every other and refused all five. Note what is NOT listed here — the workflow
# itself. It carries real job definitions, and two PRs editing a job's `run:`
# block is a genuine collision worth serializing; only the additive list moved
# out, so only the list is excluded.
_ADDITIVE_PATH_MARKERS = (
    "tools/manifest/",
    "tools/manifest.md",
    "args/ci_test_files/",
    ".claude/hooks/pre_tool_use.py",
    "tools/dashboard/templates/base.html",
    ".claude/commands/start.md",
    "args/component_registry.yaml",
    "args/projects.yaml",
    "args/genesis_config.yaml",
    "tests/conftest.py",
    "docs/reference/commands.md",
)

#: Substrings marking a DERIVED artifact — a file produced by a generator and
#: checked in, never hand-edited.
#:
#: These are excluded for a different reason than the coordination files above.
#: A coordination file is safe to co-edit because it union-merges. A generated
#: file is safe because a conflict in it is not a disagreement at all: re-running
#: the generator over the merged tree produces the correct content, so there is
#: nothing for a human to arbitrate and nothing for a serialized merge to protect.
#:
#: WHY THIS EXISTS. On 2026-08-09 a single generated file —
#: docs/research/external-benchmark-map.generated.md — deadlocked the entire
#: board. Every branch that added a module regenerated it, so every open PR
#: touched it, so hold_on_sibling_conflict made every PR a sibling of every other
#: and refused all six. The guard was behaving correctly; the input made it
#: total. The daemons were healthy the whole time, which is what made it read as
#: "the dispatcher is broken". One shared generated file is enough to stop
#: everything, so the class is excluded rather than that one path.
_GENERATED_PATH_MARKERS = (
    ".generated.",
    "/generated/",
)


def _is_generated_path(path: str) -> bool:
    """True when `path` is a generator-produced artifact (regenerate, don't merge)."""
    p = (path or "").replace("\\", "/")
    return any(marker in p for marker in _GENERATED_PATH_MARKERS)


def _is_additive_path(path: str) -> bool:
    """True when `path` is safe for two PRs to touch at once.

    Either union-merged coordination state, or a derived artifact whose conflicts
    are resolved by regeneration rather than by arbitration.
    """
    p = (path or "").replace("\\", "/")
    if _is_generated_path(p):
        return True
    return any(marker in p for marker in _ADDITIVE_PATH_MARKERS)


#: `isDraft` is not cosmetic. GitHub refuses `gh pr merge` on a draft with
#: "Pull Request is still a draft", and without this field the watcher could not
#: SEE that — so it re-attempted the same refused merge every cycle, forever.
#: Five of ten open PRs on 2026-08-09 were green drafts sitting untouched for
#: exactly that reason, each waiting on a human to click "Ready for review".
_GH_JSON_FIELDS = (
    "state,statusCheckRollup,reviews,mergeable,isDraft,"
    "headRefName,baseRefName,updatedAt,number,url"
)


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
            return proc.returncode == 0
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

    def _count_audit_actions(self, task_id: str, actions: Tuple[str, ...]) -> int:
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
                f"SELECT details FROM audit_trail "
                f"WHERE action IN ({placeholders}) "
                f"AND {details_col} LIKE %s",
                (*actions, f"%{task_id}%"),
            ).fetchall()
            return len(rows)
        except Exception:
            return 0
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _resume_cycle(self, task_id: str) -> int:
        """Count of prior pr_watcher resume events for this task."""
        return self._count_audit_actions(task_id, ("pr_watcher.resume",))

    def _rebase_attempts(self, task_id: str) -> int:
        """Count of prior auto-rebase attempts (successful or not) for this task."""
        return self._count_audit_actions(
            task_id, ("pr_watcher.rebase", "pr_watcher.rebase_failed"),
        )

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
        attempts = self._rebase_attempts(task_id)
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
            cycle = self._resume_cycle(task["id"])

            if classification == KanbanState.DONE:
                merged = (
                    (state.get("state") or "").upper() == "MERGED"
                )
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
                        if self.config.get("hold_on_sibling_conflict", False):
                            action = WatcherAction(
                                task_id=task["id"], pr_url=pr_url,
                                classification="done", action="wait",
                                reason=f"held: sibling file conflict with {len(sib)} open PR(s)",
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
                report.actions.append(action)
                self._audit(action)
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

        report.finished_at = datetime.now(timezone.utc).isoformat()
        # Liveness proof, written only once the poll has actually completed.
        self._record_heartbeat(report)
        return report

    def run_daemon(
        self, interval: int = 30, max_iterations: int = 0
    ) -> None:
        """Poll forever (or up to `max_iterations` ticks)."""
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
            if max_iterations and iteration >= max_iterations:
                return
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
