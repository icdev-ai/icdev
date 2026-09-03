# CUI // SP-CTI
"""Is the ``kanban:task:<id>`` lease guarding LIVE WORK? One answer, asked everywhere (autonomy-adm-03).

THE DEFECT. A coordination lease records the pid of the process that TOOK it,
and for a dispatched task that is the dispatcher, which exits as soon as it has
handed off to the worker. Judging liveness from that pid alone is wrong in BOTH
directions at once:

  * a DEAD pid read as dead work — a live worker's lease gets reaped and the
    task is dispatched a second time (one source of the duplicate-PR rate
    autonomy-adm-01 measured; rem-hyg-15 watched a probe reap a lease whose task
    had heartbeat FOUR SECONDS earlier);
  * a REUSED pid read as live work — a genuinely stale lease is never reaped
    and starves the queue (three tasks pinned while the board reported
    ``idle [review_bound]`` with free capacity for an hour, 2026-08-20).

rem-hyg-15 fixed the dispatch window by adding the task's own HEARTBEAT as a
second, independent signal — and left the other readers of the same lease
(``cli.py --release``, the idle advisor's diagnosis, startup recovery) each
asking ``holder_is_alive`` on its own and reading a dead pid as dead work. This
module is the one place the question is answered, so a fourth reader cannot
form a fourth opinion.

FOUR STATES, and only one of them is reapable:

  free      no unexpired lease.
  live      the holder pid is alive — OR CANNOT BE TOLD. ``holder_is_alive``
            returns ``None`` when it has no psutil, no readable process table,
            or no pid on the lease; reaping on ignorance is how a live worker
            loses its lease, so unknown collapses into live, never into litter.
  working   the holder pid is GONE but the task is heartbeating. The worker
            outlived the process that took the lease. This is the state the
            pid-only readers could not see, and it is NOT reapable.
  litter    the holder pid is gone AND the task is not heartbeating: a one-shot
            claim whose process exited, or a worker that died. The only state
            in which :func:`reap_if_litter` will touch the lease.

The heartbeat is consulted ONLY when the pid is dead: it is what the lease's
pid cannot say, and reading it for a live holder would cost a board query per
task per cycle to learn nothing the verdict needs.

``tools.coordination.leases`` stays a pid-level primitive on purpose — the
``kanban:runner:pause`` lease has no task and nothing to heartbeat, so
``release_stale`` there is still the right call. This module is the TASK-aware
layer above it.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.kanban.lease_liveness")

#: How recently a task must have heartbeat to count as actively running. Well
#: above any plausible heartbeat interval (the scheduler stamps every cycle):
#: this value only ever makes a reap MORE conservative. Being slow to reclaim a
#: genuinely abandoned lease costs one stalled task; reclaiming a live one costs
#: duplicate work.
HEARTBEAT_LIVE_MINUTES = 30

STATE_FREE = "free"
STATE_LIVE = "live"
STATE_WORKING = "working"
STATE_LITTER = "litter"

#: Every state ``task_lease_verdict`` can return. ``litter`` is the only one a
#: reaper may act on; ``live`` and ``working`` both block dispatch.
STATES = (STATE_FREE, STATE_LIVE, STATE_WORKING, STATE_LITTER)


def task_lease_resource(task_id: str) -> str:
    """The one resource name every claim, dispatch and release agrees on."""
    return f"kanban:task:{task_id}"


@dataclass(frozen=True)
class LeaseVerdict:
    """Both signals and the state they reduce to — never the state alone.

    ``pid_alive`` is ``holder_is_alive``'s answer (``None`` = cannot tell).
    ``heartbeating`` is ``None`` when it was NOT CONSULTED (free lease, or a
    holder whose pid is alive/unknown), which a reader must never render as
    "not heartbeating".
    """

    task_id: str
    resource: str
    state: str
    holder: Optional[Dict[str, Any]]
    pid_alive: Optional[bool]
    heartbeating: Optional[bool]
    #: ``True`` when the holder's session id is a REGISTERED session that has
    #: heartbeat within the TTL -- the third signal, consulted only when the pid
    #: is dead. ``None`` when not consulted, which a reader must never render
    #: as "no session".
    session_alive: Optional[bool] = None

    @property
    def reapable(self) -> bool:
        """May a reaper release this lease? Only for litter — never on unknown."""
        return self.state == STATE_LITTER

    @property
    def blocks_dispatch(self) -> bool:
        """Does somebody own this task right now (or might they)?"""
        return self.state in (STATE_LIVE, STATE_WORKING)

    @property
    def holder_session(self) -> Optional[str]:
        return (self.holder or {}).get("holder_session")

    @property
    def holder_pid(self) -> Optional[int]:
        pid = (self.holder or {}).get("pid")
        return pid if isinstance(pid, int) else None


def task_is_heartbeating(task_id: str) -> bool:
    """Has this task heartbeat recently? The signal about the WORK, not the pid.

    A dispatching process exits once it has handed off, so the pid recorded on
    the lease dies long before the worker does. ``last_heartbeat_at`` is what
    the scheduler refreshes for every worker subprocess it can still poll, and
    it is therefore the only one of the two that answers "is anybody still
    building this".

    ``False`` for a task that never started (no row, or ``last_heartbeat_at``
    NULL) — that is the one-shot-claim case the dispatch reaper exists for.
    Fail-safe to ``True`` on any error: an unreadable heartbeat must not license
    a reap.
    """
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT last_heartbeat_at FROM kanban_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        if not row:
            return False                      # no such task — nothing to protect
        raw = dict(row).get("last_heartbeat_at")
        if not raw:
            return False                      # never started: safe to reap
        ts = raw if isinstance(raw, datetime) else datetime.fromisoformat(
            str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts > datetime.now(timezone.utc) - timedelta(minutes=HEARTBEAT_LIVE_MINUTES)
    except Exception as exc:  # noqa: BLE001
        logger.debug("heartbeat check failed for %s: %s", task_id, exc)
        return True                           # unknown -> assume alive
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def session_is_live(session_id: Optional[str]) -> bool:
    """Is ``session_id`` a REGISTERED session that heartbeat within the TTL?

    The third liveness signal, and the one a human's claim needs. A lease taken
    by ``cli.py --claim`` records the pid of a process that exits a second
    later, so on the pid alone it is litter to every reader: the dispatch
    reaper takes the task, and startup recovery resets it. Measured
    2026-09-02 21:28: kpr-stale-03, claimed by hand with its PR in flight, was
    reset to backlog with "no live session was found working it" -- while the
    claiming operator's session heartbeat in agent_sessions the whole time.
    The session id on the lease is the link the pid cannot provide.

    ``False`` on ANY error, deliberately. This signal can only ever make a
    verdict MORE conservative (LIVE where it would have been litter), so an
    unreadable registry must fall back to the two signals that already exist
    rather than invent a live session and pin a task forever.
    """
    if not session_id:
        return False
    try:
        import importlib

        registry = importlib.import_module("tools.coordination.session_registry")
        return any(
            str(s.get("session_id")) == str(session_id)
            for s in registry.list_active()
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("session liveness check failed for %s: %s", session_id, exc)
        return False


def task_lease_verdict(task_id: str) -> LeaseVerdict:
    """Classify ``kanban:task:<id>`` from BOTH signals.

    Raises whatever the lease store raises — each caller already decides what
    an unreadable lease store means for ITS path (the dispatch window costs the
    check, not the cycle; a CLI refuses). What this function guarantees is that
    no caller can reach ``litter`` without a dead pid AND a silent heartbeat.
    """
    from tools.coordination import leases  # resolved at call time, for the tests' fakes

    resource = task_lease_resource(task_id)
    holder = leases.holder(resource)
    if holder is None:
        return LeaseVerdict(task_id, resource, STATE_FREE, None, None, None)

    pid_alive = leases.holder_is_alive(resource)
    if pid_alive is not False:
        # True, or None ("cannot tell"). Unknown is ALIVE: reaping on it is how
        # a live worker loses its lease. The heartbeat is not consulted — it
        # could not change the answer, and it costs a board query.
        return LeaseVerdict(task_id, resource, STATE_LIVE, holder, pid_alive, None)

    # A dead pid with a LIVE REGISTERED SESSION behind it is a human's claim
    # (or any agent that set ICDEV_SESSION_ID before claiming). Consulted
    # before the heartbeat because a claimed-but-never-dispatched task has no
    # heartbeat to give -- exactly the case that used to read as litter.
    if session_is_live(holder.get("holder_session")):
        return LeaseVerdict(task_id, resource, STATE_LIVE, holder, False, None, True)

    beating = task_is_heartbeating(task_id)
    state = STATE_WORKING if beating else STATE_LITTER
    return LeaseVerdict(task_id, resource, state, holder, False, beating)


def reap_if_litter(task_id: str) -> Tuple[LeaseVerdict, bool]:
    """Release the lease ONLY when the verdict is litter. Returns (verdict, reaped).

    ``reaped`` is ``False`` for every other state AND for a litter lease the
    lease layer declined to release (``release_stale`` re-checks the pid under
    the file lock, so a holder that came alive between the two reads is still
    refused). A caller must read the verdict to tell those apart.
    """
    verdict = task_lease_verdict(task_id)
    if not verdict.reapable:
        return verdict, False
    from tools.coordination import leases

    reaped = bool(leases.release_stale(verdict.resource))
    if reaped:
        logger.info(
            "lease %s reaped: holder pid %s is gone and the task is not "
            "heartbeating", verdict.resource, verdict.holder_pid,
        )
    return verdict, reaped


def describe(verdict: LeaseVerdict) -> str:
    """One human line per state, for CLIs and diagnoses that must say WHY."""
    sid, pid = verdict.holder_session, verdict.holder_pid
    if verdict.state == STATE_FREE:
        return "not claimed"
    if verdict.state == STATE_WORKING:
        return (
            f"holder pid {pid} is gone but the task heartbeat within the last "
            f"{HEARTBEAT_LIVE_MINUTES} min — the worker outlived the process that "
            f"took the lease (session {sid}); this is live work, not litter"
        )
    if verdict.state == STATE_LITTER:
        return (
            f"holder pid {pid} is gone and the task is not heartbeating "
            f"(session {sid}) — litter"
        )
    if verdict.session_alive:
        return (
            f"holder pid {pid} is gone but session {sid} is registered and "
            f"heartbeating — a claim held by a live session, not litter"
        )
    if verdict.pid_alive is None:
        return (
            f"cannot tell whether holder pid {pid} (session {sid}) is alive — "
            "treated as LIVE; reaping on an unknown is how a live worker loses "
            "its lease"
        )
    return f"held by a LIVE session {sid} (pid {pid})"
