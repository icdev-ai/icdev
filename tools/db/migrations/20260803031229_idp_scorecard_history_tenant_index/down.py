# CUI // SP-CTI
"""Migration 20260803031229 rollback — drop the two tenant-leading indexes.

Only the indexes come off. ``tenant_id`` itself stays, for two reasons: the
creating migration (``20260802222900_idp_score_history``) already ships the
column in its own ``CREATE TABLE``, so dropping it here would leave the schema
disagreeing with the migration that owns it; and the recorder's INSERT names
``tenant_id``, so removing it would break every write rather than reverting a
performance change.

Dropping an index is safe on both backends and never loses data — the reads
that used them still return the same rows, just by a scan.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_INDEXES = ("idx_isch_tenant_component", "idx_isch_tenant_window")


def down(conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    dropped = []
    try:
        for name in _INDEXES:
            try:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
                dropped.append(name)
            except Exception as exc:  # noqa: BLE001
                print(f"[20260803031229] index {name} not dropped: {exc}")
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"status": "rolled_back", "dropped": dropped}


if __name__ == "__main__":
    print(down())
