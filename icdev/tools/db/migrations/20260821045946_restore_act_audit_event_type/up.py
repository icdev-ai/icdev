#!/usr/bin/env python3
# CUI // SP-CTI
"""Admit the restore-tier audit event type (autonomy-act-03).

One name is added to `tools.audit.audit_logger.VALID_EVENT_TYPES`:
`awareness.restore_act`, written by `tools/awareness/restore_acts.py` before
and after every enumerated restore act.

`audit_trail.event_type` carries a CHECK derived from that tuple. Adding the
name to the tuple only changes what a *fresh* database declares — an existing
PostgreSQL table keeps the CHECK it was created with, so the INSERT would raise
at runtime. That raise is fail-closed BY DESIGN (`log_event(raise_on_error=
True)` — no audit row, no act), so until this migration runs every restore act
on such a database is refused as `unaudited_refused`. Following 20260819021003.

`rebuild_event_type_constraint` is a no-op on SQLite (a CHECK cannot be ALTERed
there); fresh SQLite databases pick the name up from `init_icdev_db.py`, which
generates the constraint from the same tuple.
"""
from __future__ import annotations


def up(conn):
    """Regenerate audit_trail's event_type CHECK from VALID_EVENT_TYPES."""
    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuild_event_type_constraint(conn)


def down(conn):
    """Rebuilding the constraint is its own inverse: reverting the code
    reverts the constraint."""
    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuild_event_type_constraint(conn)
