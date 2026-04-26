# Database

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Database
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Init ICDEV™ DB | tools/db/init_icdev_db.py | Initialize ICDEV™ operational database (176 tables) — detects migration system (D150) | --db-path, --reset | Confirmation + table list |
| Migration Runner | tools/db/migration_runner.py | Lightweight DB migration framework (D150) — schema versioning, checksums, dual-engine | (library) | MigrationRunner class |
| Migrate CLI | tools/db/migrate.py | CLI wrapper for migration runner | --status, --up, --down, --create, --validate, --mark-applied, --all-tenants | Status / results |
| Backup Manager | tools/db/backup_manager.py | Database backup/restore with WAL-safe sqlite3.backup() API (D152) | (library) | BackupManager class |
| Backup CLI | tools/db/backup.py | CLI wrapper for backup manager | --backup, --restore, --verify, --list, --prune, --all, --tenants | Backup metadata / results |
| Storage | tools/db/storage.py | Dual-backend storage abstraction (PostgreSQL primary, SQLite fallback) with transparent SQL translation | (library) | get_connection() |


## Database (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Migrate Add Missing Columns | tools/db/migrate_add_missing_columns.py | Add missing columns migration utility | --json | Migration results |
| Migrate FathomDesk | tools/db/migrate_fathomdesk.py | Idempotent migration of ad_*, kg_*, and trading_daemon_* tables from legacy data/fathomdesk.db (SQLite) into the main ICDEV database (SQLite or PostgreSQL) | --source, --json, --gate | Migration results; exit 1 on --gate + failures |
| Migrate to Storage | tools/db/migrate_to_storage.py | Migrate to centralized storage module | --json | Migration results |
| PG Init | tools/db/pg_init.py | PostgreSQL database initialization | --json | Initialization status |
| PG Optimize All | tools/db/pg_optimize_all.py | PostgreSQL optimization for all tables | --json | Optimization results |
| PG Optimize DataBridge | tools/db/pg_optimize_databridge.py | PostgreSQL optimization for DataBridge tables | --json | Optimization results |
| Migration 018 — Reflex Observations | tools/db/migrations/018_reflex_observations.py | Idempotent DDL: creates `reflex_observations` table + 2 indexes for Genesis reflex execution telemetry (name, started_at, finished_at, duration_ms, status, artifact_count, error_msg, result_json) | (run via migrate.py --up) | Table + indexes created |


## Database Migrations
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Migration 018 — reflex_observations | tools/db/migrations/018_reflex_observations.py | Create reflex_observations table for tracking reflex execution telemetry (name, timing, status, artifact count, errors) | (library / `python … up()`) | Applies DDL; prints confirmation |
| Migration 019 — backlog_task_reassign | tools/db/migrations/019_backlog_task_reassign.py | Re-assign orphaned backlog tasks (task-XXXXXXXX / diag-XXXXXXXX) to project-scoped IDs matching args/projects.yaml | (library / `python … up()`) | Applies data migration; prints confirmation |
| Migration 020 — nc_topologies_schema | tools/db/migrations/020_nc_topologies_schema.py | Register nc_topologies table in icdev.db so the orphan_db_table gap rule resolves; primary schema lives in network_canvas.db | (library / `python … up()`) | Applies DDL; prints confirmation |
