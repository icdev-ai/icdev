# Migration Canvas

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Migration Canvas

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Blueprint | tools/migration_canvas/blueprint.py | Flask Blueprint for ICDEV™ Migration Design Canvas; self-contained at /migration-canvas/ with separate database and ICDEV_MIGRATION_CANVAS_ENABLED feature flag | (library) | create_migration_blueprint() |
| SOPs | tools/migration_canvas/sops.py | Standard Operating Procedures CRUD and approval workflow for Migration Design Canvas (readiness assessment, cutover planning, rollback, validation) | (library) | SOP CRUD functions |

