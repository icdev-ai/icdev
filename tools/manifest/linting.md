# Linting

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Linting
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SQLite3 Connect Linter | tools/lint/sqlite3_connect_linter.py | Scans tools/ for bare sqlite3.connect() calls that bypass get_connection(); exempt files: tools/db/storage.py, init_icdev_db.py, migration_runner.py, backup_manager.py, */init_db.py; lines with # sqlite3-ok are skipped | --path, --json, --gate | Violation list; exit 1 on --gate + violations |
| PG Portability Linter | tools/lint/pg_portability_linter.py | AST-scans runtime tools/ for PG-unsafe SQLite-dialect SQL the translator should not handle at runtime: json_each, nested json_array_length(json_extract(...)) (high); standalone json_extract/json_array_length + PRAGMA (medium); runtime sqlite3.connect (high, reuses sibling exemptions). Ignores comments/docstrings; excludes init_db.py/seeds/migrations/schema/lint/tests; inline `# pg-ok` opt-out. Baseline allowlist (tools/lint/pg_portability_baseline.json) snapshots existing debt; only NEW high findings fail. | --path, --files, --json, --baseline, --write-baseline, --gate | {file,line,pattern,severity,fix} findings; exit 1 on high-severity above baseline |
| Canvas Table Collision Auditor | tools/lint/canvas_table_collision_auditor.py | PGP-sch-03 detector: parses `tools/<canvas>/db/init_db.py` CREATE TABLE statements and compares against live `information_schema.columns`; groups by table name, flags divergent (same name + different column signatures - high severity, must namespace per pgp-sch-02) vs benign-shared (same name + identical columns - info only). Live PG scan reads scope=public by default. Feeds pgp-sch-02 and pgp-gate-01. | --scope, --json, --md, --canvas | JSON {static_module_audit, live_pg_audit} or markdown summary; exit 1 on divergent_count > 0 |

