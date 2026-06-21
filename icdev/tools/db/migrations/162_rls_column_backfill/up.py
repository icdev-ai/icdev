# CUI // SP-CTI
"""Migration 162 — Backfill RLS columns (classification, tenant_id) onto agents table.

The agents table was created before RLS was enforced on core tables.
Adding these columns makes agents.* queries compatible with the
get_connection() security-context predicate injector.
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()

    if is_pg():
        conn.execute(
            "ALTER TABLE agents ADD COLUMN IF NOT EXISTS classification TEXT NOT NULL DEFAULT 'CUI'"
        )
        conn.execute(
            "ALTER TABLE agents ADD COLUMN IF NOT EXISTS tenant_id TEXT"
        )
    else:
        # SQLite: ignore "duplicate column name" errors
        for ddl in [
            "ALTER TABLE agents ADD COLUMN classification TEXT NOT NULL DEFAULT 'CUI'",
            "ALTER TABLE agents ADD COLUMN tenant_id TEXT",
        ]:
            try:
                conn.execute(ddl)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 162 applied.")
