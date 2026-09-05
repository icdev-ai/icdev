# CUI // SP-CTI
"""A claim from a plain shell that HOLDS (mfx-own-02).

THE DEFECT, measured 2026-09-03. An operator ran ``cli.py --claim rmf-ui-13``
to hold the task while repairing its PR by hand. The CLI answered truthfully
that the lease it had just taken was bound to a pid that exits on the next line
and to an UNREGISTERED session id -- so every reader (the dispatch reaper,
startup recovery, ``--release``, ``restore_acts --plan``) would read it as
litter within seconds. A second session repaired the same branch concurrently
at 14:01 and the two resolutions had to be merged into each other. The lease
primitive existed (``kanban:task:<id>``, ``lease_liveness``); only a REGISTERED
service session could make it hold, and a shell is not one.

THE MECHANISM. ``claim()`` takes the lease synchronously under the calling
process's own session id -- the SAME ``leases.acquire`` refusal the seeder and
the runner already rely on, so "held by somebody else" is answered before
anything is spawned -- and then HANDS IT TO A KEEPER: a small detached process
that

  * adopts a dedicated interactive identity, ``cli-claim-<task>-t<hex>``,
    registered in ``agent_sessions`` with ``agent_type`` ``cli`` and the
    operator's stated intent (``--intent``, default "manual repair of <id>"),
  * re-takes the lease under that id so the lease's pid is ITS pid, alive for
    the life of the claim,
  * heartbeats the session every ``BEAT_SECONDS`` until the claim's TTL
    (default ``DEFAULT_TTL_SECONDS``, two hours; ``--claim`` again renews it),
    the state file is removed (``--release``), the lease is lost to a reaper,
    or the task reaches a terminal status.

Every reader then finds what it already knows how to honour: a live holder pid,
and behind it a registered session whose heartbeat is fresh --
``lease_liveness.task_lease_verdict`` reads ``live`` on either signal, the
dispatch window yields the slot, startup recovery keeps the row, and
``restore_acts`` refuses to reap "the holder process is running".

THE IDENTITY IS NEVER THE INHERITED ONE (claim-verif-33c9f4cd11). A shell
opened inside a kanban worker carries ``ICDEV_SESSION_ID=kanban-scheduler-<pid>``
and ``ICDEV_AGENT=kanban`` in its environment. Binding the claim to that id
would make a human's hold read as the scheduler's own, and the keeper's
heartbeat would refresh the SCHEDULER's registry row. The keeper is therefore
handed an id it did not inherit, its environment is rewritten before the
registry is touched (``CLAUDE_SESSION_ID`` dropped, ``ICDEV_SESSION_ID`` and
``ICDEV_AGENT`` set), and the token suffix is ``t<hex>`` -- never digits -- so
``service_identity.embedded_pid`` cannot mistake it for a ``<name>-<pid>``
service id and route the registry write to a ``/child-`` row.

WHAT THE HANDOVER IS. ``leases.handover`` re-binds a lease THIS session holds to
the keeper's id without a release-then-acquire window in which the runner could
take the task. It happens BEFORE the spawn: the id embeds a token rather than
the keeper's pid, so nothing about it waits on the child, and a handover that
finds no lease to re-bind (the caller never held one) spawns nothing at all.

STATE ON DISK, one file per task under ``.tmp/coordination/claims/``: the
keeper writes it once it holds the lease (the CLI's handshake waits for it),
re-reads it every beat (``expires_at`` is how a renewal reaches a running
keeper; its absence is how ``--release`` stops one), and removes it on exit.
The keeper's stdout/stderr go to ``<task>.log`` beside it.

A DEAD KEEPER IS OUR OWN LITTER and nobody else's. An interactive claim has no
worker heartbeating under it -- the keeper IS the liveness -- so a lease whose
holder is a ``cli-claim-<task>-*`` id with no living keeper pid is released by
the next ``--claim`` on that task rather than refused for the remainder of the
registry TTL. Cannot-tell (no readable process table) still refuses.

Nothing here changes the verdict a lease gets: ``lease_liveness`` is untouched
and a service's lease, a dispatched worker's lease and a one-shot seed's lease
are read exactly as before. This module only makes a shell's claim look like
what the verdict already honours.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402
from tools.coordination.constants import COORD_DIR  # noqa: E402
from tools.coordination.service_identity import AGENT_ENV, SESSION_ENV  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.kanban.interactive_claim")

#: Where the keeper runs from (its cwd and PYTHONPATH): the ONE root resolver,
#: never this file's own location -- the kernel packages move (xit-decl-03).
_ROOT = repo_root(__file__)

#: One state file + one log per claimed task.
CLAIM_DIR = COORD_DIR / "claims"

#: Every keeper session id starts with this; the task id and a token follow.
SESSION_PREFIX = "cli-claim-"

#: What the keeper registers as in ``agent_sessions.agent_type``.
AGENT_TYPE = "cli"

#: How long a claim holds before the keeper lets go, unless renewed.
DEFAULT_TTL_SECONDS = 2 * 60 * 60

#: How often the keeper heartbeats its session and refreshes the lease. Well
#: inside ``SESSION_TTL_SECONDS`` (900s) and ``HEARTBEAT_LIVE_MINUTES``.
BEAT_SECONDS = 60

#: How long ``claim()`` waits for the keeper to report that it holds the lease.
HANDSHAKE_TIMEOUT_SECONDS = 20.0

#: How long a keeper waits for the handover before giving up.
HANDOVER_WAIT_SECONDS = 15.0

#: A claim on a task in one of these is over; the keeper releases and exits.
#: The same set ``pr_linker`` stops linking on -- read from there, with a
#: literal fallback only so a broken import cannot leave a keeper immortal.
try:
    from tools.kanban.pr_linker import TERMINAL_STATUSES  # noqa: E402
except Exception:  # noqa: BLE001 -- pragma: no cover
    TERMINAL_STATUSES = frozenset({"done", "cancelled", "decomposed", "superseded"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
def task_lease_resource(task_id: str) -> str:
    """The one resource name every claim, dispatch and release agrees on."""
    return f"kanban:task:{task_id}"


def session_name(task_id: str) -> str:
    """The stable part of a keeper's id -- ``cli-claim-<task>``."""
    return f"{SESSION_PREFIX}{task_id}"


