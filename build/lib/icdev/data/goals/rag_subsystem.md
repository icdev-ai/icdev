# CUI // SP-CTI
# RAG Subsystem

> Phase 64 — Universal RAG subsystem for multi-source knowledge ingestion, semantic retrieval, and cross-engine intelligence reuse.

---

## Overview

ICDEV™'s Innovation, Creative, and Research engines produce structured intelligence (signals, pain points, dossiers, forecasts). Compliance artifacts (SSP, POAM, STIG, SBOM) add per-ATO system data. Today, each LLM call starts from scratch with no pre-retrieved context and no cross-engine knowledge reuse. This wastes tokens and misses connections across engines.

The RAG subsystem solves this by: (1) indexing all ICDEV™ data into a unified vector store, (2) auto-retrieving relevant context before every two-tier LLM call, (3) enabling natural language search across all knowledge, and (4) providing child apps with federated knowledge access.

Design principles:
- Air-gap safe: Ollama nomic-embed-text for embeddings, no cloud dependency
- Provider ABC pattern (D66): SQLite default, ChromaDB and FAISS as optional backends
- Zero code duplication: reuses existing BM25 search, time-decay ranking, and embedding infrastructure
- Privacy-preserving: query content stored as SHA-256 hash by default (D282)

## Architecture Decisions

- **D-RAG-1:** VectorStoreProvider ABC with SQLite/ChromaDB/FAISS (D66 pattern). SQLite always available, others graceful ImportError
- **D-RAG-2:** RAG context injected into system prompt of `_draft_request()`, not user message. Claude reviews draft without raw chunks
- **D-RAG-3:** Two-stage retrieval: vector top-50 → qwen3 re-rank to top-5. Re-ranking is scanner_function (qwen3 only)
- **D-RAG-4:** Adaptive chunking: <500 tok whole, >2000 tok overlap. Deterministic, no LLM needed, air-gap safe
- **D-RAG-5:** Content hash (SHA-256) dedup on ingest. Skips re-embedding unchanged content
- **D-RAG-6:** Tiered retention: hot(30d)/warm(365d,float16)/cold(archive). Originals always preserved in source tables
- **D-RAG-7:** Multi-tenant isolation via namespacing. Mirrors D60 SaaS isolation
- **D-RAG-8:** Full PROV-AGENT provenance chain per retrieval. NIST AU-3 compliant
- **D-RAG-9:** Real-time via extension hooks (D261) + batch sweep. Hybrid ingestion
- **D-RAG-10:** Embeddings via existing `get_embedding_provider()` -> Ollama nomic-embed-text. No new embedding infra
- **D-RAG-11:** retrieval_log and ingestion_log append-only (D6). Added to APPEND_ONLY_TABLES
- **D-RAG-12:** BM25 boost reuses `hybrid_search.py`, time-decay reuses `time_decay.py`. Zero code duplication
- **D-RAG-13:** Child apps get RAG via capability flag. 3-tier: local-only, parent-federated (A2A), or hybrid
- **D-RAG-14:** Parent RAG queries from children logged with `agent_id="child:{child_id}"` for audit

## Prerequisites

- Phase 29 (Time-Decay Memory) — reuse `tools/memory/time_decay.py`
- Phase 29 (Hybrid Search) — reuse `tools/memory/hybrid_search.py`
- Phase 35 (Innovation Engine) — source data for ingestion
- Phase 44 (Extension Hooks) — TOOL_EXECUTE_AFTER hook for real-time ingestion
- Phase 46 (Observability) — PROV-AGENT provenance recording
- LLM Router (Two-Tier) — `_draft_request()` injection point

## Component 1: Vector Store Layer

**Files:** `tools/rag/vector_store_provider.py`, `tools/rag/sqlite_vector_store.py`, `tools/rag/chroma_vector_store.py`, `tools/rag/faiss_vector_store.py`, `tools/rag/vector_store_factory.py`

### Capabilities
1. VectorStoreProvider ABC defining upsert, search, delete, count, check_availability
2. VectorChunk and SearchResult dataclasses for type-safe data flow
3. SQLite BLOB backend with cosine similarity (numpy fast path + pure-Python fallback)
4. ChromaDB backend with persistent collections and tenant namespacing
5. FAISS backend with IndexFlatIP for fast approximate nearest neighbor search
6. Config-driven factory with auto-detection fallback chain

### Configuration
```yaml
# args/rag_config.yaml
rag:
  vector_store:
    backend: auto  # auto, sqlite, chromadb, faiss
```

## Component 2: Chunking and Ingestion

**Files:** `tools/rag/chunker.py`, `tools/rag/source_registry.py`, `tools/rag/ingestion_manager.py`, `tools/extensions/builtins/020_rag_ingestion.py`

