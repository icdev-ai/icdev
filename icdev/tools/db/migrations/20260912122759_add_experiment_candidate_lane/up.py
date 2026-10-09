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


TABLE = "experiment_candidates"
COLUMN = "lane"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_present(conn) -> bool:
    """Is the table there at all? A migrate-only fresh database may lack it.

    `experiment_candidates` is created by `init_icdev_db.py` at app runtime and
    not by this chain, so a `migrate.py --up` against an empty database
    legitimately has no such table -- migration 020 records the same reasoning
    for `kanban_tasks`. Skip rather than abort the whole chain.
    """
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (TABLE,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,),
        ).fetchone()
    return row is not None


def _lane_present(conn) -> bool:
    """Ask the LIVE CATALOGUE -- never a SELECT that raises when absent.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so the DDL in
    `init_icdev_db.py` says nothing about a table an older migration created,
    and the catalogue is the only honest source.

    THE PROBE MUST NOT BE THE THING THAT BREAKS THE MIGRATION. This asked
    `SELECT lane FROM experiment_candidates LIMIT 1` and read the exception as
    absence. That reads correctly on SQLite and is fatal on PostgreSQL, where a
    failed statement ABORTS THE TRANSACTION: every later command in the same
    transaction -- here the ALTER this function exists to authorise -- returns
    "current transaction is aborted, commands ignored until end of transaction
    block". So on PostgreSQL the column could never be added, and because
    `bootstrap_pg` fails loudly rather than leaving a schema that claims to be
    current, it took `Test (PostgreSQL)` and all four E2E shards down with it
    (run 34696022955, 2026-09-12).
    This is the same defect rmf-rail-02 fixed in `posture._has_rows`, where a
    probe of an absent table aborted the transaction and blanked a live column.
    A catalogue read returns a row or no row and raises in neither case, so
    there is nothing to roll back.
    """
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=? AND column_name=?",
            (TABLE, COLUMN),
        ).fetchone()
        return row is not None
    rows = conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
    return any(
        (r[1] if isinstance(r, (list, tuple)) else dict(r).get("name")) == COLUMN
        for r in rows
    )


def up(conn) -> dict:
    """Add the column if the live table lacks it. Idempotent, both backends."""
    from tools.autoresearch.real_mutation import LANE_INCUBATOR, LANES

    if not _table_present(conn):
        return {"column_added": False, "reason": "table_absent",
                "lanes": list(LANES)}
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
    if not _table_present(conn) or not _lane_present(conn):
        return {"column_dropped": False, "reason": "not_present"}
    try:
        conn.execute("ALTER TABLE experiment_candidates DROP COLUMN lane")
        return {"column_dropped": True}
    except Exception as exc:  # noqa: BLE001
        return {"column_dropped": False, "reason": "drop_unsupported",
                "error": str(exc)[:200]}
