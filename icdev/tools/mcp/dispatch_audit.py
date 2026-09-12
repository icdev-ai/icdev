#!/usr/bin/env python3
# CUI // SP-CTI
"""Server-side MCP dispatch audit -- one row per ``tools/call``, whoever called.

WHY THIS EXISTS (measured 2026-09-12). ``studio_mcp_dispatch_audit`` had
exactly TWO writers, both Studio-internal
(``tools/studio/executors/mcp_executor.py`` and ``agent_tool_gate.py``), and
NOTHING under ``tools/mcp/`` -- the servers Claude Code actually talks to --
wrote a row. So ``capability_consumption --class mcp_dispatch_tool`` read 472
declared / 4 consumed / **468 inert** over the table's whole lifetime while a
Claude Code session used a dozen ``mcp__icdev-unified__*`` tools the same
afternoon. A tool used daily read ``inert``: a measurement of ONE CALLER
wearing the name of all callers. ``tools/cost/waste_survey.py`` (xrv-cost-03)
measured the same disagreement from the transcripts and deliberately stopped
short of fixing it -- "an MCP-server-side audit write is a new writer to an
append-only table and is its own card". This is that card, and it closes the
hole AT THE WRITER.

ONE CALL SITE, NEVER PER SERVER. Every MCP server in this tree subclasses
``tools/mcp/base_server.py::MCPServer`` and every ``tools/call`` funnels
through its ``_handle_tools_call``. So the audit is attached THERE and nowhere
else -- fifteen per-server hooks is fifteen chances for one to be forgotten,
which is the shape of the defect above, one layer up. An AST test pins the
call site.

NO SECOND ``INSERT``. The row is written by the EXISTING writer,
``mcp_executor.record_dispatch_audit``, imported. The table is append-only
(NIST 800-53 AU, registered in ``APPEND_ONLY_TABLES`` in
``.claude/hooks/pre_tool_use.py``), and a second INSERT site is the raw-writer
shape this repo censuses. An AST test refuses an
``INSERT INTO studio_mcp_dispatch_audit`` literal anywhere under ``tools/mcp/``.

ARGUMENTS ARE NEVER STORED. ``params_sha256`` is
``mcp_executor.params_digest`` of the arguments -- the same canonicalisation
the Studio side uses, so a digest means the same thing whichever caller
produced it. MCP tool arguments routinely carry CUI, credentials and file
paths; the audit question is "which tool, as whom, decided how", and a digest
answers it without widening the blast radius of the audit store.

OFF THE RESPONSE PATH, AND BOUNDED. The write is enqueued onto a bounded
``queue.Queue`` drained by ONE daemon thread. MEASURED against the live
PostgreSQL board, this host, 2026-09-12:

    enqueue (what a tool result pays)   p50 0.0034 ms  p90 0.0043  max 0.91
    synchronous write, PG reachable     p50 1.93 ms    p90 2.70    max 18.25
    synchronous write, PG UNREACHABLE   20,038 ms  (ONE call)

THE p50 IS UNDER THE 5 ms CEILING AND ASYNC WAS KEPT ANYWAY, for the third
row. A synchronous audit on a stdio transport hands its FAILURE MODE to the
tool result: an unreachable database blocks the connect for twenty seconds,
once per call, and the client waits. That is the audit becoming load-bearing,
which is precisely what a best-effort observability writer must never be --
and no threshold on the healthy case can see it. The tail is the lesser
argument: 18.25 ms is already over the ceiling on a warm local board, and
this host runs four CI runners.

The queue is bounded at :data:`MAX_QUEUED` and an overflow is COUNTED
(``stats()['dropped']``), never silent: a dropped row must not read as a call
that never happened.

BEST-EFFORT, NEVER LOAD-BEARING. Nothing here can change a tool result or
raise into the caller -- :func:`record_tool_call` catches everything, and so
does the worker. An unreachable database costs a warning and a
``stats()['failed']`` increment, and the tool still answers. That is
deliberate: this is an observability writer, and an audit store that can
refuse a dispatch is a GATE, which is ``tool_registry.tool_authorization``'s
job and not this module's. Authorization is explicitly out of scope here and
is unchanged.

SWITCHED OFF, AND IT SAYS SO. ``ICDEV_MCP_DISPATCH_AUDIT=0`` stands it down
and ``initialize`` reports ``enabled: false`` with the reason, so a client can
tell "switched off" from "on and recording nothing" -- never a silent no-op.

Usage (library; there is no CLI)::

    from icdev.tools.mcp.dispatch_audit import record_tool_call, audit_status
    record_tool_call("icdev-unified", "kanban_list_tasks", {"status": "done"},
                     decision="allowed", reason="dispatched")
"""

