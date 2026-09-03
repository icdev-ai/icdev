# CUI // SP-CTI
"""Startup recovery for interrupted kanban tasks — a restart must never cost work.

Two independent sweeps used to reset EVERY non-gate ``in_progress`` row to
``backlog`` when the scheduler started: the entrypoint block in
``tools/genesis/kanban_scheduler.py`` and ``reflexes/kanban.py::
_startup_recover_stale_in_progress`` on cycle 1. Neither asked whether anything
was still working the task, so restarting the daemon was a decision with a cost:

* A task whose session had already committed survived — its commits sit on
  ``kanban/<id>`` and the re-dispatched session resumes from them.
* A task with no branch and no worktree lost everything the session had done and
  was re-dispatched from scratch.

On 2026-08-08 ``kax-obs-01`` was in the second state, so a needed restart was
DEFERRED to avoid it — which delayed ~30 commits of reflex fixes that only go
live on a restart. Deferring the restart is the wrong fix; this module makes the
restart safe instead.

The reset is NOT deleted — an orphaned ``in_progress`` row is invisible to every
promotion path and must still be recovered. What changes:

1. **Provable liveness holds the reset.** Four independent sources, any one of
   which is sufficient: the scheduler's in-process handle, a fresh
   ``agent_sessions`` heartbeat whose cwd is the task's worktree, a live
   ``kanban:task:<id>`` lease holder, and a live OS process whose cwd/env/argv
   names the task. Absence of evidence is treated as death (else nothing is ever
   recovered); presence of any evidence is treated as life.
2. **The notification says what survived.** ``commits preserved on branch
   kanban/<id> (N commits)`` vs ``no branch — work discarded``, derived from git
   rather than from ``kanban_tasks.branch_name`` (that column is only written at
   completion, so it is NULL for every interrupted task and reading it as "no
   branch" is wrong).
3. **An interruption is not a failure.** ``failure_count`` and
   ``last_failure_reason`` are left alone. Writing a failure reason here is what
   fed clean tasks into the autofix queue: ``failure_triage.find_recent_failures``
   selects on ``last_failure_reason IS NOT NULL`` plus a recency window.

Operator use — check whether a restart is safe BEFORE restarting:

    python -m tools.kanban.startup_recovery --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]

# Liveness evidence sources, in the order they are consulted (cheapest first).
EV_HANDLE = "process-handle"      # the scheduler's own _running dict
EV_SESSION = "session-registry"   # agent_sessions heartbeat in the task worktree
EV_LEASE = "task-lease"           # kanban:task:<id> lease held by a live process
EV_PROCESS = "os-process"         # a live OS process whose cwd/env/argv names it

# Why a task was held rather than reset.
HELD_GATE = "manual-gate"
HELD_EXTERNAL = "external-executor"

# A session with no heartbeat inside this window is not evidence of life.
# Mirrors tools/coordination/constants.SESSION_TTL_SECONDS; imported lazily so
# this module works in a checkout without the coordination package.
DEFAULT_SESSION_TTL_SECONDS = 900

RESET_REASON = (
    "startup-recovery: the task was in_progress when the scheduler restarted and "
    "no live session was found working it"
)


@dataclass
class Liveness:
    """Is something still working this task, and what proves it."""

    alive: bool
    source: Optional[str] = None
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"alive": self.alive, "source": self.source, "detail": self.detail}


@dataclass
class Provenance:
    """What of an interrupted task's work survives the reset."""

    branch: Optional[str] = None
    commits: int = 0
    worktree: Optional[str] = None
    dirty: bool = False
    recoverable: bool = False
    summary: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "branch": self.branch,
            "commits": self.commits,
            "worktree": self.worktree,
            "dirty": self.dirty,
            "recoverable": self.recoverable,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------

def path_mentions_task(path: Optional[str], task_id: str) -> bool:
    """True when *path* has a component that IS this task's worktree directory.

    Component equality, not substring: ``kanban/kax-recover-04`` must not match
    a sibling ``kax-recover-041``. Both the dispatch worktree (``<base>/<id>``)
    and the merge worktree (``<base>/.merge-<id>``) count.
    """
    if not path or not task_id:
        return False
    tid = task_id.lower()
    wanted = {tid, f".merge-{tid}"}
    parts = str(path).lower().replace("\\", "/").split("/")
    return any(p in wanted for p in parts)


