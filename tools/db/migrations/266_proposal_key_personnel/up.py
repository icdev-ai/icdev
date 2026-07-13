#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 266: proposal_key_personnel — the bid-side person -> LCAT registry.

prem-pstaff-01. Before this, the bid side had NO people table:

  * ``pg_lcat_allocations`` is task -> LCAT -> FTE, keyed on cost_volume_id. It never
    names a human.
  * ``pma_personnel`` is POST-AWARD, keyed on contract_id — it cannot hold anyone
    until after we have already won.
  * So ``program_bridge._gather_key_personnel`` regex-scraped capitalised bigrams out
    of proposal prose. Its pattern matches "Program Manager" and "Technical Approach"
    as readily as it matches a person, and it fed the "Key Personnel & Staffing Plan"
    section of a real bid.

The DDL is IMPORTED from tools.govcon.key_personnel rather than written out here, so
the CHECK constraints stay derived from the Python constants (QUALIFICATION_VERDICTS,
PERSON_SOURCES) instead of being restated in SQL and drifting from them — per the
CLAUDE.md rule. This is why the migration is a directory with an up.py rather than a
flat .sql file.

The evidence CHECK (``evidence_json <> '' AND evidence_json <> '[]'``) is the
refuse-the-unevidenced rule expressed in the schema. register_person() refuses too and
gives the caller a reason, but a constraint cannot be forgotten by a future writer.

``classification`` and ``tenant_id`` are present FROM THE START. get_connection()
injects an RLS predicate over them, so a table lacking them raises UndefinedColumn on
every read — the retrofit migration 253 had to perform on every other proposal-side
table.

NOTE: this table may ALREADY EXIST on the live database — it was created out-of-band
on 2026-07-12 by a task that escaped its gate, and was never recorded in
schema_migrations. CREATE TABLE IF NOT EXISTS reconciles that cleanly; the shape is
identical.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

MIGRATION_ID = "266"
MIGRATION_NAME = "proposal_key_personnel"
DESCRIPTION = "Add proposal_key_personnel (bid-side person->LCAT registry, evidence required)"


def _existing_columns(conn, table: str) -> set:
    try:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        ).fetchall()
        if rows:
            return {dict(r)["column_name"] for r in rows}
    except Exception:
        pass
    try:  # SQLite fallback
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def up(conn) -> dict:
    from tools.govcon.key_personnel import table_ddl

    actions = []
    conn.execute(table_ddl())
    actions.append("created proposal_key_personnel (or already present)")

    # The table may pre-date these two columns — it was created out-of-band on the
    # live database before they existed (see the module docstring), and CREATE TABLE
    # IF NOT EXISTS will NOT add a column to a table that is already there. Backfill
    # conditionally so both a fresh install and the existing live table converge.
    #
    # gaps travel WITH a 'gap' verdict on purpose: a person with a gap can still be the
    # right person to bid, but the bid side must see the gap when they price the risk
    # rather than discover it at the debrief.
    existing = _existing_columns(conn, "proposal_key_personnel")
    for col, ddl in (
        ("key_person",
         "ALTER TABLE proposal_key_personnel ADD COLUMN key_person INTEGER NOT NULL DEFAULT 0"),
        ("gaps_json",
         "ALTER TABLE proposal_key_personnel ADD COLUMN gaps_json TEXT NOT NULL DEFAULT '[]'"),
    ):
        if col in existing:
            continue
        try:
            conn.execute(ddl)
            actions.append(f"added column {col}")
        except Exception as exc:  # pragma: no cover - raced with another writer
            actions.append(f"column {col} not added ({exc})")

    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_pkp_opportunity "
        "ON proposal_key_personnel(opportunity_id)",
        "CREATE INDEX IF NOT EXISTS idx_pkp_verdict "
        "ON proposal_key_personnel(qualification_verdict)",
    ):
        conn.execute(idx_sql)
        actions.append(idx_sql.split(" ON ")[0].replace("CREATE INDEX IF NOT EXISTS ", "index "))

    conn.commit()
    return {"status": "applied", "actions": actions}
