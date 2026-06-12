# AI/ML Triad Near-Term — GraphRAG Semantic Search, Compliance KG, HP Search, Insights Dashboard

**Date:** 2026-03-21
**Classification:** CUI // SP-CTI
**ADRs:** D-KARL-9, D-KARL-10, D-KARL-11, D-KARL-12

## Summary

Four near-term Knowledge Graph enhancements: semantic search via embedding cosine similarity, compliance crosswalk as graph, hyperparameter search orchestrator, and KG insights dashboard with dedicated tabs.

## What Was Built

### 1. GraphRAG Semantic Search (D-KARL-9)
- **File:** `tools/knowledge_graph/graph_rag.py`
- Added `_cosine_similarity()` for BLOB-packed float32 vectors
- Added `_embed_query()` via Ollama nomic-embed-text (`/api/embed`)
- Step 2 in `retrieve()` augments keyword LIKE with embedding similarity top-k
- Additive 0.2x embedding similarity scoring bonus in `_score_nodes()`
- Graceful degradation: keyword-only when Ollama unavailable

### 2. Compliance Crosswalk KG (D-KARL-10)
- **File:** `tools/knowledge_graph/compliance_graph.py` (new)
- Models NIST 800-53 controls (117), families (20), frameworks (8) as KG nodes
- Creates `belongs_to`, `satisfies`, `overlaps_with` edges (977 total)
- BFS crosswalk path traversal between controls and frameworks
- Per-framework coverage queries grouped by control family
- Deterministic content-hash IDs for idempotent rebuilds

### 3. Hyperparameter Search Orchestrator (D-KARL-11)
- **File:** `tools/finetune/hp_search.py` (new)
- Grid/random search over LoRA rank, learning rate, epochs, batch size
- Non-blocking design: `run_next_trial()` queues job, caller checks later
- Config-driven from `args/finetune_config.yaml` `hyperparam_search` section
- DB tables: `ft_hp_searches`, `ft_hp_trials`

### 4. KG Insights Dashboard (D-KARL-12)
- **Template:** `tools/dashboard/templates/knowledge_graph.html`
- Tab-based UI: Search, Graphs, Insights, Compliance KG, Query Log
- Insights tab: parallel API calls for summary, bridge gaps, orphans, research questions
- Compliance KG tab: one-click crosswalk graph builder with stats
- New API endpoints: bridge-gaps, questions, orphans, compliance-build

## Test Results

- 5224 passed, 12 skipped, 0 failures
- KG test suite: 31 passed
- All new tools: py_compile clean, ruff clean, bandit clean
