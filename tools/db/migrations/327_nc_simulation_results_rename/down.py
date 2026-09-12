# CUI // SP-CTI
"""Migration 327 rollback — hand ``simulation_results`` back to Network Canvas.

This restores the colliding state on purpose, for the case where 327 has to be
backed out. It is deliberately refusing rather than destructive:

  * If the Digital Program Twin's ``simulation_results`` holds rows, the
    rollback aborts. Dropping it would destroy simulation output that only
    exists because 327 gave the Twin a working table, and there is nowhere to
    put those rows — the Network Canvas shape has no column that can hold them.
    Empty the table deliberately first if the rollback is genuinely wanted.
  * ``nc_simulation_results`` is renamed back, never copied, so Network Canvas
    rows survive the round trip.

The PG constraint renames from up() are undone in the same explicit way, for
the same reason: they do not follow the table.
"""
from __future__ import annotations

from tools.db.storage import column_exists, get_connection, is_pg, table_exists


def _row_count(conn, table: str) -> int:
    """Rows in *table*, 0 when it is absent — ABSENCE ASKED OF THE CATALOGUE.

    This was ``try: SELECT COUNT(*) ... except Exception: return 0``, i.e. a
    read of the relation under test with failure as the expected path. On
    PostgreSQL a failed statement ABORTS THE TRANSACTION, so reading the
    exception as "absent" poisons every later statement in this rollback — the
    rename and the constraint restores below — and the caller cannot tell an
    empty table from a dead transaction. Same defect as migration 326's
    ``_table_exists`` (mfx-ci-05); the census that found that one found this.

    ``table_exists`` reads ``information_schema`` / ``sqlite_master``, which
    returns a row or no row and raises in neither case, so there is nothing to
    roll back. Behaviour is unchanged: absent still counts as empty.
    """
    if not table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _restore_nc_constraints(conn) -> None:
    """Undo the up() constraint renames (each tolerated individually)."""
    for new, old in (
        ("nc_simulation_results_pkey", "simulation_results_pkey"),
        ("nc_simulation_results_topology_id_fkey", "simulation_results_topology_id_fkey"),
    ):
        try:
            conn.execute(f"ALTER TABLE simulation_results RENAME CONSTRAINT {new} TO {old}")
        except Exception as exc:  # noqa: BLE001 - absent constraint is not an error
            conn.rollback()
            print(f"[327_nc_simulation_results_rename] constraint {new} not restored: {exc}")


def down() -> None:
    """Roll migration 327 back (idempotent)."""
    conn = get_connection()
    pg = is_pg(conn)
    try:
        if not table_exists(conn, "nc_simulation_results"):
            print(
                "[327_nc_simulation_results_rename] down: nc_simulation_results "
                "absent — nothing to roll back"
            )
            return

        twin_present = table_exists(conn, "simulation_results") and not column_exists(
            conn, "simulation_results", "topology_id"
        )
        if twin_present:
            rows = _row_count(conn, "simulation_results")
            if rows:
                raise RuntimeError(
                    f"refusing to roll back: simulation_results holds {rows} Digital "
                    "Program Twin row(s) that the Network Canvas shape cannot store. "
                    "Empty or archive the table first."
                )
            conn.execute("DROP TABLE simulation_results")

        conn.execute("ALTER TABLE nc_simulation_results RENAME TO simulation_results")
        if pg:
            _restore_nc_constraints(conn)
        conn.commit()
        print(
            "[327_nc_simulation_results_rename] down: nc_simulation_results -> "
            "simulation_results (Network Canvas owns the name again)"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    down()
