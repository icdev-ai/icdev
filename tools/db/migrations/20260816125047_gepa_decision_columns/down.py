# CUI // SP-CTI
"""Reverse of up.py — drop GEPA's decision columns (rem-cap-01)."""
from tools.db.storage import column_exists, get_connection

_TABLE = "agent_improvement_artifacts"
_COLUMNS = ("gepa_decided_at", "gepa_decision")


def down(conn=None) -> None:
    owned = conn is None
    conn = conn or get_connection()
    try:
        for column in _COLUMNS:
            if column_exists(conn, _TABLE, column):
                conn.execute(f"ALTER TABLE {_TABLE} DROP COLUMN {column}")
        conn.commit()
        print(f"Migration gepa_decision_columns down: dropped {list(_COLUMNS)} from {_TABLE}")
    finally:
        if owned:
            conn.close()


if __name__ == "__main__":
    down()
