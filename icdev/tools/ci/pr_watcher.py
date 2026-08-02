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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yaml

from tools.ci import error_classifier as ec
from tools.kanban.state_machine import KanbanState


logger = get_logger(__name__)
ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "args" / "pr_watcher_config.yaml"

# Max characters of CI log text we inject back into a resume message.
DEFAULT_CI_LOG_MAX = 4000

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


def _parse_pr_url(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = _PR_URL_RE.search(text)
    return match.group(0) if match else None



def _set_task_status(get_conn, task_id: str, status: str, reason: str = "") -> bool:
    """Write the task's status. The watcher is the ONLY component that knows when
    a PR actually merged, so it is the right owner of the pr_opened -> done edge.

    Previously it recorded 'done' in the audit trail and never touched
    kanban_tasks — the board could not tell an open PR from a finished one.
    Never raises: a status-write failure must not stall the watch loop.
    """
    try:
        conn = get_conn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pr_watcher: no DB connection to set %s -> %s: %s",
                       task_id, status, exc)
        return False
    try:
        conn.execute(
            "UPDATE kanban_tasks SET status = %s, updated_at = %s WHERE id = %s",
            (status, datetime.now(timezone.utc).isoformat(), task_id),
        )
        try:
            conn.execute(
                "INSERT INTO kanban_status_transitions "
                "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("kst-" + uuid.uuid4().hex[:12], task_id, "pr_opened", status,
                 "pr_watcher", reason[:200],
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


# Coordination / union-merged files that MANY task branches legitimately co-edit
# (manifest shards, append-only-table registry, nav/registry configs, conftest
# schema). Two PRs touching these is normal, not a collision — exclude them from
# the sibling-conflict check so it only fires on genuine same-source-file races
# (e.g. two branches each creating a different tools/cortex/blueprint.py). See the
# merge-conflict-hotspots prevention notes.
_ADDITIVE_PATH_MARKERS = (
    "tools/manifest/",
    "tools/manifest.md",
    ".claude/hooks/pre_tool_use.py",
    "tools/dashboard/templates/base.html",
    ".claude/commands/start.md",
    "args/component_registry.yaml",
    "args/projects.yaml",
    "args/genesis_config.yaml",
    "tests/conftest.py",
    "docs/reference/commands.md",
)


def _is_additive_path(path: str) -> bool:
    """True when `path` is a union-merged coordination file (not a collision risk)."""
    p = (path or "").replace("\\", "/")
    return any(marker in p for marker in _ADDITIVE_PATH_MARKERS)


_GH_JSON_FIELDS = (
    "state,statusCheckRollup,reviews,mergeable,"
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
        self._default_branch_resolver = (
            default_branch_resolver or repo_default_branch
        )
        self._default_branch_cache: Optional[str] = None

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

    def _resume_cycle(self, task_id: str) -> int:
        """Best-effort count of prior pr_watcher resume events for this
        task. Reads audit_trail details JSON."""
        get_conn = self._connection()
        try:
            conn = get_conn()
        except Exception:
            return 0
        try:
            _pg = getattr(conn, "_backend", "sqlite") == "postgresql"
            if _pg:
                rows = conn.execute(
                    "SELECT details FROM audit_trail "
                    "WHERE action = 'pr_watcher.resume' "
                    "AND details::text LIKE %s",
                    (f"%{task_id}%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT details FROM audit_trail "
                    "WHERE action = 'pr_watcher.resume' "
                    "AND details LIKE %s",
                    (f"%{task_id}%",),
                ).fetchall()
            return len(rows)
        except Exception:
            return 0
        finally:
            try:
                conn.close()
            except Exception:
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
        watcher.run_daemon(interval=args.interval)
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
