# DDC Data Science — Explore, Query Sandbox, Quality Rules

> Manifest shard for the Data Design Canvas data science extensions (Epic dds-*).
> Added 2026-05-09. Three new modules integrated into `/data/` at classification CUI // SP-CTI.

## data_profiler.py

**Path:** `tools/data_canvas/data_profiler.py`
**Purpose:** Classification-aware data profiler. Pure functions, no Flask, no LLM.
**Key functions:**
- `profile_database(conn_params, classification, tables)` → full DB profile dict
- `profile_table(conn_params, table, classification)` → single table profile
- `list_tables(conn_params)` → list table names from connected DB
- `_open_connection(conn_params)` → DB-API 2.0 connection (sqlite/postgresql/duckdb)

**Classification:** Every returned object inherits the `classification` param. Column profiles include per-column marking when overridden.
**Air-gap safe:** Uses stdlib sqlite3 by default; psycopg2/duckdb optional.

---

## query_sandbox.py

**Path:** `tools/data_canvas/query_sandbox.py`
**Purpose:** Read-only SQL sandbox. Validates (SELECT/WITH/EXPLAIN only) and executes queries. Hard row limit: 1,000.
**Key functions:**
- `validate_query(sql)` → `{valid, error}` — rejects DDL/DML via keyword blacklist + first-word check
- `execute_query(sql, conn_params, classification)` → `{columns, rows, row_count, exec_ms, classification}`

**Security:** `_FORBIDDEN_KEYWORDS` regex blocks INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/CREATE/GRANT/REVOKE/ATTACH/PRAGMA/VACUUM. `_ALLOWED_STARTS` set enforces SELECT/WITH/EXPLAIN only.

---

## quality_engine.py

**Path:** `tools/data_canvas/quality_engine.py`
**Purpose:** Data quality rule evaluator. Five check types feed results into DDC-QUA-001 assessment rule.
**Key functions:**
- `validate_rule(rule)` → `{valid, error}`
- `run_rule(rule, conn_params)` → `{passed, actual_value, threshold, detail, classification}`
- `run_all_rules(design_id, conn_params, ddc_conn)` → list of `{rule, result}`, persists to `dd_quality_runs`
- `quality_score(run_results)` → float 0–100

**Check types** (in `DS_CHECK_TYPES` constant):
| Type | Logic |
|------|-------|
| completeness | % non-null ≥ threshold |
| uniqueness | % distinct ≥ threshold |
| range | MIN ≥ params.min_val AND MAX ≤ params.max_val |
| pattern | % rows matching regex ≥ threshold |
| freshness | MAX(col) ≥ NOW − threshold days |

---

## DB Tables (data_canvas.db)

| Table | Purpose |
|-------|---------|
| `dd_explore_sessions` | Audit trail for profiler sessions |
| `dd_explore_profiles` | Stored profile JSON results per design |
| `dd_query_history` | All executed queries (SQL text, row count, exec_ms) |
| `dd_quality_rules` | Quality rule definitions per design |
| `dd_quality_runs` | Quality check run results (pass/fail, actual_value) |

---

## Routes (blueprint.py)

| Route | Handler | Notes |
|-------|---------|-------|
| GET `/data/explore` | `dc_explore` | Page |
| POST `/data/api/explore/profile` | `dc_api_explore_profile` | Runs profiler, stores profile |
| GET `/data/api/explore/profiles` | `dc_api_explore_list` | List profiles |
| GET `/data/api/explore/sessions` | `dc_api_explore_sessions` | List sessions |
| GET `/data/query` | `dc_query` | Page |
| POST `/data/api/query/execute` | `dc_api_query_execute` | Run SQL, store in history |
| GET `/data/api/query/history` | `dc_api_query_history` | List query history |
| GET `/data/quality` | `dc_quality` | Page |
| GET `/data/api/quality/rules` | `dc_api_quality_rules_list` | List rules |
| POST `/data/api/quality/rules` | `dc_api_quality_rules_create` | Create rule |
| DELETE `/data/api/quality/rules/<id>` | `dc_api_quality_rules_delete` | Delete rule |
| POST `/data/api/quality/run` | `dc_api_quality_run` | Run all rules for design |
| GET `/data/api/quality/runs` | `dc_api_quality_runs_list` | List run history |

---

## Constants (`tools/data_canvas/constants.py`)

```python
DS_CHECK_TYPES = ["completeness", "uniqueness", "range", "pattern", "freshness"]
DS_DB_TYPES    = ["sqlite", "postgresql", "duckdb"]
DS_PROFILER_MAX_ROWS = 50_000
DS_QUERY_MAX_ROWS    = 1_000
```

## Config (`args/data_canvas_config.yaml`)

```yaml
explore:
  enabled: true
  max_rows_profile: 50000
  top_values_limit: 10

query:
  enabled: true
  max_rows: 1000
  allowed_statement_types: [SELECT, WITH, EXPLAIN]

quality:
  enabled: true
  check_types: [completeness, uniqueness, range, pattern, freshness]
  default_threshold: 90.0
  min_score_gate: 70
  assessment_rule_id: DDC-QUA-001
```
