# CUI // SP-CTI
"""Migration 327 — end the ``simulation_results`` name collision.

Two unrelated subsystems declare a table called ``simulation_results``:

    Digital Program Twin      tools/db/init_icdev_db.py:1776
      (tools/simulation/*)    id INTEGER, scenario_id, dimension, metric_name,
                              baseline_value, simulated_value, delta, delta_pct,
                              confidence, impact_tier, details, visualizations,
                              calculated_at

    Network Canvas            tools/network/db/init_db.py:207
      (tools/network/*)       id TEXT, topology_id, sim_type, input_json,
                              result_json, ran_at

Both use ``CREATE TABLE IF NOT EXISTS``, so in a shared database the loser of
the init race silently gets the winner's table. Measured against the live
``icdev`` PostgreSQL database on 2026-08-02, Network Canvas won:

    icdev (PG)                simulation_results -> topology shape,  0 rows
    data/icdev.db (SQLite)    simulation_results -> Twin shape,      0 rows
    data/network_canvas.db    simulation_results -> topology shape,  0 rows

So every INSERT in tools/simulation/{coa_generator,scenario_manager,
simulation_engine}.py targets a table whose columns do not exist, against the
primary backend. The collision is also why ICDEV_NDC_ENABLED and
ICDEV_NETWORK_ENABLED are pinned false in the E2E job of
.github/workflows/icdev-ci.yml.

Resolution: **the Twin keeps the name, Network Canvas renames.**

  * ``simulation_results`` is the Twin's in the schema of record —
    tools/db/schema/pg_consolidated.sql:29501 and tools/saas/db/pg_schema.py:1484
    both declare the dimension shape, as does args/installation_manifest.yaml
    (module ``simulation``). Migration 038 tunes its ``dimension`` CHECK.
  * Network Canvas prefixes its other ~137 tables ``nc_``. ``simulation_results``
    and ``topologies`` are the only two that do not, and the consolidated schema
    already carries ``nc_topologies``, ``nc_simulation_runs``,
    ``nc_simulation_sessions`` and ``nc_simulation_artifacts`` — the ``nc_``
    convention for this subsystem is established, these two just predate it.
  * Network Canvas historically had its own database file
    (data/network_canvas.db), where an unprefixed name was harmless. Setting
    NC_STORAGE_BACKEND=postgresql moved it into the shared ``icdev`` database;
    that move is what created the collision.

``topologies`` is left alone: it is unprefixed but has only one owner (migration
010 and the Network Canvas init declare compatible shapes), so it is a naming
inconsistency, not a collision.

Data preservation: the rename is ``ALTER TABLE ... RENAME TO``, which carries
rows, indexes and inbound references with it. Nothing is copied or dropped.
Both live tables are empty today, but this must stay correct for deployments
that are not.

PostgreSQL detail that a plain rename gets wrong: constraint names do **not**
follow the table. After ``ALTER TABLE simulation_results RENAME TO
nc_simulation_results`` the primary key is still called
``simulation_results_pkey``, so creating the Twin table immediately afterwards
fails with ``relation "simulation_results_pkey" already exists``. Both
constraints are therefore renamed explicitly. Verified against the live
database — the NC table carries exactly ``simulation_results_pkey`` (p) and
``simulation_results_topology_id_fkey`` (f), and no inbound foreign keys.
"""
from __future__ import annotations

from tools.db.storage import column_exists, get_connection, is_pg, table_exists

#: Twin-shaped ``simulation_results``, copied verbatim from the two schemas of
#: record so this migration cannot drift from them:
#:   PG     -> tools/saas/db/pg_schema.py:1484
#:   SQLite -> tools/db/init_icdev_db.py:1776
_TWIN_TABLE_PG = """
CREATE TABLE IF NOT EXISTS simulation_results (
    id SERIAL PRIMARY KEY,
    scenario_id TEXT NOT NULL REFERENCES simulation_scenarios(id),
    dimension TEXT NOT NULL
        CHECK(dimension IN ('architecture', 'compliance', 'supply_chain',
            'schedule', 'cost', 'risk', 'resource_allocation', 'quality')),
    metric_name TEXT NOT NULL,
    baseline_value REAL,
    simulated_value REAL,
    delta REAL,
    delta_pct REAL,
    confidence REAL DEFAULT 0.0,
    impact_tier TEXT CHECK(impact_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    details JSONB,
    visualizations JSONB,
    calculated_at TEXT DEFAULT (now()::text),
    classification VARCHAR(50) DEFAULT 'CUI'
)
"""

