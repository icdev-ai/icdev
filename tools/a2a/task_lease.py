#!/usr/bin/env python3
# CUI // SP-CTI
"""Atomic Task Checkout — prevents double-assignment of tasks across agents.

Implements a lease-based checkout system inspired by Paperclip AI's
single-assignee invariant. Each task can be claimed by exactly one agent
at a time, with TTL-based expiration for fault tolerance.

Architecture:
    - agent_task_leases table with UNIQUE(task_id) constraint
    - checkout_task() uses INSERT OR FAIL for atomic acquisition
    - Leases expire after configurable TTL (default: 300s)
    - release_task() explicitly releases lease on completion
    - expired leases are cleaned up lazily on next checkout attempt

Usage:
    python tools/a2a/task_lease.py --checkout --task-id t-123 --agent-id builder-agent --json
    python tools/a2a/task_lease.py --release --task-id t-123 --agent-id builder-agent --json
    python tools/a2a/task_lease.py --status --task-id t-123 --json
    python tools/a2a/task_lease.py --active --json
    python tools/a2a/task_lease.py --cleanup --json
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.common.helpers import now_iso  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402

DEFAULT_LEASE_TTL_SECONDS = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
CREATE_LEASE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_task_leases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    leased_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'released', 'expired')),
    classification TEXT DEFAULT 'CUI'
);
"""

CREATE_LEASE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_task_leases_agent ON agent_task_leases(agent_id);
CREATE INDEX IF NOT EXISTS idx_task_leases_status ON agent_task_leases(status);
CREATE INDEX IF NOT EXISTS idx_task_leases_expires ON agent_task_leases(expires_at);
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create agent_task_leases table if not present."""
    conn.execute(CREATE_LEASE_TABLE_SQL)
    conn.executescript(CREATE_LEASE_INDEX_SQL)
    conn.commit()


def _connect() -> sqlite3.Connection:
    """Get DB connection with lease table guaranteed."""
    conn = get_connection()
    _ensure_table(conn)
    return conn


def _expire_time(ttl_seconds: int) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cleanup_expired(conn: Optional[sqlite3.Connection] = None) -> int:
    """Mark expired leases so they can be reclaimed. Returns count expired."""
    own_conn = conn is None
    if own_conn:
        conn = _connect()
    try:
        now = now_iso()
        cursor = conn.execute(
            """UPDATE agent_task_leases
               SET status = 'expired', released_at = %s
               WHERE status = 'active' AND expires_at < %s""",
            (now, now),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        if own_conn:
            conn.close()


def checkout_task(
    task_id: str,
    agent_id: str,
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
) -> Dict:
    """Atomically claim a task for an agent. Returns lease info or conflict error.

    If the task is already leased by another agent (and not expired), returns
    an error with the current holder's info. If the existing lease is expired,
    it's released first and the new agent can claim it.

    Args:
        task_id: The task to claim.
        agent_id: The agent claiming the task.
        ttl_seconds: Lease duration in seconds.

    Returns:
        {"status": "acquired", ...} on success
        {"status": "conflict", "held_by": ...} if already claimed
        {"status": "renewed", ...} if same agent re-claims
    """
    conn = _connect()
    try:
        # Clean up expired leases first
        cleanup_expired(conn)

        # Check for existing active lease
        existing = conn.execute(
            """SELECT task_id, agent_id, leased_at, expires_at
               FROM agent_task_leases
               WHERE task_id = %s AND status = 'active'""",
            (task_id,),
        ).fetchone()

        now = now_iso()
        expires = _expire_time(ttl_seconds)

        if existing:
            if existing["agent_id"] == agent_id:
                # Same agent re-claiming — renew lease
                conn.execute(
                    """UPDATE agent_task_leases
                       SET expires_at = %s
                       WHERE task_id = %s AND status = 'active'""",
                    (expires, task_id),
                )
                conn.commit()
                return {
                    "status": "renewed",
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "leased_at": existing["leased_at"],
                    "expires_at": expires,
                    "ttl_seconds": ttl_seconds,
                }
            else:
                # Different agent holds it — conflict
                return {
                    "status": "conflict",
                    "task_id": task_id,
                    "held_by": existing["agent_id"],
                    "leased_at": existing["leased_at"],
                    "expires_at": existing["expires_at"],
                    "message": (
                        f"Task '{task_id}' is already claimed by "
                        f"'{existing['agent_id']}' until {existing['expires_at']}"
                    ),
                }

        # No active lease — acquire
        conn.execute(
            """INSERT INTO agent_task_leases
               (task_id, agent_id, leased_at, expires_at, status)
               VALUES (%s, %s, %s, %s, 'active')""",
            (task_id, agent_id, now, expires),
        )
        conn.commit()

        return {
            "status": "acquired",
            "task_id": task_id,
            "agent_id": agent_id,
            "leased_at": now,
            "expires_at": expires,
            "ttl_seconds": ttl_seconds,
        }

    except sqlite3.IntegrityError:
        # Race condition: another process inserted between our check and insert
        # Re-read to get the winner's info
        existing = conn.execute(
            """SELECT agent_id, leased_at, expires_at
               FROM agent_task_leases
               WHERE task_id = %s AND status = 'active'""",
            (task_id,),
        ).fetchone()
        holder = existing["agent_id"] if existing else "unknown"
        return {
            "status": "conflict",
            "task_id": task_id,
            "held_by": holder,
            "message": f"Task '{task_id}' was claimed by '{holder}' (race condition)",
        }
    finally:
        conn.close()


def release_task(task_id: str, agent_id: str) -> Dict:
    """Release a task lease. Only the holding agent can release.

    Args:
        task_id: The task to release.
        agent_id: The agent releasing (must match current holder).

    Returns:
        {"status": "released"} on success, error otherwise.
    """
    conn = _connect()
    try:
        now = now_iso()
        cursor = conn.execute(
            """UPDATE agent_task_leases
               SET status = 'released', released_at = %s
               WHERE task_id = %s AND agent_id = %s AND status = 'active'""",
            (now, task_id, agent_id),
        )
        conn.commit()

        if cursor.rowcount == 0:
            # Check why it failed
            existing = conn.execute(
                """SELECT agent_id, status FROM agent_task_leases WHERE task_id = %s""",
                (task_id,),
            ).fetchone()
            if not existing:
                return {"status": "error", "message": f"No lease found for task '{task_id}'"}
            if existing["agent_id"] != agent_id:
                return {
                    "status": "error",
                    "message": (f"Task '{task_id}' is held by '{existing['agent_id']}', not '{agent_id}'"),
                }
            return {
                "status": "error",
                "message": f"Lease for task '{task_id}' is already {existing['status']}",
            }

        return {
            "status": "released",
            "task_id": task_id,
            "agent_id": agent_id,
            "released_at": now,
        }
    finally:
        conn.close()


def get_lease_status(task_id: str) -> Dict:
    """Get current lease status for a task."""
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT task_id, agent_id, leased_at, expires_at, released_at, status
               FROM agent_task_leases
               WHERE task_id = %s
               ORDER BY id DESC LIMIT 1""",
            (task_id,),
        ).fetchone()

        if not row:
            return {"task_id": task_id, "status": "available", "message": "No lease exists"}

        result = dict(row)
        # Check if expired but not yet marked
        if result["status"] == "active" and result["expires_at"] < now_iso():
            result["status"] = "expired"
            result["message"] = "Lease expired (pending cleanup)"
        return result
    finally:
        conn.close()


