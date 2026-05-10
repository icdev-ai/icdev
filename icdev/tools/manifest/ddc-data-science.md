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

## anomaly_detector.py

**Path:** `tools/data_canvas/anomaly_detector.py`
**Purpose:** CUSUM-based anomaly detection over data profiles. Deterministic; LLM fallback for summaries.
**Key functions:**
- `detect_anomalies(profile, classification)` → `{anomalies, classification, total_tables, total_columns, summary}`
- `save_anomaly_run(result, profile_id, classification)` → persists to `dd_anomaly_runs`, stamps `anomaly_json` on `dd_explore_profiles`
- `get_latest_run(profile_id)` → returns most-recent run dict or None

**Detection logic:** `abs(val - mean) > 2 * std` over rolling stats window. Flags null_rate, cardinality_ratio, min/max outliers.

---

## freshness_guardian.py

**Path:** `tools/data_canvas/freshness_guardian.py`
**Purpose:** Table/profile staleness checker. Compares last_modified timestamps against configurable stale/critical thresholds.
**Key functions:**
- `check_table_freshness(table_name, last_modified, row_count, stale_hours, critical_hours)` → `{table, status, last_modified, age_hours, row_count, message}`
- `check_profile_freshness(profile, stale_hours, critical_hours)` → iterates all tables; derives `overall_status` (fresh/stale/critical/unknown)
- `save_freshness_run(result, design_id)` → persists to `dd_freshness_runs`

**Thresholds:** Default 24h stale / 168h critical; overridable via `args/data_canvas_config.yaml` under `freshness_guardian:` key.
**CLI:** `--db` or `--profile-json` (mutually exclusive), `--stale-hours`, `--critical-hours`, `--output-json`

---

## mcp_scanner.py

**Path:** `tools/data_canvas/mcp_scanner.py`
**Purpose:** Validates MCP nodes in DDC design graphs. Checks naming conventions, URI formats, duplicate registrations.
**Key functions:**
- `scan_graph(graph)` → `{findings, risk_level, total_mcp_nodes, scan_time}`; checks snake_case tool names, URI format, duplicates, empty nodes
- `scan_design_id(design_id)` → loads graph from DDC DB and calls `scan_graph()`

**Risk levels:** high / medium / low / none (based on max finding severity).
**CLI:** `--graph-json FILE` or `--design-id UUID`, `--output-json`

---

## pii_scanner.py

**Path:** `tools/data_canvas/pii_scanner.py`
**Purpose:** PII detection in data profiles using name heuristics, regex sampling, and high-cardinality free-text detection.
**Key functions:**
- `check_column(col_name, stats, table_classification)` → finding dict or None
- `scan_profile(profile, classification)` → `{findings, total_columns_scanned, pii_columns, classification, scan_time}`
- `save_pii_scan(result, design_id)` → persists to `dd_pii_scans`

**Strategies:** (1) name heuristics (`_PII_HIGH`/`_PII_MEDIUM` sets), (2) regex on `top_values` (email/SSN/CC), (3) high-cardinality free-text heuristic.
**CLI:** `--profile-json` or `--db`, `--classification`, `--output-json`

---

## DB Tables (data_canvas.db)

| Table | Purpose |
|-------|---------|
| `dd_explore_sessions` | Audit trail for profiler sessions |
| `dd_explore_profiles` | Stored profile JSON results per design |
| `dd_query_history` | All executed queries (SQL text, row count, exec_ms) |
| `dd_quality_rules` | Quality rule definitions per design |
| `dd_quality_runs` | Quality check run results (pass/fail, actual_value) |
| `dd_anomaly_runs` | Anomaly detection run results per profile |
| `dd_freshness_runs` | Freshness check run results per design |
| `dd_pii_scans` | PII scan results per design |

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
