# CUI // SP-CTI
"""Migration 20261118010237 — exemptions that expire, and ADRs as records (icx-coe-02).

Two changes, both on the platform database.

``idp_rule_exemptions`` (migration 20260803030514) gains what an exemption that
expires needs. Columns are ADDED, never changed, so every existing row and every
existing reader keeps working:

* ``scope_kind`` / ``scope_ref``: ``rule`` (one rule on one component, the
  original shape), ``service`` (every rule of the scorecard on one component),
  ``standard`` (the rule an engineering standard's check names, on one component)
  or ``repo`` (one rule on every component of a repo);
* ``standard_ids``: the engineering standards (icx-coe-01 register) it waives;
* ``requested_expires_on``: the expiry a pending renewal asks for;
* ``exemption_digest``, ``step_id``, ``actor``, ``actor_kind``: the exemption's digest, the
  declared step (``wf_decision_steps``) that granted or renewed it, who wrote the
  row, and whether a
  person, an agent or the labelled bootstrap identity wrote the row;
* ``prev_hash`` / ``row_hash``: each new row carries the hash of the exemption's
  previous row; ``UNIQUE(exemption_key, prev_hash)`` stops two rows following the
  same one. Rows written before this migration have neither and are reported as
  legacy (pre-chain), never as verified.

The table was registered as append-only in the guard hook and had no trigger.
It now has one, on SQLite and on PostgreSQL, for UPDATE and DELETE.

THE DATA STEP. An exemption already approved with no expiry would, from here,
apply for ever, and the new rule refuses that. It is never granted again
silently and never dropped silently: ``tools.idp.exemptions.migrate_legacy``
appends one ``migrated`` row per such exemption, written by the labelled
bootstrap identity (never a person), with an expiry ``migration_grace_days``
after the migration ran (``args/coe_records.yaml``). The twin reports each as
``coe.exemption.migrated`` until a person renews it through the declared step or
lets it expire. A pending request with no expiry is left pending: it cannot be
granted until it is re-requested with an expiry.

``coe_adr_events`` is new: the lifecycle ledger of the ADR records
(``tools/idp/adrs.py``), append-only and hash-chained per ADR exactly as
``coe_standard_events`` is (``UNIQUE(adr_id, prev_hash)``).

Every statement is IF NOT EXISTS / OR REPLACE or guarded by a column check, so a
re-run converges. The PostgreSQL trigger message is concatenated, never a RAISE
format string (the storage layer rewrites ``%``).
"""
from __future__ import annotations

_TAG = "[20261118010237_coe_exemptions_adrs]"

EXEMPTIONS = "idp_rule_exemptions"
ADR_TABLE = "coe_adr_events"

#: (column, DDL type) added to idp_rule_exemptions. Asserted against
#: tools/idp/exemptions.INSERT_COLUMNS by tests/test_icx_coe_02_exemptions.py.
EXEMPTION_COLUMNS = (
    ("scope_kind", "TEXT NOT NULL DEFAULT 'rule'"),
    ("scope_ref", "TEXT NOT NULL DEFAULT ''"),
    ("standard_ids", "TEXT NOT NULL DEFAULT '[]'"),
    ("requested_expires_on", "TEXT"),
    ("exemption_digest", "TEXT NOT NULL DEFAULT ''"),
    ("step_id", "TEXT NOT NULL DEFAULT ''"),
    ("actor", "TEXT NOT NULL DEFAULT ''"),
    ("actor_kind", "TEXT NOT NULL DEFAULT 'person'"),
    ("prev_hash", "TEXT"),
    ("row_hash", "TEXT"),
)

EXEMPTION_INDEXES = (
    ("CREATE UNIQUE INDEX IF NOT EXISTS idx_ixmp_chain "
     "ON idp_rule_exemptions (exemption_key, prev_hash)"),
)

ADR_DDL = (
    """CREATE TABLE IF NOT EXISTS coe_adr_events (
    event_id        TEXT PRIMARY KEY,
    adr_id          TEXT NOT NULL,
    version         TEXT NOT NULL,
    event           TEXT NOT NULL
        CHECK(event IN ('seeded','proposed','accepted','returned','superseded','deprecated')),
    actor           TEXT NOT NULL,
    actor_kind      TEXT NOT NULL CHECK(actor_kind IN ('person','agent','bootstrap')),
    actor_role      TEXT NOT NULL DEFAULT '',
    step_id         TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    entry_digest    TEXT NOT NULL,
    entry_json      TEXT NOT NULL,
    detail_json     TEXT NOT NULL DEFAULT '{}',
    prev_hash       TEXT NOT NULL,
    row_hash        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(adr_id, prev_hash)
)""",
    "CREATE INDEX IF NOT EXISTS idx_coe_adr_events_adr ON coe_adr_events (adr_id, created_at)",
)


def _sqlite_triggers(table: str) -> tuple[str, ...]:
    return (
        (f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
         f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"),
        (f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
         f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"),
    )


def _pg_triggers(table: str) -> tuple[str, ...]:
    fn = f"{table}_immutable"
    return (
        f"""CREATE OR REPLACE FUNCTION {fn}()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || ' is append-only';
END;
$$ LANGUAGE plpgsql""",
    ) + tuple(
        stmt
        for op in ("update", "delete")
        for stmt in (
            f"DROP TRIGGER IF EXISTS {table}_no_{op} ON {table}",
            (f"CREATE TRIGGER {table}_no_{op} BEFORE {op.upper()} ON {table} "
             f"FOR EACH ROW EXECUTE FUNCTION {fn}()"),
        )
    )


def _is_pg(conn) -> bool:
    from tools.db.storage import is_pg

    return is_pg(conn)


def _exemptions(conn) -> list[str]:
    """Add the columns, the chain index and the triggers. Returns the columns added."""
    from tools.db.storage import column_exists, table_exists

    if not table_exists(conn, EXEMPTIONS):
        # 20260803030514 has not run here: create it first, then extend it.
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "20260803030514_idp_rule_exemptions" / "up.py"
        spec = importlib.util.spec_from_file_location("_mig_20260803030514", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.up(conn)
    added = []
    for column, ddl in EXEMPTION_COLUMNS:
        if not column_exists(conn, EXEMPTIONS, column):
            conn.execute(f"ALTER TABLE {EXEMPTIONS} ADD COLUMN {column} {ddl}")
            added.append(column)
    for stmt in EXEMPTION_INDEXES:
        conn.execute(stmt)
    for stmt in _pg_triggers(EXEMPTIONS) if _is_pg(conn) else _sqlite_triggers(EXEMPTIONS):
        conn.execute(stmt)
    conn.commit()
    return added


def up(conn) -> dict:
    added = _exemptions(conn)
    for stmt in ADR_DDL:
        conn.execute(stmt)
    for stmt in _pg_triggers(ADR_TABLE) if _is_pg(conn) else _sqlite_triggers(ADR_TABLE):
        conn.execute(stmt)
    conn.commit()
    from tools.idp.exemptions import migrate_legacy

    migrated = migrate_legacy(conn)
    print(f"{_TAG} {EXEMPTIONS}: +{len(added)} columns, append-only; {ADR_TABLE} created, "
          f"append-only; {len(migrated['migrated'])} legacy exemption(s) given an expiry by "
          f"{migrated['identity']}")
    return {"status": "applied", "tables": [EXEMPTIONS, ADR_TABLE], "columns_added": added,
            "migrated": migrated["migrated"]}
