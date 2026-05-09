# DDX Extensions — Data Design Canvas (DDC) Data Science Pages

**CUI // SP-CTI**
**Phase:** DDX | **Status:** COMPLETE | **Verified:** 2026-05-09

---

## Summary

Three new Data Design Canvas (DDC) pages extending the data science workflow: classification-aware data profiling (Explore), read-only SQL sandbox with PII detection (Query), and automated quality rules engine (Quality).

---

## Pages Shipped

### 1. `/data/explore` — Data Explorer (Profiler)

- SQLite / PostgreSQL / DuckDB connection form with design selector
- Runs table-level profiling: row counts, column counts, null rates, cardinality, top values
- Results inherit CUI/PII/PHI markings from the linked design automatically
- Expandable per-table column drill-down
- Anomaly detection via **Analyze** button (LLM-backed anomaly findings)
- Recent profiles history panel (last 20 runs)
- **Verified:** 23 tables profiled in 83ms, CUI marking applied, Analyze flow triggered

### 2. `/data/query` — SQL Sandbox

- Read-only SQL editor (SELECT / WITH / EXPLAIN only — max 1,000 rows)
- Ctrl+Enter keyboard shortcut to run
- Results table with Export CSV
- **PII/CUI sensitivity detection**: scans column names against CUI patterns; surfaces inline warning banner with column-level confidence (e.g. `name: NAME (MEDIUM)`)
- Query history panel with snippet library
- Classification marking auto-applied to results
- **Verified:** Query executed in 1ms, PII warning fired for `name` column, 10 rows returned with CUI badge

### 3. `/data/quality` — Data Quality Rules

- Define completeness, uniqueness, range, pattern, and freshness rules per table/column
- Check types: `completeness`, `uniqueness`, `range`, `pattern`, `freshness`
- Configurable threshold (%) per rule
- **Run All** executes all enabled rules against the live DB and streams score update
- Quality Score gauge auto-refreshes after each run
- **Freshness Monitor** panel: sourced from `dd_freshness_alerts`, populated by `FreshnessGuardianReflex` (hourly cadence)
- Run History table with per-rule pass/fail, actual vs threshold, detail text
- Feeds Assessment engine (DDC-QUA-001)
- **Verified:** Rule created (`e2e_completeness_test`, dd_audit.action, 95%), Run All executed 2 rules → score 100%

---

## V&V Results (2026-05-09)

| Check | Result |
|---|---|
| Coherence checker (19 checks) | ✓ 19/19 PASS |
| Companion sync (10 platforms, 63 skills) | ✓ COMPLETE |
| Component indexer | ✓ 1712 nodes (21 skills, 24 MCP, 8 canvas, 66 goals, 1552 tools, 41 reflexes) |
| Health prober | ✓ 1541 ok / 60 degraded (module imports + HTTP routes not in this env) |
| E2E `/data/explore` — profile flow | ✓ PASS |
| E2E `/data/explore` — analyze flow | ✓ PASS |
| E2E `/data/query` — SQL execution | ✓ PASS |
| E2E `/data/query` — PII warning | ✓ PASS |
| E2E `/data/quality` — add rule | ✓ PASS |
| E2E `/data/quality` — run all rules | ✓ PASS |
| E2E `/data/quality` — freshness monitor | ✓ PASS (no alerts, as expected) |

---

## Screenshots

- `playwright/screenshots/ddx-explore-profile.png` — Data Explorer with 23-table profile
- `playwright/screenshots/ddx-query-pii-warning.png` — SQL Sandbox PII banner triggered
- `playwright/screenshots/ddx-quality-rules-run.png` — Quality Rules 100% score after run

---

## DB Tables

| Table | Purpose |
|---|---|
| `dd_explore_sessions` | Profile session metadata |
| `dd_explore_profiles` | Per-table profile results (JSON) |
| `dd_query_history` | SQL query audit log |
| `dd_snippets` | Saved SQL snippets |
| `dd_quality_rules` | Rule definitions |
| `dd_quality_runs` | Per-rule run results |
| `dd_freshness_alerts` | FreshnessGuardianReflex outputs |

---

## Classification

All pages carry CUI // SP-CTI banner/footer. Profile results, query results, and quality scores inherit classification from the linked data design. PII/CUI column detection runs client-side on column names before results render.
