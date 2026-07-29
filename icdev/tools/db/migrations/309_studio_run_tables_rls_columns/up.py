# CUI // SP-CTI
"""Migration 309 — RLS columns on studio_workflow_runs / studio_workflow_run_steps.

Migration 305 backfilled `classification` and `tenant_id` onto studio_workflows
and stopped there. The two run tables never got them, so `get_connection()` —
which attaches the global tenant/classification predicate whenever it runs
inside a request context — produced SQL those tables cannot satisfy:

    sqlite3.OperationalError: no such column: classification
      tools/studio/workflow_runner.py::get_run

The same read succeeds OUTSIDE a request context, which is why the pytest layer
never caught it: only the browser and the E2E specs 500. That is the KNOWN
BLOCKER recorded in tests/e2e/dwo_restart_durability.spec.ts and the reason all
three DWO specs are still held out of the shared CI sweep (dwo-vv-03-d5).

The development database already had these columns added by hand at some point,
which masked the defect there. A FRESH install — CI, and the child dashboard the
restart spec spawns — does not, so it 500s. Nothing in init_db.py or any
migration created them until now.

Defaults mirror studio_workflows exactly: classification NOT NULL DEFAULT 'CUI'
so pre-existing rows stay readable under an exact-match predicate, tenant_id
nullable so single-tenant installs are unaffected.

Same shape as migrations 162 and 305: PG takes IF NOT EXISTS, SQLite has no such
clause on ADD COLUMN, so duplicate-column errors are tolerated instead — which
also makes this safe to run against the hand-patched dev database.
"""
from tools.db.storage import get_connection, is_pg

_TABLES = ("studio_workflow_runs", "studio_workflow_run_steps")


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
    print("Migration 309 applied.")