def get_active_leases(agent_id: str = None) -> Dict:
    """Get all active leases, optionally filtered by agent."""
    conn = _connect()
    try:
        cleanup_expired(conn)

        if agent_id:
            rows = conn.execute(
                """SELECT task_id, agent_id, leased_at, expires_at
                   FROM agent_task_leases
                   WHERE status = 'active' AND agent_id = %s
                   ORDER BY leased_at DESC""",
                (agent_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT task_id, agent_id, leased_at, expires_at
                   FROM agent_task_leases
                   WHERE status = 'active'
                   ORDER BY leased_at DESC""",
            ).fetchall()

        leases = [dict(r) for r in rows]
        return {
            "active_count": len(leases),
            "leases": leases,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Atomic task checkout — lease-based single-assignee enforcement")
    parser.add_argument("--checkout", action="store_true", help="Claim a task")
    parser.add_argument("--release", action="store_true", help="Release a task lease")
    parser.add_argument("--status", action="store_true", help="Check lease status")
    parser.add_argument("--active", action="store_true", help="List active leases")
    parser.add_argument("--cleanup", action="store_true", help="Clean up expired leases")
    parser.add_argument("--task-id", help="Task ID")
    parser.add_argument("--agent-id", help="Agent ID")
    parser.add_argument(
        "--ttl",
        type=int,
        default=DEFAULT_LEASE_TTL_SECONDS,
        help=f"Lease TTL in seconds (default: {DEFAULT_LEASE_TTL_SECONDS})",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    parser.add_argument("--gate", action="store_true", help="Exit 1 on conflict/error")
    args = parser.parse_args()

    result = {}

    if args.checkout:
        if not args.task_id or not args.agent_id:
            parser.error("--checkout requires --task-id and --agent-id")
        result = checkout_task(args.task_id, args.agent_id, args.ttl)
    elif args.release:
        if not args.task_id or not args.agent_id:
            parser.error("--release requires --task-id and --agent-id")
        result = release_task(args.task_id, args.agent_id)
    elif args.status:
        if not args.task_id:
            parser.error("--status requires --task-id")
        result = get_lease_status(args.task_id)
    elif args.active:
        result = get_active_leases(args.agent_id)
    elif args.cleanup:
        count = cleanup_expired()
        result = {"expired_count": count, "cleaned_at": now_iso()}
    else:
        parser.error("Specify --checkout, --release, --status, --active, or --cleanup")

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")

    if args.gate and result.get("status") in ("conflict", "error"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
