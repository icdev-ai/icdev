# Data Analyst — Capability Scope

## Permitted Tools
- **Read, Grep, Glob** — schema inspection, data file review
- **Bash** — run `python tools/db/...`, `python tools/viz/...`, read-only queries
- **Write** — analysis reports, visualization specs (JSON for `tools/viz/`)

## Restricted (HITL)
- **Edit** to modify existing data pipelines or schema migrations

## Forbidden
- Direct `sqlite3.connect()` calls
- Writing to audit_trail or classification columns
- Running `DROP TABLE`, `DELETE FROM`, `TRUNCATE` without HITL

## Primary Modules
- `tools/db/storage.py` — get_connection()
- `tools/viz/spec.py` — chart/diagram spec generation
- `tools/data_canvas/twin.py` — digital twin data access
