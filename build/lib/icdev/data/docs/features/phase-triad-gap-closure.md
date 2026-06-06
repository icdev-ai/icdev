# AI/ML Triad Gap Closure — RAG + Knowledge Graph + Fine-Tuning

**Date:** 2026-03-20
**Classification:** CUI // SP-CTI
**ADRs:** D-KARL-5, D-KARL-6, D-KARL-7, D-KARL-8

## Summary

Closed the integration seams between ICDEV™'s three AI/ML subsystems so they feed each other automatically: RAG content flows to fine-tuning, knowledge graph structures generate training pairs, and evaluation metrics trigger retraining.

## What Was Built

### Phase 1: Corrective RAG (P0)
- **File:** `tools/rag/corrective_rag.py`
- Reimplemented missing `parallel_retrieve()` — 3 strategies via ThreadPoolExecutor (RAG vector, GraphRAG, source registry scan)
- Unblocked Pulse researcher and GovCon capability enricher imports

### Phase 2: KG Test Suite + Dashboard (P1)
- **Test:** `tests/test_knowledge_graph.py` — 30+ tests covering text_network, ingester, graph_rag, insight_generator
- **Dashboard:** `tools/dashboard/templates/knowledge_graph.html` — search, graph list, insights, retrieval log
- **Route:** `/knowledge-graph` with 4 API endpoints

### Phase 3: Automated RAG-to-FT Pipeline (P1)
- **File:** `tools/finetune/rag_ft_pipeline.py`
- Detect new chunks → generate pairs → quality filter → auto-approve (threshold 0.8) → retrain trigger
- **DB:** `ft_pipeline_runs` table (append-only)

### Phase 4: KG-to-FT Pair Generation (P2)
- **File:** `tools/finetune/kg_pair_generator.py`
- Three strategies: entity-relationship, community cluster, compliance crosswalk
- Template-based (air-gap safe), deduplicates by content hash

### Phase 5: Entity Enrichment (P2)
- **File:** `tools/knowledge_graph/enricher.py`
- Centrality: weighted degree + betweenness (BFS, deterministic)
- Embeddings: nomic-embed-text via Ollama

### Phase 6: Cross-System Integration Tests (P2)
- **File:** `tests/test_triad_integration.py`
- 5 scenarios validating combined RAG+KG+FT paths

### Phase 7: Quality Feedback Loop (P3)
- **File:** `tools/finetune/quality_monitor.py`
- RAG eval metrics → threshold check → consecutive failure count → retrain recommendation
- **DB:** `ft_quality_snapshots` table (append-only)

## PostgreSQL Compatibility Fix
- Replaced 19 SQL `rowid` references with explicit columns (`last_seen`, `created_at`, `assessed_at`, `id`, `discovered_at`) across 13 files
- Fixed Creative Engine `competitor_discoverer.py` to support multi-domain config resolution

## Config Changes
- `args/finetune_config.yaml` — 3 new sections: `rag_ft_pipeline`, `kg_ft_pipeline`, `quality_feedback`
- `args/knowledge_graph_config.yaml` — `enrichment` section (centrality weights, embedding batch size)
- `context/research/verticals/knowledge_graph.json` — new research vertical

## Innovation Intelligence
- Innovation Engine scan: 10 signals (top: KG Community Fine-Tuning, score 0.684)
- Research Engine session `rsess-7168275cdbc5` created for `knowledge_graph` vertical
- Creative Engine multi-domain resolution fixed

## Test Results
- 69 new tests, all passing
- 0 ruff issues, 0 bandit medium+ issues
