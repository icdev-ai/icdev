# PostgreSQL Schema Consolidation (Migration Squash)

**Status:** Shipped
**Area:** Database / migrations / CI

## Context

The CI **E2E (Playwright)** job initializes a fresh PostgreSQL database by replaying
the full migration chain (`python tools/db/migrate.py --up`). That step had been
failing for a long time at migration `001` with `relation "projects" does not exist`.

Investigation showed the 212-migration chain is **not cleanly replayable on a fresh
PostgreSQL database**:

- A core storage bug — `StorageConnection.executescript` issued a whole-transaction
  `rollback()` on any statement error, discarding every object created earlier and
  cascading "relation … does not exist" through the rest of the script. (Fixed:
  per-statement `SAVEPOINT` isolation in `tools/db/storage.py`.)
- Inconsistent dialect/dirctive conventions across migrations (e.g. migration 166
  relies on a `-- PG_ONLY` directive the runner never implemented; `-- @pg-only`
  vs `-- PG_ONLY BEGIN`).
- **13 flat-file `.py` migrations** the runner's discovery does not execute.
- **Severe create-after-alter ordering** — e.g. the strategos *core tables* are
  created at migration **118** but altered starting at **061**.

The authoritative production PostgreSQL database, by contrast, holds the complete,
correct schema (**1217 tables**).

## Solution

Adopt the standard **"squash migrations"** practice for fresh PostgreSQL installs:

1. **Consolidated snapshot** — `tools/db/schema/pg_consolidated.sql` is a
   `pg_dump --schema-only` of the canonical production schema (no owner/privileges/
   comments). It is the single source of truth for a fresh PG schema.
2. **Bootstrap** — `tools/db/bootstrap_pg.py` loads the snapshot on a fresh PG
   database (stripping pg16 `\restrict` psql meta-commands, executed on a *raw*
   psycopg2 connection so no SQL translation is applied) and then marks every
   discovered migration as applied in `schema_migrations`. Afterward
   `migrate.py --up` reports **No pending migrations**.
3. **CI** — the E2E job now uses the `pgvector/pgvector:pg16` image (the schema
   needs the `vector` extension) and runs `python tools/db/bootstrap_pg.py`
   instead of replaying migrations.

New migrations authored after the snapshot append and run normally on top of it
via `migrate.py --up` (now also supports `--converge` for out-of-order tolerance).

### Regenerating the snapshot

After schema changes land in the canonical DB:

```bash
docker exec -e PGPASSWORD=$PW icdev-postgres pg_dump --schema-only \
  --no-owner --no-privileges --no-comments -U icdev -d icdev \
  > tools/db/schema/pg_consolidated.sql   # write without a BOM
```

## Supporting hardening (kept, complementary)

These fix real bugs and harden the migration/storage layer for incremental
migrations on top of the snapshot (and any `executescript`-on-PG path):

- `executescript` SAVEPOINT isolation + comment/empty handling + autocommit fallback.
- `StorageCursor.executescript` added (was missing).
- `translate_sql`: `ADD COLUMN` → `ADD COLUMN IF NOT EXISTS`; `ALTER TABLE` →
  `ALTER TABLE IF EXISTS` (PG-only; idempotent/order-tolerant).
- Idempotent `schema_migrations` recording (duplicate version numbers, e.g. two `010`s).
- `migrate.py --converge` (multi-pass runner).

## Verification

```bash
# Against a fresh scratch PG database:
python tools/db/bootstrap_pg.py           # -> tables: 1217, migrations_marked: 155
python tools/db/migrate.py --up           # -> "No pending migrations." (exit 0)
```

Validated end-to-end: fresh DB → 1217 tables, all key tables present
(`projects`, `kanban_tasks`, `sg_entities`, `kg_nodes`, `sg_theaters`,
`dashboard_users`), `migrate --up` is a clean no-op.
