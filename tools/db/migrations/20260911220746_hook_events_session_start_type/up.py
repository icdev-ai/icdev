#!/usr/bin/env python3
# CUI // SP-CTI
"""Admit `session_start` (and two names hooks were already writing) on hook_events (xrv-mem-01).

`hook_events.hook_type` is CHECKed. The list lived as a literal in
`init_icdev_db.py`; it now derives from `tools.hooks.hook_event_types
.HOOK_EVENT_TYPES`, and this migration rewrites the LIVE constraint from the
same tuple — following 20260902235404 (audit_trail.event_type).

Three names join:
  session_start        the new SessionStart hook (this card)
  user_prompt_submit   written by .claude/hooks/user_prompt_submit.py since it
                       was authored, refused on every write
  pre_compact          written by .claude/hooks/pre_compact.py, same

MEASURED 2026-09-11: 0 rows of either on PostgreSQL and 0 on SQLite, beside
42,569 / 341,173 post_tool_use rows. Each hook wraps its write in
`except Exception: pass`, so the refusal never surfaced.

SQLite is a STATED no-op: a CHECK cannot be ALTERed there and `hook_events` is
append-only, so a copy-and-swap rebuild is refused on principle. A fresh SQLite
database derives the constraint from the tuple in `init_icdev_db.py`; an
EXISTING SQLite `data/icdev.db` keeps refusing the three names until it is
re-initialised. The headless `hook_compat.run_session_start` reports
`event_recorded: false` on its result when that happens, so the loss is
measured rather than silent.
"""
from __future__ import annotations


def up(conn) -> dict:
    """Regenerate hook_events' hook_type CHECK from HOOK_EVENT_TYPES."""
    from tools.hooks.hook_event_types import rebuild_hook_type_constraint

    rebuilt = rebuild_hook_type_constraint(conn)
    return {
        "constraint_rebuilt": rebuilt,
        "note": None if rebuilt else "sqlite: CHECK not alterable; fresh DBs derive it from init_icdev_db.py",
    }


def down(conn) -> dict:
    """Rebuilding the constraint is its own inverse: reverting the code reverts
    the constraint. Narrowing it again FAILS on PostgreSQL once rows carrying a
    removed name exist — PostgreSQL validates a new CHECK against existing
    rows — and that is correct: deleting hook telemetry to make a rollback
    succeed is not a rollback.
    """
    from tools.hooks.hook_event_types import rebuild_hook_type_constraint

    return {"constraint_rebuilt": rebuild_hook_type_constraint(conn)}
