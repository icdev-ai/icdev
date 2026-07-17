#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 271: repair drifted ACE state CHECK constraints + finalize zombies.

PostgreSQL only. Two problems this fixes on a live database:

1. Constraint drift. ``ace_instances_state_check`` and
   ``ace_coworkers_state_check`` were created before the Python state constants
   (INSTANCE_STATES / COWORKER_STATES in tools/ace/db/init_db.py) grew. The live
   PG check allowed only a subset (e.g. coworkers: idle/active/busy/offline/
   suspended; instances lacked 'cancelled' but had a stray 'archived'), so every
   transition to a newer state ('working', 'hitl_pending', 'done', 'failed', ...)
   raised CheckViolation — silently swallowed at the call site, leaving coworkers
   stuck 'idle' and instances zombie 'active'. CREATE TABLE IF NOT EXISTS never
   repairs a constraint on a pre-existing table, so this must be an explicit
   ALTER. We delegate to repair_state_constraints() so the constraint is always
   re-derived from the Python constants (single source of truth).

2. Zombie instances. Instances left in a non-terminal state ('active', 'pending',
   'assembling') by the swallowed CheckViolation never reached a terminal state.
   This one-time sweep finalizes those older than 24h to 'failed'.

Idempotent: repair_state_constraints() no-ops when the constraint already
matches, and the sweep only touches non-terminal rows past the cutoff. SQLite is
unaffected (the test harness recreates tables from the fresh SCHEMA). Safe with
0 rows and safe if the ace_* tables live in a separate database (skips).
"""

from datetime import datetime, timedelta, timezone

_ZOMBIE_STATES = ("active", "pending", "assembling")
_ZOMBIE_AGE_HOURS = 24


def _is_postgres(conn) -> bool:
    try:
        from icdev.tools.db.storage import is_pg

        return is_pg(conn)
    except Exception:
        mod = type(conn).__module__
        return "psycopg" in mod or "psycopg2" in mod


def _table_exists(conn, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (name,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def up(conn):
    if not _is_postgres(conn):
        return {"status": "skipped", "reason": "SQLite — tables recreated from fresh SCHEMA"}

    if not (_table_exists(conn, "ace_instances") and _table_exists(conn, "ace_coworkers")):
        return {"status": "skipped", "reason": "ace_* tables absent (separate DB or not initialised)"}

    # 1. Repair drifted CHECK constraints from the Python constants.
    from icdev.tools.ace.db.init_db import repair_state_constraints

    repaired = repair_state_constraints(conn)

    # 2. One-time zombie sweep — finalize stale non-terminal instances.
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_ZOMBIE_AGE_HOURS)).isoformat()
    placeholders = ", ".join(["%s"] * len(_ZOMBIE_STATES))
    swept = 0
    try:
        cur = conn.execute(
            f"UPDATE ace_instances SET state = 'failed' "
            f"WHERE state IN ({placeholders}) "
            f"AND created_at::timestamptz < %s::timestamptz",
            (*_ZOMBIE_STATES, cutoff),
        )
        swept = getattr(cur, "rowcount", 0) or 0
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"status": "ok", "constraints": repaired, "zombies_swept": 0, "sweep_error": str(exc)}

    return {"status": "ok", "constraints": repaired, "zombies_swept": swept, "cutoff": cutoff}


def down(conn):
    # No-op: constraints reflect the current Python constants (the canonical
    # source), and re-narrowing them would reintroduce the CheckViolation bug.
    # The zombie sweep set failed rows to a terminal state — reversing it would
    # resurrect zombies, not restore correct data.
    return {"status": "noop"}