def mint_session_id(task_id: str) -> str:
    """A fresh keeper id. The suffix is ``t<hex>``: never all digits, so
    ``service_identity.embedded_pid`` returns None and the registry writes the
    row under this exact id rather than a ``/child-`` derivative."""
    return f"{session_name(task_id)}-t{secrets.token_hex(4)}"


def is_interactive_session(session_id: Optional[str]) -> bool:
    """Was this id minted by :func:`mint_session_id` (for any task)?"""
    return bool(session_id) and str(session_id).startswith(SESSION_PREFIX)


def claim_session_for(task_id: str, session_id: Optional[str]) -> bool:
    """Is ``session_id`` a keeper of THIS task?"""
    return bool(session_id) and str(session_id).startswith(f"{session_name(task_id)}-t")


def adopt_identity(session_id: str) -> str:
    """Make ``session_id`` THIS process's identity, whatever it inherited.

    ``hook_compat.get_session_id`` prefers ``CLAUDE_SESSION_ID`` and then
    ``ICDEV_SESSION_ID``; a keeper spawned from a worker shell inherits both
    from the scheduler. Both are overridden -- ``setdefault`` semantics, which
    ``claim_service_identity`` uses so an orchestrator's id wins, are exactly
    wrong here, because the inherited id IS the orchestrator's.
    """
    os.environ.pop("CLAUDE_SESSION_ID", None)
    os.environ[SESSION_ENV] = session_id
    os.environ[AGENT_ENV] = AGENT_TYPE
    return session_id


