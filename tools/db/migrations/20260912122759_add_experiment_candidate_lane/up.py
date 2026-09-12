#!/usr/bin/env python3
# CUI // SP-CTI
"""Add `experiment_candidates.lane`, CHECK DERIVED from a Python tuple (xrv-lab-02).

The evidence lanes an autoresearch candidate moves between:

    incubator  no real measurement exists for this candidate -- it is new, or
               its before/after could not be made. NOT a failure.
    frontier   kept: measured better than its base on two different trees, PR
               open, awaiting a human.
    archive    discarded, or superseded.

THE CHECK IS BUILT FROM ``real_mutation.LANES`` AND NEVER RESPELLED IN SQL.
A second list in a migration is how a constraint and the code that writes
through it come to disagree about a value neither of them changed -- the
precedent here is 20260902235404 (audit_trail.event_type rebuilt from its
tuple) rather than a hand-typed IN list.

A PYTHON MIGRATION, because this column faces THREE different populations and
only a catalogue probe can tell them apart: a PostgreSQL board that has never
had the column, a SQLite database whose `init_icdev_db.py` DDL already carries
it, and a board that has already run this migration. `ALTER TABLE ... ADD
COLUMN` raises on the last two, so the presence of the column is asked of the
LIVE catalogue and the migration is a stated no-op when it is already there.

EVERY EXISTING ROW READS `incubator`, AND THAT IS A MEASUREMENT, NOT A DEFAULT
CHOSEN FOR CONVENIENCE. Both backends backfill an added column carrying a
non-NULL DEFAULT, so every candidate created before this migration lands in
`incubator` -- which is exactly right: every one of them was decided by the
pre-xrv-lab-02 loop against an IDENTITY BASELINE (the engine evaluated the
domain, changed nothing, evaluated again), so no real measurement exists for
any of them. Laning a historically `discarded` candidate `archive` would
assert that something judged it; laning a `completed` one `frontier` would
assert it measured better than a base it was never compared against. The
DEFAULT also means `create_experiment` needs no edit: a new candidate is
`incubator` because the DDL says so, not because a writer remembered.
"""
from __future__ import annotations


def _lane_present(conn) -> bool:
    """Ask the LIVE catalogue, never the DDL.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so the DDL in
    `init_icdev_db.py` says nothing about a table an older migration created.
    """
    try:
        conn.execute("SELECT lane FROM experiment_candidates LIMIT 1").fetchall()
        return True
    except Exception:  # noqa: BLE001 - absence is the answer, not an error
        return False


def up(conn) -> dict:
    """Add the column if the live table lacks it. Idempotent, both backends."""
    from tools.autoresearch.real_mutation import LANE_INCUBATOR, LANES

    if _lane_present(conn):
        return {"column_added": False, "reason": "already_present",
                "lanes": list(LANES)}

    allowed = ", ".join("'" + lane.replace("'", "''") + "'" for lane in LANES)
    conn.execute(
        "ALTER TABLE experiment_candidates ADD COLUMN lane TEXT "
        f"DEFAULT '{LANE_INCUBATOR}' CHECK (lane IN ({allowed}))"
    )
    return {"column_added": True, "lanes": list(LANES), "default": LANE_INCUBATOR}


def down(conn) -> dict:
    """Drop the column where the backend can; REPORT the refusal where it cannot.

    MEASURED 2026-09-12 on SQLite only: `ALTER TABLE ... DROP COLUMN` succeeded
    there even with the column named in a CHECK. The PostgreSQL leg of that
    day's probe exercised `up` (add, backfill, CHECK, DEFAULT) against a
    throwaway table in `icdev_e2e` and did NOT exercise this drop, so it is
    attempted rather than asserted. SQLite gained DROP COLUMN only in 3.35 and
    refuses one named in a CHECK on some builds, and a rollback that raised
    would abort the whole migration chain. A refusal returns
    `column_dropped: False` with its reason; the column stays, which is safe
    because nothing outside xrv-lab-02 reads it.
    """
    if not _lane_present(conn):
        return {"column_dropped": False, "reason": "not_present"}
    try:
        conn.execute("ALTER TABLE experiment_candidates DROP COLUMN lane")
        return {"column_dropped": True}
    except Exception as exc:  # noqa: BLE001
        return {"column_dropped": False, "reason": "drop_unsupported",
                "error": str(exc)[:200]}
