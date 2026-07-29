# CUI // SP-CTI
"""Every column log_trigger_event() writes must exist in the schema.

On 2026-07-29 the live PostgreSQL studio_trigger_events table matched no schema
source in the tree: it carried match_reason/evaluated_at instead of
reason/received_at and lacked all six dwo-evt-02 columns. So every INSERT from
``log_trigger_event()`` raised, the exception was swallowed by design (an audit
write must never break ingest), and the function returned "". The trigger audit
trail — whose entire purpose is answering "why did this run start" — recorded
nothing on the primary backend, while SQLite and the whole test suite stayed
green.

These tests compare the INSERT's column list against the DDL, so a column added
to one and not the other fails here rather than silently disabling the audit
trail on whichever backend happens to drift. They deliberately do NOT require a
live database: the parity bug is expressible in source alone, and requiring PG
is exactly why nothing caught it.
"""
from __future__ import annotations

import inspect
import re
import sqlite3

import pytest

from tools.studio import event_sources
from tools.studio.init_db import STUDIO_TABLES

TABLE = "studio_trigger_events"


def _ddl_columns() -> set[str]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(STUDIO_TABLES[TABLE])
        return {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})")}
    finally:
        conn.close()


def _insert_columns() -> set[str]:
    """Columns named in log_trigger_event()'s INSERT statements."""
    src = inspect.getsource(event_sources.log_trigger_event)
    found: set[str] = set()
    for match in re.finditer(rf"INSERT INTO {TABLE}\s*\"?\s*\"?\s*\(([^)]*)\)", src, re.S):
        for raw in match.group(1).replace('"', " ").split(","):
            name = raw.strip().strip('"').strip()
            if name and name.isidentifier():
                found.add(name)
    return found


def test_the_insert_names_columns_at_all():
    """Guard the guard: a parser that finds nothing would pass everything."""
    assert _insert_columns(), "could not parse any INSERT columns — this test is inert"


def test_every_written_column_exists_in_the_ddl():
    written, declared = _insert_columns(), _ddl_columns()
    missing = written - declared
    assert not missing, (
        f"log_trigger_event() writes {sorted(missing)}, which {TABLE} does not "
        f"declare. The INSERT will raise, the audit write is swallowed, and the "
        f"trigger audit trail records nothing — silently."
    )


@pytest.mark.parametrize("column", [
    "reason", "received_at",          # renamed away on the drifted PG table
    "workflow_id", "outcome", "classification", "idempotency_key", "envelope_id",
])
def test_dispatch_columns_are_declared(column):
    """The exact seven the live PG table was missing or had misnamed."""
    assert column in _ddl_columns(), f"{TABLE}.{column} is missing from init_db"


def test_no_stale_names_reappear():
    """match_reason / evaluated_at are the drifted names — never canonical."""
    declared = _ddl_columns()
    for stale in ("match_reason", "evaluated_at"):
        assert stale not in declared, (
            f"{stale} is the ad-hoc name from the drifted table, not the schema's"
        )


def test_migration_310_exists_to_repair_an_already_drifted_database():
    """CREATE TABLE IF NOT EXISTS cannot fix a table that already exists.

    Fresh installs get the right shape from init_db; a database that already
    drifted needs the corrective migration, so its absence would leave every
    existing deployment broken.
    """
    from pathlib import Path
    up = (Path(__file__).resolve().parents[1]
          / "tools/db/migrations/310_studio_trigger_events_reconcile/up.py")
    assert up.exists(), "migration 310 is missing"
    text = up.read_text(encoding="utf-8")
    assert "RENAME COLUMN" in text, "310 must repair the misnamed columns"
    assert "ADD COLUMN" in text, "310 must add the missing dispatch columns"
