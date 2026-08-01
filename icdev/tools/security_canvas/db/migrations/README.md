<!-- CUI // SP-CTI -->
# Security Design Canvas — DB migrations (reference)

This directory holds a **canvas-local reference copy** of the SDC core schema so
the unified component registry's completeness path is real:

```yaml
# args/component_registry.yaml  (sdc entry)
completeness:
  db_migration: tools/security_canvas/db/migrations
```

## Authoritative migration

The schema is actually applied by the **main migration chain**, not from this
directory. The canonical migration is:

- **`tools/db/migrations/272_security_canvas_core.sql`**

That file is discovered and applied by `tools/db/migration_runner.py`
(`MigrationRunner`). Apply a single migration with
`MigrationRunner.apply_migration(...)` — never `migrate --target N`, which
applies *all* pending migrations.

`001_security_canvas_core.sql` here is a byte-for-byte copy of migration 272's
DDL, kept for reference/traceability. It is **not** discovered by the global
runner (the runner scans `tools/db/migrations/` only).

## Relationship to the runtime initializer

`tools/security_canvas/db/init_db.py` remains the runtime source of truth and
also **seeds** ZIG pillars/capabilities/activities and the template/snippet
rows. Migration 272 creates the same core tables (idempotent,
`CREATE TABLE IF NOT EXISTS`) but performs **no** data seeding, so the two
coexist safely — running either first is fine.

## Dialect

DDL is authored in the portable dialect used throughout the main chain. On
PostgreSQL the migration runner translates it (e.g.
`INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY`). The PostgreSQL
consolidated baseline (`tools/db/schema/pg_consolidated.sql`) carries a
PG-native form of the same tables (applied raw, so `SERIAL PRIMARY KEY` is
written out explicitly there).
