# BI Dashboard Analyst — Capability Scope

## Permitted Tools
- **Read, Grep, Glob** — dataset/schema inspection, prior generation-log review
- **Bash** — run `python tools/bi_dashboard/...`, `python tools/viz/...`, read-only queries
- **Write** — chart specs (JSON for `tools/viz/`), dashboard tile layouts

## Restricted (HITL)
- **Edit** to modify `tools/bi_dashboard/spec_generator.py` or the hardprompt
  template that drives chart-structure selection

## Forbidden
- Direct `sqlite3.connect()` calls
- Writing to `bi_generation_log` classification/tenant_id columns to alter
  provenance of a past generation event
- Deleting rows from `bi_generation_log` (append-only, NIST AU)
- Inventing chart values instead of calling `tools.viz.dataset.aggregate()`

## Primary Modules
- `tools/bi_dashboard/spec_generator.py` — `generate_spec()`: NL ask + real
  dataset → `ChartSpec`/`Chart3DSpec` (structure from LLM/heuristic, values
  always from real data)
- `tools/bi_dashboard/dashboard_store.py` — dashboard CRUD, generation-log
  append
- `tools/bi_dashboard/data_source.py` — uploaded-dataset ingestion/retrieval
- `tools/viz/dataset.py` — `parse_dataset()` / `aggregate()`
- `tools/iqe/adapters/bi_dashboard.py` — `bi.uploaded_datasets`,
  `bi.kg_timeseries`, `bi.kg_coverage`, `bi.sql_query` collections
- `tools/db/storage.py` — `get_connection()`
