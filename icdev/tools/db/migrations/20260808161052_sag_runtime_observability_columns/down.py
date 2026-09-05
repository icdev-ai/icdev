# CUI // SP-CTI
"""Rollback — drop the three columns added to runtime_invocations.

SQLite before 3.35 has no ``DROP COLUMN``, and rebuilding a telemetry table to
remove three nullable columns is not worth the risk of losing rows mid-rebuild.
So each drop is attempted and a failure is reported rather than raised: the
columns are nullable and unread when the feature is off, and leaving them in
place costs nothing.
"""
from __future__ import annotations

from tools.db.storage import get_connection, table_exists

_TABLE = "runtime_invocations"
_COLUMNS = ("correlation_id", "arg_values", "result_preview")


def down(conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    dropped, skipped = [], []
    try:
        if not table_exists(conn, _TABLE):
            return {"status": "rolled_back", "dropped": [], "skipped": list(_COLUMNS)}
        try:
            conn.execute("DROP INDEX IF EXISTS idx_runtime_inv_correlation")
        except Exception:  # noqa: BLE001
            pass
        for column in _COLUMNS:
            try:
                conn.execute(f"ALTER TABLE {_TABLE} DROP COLUMN {column}")  # nosec B608 — identifiers are module constants
                dropped.append(column)
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{column}: {exc}")
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"status": "rolled_back", "dropped": dropped, "skipped": skipped}


if __name__ == "__main__":
    print(down())
