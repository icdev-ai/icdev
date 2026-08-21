# CUI // SP-CTI
"""A long-running service's session id, DISTINCT PER PROCESS (autonomy-sid-01).

THE DEFECT, observed live 2026-08-20. Two `kanban_scheduler` processes were
dispatching onto the same board — the supervisor's, from the main checkout, and
one running out of `.tmp/worktrees/task-e2e-be1bfa2f`, which had its own `.env`
and so was on the REAL board.

Each did::

    os.environ.setdefault("ICDEV_SESSION_ID", "kanban-scheduler")

so both presented THE SAME session id. `leases.acquire` refuses a hard lease
held by ANOTHER live session::

    if hard and prior and prior.get("holder_session") != sid:
        return None                      # held by someone else

and to it, those two processes were one session. `kanban:task:<id>` IS a hard
namespace (`RES_KANBAN` is in `HARD_NAMESPACES`), so the refusal was armed and
correct — it simply could not see two claimants. The guard whose entire job is
stopping two workers from building the same task could not tell them apart.

Corroborating, from the same afternoon: `session_registry.list_active()` showed
ONE row for `kanban-scheduler`, carrying the WORKTREE pid, because the two
upsert over each other. The fleet view could not see there were two either.

This is a plausible contributor to the 11.6% duplicate-dispatch rate
autonomy-adm-01 measured, and it is NOT what autonomy-adm-03 fixed. That card
made liveness require a heartbeat rather than a bare pid — "is the holder
ALIVE". This is a different axis: "is the holder ME".

WHY THE PID, and not a fresh uuid per boot. `code_reload.restart_if_code_changed`
re-execs these daemons through `os.execv`, which REPLACES the process image and
KEEPS THE SAME PID. So a pid-based id survives a self-update, and a daemon that
updates itself still owns the leases it held a moment earlier. A uuid minted at
boot would orphan them on every code change — turning a working self-update into
a source of stale leases, which is the failure autonomy-adm-03 has just been
through.

WHY THE NAME STAYS A PREFIX. The session id is read by humans in
`list_active()`, by `merge_stall`'s attribution, and by the coordination hook
that prints other sessions. An opaque uuid is distinct and unreadable; a bare
name is readable and not distinct. `kanban-scheduler-12345` is both.

PID REUSE is real but already guarded, and deliberately not re-guarded here: a
lease carries a TTL, `holder_is_alive` checks the recorded pid, and
autonomy-adm-03 requires a heartbeat as a second, independent signal before
anything is treated as dead. Adding a boot nonce to defeat pid reuse would cost
the re-exec property above, which is the more common event by orders of
magnitude.

AN EXPLICIT ID ALWAYS WINS. If an orchestrator has already set
`ICDEV_SESSION_ID`, that is a deliberate act by whatever launched this process
and is never overridden — the same `setdefault` semantics these call sites
always had.
"""

from __future__ import annotations

import os
from typing import Optional

#: The env vars the resolver reads, in its own priority order. Set here so a
#: caller cannot drift from `hook_compat.get_session_id`.
SESSION_ENV = "ICDEV_SESSION_ID"
AGENT_ENV = "ICDEV_AGENT"


def service_session_id(name: str, pid: Optional[int] = None) -> str:
    """``<name>-<pid>`` — distinct per process, and still recognisable.

    `name` is the service's stable name (``kanban-scheduler``), kept as a prefix
    so every surface that reads a session id stays readable.
    """
    return f"{name}-{pid if pid is not None else os.getpid()}"


def claim_service_identity(name: str, agent: str,
                           pid: Optional[int] = None) -> str:
    """Give THIS process a per-process session id, and return it.

    Uses `setdefault` semantics: an id already set by an orchestrator wins, so
    this can be called unconditionally at service startup.
    """
    os.environ.setdefault(SESSION_ENV, service_session_id(name, pid))
    os.environ.setdefault(AGENT_ENV, agent)
    return os.environ[SESSION_ENV]


def is_service_session(session_id: str, name: str) -> bool:
    """Is `session_id` one of `name`'s processes?

    Matches the bare name too, so a row written before this change — or by a
    deployment still running the old code — is still recognised as that service
    rather than reading as an unknown session.
    """
    if not session_id:
        return False
    return session_id == name or session_id.startswith(f"{name}-")
