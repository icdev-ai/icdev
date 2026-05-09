# CUI // SP-CTI — Feature: DDC Data Science Integration

**Phase:** dds | **Shipped:** 2026-05-09 | **Classification:** CUI // SP-CTI

## Summary

Three data science tabs integrated into the Data Design Canvas (`/data/`) — closing the gap between schema governance and data analysis without leaving the ICDEV™ platform.

## Features Shipped

### Epic 1 — Data Explorer (`/data/explore`)

Classification-aware data profiler. Connect to any SQLite, PostgreSQL, or DuckDB database and automatically profile every table:

- Row counts, null%, distinct count per column
- Min/Max for numeric and datetime columns
- Top-10 value distribution (for low-cardinality / string columns)
- Null-rate progress bar (green < 5%, orange 5–20%, red > 20%)
- **Classification inheritance**: output inherits the CUI/PII/PHI marking from the connected design automatically

Profile results stored in `dd_explore_profiles` with full audit trail in `dd_audit`.

### Epic 2 — SQL Sandbox (`/data/query`)

Read-only SQL editor. SELECT/WITH/EXPLAIN only — DDL/DML is blocked at both validation and keyword-scan layers:

- SQL textarea with Ctrl+Enter shortcut
- Results grid with column headers, row pagination (100/page), CSV export
- Query history (last 50 queries, clickable to reload)
- Classification badge on every result set
- All executed queries stored in `dd_query_history` with row count + exec_ms

Security: `validate_query()` enforces first-word allowlist AND keyword blacklist (`_FORBIDDEN_KEYWORDS` regex). Non-SELECT → HTTP 400.

### Epic 3 — Data Quality Rules (`/data/quality`)

Define, run, and track quality expectations. Five check types:

| Check | Logic |
|-------|-------|
| `completeness` | % non-null ≥ threshold |
| `uniqueness` | % distinct ≥ threshold |
| `range` | MIN ≥ min_val AND MAX ≤ max_val |
| `pattern` | % rows matching regex ≥ threshold |
| `freshness` | MAX(col) ≥ NOW − N days |

- Quality score gauge (0–100%, color-coded green/orange/red)
- Rule builder form — design, table, column, check type, threshold
- Rules table with enabled toggle and delete
- Run history with pass/fail badges
- **Assessment bridge**: `run_all_rules()` results feed DDC-QUA-001 finding in the existing Assessment engine; designs scoring < 70 generate a CAT-2 finding

## Architecture

```
tools/data_canvas/
  data_profiler.py      # Pure-function profiler (sqlite/postgresql/duckdb)
  query_sandbox.py      # Read-only SQL executor with validation
  quality_engine.py     # Quality rule runner (5 check types)
  db/init_db.py         # +5 new tables (dd_explore_*, dd_query_history, dd_quality_*)
  blueprint.py          # +13 new routes (/explore, /query, /quality + APIs)
  constants.py          # DS_CHECK_TYPES, DS_DB_TYPES, DS_PROFILER_MAX_ROWS, DS_QUERY_MAX_ROWS

tools/dashboard/templates/data_canvas/
  explore.html           # Profiler UI
  query.html             # SQL sandbox UI
  quality.html           # Quality rules UI
  index.html             # +3 nav buttons (Explore, Query, Quality)

icdev/tools/dashboard/templates/data_canvas/
  [mirrored]            # explore.html, query.html, quality.html, index.html

args/data_canvas_config.yaml  # +explore/query/quality config sections
tools/manifest/ddc-data-science.md  # Manifest shard
```

## DB Tables Added

| Table | Purpose |
|-------|---------|
| `dd_explore_sessions` | Profiler session audit |
| `dd_explore_profiles` | Profile JSON results + table count |
| `dd_query_history` | SQL history (text, rows, exec_ms, classification) |
| `dd_quality_rules` | Quality rule definitions per design |
| `dd_quality_runs` | Check results (passed, actual_value, detail) |

## E2E Verification (2026-05-09)

- 17/18 Playwright checks passed (1 false-negative: success banner hidden by 800ms auto-reload)
- DB persistence verified: 1 rule, 1 run (100% pass), 1 profile (21 tables), 2 query history entries
- Coherence checker: PASS
- Companion sync: 10 platforms, 63 skills translated

## Compliance

- All audit writes use `dd_audit` (immutable — NIST AU-6 trigger enforced)
- Classification inherited throughout — no unmarked outputs
- SQL sandbox enforces read-only at validation layer (no `PRAGMA query_only` bypass possible)
- Quality scores below 70 generate DDC-QUA-001 finding in existing Assessment pipeline