# --------------------------------------------------------------------------- #
# State file
# --------------------------------------------------------------------------- #
def state_path(task_id: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in task_id)
    return CLAIM_DIR / f"{safe}.json"


def log_path(task_id: str) -> Path:
    return state_path(task_id).with_suffix(".log")


def read_state(task_id: str) -> Optional[Dict[str, Any]]:
    try:
        p = state_path(task_id)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return None


def write_state(task_id: str, state: Dict[str, Any]) -> None:
    """Atomic: a reader never sees a half-written file."""
    p = state_path(task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, p)


def remove_state(task_id: str, session_id: Optional[str] = None) -> bool:
    """Drop the state file -- only if it names ``session_id`` when one is given."""
    try:
        p = state_path(task_id)
        if not p.exists():
            return False
        if session_id is not None:
            cur = read_state(task_id) or {}
            if cur.get("session_id") != session_id:
                return False
        p.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False


def pid_alive(pid: Any) -> Optional[bool]:
    """Is ``pid`` running? ``None`` when it cannot be told."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return None
    try:
        from tools.compat.platform_utils import pid_exists

        return bool(pid_exists(pid))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# The keeper
# --------------------------------------------------------------------------- #
class Keeper:
    """The process that makes a shell's claim hold. Drive it with
    :meth:`start` then :meth:`beat` until one returns an exit reason, then
    :meth:`finish` -- :func:`run_keeper` does exactly that; the tests drive the
    steps by hand so no thread and no sleep is needed to prove them."""

    def __init__(self, task_id: str, session_id: str, intent: str,
                 ttl_seconds: int, *, beat_seconds: int = BEAT_SECONDS,
                 handover_wait: float = HANDOVER_WAIT_SECONDS,
                 sleep: Callable[[float], None] = time.sleep):
        self.task_id = task_id
        self.session_id = session_id
        self.intent = intent
        self.ttl_seconds = int(ttl_seconds)
        self.beat_seconds = int(beat_seconds)
        self.handover_wait = float(handover_wait)
        self.sleep = sleep
        self.resource = task_lease_resource(task_id)
        self.expires_at: Optional[datetime] = None
        self.beats = 0

    # -- start -------------------------------------------------------------
    def _holds(self) -> bool:
        from tools.coordination import leases

        h = leases.holder(self.resource)
        return bool(h) and h.get("holder_session") == self.session_id

    def _fail(self, error: str) -> str:
        logger.warning("keeper %s: %s", self.task_id, error)
        write_state(self.task_id, {
            "task_id": self.task_id, "session_id": self.session_id,
            "pid": os.getpid(), "error": error, "written_at": _iso(_now()),
        })
        return "failed"

    def start(self) -> Optional[str]:
        """Adopt the identity, wait for the handover, register, re-take the
        lease under our own pid, publish the state file. ``None`` on success,
        else the exit reason (the state file then carries ``error``)."""
        from tools.coordination import leases, session_registry

        adopt_identity(self.session_id)
        deadline = time.monotonic() + self.handover_wait
        while not self._holds():
            if time.monotonic() >= deadline:
                return self._fail(
                    f"lease {self.resource} was never handed to session "
                    f"{self.session_id} within {self.handover_wait:.0f}s")
            self.sleep(0.2)

        reg = session_registry.register(intent=self.intent)
        if not reg.get("ok"):
            return self._fail(f"session registration failed: {reg.get('reason')}")
        if reg.get("session_id") != self.session_id:
            # The registry wrote a different row -- an inherited id was not
            # overridden. That row is somebody else's; refuse rather than
            # heartbeat it on their behalf.
            session_registry.end_session(session_id=reg.get("session_id"))
            return self._fail(
                f"registry wrote {reg.get('session_id')!r}, not {self.session_id!r}")

        started = _now()
        self.expires_at = started + timedelta(seconds=self.ttl_seconds)
        if leases.acquire(self.resource, intent=self.intent,
                          ttl_seconds=self._lease_ttl(), block=False) is None:
            session_registry.end_session()
            return self._fail("could not re-take the lease under the keeper's pid")
        write_state(self.task_id, {
            "task_id": self.task_id, "session_id": self.session_id,
            "pid": os.getpid(), "intent": self.intent,
            "ttl_seconds": self.ttl_seconds,
            "started_at": _iso(started), "expires_at": _iso(self.expires_at),
            "renewed_at": None, "beats": 0, "last_beat_at": None,
            "log": str(log_path(self.task_id)),
        })
        logger.info("keeper %s: holding as %s (pid %s) until %s", self.task_id,
                    self.session_id, os.getpid(), _iso(self.expires_at))
        return None

    def _lease_ttl(self) -> int:
        """The lease outlives the NEXT beat by a margin, never the claim by much:
        a keeper that dies leaves a lease its dead pid and stale session make
        litter, reaped by the next dispatch window."""
        remaining = 0
        if self.expires_at is not None:
            remaining = int((self.expires_at - _now()).total_seconds())
        return max(remaining, 0) + 2 * self.beat_seconds

    # -- one beat ----------------------------------------------------------
    def _task_status(self) -> Optional[str]:
        """The task's status, or ``None`` when the board cannot be read -- an
        unreadable board never reads as terminal."""
        conn = None
        try:
            conn = get_connection()
            row = conn.execute("SELECT status FROM kanban_tasks WHERE id = %s",
                               (self.task_id,)).fetchone()
            return str(dict(row).get("status")) if row else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("keeper %s: status unreadable: %s", self.task_id, exc)
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    def beat(self) -> Optional[str]:
        """One cycle. ``None`` to keep going, else the exit reason."""
        from tools.coordination import leases, session_registry

        self.beats += 1
        st = read_state(self.task_id)
        if not st or st.get("session_id") != self.session_id:
            return "released"
        exp = _parse(st.get("expires_at"))
        if exp is not None:
            self.expires_at = exp
        if self.expires_at is None or _now() >= self.expires_at:
            return "expired"
        if not self._holds():
            return "lease_lost"
        status = self._task_status()
        if status in TERMINAL_STATUSES:
            return f"task_{status}"
        intent = st.get("intent") or self.intent
        session_registry.heartbeat(intent=intent)
        leases.acquire(self.resource, intent=intent, ttl_seconds=self._lease_ttl(),
                       block=False)
        st["beats"] = self.beats
        st["last_beat_at"] = _iso(_now())
        write_state(self.task_id, st)
        return None

    # -- finish ------------------------------------------------------------
    def finish(self, reason: str) -> None:
        """Let go: the lease (if still ours), the state file, the session row."""
        from tools.coordination import leases, session_registry

        logger.info("keeper %s: stopping (%s) after %d beat(s)", self.task_id,
                    reason, self.beats)
        if reason != "lease_lost":
            try:
                leases.release(self.resource)
            except Exception:  # noqa: BLE001
                pass
        remove_state(self.task_id, self.session_id)
        try:
            session_registry.end_session()
        except Exception:  # noqa: BLE001
            pass


def run_keeper(task_id: str, session_id: str, intent: str, ttl_seconds: int, *,
               beat_seconds: int = BEAT_SECONDS, sleep: Callable[[float], None] = time.sleep,
               max_beats: Optional[int] = None) -> str:
    """The keeper's whole life. Returns the exit reason."""
    k = Keeper(task_id, session_id, intent, ttl_seconds, beat_seconds=beat_seconds,
               sleep=sleep)
    reason = k.start()
    if reason is not None:
        return reason
    while True:
        if max_beats is not None and k.beats >= max_beats:
            reason = "max_beats"
            break
        sleep(k.beat_seconds)
        reason = k.beat()
        if reason is not None:
            break
    k.finish(reason)
    return reason