def _session_ttl_seconds() -> int:
    try:
        from tools.coordination.constants import SESSION_TTL_SECONDS

        return int(SESSION_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — coordination package optional
        return DEFAULT_SESSION_TTL_SECONDS


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def live_sessions(conn, ttl_seconds: Optional[int] = None) -> List[Dict[str, Any]]:
    """Active ``agent_sessions`` rows with a heartbeat inside the TTL.

    Read through the CALLER's connection rather than
    ``session_registry.list_active()`` so the sweep and its liveness evidence
    always come from one database — a test (or an operator with a redirected
    ``ICDEV_DB_PATH``) cannot end up resetting rows in one database on the
    strength of sessions read from another. Freshness is filtered in Python:
    portable across PostgreSQL and SQLite, and the table is tiny.
    """
    ttl = _session_ttl_seconds() if ttl_seconds is None else ttl_seconds
    try:
        rows = conn.execute(
            "SELECT session_id, agent_type, pid, cwd, last_heartbeat, status "
            "FROM agent_sessions WHERE status = 'active'"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — self-creating table may not exist yet
        logger.debug("startup-recovery: agent_sessions unreadable (%s)", exc)
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl)
    fresh: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        beat = _parse_ts(d.get("last_heartbeat"))
        if beat is not None and beat >= cutoff:
            fresh.append(d)
    return fresh


def scan_live_task_processes(task_ids: Sequence[str]) -> Dict[str, int]:
    """Map task_id -> pid for tasks that a live OS process is demonstrably in.

    ONE pass over the process table for all candidates — a per-task scan would
    multiply the cost by the number of stuck rows. Three signals, in order of
    precision: ``ICDEV_DISPATCH_TASK_ID`` in the environment (set by
    ``_dispatch_via_claude_cli``), a cwd inside the task's worktree, and the
    task's prompt file on the command line. Never raises; psutil is optional and
    per-process access errors are expected (system processes deny cwd/environ).
    """
    found: Dict[str, int] = {}
    wanted = [t for t in task_ids if t]
    if not wanted:
        return found
    try:
        import psutil
    except Exception:  # noqa: BLE001 — psutil optional
        logger.debug("startup-recovery: psutil unavailable, skipping process scan")
        return found

    self_pid = os.getpid()
    for proc in psutil.process_iter(["pid"]):
        if len(found) == len(wanted):
            break
        try:
            if proc.pid == self_pid:
                continue
            cwd = None
            env_task = None
            cmdline = ""
            try:
                cwd = proc.cwd()
            except Exception:  # noqa: BLE001
                pass
            try:
                env_task = (proc.environ() or {}).get("ICDEV_DISPATCH_TASK_ID")
            except Exception:  # noqa: BLE001
                pass
            try:
                cmdline = " ".join(proc.cmdline() or [])
            except Exception:  # noqa: BLE001
                pass
            for tid in wanted:
                if tid in found:
                    continue
                if env_task and env_task == tid:
                    found[tid] = proc.pid
                elif path_mentions_task(cwd, tid):
                    found[tid] = proc.pid
                elif cmdline and f"/{tid}.md" in cmdline.replace("\\", "/"):
                    found[tid] = proc.pid
        except Exception:  # noqa: BLE001 — a process that vanished mid-scan
            continue
    return found


def _lease_holder_pid(task_id: str) -> Optional[int]:
    """PID of a LIVE holder of ``kanban:task:<id>``, or None.

    Read through the shared verdict (``tools.kanban.lease_liveness``,
    autonomy-adm-03) so this rung cannot drift from the dispatch reaper's idea
    of what a lease holder is; it consumes the PID half only.

    ``pid_alive`` is None when the answer is unknowable; per its own contract
    that must not be read as death, but it is not evidence of life either — the
    other three sources decide in that case.

    The HEARTBEAT half is deliberately not evidence here. ``last_heartbeat_at``
    is stamped by the scheduler for every child it can still poll, and this
    sweep runs precisely when that scheduler has just restarted: a fresh
    heartbeat proves only that the OLD process saw the worker alive before it
    exited, not that the worker is alive now. Whether it is alive now is the
    process scan's question (``EV_PROCESS``), answered directly. Reading the
    stale stamp as life would hold every interrupted task for
    ``HEARTBEAT_LIVE_MINUTES`` after each restart — the recovery policy
    (kax-recover-04) is unchanged by this consolidation.
    """
    try:
        from tools.kanban import lease_liveness

        verdict = lease_liveness.task_lease_verdict(task_id)
        if verdict.pid_alive is not True:
            return None
        return verdict.holder_pid
    except Exception as exc:  # noqa: BLE001
        logger.debug("startup-recovery: lease check failed for %s: %s", task_id, exc)
        return None


def _lease_session(task_id: str) -> Optional[str]:
    """Session id of a LIVE REGISTERED holder of ``kanban:task:<id>``, or None.

    The pid half (``_lease_holder_pid``) misses the holder that matters most
    here: an operator's ``cli.py --claim``, whose pid exits a second after
    claiming. On 2026-09-02 21:28 this sweep reset kpr-stale-03 -- claimed by
    hand, PR in flight -- to backlog with "no live session was found working
    it", while the claiming session heartbeat in agent_sessions the whole time.
    Nothing read the session id on the lease.

    Read through the shared verdict, like the pid half: the SAME
    ``session_is_live`` the dispatch reaper consults, so startup recovery and
    the reaper cannot disagree about what a claim is worth.
    """
    try:
        from tools.kanban import lease_liveness

        verdict = lease_liveness.task_lease_verdict(task_id)
        if verdict.state == lease_liveness.STATE_LIVE and verdict.session_alive:
            return verdict.holder_session
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("startup-recovery: lease session check failed for %s: %s", task_id, exc)
        return None


def task_liveness(
    task_id: str,
    *,
    running_ids: Iterable[str] = (),
    sessions: Optional[Sequence[Dict[str, Any]]] = None,
    process_pids: Optional[Dict[str, int]] = None,
) -> Liveness:
    """Whether anything is provably still working *task_id*.

    Absence of evidence is death: an orphan must still be recovered, and every
    signal here requires something to be alive right now. Presence of ANY
    evidence is life — the four sources cover different failure modes (hooks
    disabled, psutil missing, coordination tables absent) and no one of them is
    trustworthy enough to be the only one consulted.
    """
    if task_id in set(running_ids or ()):
        return Liveness(True, EV_HANDLE, "tracked in this scheduler's _running")

    for s in sessions or ():
        if path_mentions_task(s.get("cwd"), task_id):
            return Liveness(
                True,
                EV_SESSION,
                "session {sid} ({atype}, pid={pid}) heartbeating in {cwd}".format(
                    sid=str(s.get("session_id"))[:12],
                    atype=s.get("agent_type") or "?",
                    pid=s.get("pid"),
                    cwd=s.get("cwd"),
                ),
            )

    lease_pid = _lease_holder_pid(task_id)
    if lease_pid is not None:
        return Liveness(True, EV_LEASE, f"kanban:task:{task_id} held by live pid {lease_pid}")

    lease_sid = _lease_session(task_id)
    if lease_sid:
        return Liveness(
            True, EV_LEASE,
            f"kanban:task:{task_id} held by live session {lease_sid} (claimed by hand)",
        )

    if process_pids and task_id in process_pids:
        return Liveness(True, EV_PROCESS, f"live pid {process_pids[task_id]} working this task")

    return Liveness(False, None, "no live session, lease, or process found")


# ---------------------------------------------------------------------------
# What survives — derived from git, not from kanban_tasks.branch_name
# ---------------------------------------------------------------------------

def _git(args: Sequence[str], cwd: Path, timeout: int = 15) -> Tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, (r.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001 — git missing/slow must not block recovery
        logger.debug("startup-recovery: git %s failed: %s", " ".join(args), exc)
        return 1, ""


def _base_ref(repo_root: Path) -> str:
    rc, out = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo_root, timeout=5)
    if rc == 0 and out:
        return out
    for candidate in ("origin/main", "origin/master", "main", "master"):
        rc, out = _git(["rev-parse", "--verify", "--quiet", candidate], repo_root, timeout=5)
        if rc == 0 and out:
            return candidate
    return "main"


def _worktree_for_branch(repo_root: Path, branch: str) -> Optional[str]:
    rc, out = _git(["worktree", "list", "--porcelain"], repo_root)
    if rc != 0 or not out:
        return None
    current: Optional[str] = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):].strip()
        elif line.startswith("branch ") and current:
            if line[len("branch "):].strip() in (branch, f"refs/heads/{branch}"):
                return current
    return None