from __future__ import annotations

import atexit
import os
import queue
import threading
from typing import Any, Dict, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("mcp.dispatch_audit")

#: Env switch. ``0`` stands the writer down; anything else (including unset)
#: leaves it on.
ENV_TOGGLE = "ICDEV_MCP_DISPATCH_AUDIT"

#: Prefix for ``caller_source``. That column exists precisely so two callers
#: of one table stay distinguishable; a server-side row says which SERVER, and
#: ``capability_consumption``'s ``extra.by_caller_source`` reports the Studio
#: and server figures side by side rather than as one number.
CALLER_SOURCE_PREFIX = "mcp_server:"

#: The two decisions a server-side dispatch can reach. Spelled here so
#: ``base_server`` need not import the Studio executor on its response path;
#: ``tests/mcp/test_dispatch_audit_on_server.py`` asserts these are the SAME
#: strings as ``mcp_executor.DECISION_ALLOWED`` / ``DECISION_REFUSED`` /
#: ``REASON_DISPATCHED``, so the vocabulary cannot drift into a value the
#: table's ``decision`` CHECK refuses. ``pending_approval`` is deliberately
#: absent: a stdio server runs no human gate.
DECISION_ALLOWED = "allowed"
DECISION_REFUSED = "refused"

#: Reason recorded for a call whose handler returned.
REASON_DISPATCHED = "dispatched"

#: Reason recorded for a ``tools/call`` naming a tool the server does not have.
REASON_UNKNOWN_TOOL = "mcp_tool_unknown"

#: Queue ceiling. A stdio server dispatches one tool at a time, so this is
#: reached only when the database is slow or gone -- at which point dropping
#: is the right answer and COUNTING the drop is the requirement.
MAX_QUEUED = 1000

#: Seconds the atexit drain will wait. A stdio server exits when its client
#: does; an unbounded block there would hang the client's shutdown.
EXIT_FLUSH_SECONDS = 3.0

_QUEUE: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=MAX_QUEUED)
_WORKER: Optional[threading.Thread] = None
_WORKER_LOCK = threading.Lock()

_STATS_LOCK = threading.Lock()
_STATS: Dict[str, Any] = {
    "enqueued": 0,
    "written": 0,
    "failed": 0,
    "dropped": 0,
    "last_error": "",
}


def audit_enabled() -> bool:
    """True unless ``ICDEV_MCP_DISPATCH_AUDIT`` is exactly ``0``."""
    return (os.environ.get(ENV_TOGGLE) or "").strip() != "0"


def audit_status() -> Dict[str, Any]:
    """What ``initialize`` reports. Never a bare boolean without its reason."""
    enabled = audit_enabled()
    return {
        "enabled": enabled,
        "table": "studio_mcp_dispatch_audit",
        "reason": (
            "server-side dispatch audit active"
            if enabled
            else f"disabled by {ENV_TOGGLE}=0"
        ),
    }


def stats() -> Dict[str, Any]:
    """A snapshot of what this process has enqueued, written, failed, dropped."""
    with _STATS_LOCK:
        return dict(_STATS)


def reset_stats() -> None:
    """Zero the counters. For tests; never called on a serving path."""
    with _STATS_LOCK:
        _STATS.update(
            {"enqueued": 0, "written": 0, "failed": 0, "dropped": 0,
             "last_error": ""}
        )