# --------------------------------------------------------------------------- #
# Spawning
# --------------------------------------------------------------------------- #
def keeper_command(task_id: str, session_id: str, intent: str, ttl_seconds: int) -> list:
    return [sys.executable, "-m", "tools.kanban.interactive_claim", "--keep", task_id,
            "--session-id", session_id, "--ttl", str(int(ttl_seconds)),
            "--intent", intent]


def keeper_env(session_id: str) -> Dict[str, str]:
    """The child's environment: the inherited one, minus the inherited identity."""
    env = dict(os.environ)
    env.pop("CLAUDE_SESSION_ID", None)
    env[SESSION_ENV] = session_id
    env[AGENT_ENV] = AGENT_TYPE
    env["PYTHONPATH"] = str(_ROOT) + (os.pathsep + env["PYTHONPATH"]
                                      if env.get("PYTHONPATH") else "")
    return env


def spawn_keeper(task_id: str, session_id: str, intent: str, ttl_seconds: int) -> int:
    """Start the keeper detached from this shell; returns its pid.

    Detached, so it outlives the CLI invocation and the terminal that ran it:
    ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` on Windows, its own session
    on POSIX. Output goes to the task's log beside the state file.
    """
    CLAIM_DIR.mkdir(parents=True, exist_ok=True)
    log = open(log_path(task_id), "ab")  # noqa: SIM115 -- handed to the child
    try:
        kwargs: Dict[str, Any] = {
            "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
            "cwd": str(_ROOT), "env": keeper_env(session_id),
        }
        if os.name == "nt":
            kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(keeper_command(task_id, session_id, intent, ttl_seconds),  # noqa: S603
                                **kwargs)
        return int(proc.pid)
    finally:
        log.close()