def _task_repo_root(task_id: str) -> Path:
    """The repo a task builds in — ICDev unless the prefix routes it elsewhere.

    An EXTERNAL task's branch lives in ITS repo (``args/kanban_external_repos.yaml``).
    Asking ICDev whether a compass branch exists always answers no, which would
    report every external task's work as discarded.
    """
    try:
        from tools.kanban.repo_registry import resolve_task_repo

        target = resolve_task_repo(task_id)
        if target is not None and target.is_external and target.root is not None:
            return Path(target.root)
    except Exception as exc:  # noqa: BLE001 — registry optional; ICDev is the default
        logger.debug("startup-recovery: repo resolution for %s failed: %s", task_id, exc)
    return BASE_DIR


def work_provenance(task_id: str, *, repo_root: Optional[Path] = None) -> Provenance:
    """What of this task's in-flight work survives a reset to backlog.

    Answers from git because ``kanban_tasks.branch_name`` is written at
    COMPLETION (migration 114) — it is NULL for every interrupted task, so
    reading it as "no branch, nothing to preserve" is wrong for exactly the
    tasks this function exists to describe.
    """
    root = Path(repo_root) if repo_root else _task_repo_root(task_id)
    branch = f"kanban/{task_id}"
    prov = Provenance(branch=None)

    rc, _ = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root, timeout=5)
    if rc == 0:
        prov.branch = branch
        rc_c, out_c = _git(["rev-list", "--count", f"{_base_ref(root)}..refs/heads/{branch}"], root)
        if rc_c == 0 and out_c.isdigit():
            prov.commits = int(out_c)
        prov.worktree = _worktree_for_branch(root, branch)
        if prov.worktree:
            rc_s, out_s = _git(["status", "--porcelain"], Path(prov.worktree))
            prov.dirty = rc_s == 0 and bool(out_s)

    prov.recoverable = prov.commits > 0 or bool(prov.worktree)
    if prov.commits > 0:
        prov.summary = f"commits preserved on branch {branch} ({prov.commits} commit(s))"
        if prov.dirty:
            prov.summary += "; worktree also holds uncommitted changes"
    elif prov.worktree:
        prov.summary = (
            f"no commits on {branch} yet; worktree retained at {prov.worktree}"
            + (" with uncommitted changes" if prov.dirty else "")
            + " — the re-dispatched session reuses it"
        )
    elif prov.branch:
        prov.summary = f"branch {branch} exists but has no commits and no worktree — work discarded"
    else:
        prov.summary = "no branch — work discarded"
    return prov


