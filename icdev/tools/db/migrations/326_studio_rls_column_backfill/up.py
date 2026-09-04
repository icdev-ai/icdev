# CUI // SP-CTI
"""Migration 326 — finish the studio_* RLS column backfill.

Migration 305 gave `studio_workflows` its RLS columns, 309 did the two run
tables, 311 did the three event/trigger tables. Ten `studio_*` tables were never
covered, and `get_connection()` attaches the global predicate to all of them
whenever it runs inside a request context — producing SQL those tables cannot
satisfy.

Measured against the live database on 2026-08-02, driving a real
SecurityContext rather than reasoning from the schema. Two distinct severities:

    single-tenant ctx (tenant_id=None)   tenant-scoped ctx (tenant_id='acme')
    studio_workflows      OK             studio_workflows      OK
    studio_run_memory     ERR            studio_run_memory     ERR
    studio_cases          OK             studio_cases          ERR
    studio_forms          OK             studio_forms          ERR

* `studio_run_memory` has NEITHER column, so `classification` — which is
  injected for every authenticated caller — already fails. All four statements
  in tools/studio/run_memory.py go through get_connection(), so run memory is
  unreadable and unwritable from any request context today.
* The other nine have `classification` but no `tenant_id`. `tenant_id` is only
  injected when the caller carries one, so these are green for a single-tenant
  install and broken for every multi-tenant one. Latent, not theoretical.

This is the same trap 309 documented: the identical read SUCCEEDS outside a
request context, so the pytest layer never sees it. Only the browser does.

Surfaced by the tsr-canv-01-d2 seed-verification evidence (PR #1140), which
flagged studio_run_memory as out of scope. Probing it found the other nine.

Defaults mirror studio_workflows exactly, as 309 and 311 did: classification
NOT NULL DEFAULT 'CUI' so pre-existing rows stay readable under a read-down
predicate, tenant_id nullable so single-tenant installs are unaffected.

Same portability shape as 162/305/309: PostgreSQL takes IF NOT EXISTS, SQLite
has no such clause on ADD COLUMN, so duplicate-column errors are tolerated
instead — which also makes this safe to re-run and safe against databases where
a column was already added by hand.
"""
from tools.db.storage import get_connection, is_pg

#: Every studio_* table the earlier migrations left uncovered. Both columns are
#: ensured on each — the adds are individually idempotent, so listing a table
#: that already has one of them is a no-op rather than a special case.
#:
#: On the live database these split into two observed severities (run_memory has
#: neither column and fails for everyone; the rest have classification from the
#: schema snapshot and fail only for tenant-scoped callers). That split is an
#: accident of how each database was built, not a property of the code:
#: tools/studio/init_db.py declares NEITHER column for any of these, so a fresh
#: install fails on all ten for every authenticated caller. Hence both columns
#: everywhere rather than encoding one database's history.
_TABLES = (
    "studio_run_memory",
    "studio_automation_runs",
    "studio_automations",
    "studio_case_history",
    "studio_case_types",
    "studio_cases",
    "studio_dashboards",
    "studio_form_submissions",
    "studio_forms",
    "studio_workflow_heal_log",
)


def _add_column(conn, table: str, column: str, definition: str) -> None:
    """Idempotent ADD COLUMN across both backends."""
    if is_pg():
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"
        )
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception as exc:  # noqa: BLE001
        if "duplicate column" not in str(exc).lower():
            raise


def _table_exists(conn, table: str) -> bool:
    """Skip tables absent from this database rather than aborting the run.

    The studio schema is created by tools/studio/init_db.py, not by the
    migration chain, so a database that has never had studio initialised will
    not have these tables at all. That is not an error for this migration.
    """
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchall()
        return True
    except Exception:  # noqa: BLE001
        return False


def up(conn=None) -> None:
    conn = get_connection()
    try:
        for table in _TABLES:
            if not _table_exists(conn, table):
                continue
            _add_column(conn, table, "classification", "TEXT NOT NULL DEFAULT 'CUI'")
            _add_column(conn, table, "tenant_id", "TEXT")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant ON {table} (tenant_id)"
            )
            # Commit per table: a table absent from this database must not roll
            # back the ones already done, and on PostgreSQL a failed statement
            # poisons the whole transaction until it ends.
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    up()
    print("Migration 326 applied.")
