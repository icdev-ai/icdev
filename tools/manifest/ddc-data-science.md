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
**Purpose:** PII detection in data profiles using name heuristics, regex sampling, and high-cardinality free-text detection. Advisory only — never blocks queries.
**Key functions:**
- `check_column(col_name, stats, table_classification)` → finding dict or None
- `scan_profile(profile, classification)` → `{findings, overall_risk, columns_scanned, pii_candidates, scanned_at}`
- `scan_result(col_names, rows, classification)` → `{warnings, has_warnings}` — scans live query result columns (samples up to 20 rows); called by the query sandbox after every execution; **advisory only, never blocks**
- `save_pii_scan(result, design_id)` → persists to `dd_pii_scans`

**PII_COLUMN_PATTERNS (`_PII_HIGH` / `_PII_MEDIUM` sets):**

| Severity | Column name tokens matched |
|----------|---------------------------|
| HIGH | `ssn`, `social_security`, `social_security_number`, `sin`, `tax_id`, `taxpayer_id`, `nin`, `nino`, `passport`, `passport_number`, `drivers_license`, `license_number`, `date_of_birth`, `dob`, `birth_date`, `birthdate`, `biometric`, `fingerprint`, `retina`, `iris` |
| MEDIUM | `email`, `email_address`, `e_mail`, `phone`, `phone_number`, `mobile`, `cell`, `fax`, `telephone`, `address`, `street_address`, `mailing_address`, `zip`, `zipcode`, `postal_code`, `postcode`, `first_name`, `last_name`, `full_name`, `name`, `username`, `user_name`, `login`, `userid`, `user_id`, `ip_address`, `ip_addr`, `ipv4`, `ipv6`, `credit_card`, `card_number`, `cvv`, `pan`, `bank_account`, `account_number`, `routing_number`, `iban`, `gender`, `race`, `ethnicity`, `religion`, `salary`, `income`, `wage`, `location`, `gps`, `latitude`, `longitude`, `device_id`, `mac_address`, `imei`, `cookie` |

**Regex patterns (applied to sampled `top_values`):**

| Pattern name | Regex |
|--------------|-------|
| `_EMAIL_RE` | `[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}` |
| `_PHONE_RE` | `(\+?1?\s?)?(\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}` |
| `_SSN_RE` | `\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b` |
| `_CC_RE` | Luhn-prefix pattern for Visa/MC/Amex/Discover card numbers |

**Severity escalation:** HIGH name token + low classification (public/unclassified) → `high`; HIGH name token + CUI → `medium`; MEDIUM name token or regex match → `medium`; high-cardinality free-text (>1 000 distinct, >80% unique) → `low`.

**Security note:** Advisory-only — `scan_result` warns via the UI PII banner but does **not** block query execution, reject results, or raise exceptions. The banner is dismissible by the user. No actual data rows are stored; only column-level metadata is persisted to `dd_pii_scans`.

**Strategies:** (1) name heuristics (`_PII_HIGH`/`_PII_MEDIUM` sets), (2) regex on `top_values` (email/SSN/CC/phone), (3) high-cardinality free-text heuristic.
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

---

## Data Canvas Core Modules (`tools/data_canvas/` root)

> Reconciliation (dcpr-qa-04, 2026-07-18): these library modules back the primary
> `/data/*` routes but were previously only partially indexed (some in
> `design-canvases.md` / `dashboard.md`). Listed here for shard completeness.
> All are **pure library modules — no CLI entrypoint** (no `argparse` / `__main__`);
> they are imported by `tools/data_canvas/blueprint.py`.

| Module | File | Purpose | CLI | Key functions |
|--------|------|---------|-----|---------------|
| Data Assessment Engine | `tools/data_canvas/data_engine.py` | Compliance assessment engine: 12 deterministic rules (DDC-ENC-001…DDC-DLP-001) across 7 categories → findings, risk_score (0–100), posture_grade (A–F), NIST 800-53 family coverage. Wraps lineage primitives. | No CLI (library) | `assess_data_design(design_id, graph_data, rules=None)`, `analyze_column_lineage`, `detect_data_gaps`, `compute_nist_coverage` |
| Column Lineage Engine | `tools/data_canvas/lineage.py` | Column-level lineage DAG builder, 8 lineage types. Classification-aware (blocks SECRET→UNCLASSIFIED downgrade, CAT1). Max BFS depth 20. Table: `dd_lineage`. | No CLI (library) | `validate_lineage_edge`, `build_column_lineage_dag`, `compute_downstream_impact`, `compute_upstream_provenance`, `summarize_lineage`, `generate_contract_assertions` |
| Data Digital Twin | `tools/data_canvas/twin.py` | Lineage digital twin — snapshot schema+lineage, simulate downstream impact of schema changes, enforce quality gates. Tables: `data_nodes`, `data_edges`, `data_twin_snapshots` (append-only, NIST AU). | No CLI (library) | `take_snapshot(design_id, label, classification)`, `simulate_delta(design_id, schema_changes, classification, baseline_snap_id)`, `quality_gate(design_id, schema_changes)` |
| AI Data Mapper | `tools/data_canvas/ai_mapper.py` | AI-assisted schema-to-schema field mapping. Degrades to name+type similarity when LLM unavailable. Backs `/data/mapping/*`. | No CLI (library) | `parse_schema(raw, fmt)`, `score_field_pairs(src, tgt)`, `assign_status(conf)`, `generate_transforms(session_id, pairs, artifact_type)` |
| Data Mesh (flat) | `tools/data_canvas/data_mesh.py` | Data mesh CRUD for `dm_*` tables: domains, data products, ODCS contracts, maturity scoring. **Shadowed by the `data_mesh/` package dir** but explicitly re-exported via `data_mesh/__init__.py`, so `from tools.data_canvas.data_mesh import …` resolves to this flat module's public API. | No CLI (library) | `create_domain`, `list_domains`, `get_domain`, `create_data_product`, `list_products`, `assess_domain_maturity` |
| Data Mesh Governance (flat) | `tools/data_canvas/governance_engine.py` | Data mesh governance — policy CRUD, ABAC-style `check_access`, and governance scoring. Backs `/data/governance`. Distinct from the OPA-client variant `data_mesh/governance_engine.py` (see `data-mesh.md`). | No CLI (library) | `list_policies`, `create_policy`, `check_access(user_attrs, resource)`, `compute_governance_score(domain_id=None)` |
| Data Mesh CSP Sync | `tools/data_canvas/csp.py` | Data Mesh CSP sync router (AWS DataZone / Azure Purview / GCP Dataplex). Backs `/data/csp`. Delegates to the `data_mesh/csp/` provider adapters (optional boto3/azure/google deps). | No CLI (library) | `get_csp_status()`, `run_sync(provider, domain_ids, dry_run=True)` |

### CLI claims — reconciliation note

The `--output-json` CLIs documented above for `data_profiler.py`, `freshness_guardian.py`,
`mcp_scanner.py`, and `pii_scanner.py` are accurate (each has an `argparse`/`main()` entrypoint).
The remaining data-canvas modules — `data_engine.py`, `quality_engine.py`, `anomaly_detector.py`,
`ai_mapper.py`, `lineage.py`, `twin.py`, `data_mesh.py`, `governance_engine.py`, `csp.py` — are
**library-only (no CLI)**. In particular, `data_engine.py` has **no** `--json` CLI despite the
auto-generated row in `auto-registered.md`; call `assess_data_design()` in-process instead.
