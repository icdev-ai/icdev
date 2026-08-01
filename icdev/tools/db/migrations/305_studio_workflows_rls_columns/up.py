# CUI // SP-CTI
"""Migration 305 — Backfill RLS columns (classification, tenant_id) onto studio_workflows.

`workflow_editor.create_workflow()` stamps the caller's classification onto the
row so the exact-match RLS predicate injected by `get_connection()` lets that
same caller read it back. The column it writes to was never added to the schema
in `tools/studio/init_db.py`, so on any database built from the canonical schema
the INSERT raised

    sqlite3.OperationalError: table studio_workflows has no column named classification

and `POST /api/studio/workflows` answered 500 — every time, for every caller.

Same shape as migration 162 (agents): PG takes IF NOT EXISTS, SQLite has no such
clause on ADD COLUMN, so duplicate-column errors are tolerated instead.
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()
    try:
        if is_pg():
            conn.execute(
                "ALTER TABLE studio_workflows "
                "ADD COLUMN IF NOT EXISTS classification TEXT NOT NULL DEFAULT 'CUI'"
            )
            conn.execute("ALTER TABLE studio_workflows ADD COLUMN IF NOT EXISTS tenant_id TEXT")
        else:
            for ddl in [
                "ALTER TABLE studio_workflows "
                "ADD COLUMN classification TEXT NOT NULL DEFAULT 'CUI'",
                "ALTER TABLE studio_workflows ADD COLUMN tenant_id TEXT",
            ]:
                try:
                    conn.execute(ddl)
                except Exception as exc:  # noqa: BLE001
                    if "duplicate column" not in str(exc).lower():
                        raise
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    up()
    print("Migration 305 applied.")
