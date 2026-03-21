# PostgreSQL Compatibility Hardening

**Date:** 2026-03-21
**Classification:** CUI // SP-CTI
**ADRs:** D-DB-26, D-DB-27, D-DB-28

## Summary

Extended the SQL translation layer in `tools/db/storage.py` with 4 new rules, eliminated 8 direct `last_insert_rowid()` calls, and migrated 8 production tools from direct `sqlite3.connect()` to the abstraction layer. Migration readiness improved from 65% to ~90%.

## Changes

### 1. SQL Translation Layer Extensions (D-DB-26)
**File:** `tools/db/storage.py`
- Step 10: `LIKE` → `ILIKE` (case-insensitive matching for PG)
- Step 11: `GROUP_CONCAT(col, sep)` → `string_agg(col::text, sep)`
- Step 12: `GLOB` → `~` (PostgreSQL regex match)
- Step 13: `last_insert_rowid()` → `lastval()`
- Total: 13 translation rules now active

### 2. Eliminated last_insert_rowid() SQL (D-DB-27)
8 files updated from `SELECT last_insert_rowid()` to `cursor.lastrowid`:
- `tools/ci/triggers/gitlab_task_monitor.py`
- `tools/compliance/ai_incident_response.py`
- `tools/compliance/fips199_categorizer.py`
- `tools/mcp/knowledge_server.py`
- `tools/memory/auto_capture.py`
- `tools/supply_chain/cve_triager.py`
- `tools/supply_chain/dependency_graph.py`
- `tests/test_time_decay.py`

### 3. Migrated sqlite3.connect() Calls (D-DB-28)
8 production tools migrated to `get_connection()`:
- `tools/autoresearch/experiment_engine.py`
- `tools/finetune/kg_pair_generator.py`
- `tools/finetune/quality_monitor.py`
- `tools/finetune/rag_ft_pipeline.py`
- `tools/knowledge_graph/compliance_graph.py`
- `tools/knowledge_graph/disambiguator.py`
- `tools/knowledge_graph/enricher.py`
- `tools/marketplace/openclaw_bridge.py`

11 files intentionally NOT migrated (infrastructure: init, migration, backup, code generators, sqlite_tracer).

## Migration Readiness

| Category | Before | After |
|----------|--------|-------|
| SQL translation rules | 9 | 13 |
| Direct `last_insert_rowid()` SQL | 8 | 0 |
| Direct `sqlite3.connect()` in business tools | 8 | 0 |
| Overall PG readiness | ~65% | ~90% |
