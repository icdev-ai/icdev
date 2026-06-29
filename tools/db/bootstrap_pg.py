#!/usr/bin/env python3
# CUI // SP-CTI
"""Bootstrap a fresh PostgreSQL database from the consolidated schema snapshot.

The historical 212-migration chain is not cleanly replayable on a fresh
PostgreSQL database (inconsistent directive conventions, unsupported flat-file
.py migrations, and severe create-after-alter ordering). For fresh PG installs
(CI E2E, new deployments) this "squash" loads the authoritative consolidated
schema — a pg_dump of the canonical production schema at
``tools/db/schema/pg_consolidated.sql`` — and marks every existing migration as
applied, so ``migrate.py --up`` reports no pending work. New migrations append
and run normally on top of the snapshot.

Regenerate the snapshot after schema changes land in the canonical DB:

    docker exec -e PGPASSWORD=$PW icdev-postgres pg_dump --schema-only \
        --no-owner --no-privileges --no-comments -U icdev -d icdev \
        > tools/db/schema/pg_consolidated.sql

Usage:
    python tools/db/bootstrap_pg.py            # load schema + mark migrations applied
    python tools/db/bootstrap_pg.py --check    # report bootstrap state, do nothing
    python tools/db/bootstrap_pg.py --json
"""
import argparse
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SCHEMA_FILE = BASE_DIR / "tools" / "db" / "schema" / "pg_consolidated.sql"


def _raw_pg_conn():
    """Raw psycopg2 connection — NOT the StorageConnection wrapper, so the
    already-PostgreSQL dump is executed verbatim without SQL translation."""
    import psycopg2

    url = os.environ.get("ICDEV_DATABASE_URL")
    if url:
        return psycopg2.connect(url)
    host = os.environ.get("ICDEV_PG_HOST", "127.0.0.1")
    if host == "localhost":
        host = "127.0.0.1"
    return psycopg2.connect(
        host=host,
        port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
        user=os.environ.get("ICDEV_PG_USER", "icdev"),
        password=os.environ.get("ICDEV_PG_PASSWORD", ""),
        dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
    )


def _strip_psql_meta(sql: str) -> str:
    """Drop psql-only backslash meta-commands (\\restrict, \\unrestrict, …) that
    pg_dump (PG16+) emits — they are not valid SQL for the wire protocol."""
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("\\"))


def _table_count(cur) -> int:
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
    return cur.fetchone()[0]


def check() -> dict:
    conn = _raw_pg_conn()
    try:
        cur = conn.cursor()
        n = _table_count(cur)
        applied = 0
        cur.execute("SELECT to_regclass('public.schema_migrations')")
        if cur.fetchone()[0]:
            cur.execute("SELECT count(*) FROM public.schema_migrations")
            applied = cur.fetchone()[0]
        return {"tables": n, "migrations_applied": applied, "bootstrapped": n > 100}
    finally:
        conn.close()


def bootstrap() -> dict:
    if not SCHEMA_FILE.exists():
        raise SystemExit(f"consolidated schema not found: {SCHEMA_FILE}")

    # Idempotency guard: if another job in the same CI run already bootstrapped
    # (Test job creates tables via init_icdev_db.py before E2E runs), skip the
    # schema load and only ensure migrations are marked. DuplicateTable errors
    # were caused by unconditional CREATE TABLE execution on a non-empty DB.
    state = check()
    if state["bootstrapped"]:
        conn = _raw_pg_conn()
        try:
            cur = conn.cursor()
            cur.execute("SET search_path TO public, pg_catalog")
            from tools.db.migration_runner import MigrationRunner
            runner = MigrationRunner(engine="postgresql")
            versions = sorted({m["version"] for m in runner.discover_migrations()})
            for v in versions:
                cur.execute(
                    "INSERT INTO public.schema_migrations (version, name, checksum, execution_time_ms) "
                    "VALUES (%s, %s, '', 0) ON CONFLICT (version) DO NOTHING",
                    (v, f"squashed-{v}"),
                )
            conn.commit()
            return {
                "status": "already_bootstrapped",
                "tables": state["tables"],
                "migrations_marked": len(versions),
            }
        finally:
            conn.close()

    sql = _strip_psql_meta(SCHEMA_FILE.read_text(encoding="utf-8-sig"))  # utf-8-sig strips any BOM

    conn = _raw_pg_conn()
    try:
        cur = conn.cursor()
        # Load the entire consolidated schema as one batch (psycopg2 sends it via
        # the simple-query protocol when there are no params).
        cur.execute(sql)
        conn.commit()

        # The dump sets search_path='' for safety; restore it so unqualified
        # names resolve for the migration bookkeeping below.
        cur.execute("SET search_path TO public, pg_catalog")

        # Mark every discovered migration as applied so migrate.py --up is a no-op.
        from tools.db.migration_runner import MigrationRunner

        runner = MigrationRunner(engine="postgresql")
        versions = sorted({m["version"] for m in runner.discover_migrations()})
        for v in versions:
            cur.execute(
                "INSERT INTO public.schema_migrations (version, name, checksum, execution_time_ms) "
                "VALUES (%s, %s, '', 0) ON CONFLICT (version) DO NOTHING",
                (v, f"squashed-{v}"),
            )
        conn.commit()

        return {
            "status": "bootstrapped",
            "tables": _table_count(cur),
            "migrations_marked": len(versions),
        }
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="Bootstrap fresh PostgreSQL from consolidated schema")
    ap.add_argument("--check", action="store_true", help="Report state only; do not modify")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = check() if args.check else bootstrap()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
