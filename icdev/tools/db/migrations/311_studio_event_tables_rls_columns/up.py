# CUI // SP-CTI
"""Migration 311 — RLS columns on the three studio event tables.

The third and last instalment of the same omission. Migration 305 gave
``studio_workflows`` ``classification``/``tenant_id``; migration 309 gave them to
the two run tables; the event tables created by migration 304 —
``studio_event_sources``, ``studio_workflow_triggers``, ``studio_trigger_events``
— were never given them at all.

``get_connection()`` attaches the global tenant/classification predicate whenever
it runs inside a request context, so every dashboard read of these tables
produced SQL they cannot satisfy::

    sqlite3.OperationalError: no such column: classification
      tools/studio/event_sources.py::list_event_sources

and because ``list_event_sources`` (like its siblings) catches broadly and
returns ``[]`` so a pre-migration database still renders, the failure was
**totally silent**: the browser showed "no event sources" on an installation
with 29 of them, and the API answered 200. Nothing logged, nothing 500'd. The
whole trigger subsystem was unreachable through the UI while every unit test
stayed green — they run outside a request context, where the predicate is never
attached and the same query succeeds.

Found while building the editor's Triggers panel (dwo-evt-04-d5): the panel was
correct and permanently empty.

Defaults mirror 305 and 309 exactly: ``classification`` NOT NULL DEFAULT 'CUI'
so existing rows stay readable under an exact-match predicate, ``tenant_id``
nullable so single-tenant installs are unaffected.

Same shape as 162 / 305 / 309: PostgreSQL takes IF NOT EXISTS; SQLite has no
such clause on ADD COLUMN, so duplicate-column errors are tolerated instead,
which also makes this safe on a database already patched by hand.
"""
from tools.db.storage import get_connection, is_pg

_TABLES = (
    "studio_event_sources",
    "studio_workflow_triggers",
    "studio_trigger_events",
)


def up(conn=None) -> None:
    conn = get_connection()
    try:
        for table in _TABLES:
            if is_pg():
                conn.execute(
                    f"ALTER TABLE {table} "
                    "ADD COLUMN IF NOT EXISTS classification TEXT NOT NULL DEFAULT 'CUI'"
                )
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id TEXT")
            else:
                for ddl in [
                    f"ALTER TABLE {table} "
                    "ADD COLUMN classification TEXT NOT NULL DEFAULT 'CUI'",
                    f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT",
                ]:
                    try:
                        conn.execute(ddl)
                    except Exception as exc:  # noqa: BLE001
                        if "duplicate column" not in str(exc).lower():
                            raise
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant ON {table} (tenant_id)"
            )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    up()
    print("Migration 311 applied.")