### Capabilities
1. Adaptive chunking: short (<500 tok) whole, long (>2000 tok) sliding window with 10% overlap
2. Declarative SOURCE_REGISTRY mapping 20+ source types to tables, columns, priorities
3. Content hash (SHA-256) dedup — skips re-embedding unchanged content
4. Real-time ingestion via TOOL_EXECUTE_AFTER extension hook
5. Batch sweep for historical data and periodic refresh

### CLI
```bash
python tools/rag/ingestion_manager.py --ingest --source innovation_signals --json
python tools/rag/ingestion_manager.py --sweep --json
python tools/rag/ingestion_manager.py --status --json
python tools/rag/ingestion_manager.py --daemon --json
```

## Component 3: Retrieval and Re-ranking

**Files:** `tools/rag/retriever.py`, `tools/rag/reranker.py`, `hardprompts/rag_rerank.md`

### Pipeline
```
1. Embed query (Ollama nomic-embed-text)
2. Vector similarity top-50 (VectorStoreProvider.search())
3. BM25 keyword boost (reuse hybrid_search.py)
4. Time-decay adjustment (reuse time_decay.py)
5. qwen3 re-rank top-50 → top-5 (scanner_function, no Claude review)
6. Record provenance (PROV-AGENT)
```

### CLI
```bash
python tools/rag/retriever.py --query "FedRAMP AC-2 patterns" --json
```

## Component 4: Two-Tier LLM Integration

**Modified file:** `tools/llm/router.py`

RAG context injected into the system prompt of `_draft_request()`:
```
_rag_augment(request, function) → _draft_request(augmented) → qwen3 draft → _review_request(original, draft) → Claude review
```

Key design: qwen3 uses RAG to produce a better draft; Claude reviews draft quality. Maximum token savings.