_TWIN_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS simulation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL REFERENCES simulation_scenarios(id),
    dimension TEXT NOT NULL
        CHECK(dimension IN ('architecture', 'compliance', 'supply_chain',
            'schedule', 'cost', 'risk', 'resource_allocation', 'quality')),
    metric_name TEXT NOT NULL,
    baseline_value REAL,
    simulated_value REAL,
    delta REAL,
    delta_pct REAL,
    confidence REAL DEFAULT 0.0,
    impact_tier TEXT CHECK(impact_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    details TEXT,
    visualizations TEXT,
    calculated_at TEXT DEFAULT (datetime('now'))
)
"""

#: Ordered columns shared by the legacy and renamed Network Canvas table. Only
#: used on the merge path (target already exists), never on the rename path.
_NC_COLUMNS = ("id", "topology_id", "sim_type", "input_json", "result_json", "ran_at")


def _is_nc_shape(conn) -> bool:
    """True when ``simulation_results`` is the Network Canvas table.

    ``topology_id`` exists in the NC shape and in no version of the Twin shape,
    so it is an unambiguous discriminator.
    """
    return column_exists(conn, "simulation_results", "topology_id")


def _rename_nc_constraints(conn) -> None:
    """Rename the PG constraints the table rename left behind.

    Each is tolerated individually: a database built by a path that never
    created one of them is still a valid starting point.
    """
    for old, new in (
        ("simulation_results_pkey", "nc_simulation_results_pkey"),
        ("simulation_results_topology_id_fkey", "nc_simulation_results_topology_id_fkey"),
    ):
        try:
            conn.execute(
                f"ALTER TABLE nc_simulation_results RENAME CONSTRAINT {old} TO {new}"
            )
        except Exception as exc:  # noqa: BLE001 - absent constraint is not an error
            conn.rollback()
            print(f"[327_nc_simulation_results_rename] constraint {old} not renamed: {exc}")


def up(conn=None) -> None:
    """Apply migration 327 (idempotent, safe to re-run)."""
    conn = get_connection()
    pg = is_pg(conn)
    try:
        # 1. Move the Network Canvas table off the shared name, preserving rows.
        if table_exists(conn, "simulation_results") and _is_nc_shape(conn):
            if not table_exists(conn, "nc_simulation_results"):
                conn.execute("ALTER TABLE simulation_results RENAME TO nc_simulation_results")
                if pg:
                    _rename_nc_constraints(conn)
                conn.commit()
                print(
                    "[327_nc_simulation_results_rename] renamed Network Canvas "
                    "simulation_results -> nc_simulation_results"
                )
            else:
                # Both names present: fold the legacy rows into the new table and
                # drop the squatter. Existing ids win — the new table is the one
                # the application has been writing to.
                cols = ", ".join(_NC_COLUMNS)
                conn.execute(
                    f"INSERT INTO nc_simulation_results ({cols}) "
                    f"SELECT {cols} FROM simulation_results WHERE id NOT IN "
                    "(SELECT id FROM nc_simulation_results)"
                )
                conn.execute("DROP TABLE simulation_results")
                conn.commit()
                print(
                    "[327_nc_simulation_results_rename] merged legacy rows into "
                    "nc_simulation_results and dropped the colliding table"
                )

        # 2. Give the Twin its table back. Skipped when simulation_scenarios is
        #    absent (fresh database mid-init) — init_icdev_db.py creates both in
        #    dependency order, so there is nothing to repair yet.
        if table_exists(conn, "simulation_results"):
            print(
                "[327_nc_simulation_results_rename] simulation_results already "
                "has the Digital Program Twin shape — nothing to do"
            )
        elif not table_exists(conn, "simulation_scenarios"):
            print(
                "[327_nc_simulation_results_rename] simulation_scenarios absent; "
                "leaving simulation_results to init_icdev_db.py"
            )
        else:
            conn.execute(_TWIN_TABLE_PG if pg else _TWIN_TABLE_SQLITE)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sim_result_scenario "
                "ON simulation_results(scenario_id)"
            )
            conn.commit()
            print(
                "[327_nc_simulation_results_rename] created Digital Program Twin "
                "simulation_results"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    up()
