# CUI // SP-CTI
"""Agent session registry — mutual awareness across concurrent sessions.

Each agent session (Claude Code, Cursor, …) registers a row and heartbeats so
other sessions can SEE who is active and what they're doing. Liveness is by
heartbeat freshness (a session's OS pid is unreliable — Claude runs each turn in
a fresh process), with pid kept for display only.

The `agent_sessions` table is self-creating (CREATE TABLE IF NOT EXISTS) so this
module needs no migration and won't collide with init_icdev_db.py.

    from tools.coordination import session_registry as reg
    reg.register(intent="implementing chyg sweep")
    reg.heartbeat()
    for s in reg.others():        # active sessions other than me
        print(s["session_id"], s["agent_type"], s["current_intent"])
"""
from __future__ import annotations

import os
import socket
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from tools.coordination.constants import (
    SESSION_TTL_SECONDS,
    get_agent_type,
    get_session_id,
)

try:
    from tools.db.storage import get_connection
except Exception:  # pragma: no cover
    get_connection = None  # type: ignore[assignment]

_DDL = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id     TEXT PRIMARY KEY,
    agent_type     TEXT,
    pid            INTEGER,
    host           TEXT,
    cwd            TEXT,
    started_at     TEXT,
    last_heartbeat TEXT,
    current_intent TEXT,
    status         TEXT DEFAULT 'active',
    -- WHICH CODE this process is running (autonomy-id-01). Nullable on
    -- purpose: a process that cannot determine its version must still be able
    -- to register, reporting unknown, rather than fail to register at all.
    -- These four are kept in step with migration
    -- 20260821024132_agent_sessions_code_identity, which adds them to a table
    -- that already exists. Both are needed: CREATE TABLE IF NOT EXISTS never
    -- alters a live table, so the DDL alone leaves every existing deployment
    -- without the columns, and the migration alone leaves a fresh database
    -- without them until it runs.
    module              TEXT,
    code_version        TEXT,
    code_version_source TEXT,
    code_dirty          INTEGER
)
"""

#: Written by :func:`register` from :func:`code_identity.boot_identity`. Named
#: once so the INSERT, the DDL and the migration cannot drift apart silently.
_IDENTITY_COLUMNS = ("module", "code_version", "code_version_source", "code_dirty")

_table_ready = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    conn = get_connection()
    try:
        conn.set_security_context(None)  # rls-bypass: infra registry, no tenant context
    except Exception:
        pass
    return conn


def _ensure_table(conn) -> None:
    global _table_ready
    if _table_ready:
        return
    try:
        conn.execute(_DDL)
        conn.commit()
        _table_ready = True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _live_columns(conn) -> set:
    """Column names `agent_sessions` ACTUALLY has, read from the catalogue.

    Not from :data:`_DDL`. ``CREATE TABLE IF NOT EXISTS`` never alters a table
    that already exists, so on every deployment that registered a session before
    autonomy-id-01 the identity columns are absent until migration
    20260821024132 runs. Naming them in the INSERT regardless would raise, and
    :func:`register` swallows its exceptions — so the session would silently stop
    registering at all, trading a missing code version for a missing process.
    """
    try:
        if getattr(conn, "_backend", "") == "postgresql":
            rows = conn.execute(
                "SELECT column_name AS c FROM information_schema.columns "
                "WHERE table_name = 'agent_sessions'"
            ).fetchall()
        else:
            rows = conn.execute("PRAGMA table_info(agent_sessions)").fetchall()
    except Exception:  # noqa: BLE001 — an unreadable catalogue means "assume none"
        return set()
    out = set()
    for row in rows or []:
        d = dict(row)
        name = d.get("c") or d.get("column_name") or d.get("name")
        if name:
            out.add(str(name))
    return out


def _own_session_id() -> str:
    """The id THIS process writes under.

    `get_session_id()` reads the environment, and a service's `<name>-<pid>` id
    is inherited by everything it spawns (claim-verif-33c9f4cd11): a kanban
    worker, and every command run inside it, carried `kanban-scheduler-22508`
    and could rewrite the scheduler's own row. A descendant writes under
    `child_session_id` instead; the parent's row is only ever written by the
    process that claimed the name. Best-effort: if the helper is unavailable
    the raw id is used, which is the pre-existing behaviour.
    """
    sid = get_session_id()
    try:
        from tools.coordination.service_identity import (
            child_session_id,
            is_inherited_identity,
        )
        if is_inherited_identity(sid):
            return child_session_id(sid)
    except Exception:  # noqa: BLE001 -- observability must never fail to name itself
        pass
    return sid


def register(intent: Optional[str] = None) -> Dict[str, Any]:
    """Register (or refresh) the current session. Idempotent upsert.

    Also records WHICH CODE this process is running (autonomy-id-01), so the
    fleet can be asked whether the code doing the work is the code that was
    merged. The identity is frozen at first read — see
    :mod:`tools.coordination.code_identity`.
    """
    if get_connection is None:
        return {"ok": False, "reason": "no db"}
    sid = _own_session_id()
    now = _now()
    conn = _conn()
    _ensure_table(conn)
    try:
        existing = conn.execute(
            "SELECT started_at FROM agent_sessions WHERE session_id = %s", (sid,)
        ).fetchone()
        started = (dict(existing).get("started_at") if existing else None) or now

        cols = ["session_id", "agent_type", "pid", "host", "cwd", "started_at",
                "last_heartbeat", "current_intent"]
        vals = [sid, get_agent_type(), os.getpid(), socket.gethostname(),
                os.getcwd(), started, now, intent]

        # Identity is best-effort and must never stop a session registering:
        # a process that cannot name its code still needs to be visible as a
        # process. Absent columns and an unreadable git both end as unknown.
        if set(_IDENTITY_COLUMNS).issubset(_live_columns(conn)):
            try:
                from tools.coordination.code_identity import boot_identity
                ident = boot_identity()
            except Exception:  # noqa: BLE001
                ident = {}
            cols.extend(_IDENTITY_COLUMNS)
            vals.extend(ident.get(c) for c in _IDENTITY_COLUMNS)

        placeholders = ", ".join(["%s"] * len(cols))
        # portable upsert: delete-then-insert keeps it backend-agnostic
        conn.execute("DELETE FROM agent_sessions WHERE session_id = %s", (sid,))
        conn.execute(
            f"INSERT INTO agent_sessions ({', '.join(cols)}, status) "  # nosec B608
            f"VALUES ({placeholders}, 'active')",
            tuple(vals),
        )
        conn.commit()
        _reap_on_register()
        return {"ok": True, "session_id": sid}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "reason": str(exc)[:160]}
    finally:
        conn.close()


def _reap_on_register() -> int:
    """Drop rows whose heartbeat aged past the TTL. Never raises.

    THE DEFECT THIS CLOSES. `reap_stale()` has existed since this module was written and
    was called by NOBODY -- not a reflex, not the supervisor, not a scheduler. Rows for
    dead processes therefore accumulated forever. `list_active()` filters by TTL so they
    stopped being DISPLAYED, which is precisely why nobody noticed the table growing.

    Measured on the live board 2026-08-28: 9 rows carried `status='active'`, 4 of them
    past the 900s TTL, for processes whose pids were provably dead. During the window
    where those rows were still INSIDE the TTL they were shown to every session as live
    peers -- reported as duplicate schedulers, pr_watchers and daemons that did not exist.
    A reader acting on that would have gone looking for processes to stop.

    REGISTER is the right hook and heartbeat is not. Registration happens once per process
    start -- rare, already doing a write, and exactly the moment a NEW process arrives to
    replace ones that died. Reaping on every heartbeat would put a scan and a DELETE on a
    path that runs every cycle of every daemon, to clean up something that changes only
    when a process starts or stops.

    Best-effort by construction: a failed reap must never stop a session registering. The
    cost of a lost reap is a stale row that the next registration clears; the cost of a
    raised exception here is a process that cannot announce itself at all.
    """
    try:
        return reap_stale()
    except Exception:  # noqa: BLE001 -- see the docstring
        return 0


def heartbeat(intent: Optional[str] = None) -> bool:
    """Refresh last_heartbeat (+ intent if given). Registers if missing."""
    if get_connection is None:
        return False
    sid = _own_session_id()
    conn = _conn()
    _ensure_table(conn)
    try:
        if intent is not None:
            n = conn.execute(
                "UPDATE agent_sessions SET last_heartbeat = %s, current_intent = %s, status='active' "
                "WHERE session_id = %s", (_now(), intent, sid),
            )
        else:
            n = conn.execute(
                "UPDATE agent_sessions SET last_heartbeat = %s, status='active' WHERE session_id = %s",
                (_now(), sid),
            )
        conn.commit()
        rowcount = getattr(n, "rowcount", 1)
        if not rowcount:
            conn.close()
            register(intent)
            return True
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_active(ttl_seconds: int = SESSION_TTL_SECONDS) -> List[Dict[str, Any]]:
    """Sessions whose heartbeat is within ttl_seconds and status='active'."""
    if get_connection is None:
        return []
    conn = _conn()
    _ensure_table(conn)
    try:
        rows = conn.execute(
            "SELECT * FROM agent_sessions WHERE status = 'active' ORDER BY last_heartbeat DESC"
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    out = []
    for r in rows:
        d = dict(r)
        hb = _parse(d.get("last_heartbeat"))
        if hb is not None and hb >= cutoff:
            out.append(d)
    return out


def others(ttl_seconds: int = SESSION_TTL_SECONDS) -> List[Dict[str, Any]]:
    """Active sessions other than the current one."""
    sid = _own_session_id()
    return [s for s in list_active(ttl_seconds) if s.get("session_id") != sid]


def end_session(session_id: Optional[str] = None) -> bool:
    """Mark a session ended -- the current one (the Stop hook), or ``session_id``.

    The explicit form exists for an interactive claim's keeper (mfx-own-02):
    ``cli.py --release`` runs in a shell with no identity of its own and must
    end the KEEPER's row, not the shell's.
    """
    if get_connection is None:
        return False
    conn = _conn()
    _ensure_table(conn)
    try:
        conn.execute(
            "UPDATE agent_sessions SET status='ended', last_heartbeat=%s WHERE session_id = %s",
            (_now(), session_id or _own_session_id()),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def reap_stale(ttl_seconds: int = SESSION_TTL_SECONDS) -> int:
    """Delete sessions whose heartbeat is older than ttl_seconds. Returns count."""
    if get_connection is None:
        return 0
    conn = _conn()
    _ensure_table(conn)
    try:
        rows = conn.execute("SELECT session_id, last_heartbeat FROM agent_sessions").fetchall()
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
        dead = []
        for r in rows:
            d = dict(r)
            hb = _parse(d.get("last_heartbeat"))
            if hb is None or hb < cutoff:
                dead.append(d["session_id"])
        for sid in dead:
            conn.execute("DELETE FROM agent_sessions WHERE session_id = %s", (sid,))
        conn.commit()
        return len(dead)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        conn.close()