Function denylist: `attachment_analysis` (vision tasks don't benefit from text RAG).

## Component 5: Retention

**File:** `tools/rag/retention_manager.py`

| Tier | Age | Storage | Speed |
|------|-----|---------|-------|
| Hot | 0-30 days | Full float32 embeddings | <50ms |
| Warm | 30-365 days | float16 compressed | ~200ms |
| Cold | 365+ days | Metadata only (re-embed on demand) | Slow |

### CLI
```bash
python tools/rag/retention_manager.py --migrate --json
python tools/rag/retention_manager.py --status --json
```

## Component 6: Dashboard

**File:** `tools/dashboard/templates/rag/knowledge_search.html`

### Page: `/knowledge-search`
- Stat grid: Total chunks, source types, active tiers, backend, status
- NLQ search input with example queries
- Search results: content preview, source attribution, relevance scores, tier badges
- Source distribution chart (SVG bar chart)
- Recent searches table (from rag_retrieval_log)

## Component 7: Fine-Tuning Integration (Phase 64 Extension)

RAG and fine-tuning form a bidirectional loop:

1. **RAG → Fine-Tuning:** RAG chunks become training data via `pair_generator.py`. qwen3 generates Q&A pairs from chunk content; humans label quality/compliance/relevance in `/finetune/label` dashboard.
2. **Fine-Tuning → RAG:** Fine-tuned models replace qwen3 as worker tier via `ft_active_models` lookup in `router.py`. The fine-tuned model learns to work WITH RAG-augmented context, producing higher-quality drafts that Claude reviews.
3. **RAG → Training Context:** RAG context injected via `_rag_augment()` during fine-tuned model serving — the model sees the same RAG patterns during inference as during training.

### Architecture Decisions (D-FT-1 through D-FT-22)

- **D-FT-1:** `FineTuneProvider` ABC (D66 pattern) with 4 implementations: `UnslothLocalProvider`, `OpenAIFineTuneProvider`, `BedrockFineTuneProvider`, `AzureOpenAIFineTuneProvider`
- **D-FT-2:** Unsloth as sole local QLoRA engine (MIT, air-gap safe). Handles 4-bit quantization, LoRA injection, GGUF export
- **D-FT-6:** Fine-tuned model slots into two-tier via `ft_active_models` table lookup. Additive runtime override — does NOT modify `llm_config.yaml`
- **D-FT-9:** Datasets append-only versioned. Content-hashed snapshots
- **D-FT-14:** Pure Python BLEU/ROUGE-L/perplexity scoring (air-gap safe)
- **D-FT-16:** Auto-promotion: BLEU >= 0.30 AND ROUGE-L >= 0.40 AND perplexity improvement >= 10%
- **D-FT-18:** LoRA adapters as marketplace asset type with training data provenance gate (Gate 10)
- **D-FT-22:** Full PROV-AGENT chain: source document → RAG chunk → training pair → dataset → job → adapter → active model

### Database Tables (9 new)

`ft_datasets`, `ft_dataset_examples` (append-only), `ft_training_jobs`, `ft_training_job_events` (append-only), `ft_model_versions`, `ft_active_models`, `ft_evaluations` (append-only), `ft_promotion_log` (append-only), `ft_hyperparam_results` (append-only)

### Key Tools

| Tool | Purpose |
|------|---------|
| `tools/finetune/provider.py` | FineTuneProvider ABC + dataclasses |
| `tools/finetune/dataset_manager.py` | Dataset CRUD, versioning, JSONL export |
| `tools/finetune/pair_generator.py` | Q&A pairs from RAG chunks via qwen3 |
| `tools/finetune/training_engine.py` | Full training pipeline orchestrator |
| `tools/finetune/evaluator.py` | BLEU/ROUGE-L/perplexity (pure Python) |
| `tools/finetune/promotion_manager.py` | Auto-promote + manual override |
| `tools/finetune/model_registry.py` | Model version management |
| `tools/llm/router.py` | `_check_finetuned_override()` for runtime routing |

### Config

`args/finetune_config.yaml` — local engine, GPU, LoRA defaults, training, hyperparameter search, dataset, evaluation, promotion thresholds, retrain trigger, cloud providers, export, marketplace, child app, provenance.

### Dashboard Pages

`/finetune`, `/finetune/datasets`, `/finetune/datasets/<id>`, `/finetune/label`, `/finetune/jobs`, `/finetune/jobs/<id>`, `/finetune/models`, `/finetune/models/<id>`, `/finetune/evaluate`

## Database Tables

### `rag_chunks`
Core chunk storage with embeddings as BLOBs, source attribution, tier classification.

### `rag_ingestion_log` (append-only, D6/D-RAG-11)
Tracks every ingestion event for audit.

### `rag_retrieval_log` (append-only, D6/D-RAG-11)
Every retrieval logged with query hash (not content), result count, scores.

### `rag_parent_cache`
TTL-based cache for parent RAG queries from child apps.

## Security Gate

```yaml
rag:
  blocking:
    - rag_injection_without_provenance
    - rag_cross_tenant_query_detected
    - rag_content_tracing_in_cui_without_approval
  warning:
    - rag_ingestion_stale_over_7_days
    - rag_retrieval_low_relevance_trend
    - rag_vector_store_unavailable
  thresholds:
    provenance_required: true
    tenant_isolation_required: true
    max_ingestion_staleness_days: 7
```

## MCP Tools

| Tool | Handler | Description |
|------|---------|-------------|
| rag_search | handle_rag_search | Semantic search with optional filters |
| rag_ingest | handle_rag_ingest | Ingest specific source type |
| rag_status | handle_rag_status | System status and statistics |
| rag_chunk_info | handle_rag_chunk_info | Get chunk details by ID |
| rag_delete_source | handle_rag_delete_source | Delete chunks by source |
| rag_retention_migrate | handle_rag_retention_migrate | Run tier migration |
| rag_reindex | handle_rag_reindex | Rebuild index from source data |
| rag_retrieval_history | handle_rag_retrieval_history | Recent retrieval log |
| rag_providers | handle_rag_providers | List available vector store backends |

## Child App Integration (D-RAG-13)

Three-tier architecture:
1. **Tier 1 (Local):** Child has own `tools/rag/` and local vector store. Capability flag `"rag": true`.
2. **Tier 2 (Federated):** Child queries parent RAG via A2A callback `query_parent_rag()`. Results cached with 1-hour TTL.
3. **Tier 3 (Hybrid):** Tier 1 + Tier 2. Local results merged with parent results, deduped by content_hash.

## Tests

```bash
pytest tests/test_rag_vector_stores.py -v    # Vector store backend tests
pytest tests/test_rag_chunker.py -v           # Adaptive chunking tests
pytest tests/test_rag_retriever.py -v         # Retrieval pipeline tests
pytest tests/test_rag_reranker.py -v          # Re-ranking tests
pytest tests/test_rag_ingestion.py -v         # Ingestion manager tests
pytest tests/test_rag_retention.py -v         # Tier migration tests
pytest tests/test_rag_two_tier.py -v          # LLM integration tests
pytest tests/test_rag_child_app.py -v         # Child app RAG tests
```

## Verification

```bash
# 1. Ingestion
python tools/rag/ingestion_manager.py --status --json

# 2. Retrieval
python tools/rag/retriever.py --query "FedRAMP AC-2" --json

# 3. Dashboard
# Start dashboard, navigate to /knowledge-search, enter query, verify results

# 4. Health check
python tools/testing/health_check.py --json  # Verify RAG subsystem appears

# 5. Production audit
python tools/testing/production_audit.py --json  # Verify rag gate evaluates
```
