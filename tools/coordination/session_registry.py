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
    status         TEXT DEFAULT 'active'
)
"""

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


def register(intent: Optional[str] = None) -> Dict[str, Any]:
    """Register (or refresh) the current session. Idempotent upsert."""
    if get_connection is None:
        return {"ok": False, "reason": "no db"}
    sid = get_session_id()
    now = _now()
    conn = _conn()
    _ensure_table(conn)
    try:
        existing = conn.execute(
            "SELECT started_at FROM agent_sessions WHERE session_id = %s", (sid,)
        ).fetchone()
        started = (dict(existing).get("started_at") if existing else None) or now
        # portable upsert: delete-then-insert keeps it backend-agnostic
        conn.execute("DELETE FROM agent_sessions WHERE session_id = %s", (sid,))
        conn.execute(
            "INSERT INTO agent_sessions "
            "(session_id, agent_type, pid, host, cwd, started_at, last_heartbeat, current_intent, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active')",
            (sid, get_agent_type(), os.getpid(), socket.gethostname(),
             os.getcwd(), started, now, intent),
        )
        conn.commit()
        return {"ok": True, "session_id": sid}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "reason": str(exc)[:160]}
    finally:
        conn.close()


def heartbeat(intent: Optional[str] = None) -> bool:
    """Refresh last_heartbeat (+ intent if given). Registers if missing."""
    if get_connection is None:
        return False
    sid = get_session_id()
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
    sid = get_session_id()
    return [s for s in list_active(ttl_seconds) if s.get("session_id") != sid]


def end_session() -> bool:
    """Mark the current session ended (called from the Stop hook)."""
    if get_connection is None:
        return False
    conn = _conn()
    _ensure_table(conn)
    try:
        conn.execute(
            "UPDATE agent_sessions SET status='ended', last_heartbeat=%s WHERE session_id = %s",
            (_now(), get_session_id()),
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