# --------------------------------------------------------------------------- #
# The caller's side
# --------------------------------------------------------------------------- #
def default_intent(task_id: str) -> str:
    return f"manual repair of {task_id}"


def keep(task_id: str, *, intent: Optional[str] = None,
         ttl_seconds: Optional[int] = None, spawner: Optional[Callable[..., int]] = None,
         wait_seconds: float = HANDSHAKE_TIMEOUT_SECONDS,
         sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Hand a lease THIS session already holds to a keeper.

    Handover first, spawn second: a caller that does not hold the lease gets
    ``keeper: none`` and no process is started. Then the handshake -- the
    keeper's state file, or its death, or the timeout -- decides between
    ``running`` / ``failed`` / ``unconfirmed``. In every non-``running`` case
    the lease is left where it is (bound to the keeper id, which nobody
    heartbeats) and the reason is returned: the caller says it out loud.
    """
    from tools.coordination import leases

    intent = intent or default_intent(task_id)
    ttl = int(ttl_seconds or DEFAULT_TTL_SECONDS)
    res = task_lease_resource(task_id)
    out: Dict[str, Any] = {"task_id": task_id, "keeper": "none", "session_id": None,
                           "pid": None, "expires_at": None, "reason": None,
                           "log": str(log_path(task_id))}
    sid = mint_session_id(task_id)
    if not leases.handover(res, sid):
        out["reason"] = "this session does not hold the lease, so there is nothing to keep"
        return out
    out["session_id"] = sid
    # A stale state file from an earlier keeper of this task must not satisfy
    # the handshake below; it names a different session id, and is dropped.
    remove_state(task_id)
    try:
        pid = int((spawner or spawn_keeper)(task_id, sid, intent, ttl))
    except Exception as exc:  # noqa: BLE001 -- say it; do not hide a claim that will rot
        out.update(keeper="failed", reason=f"keeper did not start: {exc}")
        return out
    out["pid"] = pid
    deadline = time.monotonic() + float(wait_seconds)
    while True:
        st = read_state(task_id)
        if st and st.get("session_id") == sid:
            if st.get("error"):
                out.update(keeper="failed", reason=str(st["error"]))
            else:
                out.update(keeper="running", pid=st.get("pid", pid),
                           expires_at=st.get("expires_at"))
            return out
        if pid_alive(pid) is False:
            out.update(keeper="failed",
                       reason=f"keeper pid {pid} exited before reporting; see {out['log']}")
            return out
        if time.monotonic() >= deadline:
            out.update(keeper="unconfirmed",
                       reason=f"keeper pid {pid} did not report within {wait_seconds:.0f}s; "
                              f"see {out['log']}")
            return out
        sleep(0.25)


def _drop(task_id: str, session_id: str) -> Dict[str, Any]:
    """End an interactive claim by id: state file, lease, registry row."""
    from tools.coordination import leases, session_registry

    st = read_state(task_id) or {}
    pid = st.get("pid")
    remove_state(task_id)
    released = leases.release_all_for_session(session_id)
    try:
        session_registry.end_session(session_id=session_id)
    except Exception:  # noqa: BLE001
        pass
    return {"released": bool(released), "session_id": session_id, "keeper_pid": pid,
            "keeper_pid_alive": pid_alive(pid)}


def claim(task_id: str, *, intent: Optional[str] = None,
          ttl_seconds: Optional[int] = None, spawner: Optional[Callable[..., int]] = None,
          wait_seconds: float = HANDSHAKE_TIMEOUT_SECONDS,
          sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Take ``kanban:task:<id>`` for interactive work and make it hold.

    Returns ``claimed`` (the lease is ours -- with or without a keeper),
    ``renewed`` (a running keeper's TTL was extended instead), ``keeper``
    (``running`` | ``failed`` | ``unconfirmed`` | ``none``), ``session_id``,
    ``pid``, ``expires_at`` and ``reason``. A refusal is ``claimed: False``
    with ``held_by`` and the shared verdict's description of why.
    """
    from tools.coordination import leases
    from tools.kanban import lease_liveness

    intent = intent or default_intent(task_id)
    ttl = int(ttl_seconds or DEFAULT_TTL_SECONDS)
    res = task_lease_resource(task_id)
    out: Dict[str, Any] = {"task_id": task_id, "claimed": False, "renewed": False,
                           "keeper": "none", "session_id": None, "pid": None,
                           "expires_at": None, "reason": None, "held_by": None}

    holder = leases.holder(res)
    if holder and claim_session_for(task_id, holder.get("holder_session")):
        # A keeper of THIS task holds it. Alive: renew. Dead: our own litter.
        st = read_state(task_id) or {}
        pid = st.get("pid") if isinstance(st.get("pid"), int) else holder.get("pid")
        alive = pid_alive(pid)
        if alive:
            expires = _now() + timedelta(seconds=ttl)
            st.update({"expires_at": _iso(expires), "renewed_at": _iso(_now()),
                       "ttl_seconds": ttl, "intent": intent})
            st.setdefault("task_id", task_id)
            st.setdefault("session_id", holder.get("holder_session"))
            st.setdefault("pid", pid)
            write_state(task_id, st)
            out.update(claimed=True, renewed=True, keeper="running",
                       session_id=holder.get("holder_session"), pid=pid,
                       expires_at=_iso(expires))
            return out
        if alive is None:
            out.update(reason=f"cannot tell whether keeper pid {pid} of session "
                              f"{holder.get('holder_session')} is alive; refusing to "
                              "replace it", held_by=holder.get("holder_session"))
            return out
        logger.info("claim %s: keeper session %s (pid %s) is dead -- reclaiming our "
                    "own litter", task_id, holder.get("holder_session"), pid)
        _drop(task_id, holder.get("holder_session"))
        holder = leases.holder(res)

    if holder is not None:
        verdict, _reaped = lease_liveness.reap_if_litter(task_id)
        if verdict.blocks_dispatch:
            out.update(reason=lease_liveness.describe(verdict),
                       held_by=verdict.holder_session)
            return out

    lease = leases.acquire(res, intent=intent, ttl_seconds=ttl, block=False)
    if lease is None:
        h = leases.holder(res) or {}
        out.update(reason=f"held by session {h.get('holder_session')}",
                   held_by=h.get("holder_session"))
        return out
    out["claimed"] = True
    kept = keep(task_id, intent=intent, ttl_seconds=ttl, spawner=spawner,
                wait_seconds=wait_seconds, sleep=sleep)
    out.update(keeper=kept["keeper"], session_id=kept["session_id"], pid=kept["pid"],
               expires_at=kept["expires_at"], reason=kept["reason"], log=kept["log"])
    return out


def release(task_id: str) -> Dict[str, Any]:
    """End the interactive claim on ``task_id``, if that is what holds it.

    ``interactive: False`` when the holder is not a keeper of this task --
    the caller climbs the ordinary ladder then. Anybody may end an
    interactive claim: the shell that took it has no identity of its own to
    match, which is the very reason the keeper exists.
    """
    from tools.coordination import leases

    res = task_lease_resource(task_id)
    holder = leases.holder(res) or {}
    st = read_state(task_id) or {}
    sid = holder.get("holder_session") or st.get("session_id")
    if not claim_session_for(task_id, sid):
        return {"released": False, "interactive": False, "session_id": None}
    if not holder:
        # No lease, but a state file naming a keeper: stop the keeper anyway.
        dropped = _drop(task_id, sid)
        dropped.update(interactive=True, released=False, lease_was_free=True)
        return dropped
    dropped = _drop(task_id, sid)
    dropped["interactive"] = True
    return dropped


def status(task_id: str) -> Dict[str, Any]:
    """What holds ``task_id`` right now, from the caller's seat."""
    from tools.coordination import leases

    holder = leases.holder(task_lease_resource(task_id)) or {}
    st = read_state(task_id) or {}
    sid = holder.get("holder_session")
    pid = st.get("pid") if isinstance(st.get("pid"), int) else None
    return {
        "task_id": task_id,
        "holder_session": sid,
        "interactive": claim_session_for(task_id, sid),
        "keeper_pid": pid,
        "keeper_pid_alive": pid_alive(pid) if pid is not None else None,
        "expires_at": st.get("expires_at"),
        "intent": st.get("intent"),
        "error": st.get("error"),
        "log": str(log_path(task_id)),
    }


# --------------------------------------------------------------------------- #
# CLI: the keeper entry point (spawned), plus a status view for humans
# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Keeper for an interactive kanban claim (spawned by "
                    "`kanban/cli.py --claim`; see --status for what holds a task)")
    ap.add_argument("--keep", metavar="TASK_ID", help="run as the keeper for this task")
    ap.add_argument("--session-id", metavar="ID", help="the keeper id handed the lease")
    ap.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS, metavar="SECONDS")
    ap.add_argument("--intent", default=None, metavar="TEXT")
    ap.add_argument("--beat", type=int, default=BEAT_SECONDS, metavar="SECONDS")
    ap.add_argument("--status", metavar="TASK_ID", help="what holds this task")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.status:
        s = status(args.status)
        print(json.dumps(s, indent=2, default=str) if args.json else
              "\n".join(f"  {k}: {v}" for k, v in s.items()))
        return 0
    if not args.keep:
        ap.error("--keep TASK_ID --session-id ID, or --status TASK_ID")
    if not args.session_id or not claim_session_for(args.keep, args.session_id):
        ap.error("--session-id must be a cli-claim id minted for --keep's task")
    reason = run_keeper(args.keep, args.session_id, args.intent or default_intent(args.keep),
                        args.ttl, beat_seconds=args.beat)
    print(f"keeper {args.keep}: exit ({reason})")
    return 0 if reason not in ("failed",) else 1


if __name__ == "__main__":
    sys.exit(main())
