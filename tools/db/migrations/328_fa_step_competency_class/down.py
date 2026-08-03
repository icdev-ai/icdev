# CUI // SP-CTI
"""Migration 328 down — drop the step competency class.

The index goes unconditionally. The column is dropped only where DROP COLUMN is
supported: PostgreSQL always, SQLite from 3.35. On an older SQLite the column is
left in place and reported — a stray nullable column is inert, and rebuilding the
table to remove it would risk the rows for no gain.
"""
from __future__ import annotations

import sqlite3

from tools.db.storage import column_exists, get_connection, is_pg, table_exists

_TABLE = "fa_step_ontology"
_COLUMN = "competency_class"
_TAG = "[328_fa_step_competency_class]"


def down(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        if not table_exists(conn, _TABLE):
            print(f"{_TAG} down: {_TABLE} absent; nothing to do")
            return

        conn.execute("DROP INDEX IF EXISTS idx_fa_step_ontology_competency")

        if not column_exists(conn, _TABLE, _COLUMN):
            print(f"{_TAG} down: {_TABLE}.{_COLUMN} already absent")
        elif is_pg(conn) or sqlite3.sqlite_version_info >= (3, 35, 0):
            conn.execute(f"ALTER TABLE {_TABLE} DROP COLUMN {_COLUMN}")
            print(f"{_TAG} down: dropped {_TABLE}.{_COLUMN}")
        else:
            print(
                f"{_TAG} down: SQLite {sqlite3.sqlite_version} has no DROP COLUMN; "
                f"leaving {_TABLE}.{_COLUMN} in place (inert)"
            )
        conn.commit()
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    down()
