# CUI // SP-CTI
"""Admit the "redraft with my comments" audit event type (dwr-ev-03).

One name is added to `tools.audit.audit_logger.VALID_EVENT_TYPES`:
`dic.redraft`, written by `tools/document_intelligence/redraft.py` around a
human-requested redraft of an AI-drafted change. The act and the phase are in
`action` -- `dic_suggestion.redraft.intent` BEFORE the draft and
`dic_suggestion.redraft.<outcome>` after -- following the `restore_acts` and
`migration_canvas` precedents rather than several near-identical types.

It is NOT `dic.hitl_decision`. That type records a human DISPOSING of a
proposal (accepted / rejected / approved). A redraft disposes of nothing: it
asks for a different proposal and retires the one it replaces without any
verdict on it. Recording it as a decision would put an accept-or-reject on the
board that no human ever made, in the very table cef-ui-03 reads to answer "was
this reviewed?".

`audit_trail.event_type` carries a CHECK derived from that tuple. Adding the
name to the tuple only changes what a *fresh* database declares -- an existing
PostgreSQL table keeps the CHECK it was created with, so the INSERT would raise
at runtime. That raise is fail-closed BY DESIGN (`log_event(raise_on_error=
True)` -- no intent row, no redraft), so until this migration runs every
redraft on such a database is refused as `unaudited_refused`. That is the
correct reading, not an obstacle. Following 20260819021003 and 20260906120818.

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
