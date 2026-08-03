# CUI // SP-CTI
"""Migration 343 rollback — remove the component key from developer_scorecards.

PARTIAL BY DESIGN. This drops the two added columns and the composite index.
It deliberately does **not** restore ``NOT NULL`` on ``project_id``,
``overall_score`` or ``letter_grade``.

Restoring them would fail outright on any database the scorer has written to —
a component scorecard row has no ``project_id``, and an unassessed one has no
score or grade, so ``SET NOT NULL`` would raise on exactly the rows migration
343 exists to permit. Silently deleting those rows to make the constraint
re-appliable would be worse. The relaxation is forward-only; if it must be
undone, delete the component-keyed rows first and re-apply the constraints by
hand, having decided what should happen to them.
"""
from __future__ import annotations

from tools.db.storage import column_exists, get_connection, table_exists

_TABLE = "developer_scorecards"
_TAG = "[343_scorecard_component_id]"


def down(conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    dropped = []
    try:
        if not table_exists(conn, _TABLE):
            return {"status": "rolled_back", "dropped": [], "note": f"{_TABLE} absent"}

        # SQLite refuses to drop an indexed column, so the index goes first on
        # both engines.
        conn.execute("DROP INDEX IF EXISTS idx_sc_component_evaluated")

        for column in ("component_id", "evaluated_at"):
            if column_exists(conn, _TABLE, column):
                conn.execute(f"ALTER TABLE {_TABLE} DROP COLUMN {column}")
                dropped.append(column)

        conn.commit()
        print(f"{_TAG} rolled back: dropped {', '.join(dropped) or 'nothing'}")
    finally:
        if own:
            conn.close()
    return {
        "status": "rolled_back",
        "dropped": dropped,
        "not_restored": ["project_id", "overall_score", "letter_grade"],
    }


if __name__ == "__main__":
    print(down())
