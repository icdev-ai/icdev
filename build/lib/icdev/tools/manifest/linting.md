# Linting

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Linting
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SQLite3 Connect Linter | tools/lint/sqlite3_connect_linter.py | Scans tools/ for bare sqlite3.connect() calls that bypass get_connection(); exempt files: tools/db/storage.py, init_icdev_db.py, migration_runner.py, backup_manager.py, */init_db.py; lines with # sqlite3-ok are skipped | --path, --json, --gate | Violation list; exit 1 on --gate + violations |

