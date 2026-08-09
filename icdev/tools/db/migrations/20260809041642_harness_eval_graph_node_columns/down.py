# CUI // SP-CTI
"""Migration 20260809041642 rollback — drop the graph-node columns from harness_eval.

DESTRUCTIVE in one specific way worth naming: any per-node correlation already
recorded is lost, because the run/node identity lives nowhere else. The decision
rows themselves survive — they simply revert to the task_id grain they had
before. Indexes go first: SQLite refuses to drop an indexed column.
"""
from __future__ import annotations

from tools.db.storage import column_exists, get_connection, table_exists

_TABLE = "harness_eval"
_TAG = "[20260809041642_harness_eval_graph_node_columns]"

_COLUMNS = ("run_id", "node_id", "node_type", "edge_condition")
_INDEXES = ("idx_harness_eval_run_node", "idx_harness_eval_node_type")


def down(conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    dropped: list[str] = []
    try:
        if not table_exists(conn, _TABLE):
            return {"status": "rolled_back", "dropped": [], "note": f"{_TABLE} absent"}

        for index in _INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {index}")

        for column in _COLUMNS:
            if column_exists(conn, _TABLE, column):
                conn.execute(f"ALTER TABLE {_TABLE} DROP COLUMN {column}")
                dropped.append(column)

        conn.commit()
        print(f"{_TAG} rolled back: dropped {', '.join(dropped) or 'nothing'}")
    finally:
        if own:
            conn.close()
    return {"status": "rolled_back", "dropped": dropped}


if __name__ == "__main__":
    print(down())