def caller_context(server_name: str) -> Dict[str, Any]:
    """The caller fields a stdio server can HONESTLY supply.

    A stdio transport has NO authenticated principal (``base_server``'s own
    docstring says so, exa-policy-05), so nothing here is an authentication --
    it is the session the server was launched inside, which is exactly what
    ``ICDEV_SESSION_ID`` and ``ICDEV_AGENT`` record. An absent variable yields
    ``""`` rather than a guess: an invented principal on an append-only audit
    row is worse than an empty one.
    """
    return {
        "principal_id": (os.environ.get("ICDEV_SESSION_ID") or "").strip(),
        "tenant_id": (os.environ.get("ICDEV_TENANT_ID") or "").strip(),
        "impact_level": (os.environ.get("ICDEV_CALLER_IL") or "").strip(),
        "roles": tuple(
            role for role in ((os.environ.get("ICDEV_AGENT") or "").strip(),)
            if role
        ),
        "source": f"{CALLER_SOURCE_PREFIX}{server_name}",
    }


def _bump(field: str, error: str = "") -> None:
    with _STATS_LOCK:
        _STATS[field] = int(_STATS.get(field, 0)) + 1
        if error:
            _STATS["last_error"] = error


def _write(item: dict) -> None:
    """Hand ONE queued item to the existing writer. Never raises."""
    try:
        from tools.studio.executors.mcp_executor import (  # noqa: PLC0415
            record_dispatch_audit,
        )

        written, why_not = record_dispatch_audit(
            item["tool"],
            item["arguments"],
            item["decision"],
            item["reason"],
            caller=item["caller"],
            detail=item["detail"],
        )
    except Exception as exc:  # noqa: BLE001 -- see module docstring
        _bump("failed", f"{type(exc).__name__}: {exc}")
        logger.warning("MCP dispatch audit failed for %s: %s", item["tool"], exc)
        return
    if written:
        _bump("written")
    else:
        _bump("failed", why_not)
        logger.warning(
            "MCP dispatch audit not written for %s: %s", item["tool"], why_not
        )


def _run() -> None:
    while True:
        item = _QUEUE.get()
        try:
            if item is None:
                return
            _write(item)
        finally:
            _QUEUE.task_done()


def _ensure_worker() -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(
            target=_run, name="mcp-dispatch-audit", daemon=True
        )
        _WORKER.start()


def flush(timeout: float = 5.0) -> bool:
    """Block until the queue drains or ``timeout`` elapses. Returns "drained?".

    Tests and :func:`_flush_at_exit` use it. A SERVING path never does -- the
    whole point is that the response does not wait for a database.
    """
    drained = threading.Event()
    waiter = threading.Thread(
        target=lambda: (_QUEUE.join(), drained.set()), daemon=True
    )
    waiter.start()
    return drained.wait(timeout)


def _flush_at_exit() -> None:
    # A stdio server exits when its client does; rows still queued at that
    # moment are real dispatches and are worth a BOUNDED wait, never an
    # unbounded one.
    if _QUEUE.unfinished_tasks:
        flush(EXIT_FLUSH_SECONDS)


atexit.register(_flush_at_exit)


def record_tool_call(
    server_name: str,
    tool: str,
    arguments: Any,
    *,
    decision: str,
    reason: str,
    detail: str = "",
) -> bool:
    """Enqueue ONE audit row for a ``tools/call``. Returns "was it enqueued".

    NEVER RAISES and never touches the tool result. The return value is for
    tests and for :func:`stats`; a serving caller ignores it, because there is
    no outcome here that should change what the client is told.
    """
    if not audit_enabled():
        return False
    try:
        item = {
            "tool": str(tool),
            "arguments": arguments,
            "decision": decision,
            "reason": reason,
            "detail": str(detail or "")[:2000],
            "caller": caller_context(server_name),
        }
        _ensure_worker()
        _QUEUE.put_nowait(item)
    except queue.Full:
        _bump("dropped")
        logger.warning(
            "MCP dispatch audit queue full (%d); dropped a row for %s",
            MAX_QUEUED,
            tool,
        )
        return False
    except Exception as exc:  # noqa: BLE001 -- see docstring
        _bump("failed", f"{type(exc).__name__}: {exc}")
        logger.warning("MCP dispatch audit could not enqueue %s: %s", tool, exc)
        return False
    _bump("enqueued")
    return True
