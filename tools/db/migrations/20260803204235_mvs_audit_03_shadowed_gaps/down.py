# CUI // SP-CTI
"""Down for 20260803204235 — drop only what this migration can be sure it added.

Deliberately partial. The columns and the role CHECK are NOT reverted:

* ``dashboard_users.role`` — narrowing the constraint back would reject rows
  that exist by then, so the DROP would fail or the data would have to be
  deleted. A widened CHECK is not a state worth rolling back to.
* ``kanban_tasks`` / ``sg_conflict_events`` columns — present on PostgreSQL
  before this migration ran (via ``pg_consolidated.sql``), so dropping them on
  the way down would remove schema this migration did not introduce.

The three table groups are safe to drop because nothing else declares them:
that absence is precisely what made them gaps.
"""
from __future__ import annotations

_TAG = "[20260803204235_mvs_audit_03_shadowed_gaps down]"

_DROPS = (
    "DROP TABLE IF EXISTS rfi_workbench_exports",
    "DROP TABLE IF EXISTS rfi_workbench_sections",
    "DROP TABLE IF EXISTS rfi_workbench_sessions",
    "DROP TABLE IF EXISTS sso_sessions",
    "DROP TABLE IF EXISTS sso_providers",
    "DROP TABLE IF EXISTS memory_fts",
)


def down(conn=None) -> dict:
    from tools.db.storage import get_connection

    own = conn is None
    conn = conn or get_connection()
    dropped = []
    try:
        for stmt in _DROPS:
            try:
                conn.execute(stmt)
                dropped.append(stmt.rsplit(" ", 1)[-1])
            except Exception as exc:  # noqa: BLE001
                print(f"{_TAG} {stmt} failed: {exc}")
        conn.commit()
        print(f"{_TAG} dropped {dropped}")
        return {"status": "reverted", "dropped": dropped}
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