def build_recovery_message(
    task_id: str, title: Optional[str], prov: Provenance,
) -> Tuple[str, str, str]:
    """(subject, body, severity) for one reset task.

    Severity is the honest signal: losing in-flight work is a warning, resuming
    from commits is informational.
    """
    display = (title or task_id)[:60]
    severity = "info" if prov.recoverable else "warning"
    body = (
        f"Task '{display}' ({task_id}) was in_progress when the scheduler restarted "
        "and no live session was found working it. Reset to backlog for "
        "re-dispatch; failure_count and last_failure_reason are unchanged "
        "(an interruption is not a failure).\n"
        f"Work state: {prov.summary}."
    )
    return f"RESTARTED: {display}", body, severity


def build_held_message(held: Sequence[Dict[str, Any]]) -> Tuple[str, str, str]:
    """(subject, body, severity) for tasks the restart deliberately left alone."""
    lines = [
        f"- {h['id']}: {h.get('detail') or h.get('reason')}" for h in held
    ]
    return (
        f"RESTART HELD {len(held)} LIVE TASK(S)",
        "The scheduler restarted and did NOT reset these in_progress tasks — "
        "something is provably still working them:\n" + "\n".join(lines),
        "info",
    )


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

def _is_manual_gate(task_id: Optional[str], title: Optional[str]) -> bool:
    try:
        from tools.kanban.gates import is_manual_gate

        return bool(is_manual_gate(task_id, title))
    except Exception:  # noqa: BLE001 — never let this check reset a gate
        return bool(task_id and str(task_id).endswith("-gate-00"))


