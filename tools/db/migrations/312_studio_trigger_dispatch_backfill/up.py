# CUI // SP-CTI
"""Migration 312 — the three columns migration 308 was recorded as having added.

``schema_migrations`` claimed 308 was applied on the canonical PostgreSQL
database, but only five of its eight columns existed. The row's name was
``squashed-308``: the marker ``bootstrap_pg.py`` writes for a *baseline* entry.
Before that bootstrap was fixed it marked every migration on disk as applied
without running any of them, so 308's DDL never executed anywhere, and the five
columns that do exist are there only because migration 310 adds them
conditionally. The three 310 does not cover were simply absent:

    studio_event_sources.max_il
    studio_workflow_triggers.workflow_il
    studio_workflow_triggers.project_id

This matters beyond tidiness, because the readers default rather than fail::

    tools/studio/event_dispatch.py:230
        workflow_il = trigger.get("workflow_il") or "IL6"

``IL6`` is the top of ``IL_ORDER``, so ``classification_allows(event_il, "IL6")``
is true for every classification. A missing column therefore does not raise or
refuse — it silently makes the classification gate **fail open**, admitting
events a trigger's real IL rating would have refused. ``max_il`` defaults the
other way (``IL2``), understating a source's ceiling. Neither is visible in
behaviour; both look like a working system.

Any database created from a consolidated snapshot between migrations 302 and 311
has this gap, which is why this is a migration rather than a one-off fix applied
to one server.

Conditional and idempotent, matching 310 and 311: safe on a database that
already has the columns, on one that lacks them, and on a fresh install where
init_db.py has already declared them.
"""
from tools.db.storage import get_connection, is_pg

#: table -> (column, DDL type + default) exactly as 308 declares them.
_ADDITIONS = {
    "studio_event_sources": (
        ("max_il", "TEXT DEFAULT 'IL2'"),
    ),
    "studio_workflow_triggers": (
        ("workflow_il", "TEXT DEFAULT 'IL6'"),
        ("project_id", "TEXT DEFAULT 'default'"),
    ),
}


def _columns(conn, table: str) -> set:
    try:
        if is_pg():
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table,),
            ).fetchall()
            return {dict(r)["column_name"] for r in rows}
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {dict(r).get("name") for r in rows}
    except Exception:
        return set()


def up(conn=None) -> None:
    conn = get_connection()
    try:
        for table, additions in _ADDITIONS.items():
            cols = _columns(conn, table)
            if not cols:
                continue  # table not created yet; init_db/304 will build it correctly
            for name, ddl_type in additions:
                if name not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    up()
    print("Migration 312 applied.")
