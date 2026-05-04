# AI/ML Triad Medium-Term — Entity Disambiguation, Federation, Temporal Reasoning, Operational Cleanup

**Date:** 2026-03-21
**Classification:** CUI // SP-CTI
**ADRs:** D-KARL-13, D-KARL-14, D-KARL-15

## Summary

Three new KG capabilities (entity disambiguation, cross-project federation, temporal reasoning) plus operational cleanup across the codebase (PostgreSQL compatibility, deprecation fixes, stale artifacts).

## What Was Built

### 1. Entity Disambiguation (D-KARL-13)
- **File:** `tools/knowledge_graph/disambiguator.py` (new)
- Find duplicates via exact label, normalized label, and embedding cosine similarity
- Merge entities: re-point edges, merge properties, add source label as alias
- Add aliases without merging for disambiguation
- Resolve ambiguous labels with context-aware keyword scoring

### 2. Cross-Project Graph Federation (D-KARL-14)
- **File:** `tools/knowledge_graph/federation.py` (new)
- Federated search across all project graphs with dedup and source attribution
- Shared entity discovery between two projects
- Virtual federated views (metadata-only, no data duplication)
- Cross-project compliance coverage matrix

### 3. Temporal Reasoning (D-KARL-15)
- **File:** `tools/knowledge_graph/temporal.py` (new)
- Time-range queries with optional entity_type filter
- Graph evolution time series (day/week/month intervals)
- Recent changes summary grouped by entity type
- Stale entity detection (configurable age threshold)
- Temporal diff between two dates

## Operational Cleanup

| Fix | File | Issue |
|-----|------|-------|
| `cursor.lastrowid` PostgreSQL compat | `tools/registry/learning_collector.py` | Added try/except fallback SELECT |
| `NULLS LAST` SQLite compat | `tools/research/challenge_scorer.py` | Replaced with CASE WHEN pattern |
| Creative Engine KG URLs | `args/creative_config.yaml` | Added knowledge_graph domain block |
| Orphaned .pyc cleanup | `tools/rag/__pycache__/` | Deleted stale corrective_rag.cpython-314.pyc |
| `datetime.utcnow()` deprecation | 3 files | Replaced with `datetime.now(timezone.utc)` |
| Unused `db_path` variables | `tools/rag/retention_manager.py` | Removed 2 unused assignments |
| Unused `Tuple` import | `tools/knowledge_graph/disambiguator.py` | Removed by ruff --fix |
| f-string without placeholders | `tools/research/challenge_scorer.py` | Fixed 3 instances |