def classify_task(
    row: Dict[str, Any],
    *,
    running_ids: Iterable[str] = (),
    sessions: Optional[Sequence[Dict[str, Any]]] = None,
    process_pids: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Decide ``reset`` or ``hold`` for one in_progress row. Pure — no I/O."""
    tid = row.get("id")
    title = row.get("title")
    if _is_manual_gate(tid, title):
        return {"id": tid, "title": title, "action": "hold", "reason": HELD_GATE,
                "detail": "manual-mode gate — held in_progress by design"}
    if (row.get("executor_type") or "") == "github_actions":
        return {"id": tid, "title": title, "action": "hold", "reason": HELD_EXTERNAL,
                "detail": "GitHub Actions executor — runs independently of this scheduler"}

    live = task_liveness(
        tid, running_ids=running_ids, sessions=sessions, process_pids=process_pids,
    )
    if live.alive:
        return {"id": tid, "title": title, "action": "hold", "reason": live.source,
                "detail": live.detail}
    return {"id": tid, "title": title, "action": "reset", "reason": None,
            "detail": live.detail}


def _record_transition(conn, task_id: str, reason: str) -> None:
    """Append the reset to the kanban_status_transitions ledger (best-effort).

    Without a row here the board shows a task back in backlog with nothing
    naming who moved it, and the stale-reaper's actor check (which reads the most
    recent transition) has no record of this sweep at all.
    """
    try:
        from tools.kanban.transition_reason import resolve_transition_reason

        conn.execute(
            "INSERT INTO kanban_status_transitions "
            "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                "kst-" + secrets.token_hex(6),
                task_id, "in_progress", "backlog", "startup-recovery",
                resolve_transition_reason(
                    reason, from_status="in_progress", to_status="backlog",
                    actor="startup-recovery",
                ),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — ledger write must never block recovery
        logger.debug("startup-recovery: transition row for %s skipped: %s", task_id, exc)


def foreign_scheduler_pid() -> int:
    """PID of a live kanban scheduler other than this process, or 0."""
    try:
        from tools.kanban.scheduler_control import scheduler_lock_owner_pid

        return scheduler_lock_owner_pid()
    except Exception as exc:  # noqa: BLE001
        logger.debug("startup-recovery: owner check failed: %s", exc)
        return 0


def dispatch_liveness(
    pid: Optional[int], recorded_start: Optional[str] = None
) -> Optional[bool]:
    """``True`` still ours, ``False`` provably gone, ``None`` cannot tell.

    THREE-VALUED ON PURPOSE. ``None`` is not "dead": the caller must leave the
    row alone, because clearing a dispatch stamp we cannot disprove would
    double-dispatch a task that is quietly working.

    PID EXISTENCE ALONE IS NOT ENOUGH, and this is the Windows case. PIDs are
    recycled, aggressively so on Windows, and a stale pid that now belongs to an
    unrelated process reads as "alive" — so a bare ``pid_exists`` check skips the
    stalled row forever and the slot is never reclaimed. That is the failure this
    function exists to avoid, and it is the more likely one here: a recycled pid
    silently perpetuates the stall, where the opposite error is caught by the
    conservative default.

    Layered so the portable path always works and the precise one is used when
    the OS can supply it:

    * ``psutil.pid_exists`` — OS-agnostic, answers "gone" unambiguously;
    * ``dispatch_reaper.process_start_time`` — ``GetProcessTimes`` via ctypes on
      Windows (deliberately not ``wmic``, removed on Windows 11 build 26200) and
      ``ps -o lstart=`` on POSIX. A pid that exists but whose start time differs
      from the recorded one is a RECYCLED pid, so the process we dispatched is
      provably gone.

    Note the polarity differs from ``dispatch_reaper.is_same_process``, which
    fails closed toward "do not kill". Reclaiming fails closed toward "do not
    reclaim", so an unknown cannot reuse that predicate directly.
    """
    if not pid:
        return None
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None

    try:
        import psutil
    except Exception:  # noqa: BLE001 — psutil optional
        return None
    try:
        if not psutil.pid_exists(pid_int):
            return False  # unambiguous: nothing holds that pid
    except Exception:  # noqa: BLE001
        return None

    # The pid exists. Is it still OUR process, or has the number been reused?
    if not recorded_start:
        return None  # nothing to compare against — cannot tell, so do not touch
    try:
        from tools.kanban.dispatch_reaper import process_start_time

        current = process_start_time(pid_int)
    except Exception:  # noqa: BLE001
        return None
    if not current:
        return None  # exists but unidentifiable — cannot tell
    return current.strip() == str(recorded_start).strip()


def reclaim_stale_scheduled_dispatches(
    conn=None, *, dry_run: bool = False, conn_factory: Optional[Callable[[], Any]] = None
) -> Dict[str, Any]:
    """Clear the dispatch stamp on a ``scheduled`` row whose PID is gone.

    THE GAP THIS CLOSES. ``recover_interrupted_tasks`` sweeps
    ``WHERE status = 'in_progress'``. A dispatch that dies BEFORE the row moves
    to ``in_progress`` leaves it in ``scheduled`` holding a dead
    ``dispatch_pid``, which no sweep looks at — so the row is neither running
    nor reclaimable, and the slot it was going to use is simply never used.

    Measured 2026-08-12: exa-bench-10 sat in ``scheduled`` with pid 17016 dead
    and a heartbeat 71 minutes stale while two of three dispatch slots were
    free and the board had no other eligible work. Nothing went red; the
    scheduler logged "idle" and the task was the only thing it could have run.

    Deliberately conservative: a PID whose liveness cannot be determined
    (psutil absent, access denied) is LEFT ALONE. PID reuse also resolves in the
    safe direction — an unrelated process on the same number reads as alive, so
    the row is skipped rather than double-dispatched.
    """
    out: Dict[str, Any] = {"scanned": 0, "reclaimed": [], "skipped": [], "dry_run": dry_run}
    owns = conn is None
    if conn is None:
        if conn_factory is None:
            from tools.db.storage import get_connection as _get_connection

            conn_factory = _get_connection
        conn = conn_factory()
    try:
        rows = [
            dict(r) for r in conn.execute(
                "SELECT id, dispatch_pid, dispatch_pid_started_at "
                "FROM kanban_tasks "
                "WHERE status = 'scheduled' AND dispatch_pid IS NOT NULL"
            ).fetchall()
        ]
        out["scanned"] = len(rows)
        for row in rows:
            alive = dispatch_liveness(
                row.get("dispatch_pid"), row.get("dispatch_pid_started_at")
            )
            if alive is not False:
                out["skipped"].append(
                    {"id": row["id"], "pid": row.get("dispatch_pid"),
                     "why": "still ours" if alive else "liveness undeterminable"}
                )
                continue
            out["reclaimed"].append({"id": row["id"], "pid": row.get("dispatch_pid")})
            if dry_run:
                continue
            conn.execute(
                "UPDATE kanban_tasks SET dispatch_pid = NULL, "
                "dispatch_pid_started_at = NULL, execution_id = NULL, "
                "last_heartbeat_at = NULL, updated_at = %s WHERE id = %s",
                (datetime.now(timezone.utc).isoformat(), row["id"]),
            )
        if out["reclaimed"] and not dry_run:
            conn.commit()
            logger.info(
                "startup-recovery: reclaimed %d scheduled row(s) holding a dead "
                "dispatch pid: %s",
                len(out["reclaimed"]), [r["id"] for r in out["reclaimed"]],
            )
    except Exception as exc:  # noqa: BLE001 — reclaiming must never wedge dispatch
        logger.warning("startup-recovery: scheduled reclaim skipped: %s", exc)
    finally:
        if owns:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return out


def recover_interrupted_tasks(
    *,
    running_ids: Iterable[str] = (),
    dry_run: bool = False,
    notify: bool = True,
    scan_processes: bool = True,
    respect_foreign_owner: bool = True,
    conn_factory: Optional[Callable[[], Any]] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Reset genuinely orphaned in_progress tasks; hold everything still alive.

    Returns ``{swept, reset[], held[], dry_run, sweep_skipped, reason}``. Never
    raises — a broken recovery must not stop the scheduler from starting.
    """
    out: Dict[str, Any] = {
        "swept": 0, "reset": [], "held": [], "dry_run": dry_run,
        "sweep_skipped": False, "reason": None,
    }

    if respect_foreign_owner:
        owner = foreign_scheduler_pid()
        if owner:
            out["sweep_skipped"] = True
            out["reason"] = f"another live scheduler (pid {owner}) owns the runner"
            logger.info("startup-recovery: skipped — %s", out["reason"])
            return out

    if conn_factory is None:
        try:
            from tools.db.storage import get_connection as _get_connection

            conn_factory = _get_connection
        except Exception as exc:  # noqa: BLE001
            out["sweep_skipped"] = True
            out["reason"] = f"storage unavailable: {exc}"
            return out

    decisions: List[Dict[str, Any]] = []
    conn = None
    try:
        conn = conn_factory()
        rows = [
            dict(r) for r in conn.execute(
                "SELECT id, title, executor_type FROM kanban_tasks "
                "WHERE status = 'in_progress'"
            ).fetchall()
        ]
        out["swept"] = len(rows)
        if not rows:
            return out

        sessions = live_sessions(conn)
        process_pids = (
            scan_live_task_processes([r["id"] for r in rows]) if scan_processes else {}
        )
        decisions = [
            classify_task(r, running_ids=running_ids, sessions=sessions,
                          process_pids=process_pids)
            for r in rows
        ]

        now_iso = datetime.now(timezone.utc).isoformat()
        for d in decisions:
            if d["action"] != "reset" or dry_run:
                continue
            # failure_count / last_failure_reason deliberately untouched: an
            # interruption is not a failure, and a reason written here puts a
            # healthy task into failure_triage's autofix queue.
            conn.execute(
                "UPDATE kanban_tasks SET status = 'backlog', updated_at = %s "
                "WHERE id = %s AND status = 'in_progress'",
                (now_iso, d["id"]),
            )
            _record_transition(conn, d["id"], RESET_REASON)
        if not dry_run:
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        out["reason"] = str(exc)[:200]
        logger.warning("startup-recovery: sweep failed: %s", out["reason"])
        try:
            if conn is not None:
                conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return out
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001
            pass

    # Provenance + notifications outside the DB connection: git subprocesses and
    # Telegram are slow, and holding a connection across them is what turns a
    # recovery sweep into a lock storm on the tasks table.
    for d in decisions:
        if d["action"] == "hold":
            out["held"].append(d)
            continue
        prov = work_provenance(d["id"], repo_root=repo_root)
        entry = {**d, "provenance": prov.as_dict()}
        out["reset"].append(entry)
        logger.info(
            "startup-recovery: %s%s in_progress -> backlog (%s)",
            d["id"], " [dry-run]" if dry_run else "", prov.summary,
        )
        if notify and not dry_run:
            subject, body, severity = build_recovery_message(d["id"], d.get("title"), prov)
            _send(subject, body, severity)

    live_held = [h for h in out["held"] if h.get("reason") not in (HELD_GATE, HELD_EXTERNAL)]
    for h in live_held:
        logger.info("startup-recovery: HELD %s — %s", h["id"], h["detail"])
    if notify and not dry_run and live_held:
        _send(*build_held_message(live_held))
    return out


def _send(subject: str, body: str, severity: str) -> None:
    try:
        from tools.notifications.adapters.telegram import send as tg_send

        tg_send(subject, body, severity=severity)
    except Exception as exc:  # noqa: BLE001 — notification failure never blocks recovery
        logger.debug("startup-recovery: notification failed: %s", exc)


# ---------------------------------------------------------------------------
# CLI — answer "is restarting the scheduler safe right now?"
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report (or perform) recovery of interrupted kanban tasks.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="classify only — change nothing, send nothing")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--no-notify", action="store_true", help="suppress Telegram")
    parser.add_argument("--no-process-scan", action="store_true",
                        help="skip the psutil pass (faster, less evidence)")
    parser.add_argument("--force", action="store_true",
                        help="sweep even when another live scheduler owns the runner")
    args = parser.parse_args(argv)

    result = recover_interrupted_tasks(
        dry_run=args.dry_run,
        notify=not args.no_notify,
        scan_processes=not args.no_process_scan,
        respect_foreign_owner=not args.force,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    if result["sweep_skipped"]:
        print(f"sweep skipped: {result['reason']}")
        return 0
    print(f"in_progress tasks: {result['swept']}")
    for h in result["held"]:
        print(f"  HELD  {h['id']}: {h['detail']}")
    for r in result["reset"]:
        verb = "WOULD RESET" if args.dry_run else "RESET"
        print(f"  {verb} {r['id']}: {r['provenance']['summary']}")
    lost = [r for r in result["reset"] if not r["provenance"]["recoverable"]]
    if lost:
        print(f"\n{len(lost)} task(s) have NO branch and NO worktree — "
              "a restart discards their in-flight work.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
