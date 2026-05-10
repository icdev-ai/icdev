# Phase 64 — Universal RAG Subsystem

CUI // SP-CTI

| Field | Value |
|-------|-------|
| Phase | 64 |
| Title | Universal RAG Subsystem |
| Status | Complete |
| Priority | High |
| Dependencies | Phase 29 (Memory), Phase 35 (Innovation), Phase 44 (Extensions), Phase 46 (Observability) |
| Author | ICDEV™ System |
| Date | 2026-03-03 |

---

## 1. Problem Statement

ICDEV™'s Intelligence engines (Innovation, Creative, Research) produce ~300 MB/year of structured intelligence across 20+ database tables. Compliance artifacts add ~10 MB per ATO system. Key problems:

1. **No context reuse** — Each LLM call starts from scratch with no pre-retrieved prior knowledge
2. **Token waste** — qwen3 drafts without relevant context, producing less accurate output that Claude must correct
3. **Cross-engine blindness** — Innovation signals, Creative pain points, and Research dossiers exist in silos with no unified search
4. **Child app isolation** — Child applications cannot query parent knowledge without custom code

## 2. Goals

- Index all ICDEV™ data into a unified vector store with adaptive chunking
- Auto-retrieve relevant context before every two-tier LLM call (qwen3 draft phase)
- Enable natural language search across all knowledge via dashboard and MCP
- Provide child apps with federated knowledge access via A2A callback
- Support air-gapped deployment with Ollama nomic-embed-text embeddings
- Enforce multi-tenant isolation from day one
- Maintain full provenance chain per retrieval (NIST AU-3)
- Implement tiered retention (hot/warm/cold) for cost-effective storage

## 3. Architecture

### 3.1 Data Flow

```
SOURCE DATA (20+ DB tables, files, artifacts)
    |
    v
INGESTION (real-time hooks + batch sweep)
    |
    v
ADAPTIVE CHUNKER (<500 tok: whole | >2000 tok: overlap chunks)
    |
    v
EMBEDDING (Ollama nomic-embed-text, 768 dims, air-gap safe)
    |
    v
VECTOR STORE (SQLite BLOB default | ChromaDB | FAISS -- Provider ABC)
    |
    v
RETRIEVAL (vector top-50 -> BM25 boost -> time-decay -> qwen3 re-rank -> top-5)
    |
    v
LLM INJECTION (prepend RAG context to _draft_request() system prompt)
    |
    v
PROVENANCE (query -> chunks -> LLM call -> output logged to PROV-AGENT)
```

### 3.2 Vector Store Backends

| Backend | Package | Air-Gap | Speed | Features |
|---------|---------|---------|-------|----------|
| SQLite | stdlib | Yes | Moderate | Default, BLOB embeddings, cosine similarity |
| ChromaDB | `chromadb` | Yes | Fast | Persistent collections, metadata filtering |
| FAISS | `faiss-cpu` | Yes | Fastest | IndexFlatIP, L2-normalized cosine similarity |

### 3.3 Source Types (20+)

| Category | Sources | Ingestion Mode |
|----------|---------|----------------|
| Intelligence | innovation_signals, creative_pain_points, creative_feature_gaps, creative_specs | Real-time |
| Research | research_dossiers, research_challenges, research_forecasts | Real-time |
| Compliance | compliance_artifacts, ssp_sections, poam_items | Real-time |
| Memory | memory_entries, agent_memory | Real-time |
| Telemetry | audit_trail, ai_telemetry | Batch |
| Engineering | mbse_model_elements, supply_chain_dependencies, code_quality_metrics | Batch |

### 3.4 Two-Stage Retrieval Pipeline

1. **Embed query** — Ollama nomic-embed-text via `get_embedding_provider()`
2. **Vector similarity top-50** — cosine similarity via VectorStoreProvider.search()
3. **BM25 keyword boost** — reuse `tools/memory/hybrid_search.py` bm25_search()
4. **Time-decay adjustment** — reuse `tools/memory/time_decay.py`
5. **qwen3 re-rank** — scanner_function (qwen3 only, no Claude review), top-50 -> top-5
6. **Record provenance** — PROV-AGENT entity/activity/relation

### 3.5 LLM Integration

RAG context injected into the system prompt of `_draft_request()`:
- qwen3 receives: original task + `[RELEVANT CONTEXT]` block from RAG
- Claude receives: original task + qwen3's draft (NOT raw RAG chunks)
- Net effect: better drafts from qwen3, fewer correction tokens from Claude

Function denylist: `attachment_analysis` (vision tasks don't benefit from text RAG).

## 4. Database Schema

### `rag_chunks` — Core chunk storage
- id, content, content_hash, embedding (BLOB), source_type, source_id, source_table
- chunk_index, total_chunks, metadata (JSON), tier (hot/warm/cold)
- tenant_id, project_id, classification, timestamps

### `rag_ingestion_log` (append-only, D6)
- source_type, source_id, chunks_created, chunks_skipped, ingestion_mode
- tenant_id, classification, created_at

### `rag_retrieval_log` (append-only, D6)
- query_hash (SHA-256, not plaintext), results_count, top_score, rerank_used
- filters, agent_id, duration_ms, tenant_id

### `rag_parent_cache` — Child app cache
- query_hash, results (JSON), retrieved_at, expires_at, source

## 5. Configuration

**File:** `args/rag_config.yaml`

Key settings:
- `vector_store.backend`: auto/sqlite/chromadb/faiss
- `embedding.dimensions`: 768 (nomic-embed-text)
- `retrieval.vector_top_k`: 50, `retrieval.final_top_k`: 5
- `rerank.enabled`: true, `rerank.model`: qwen3-local
- `injection.enabled`: true, `injection.max_injection_chars`: 4000
- `retention.hot_days`: 30, `retention.warm_days`: 365
- `provenance.record_query_content`: false (D282)

## 6. CLI Commands

```bash
# Ingestion
python tools/rag/ingestion_manager.py --ingest --source innovation_signals --json
python tools/rag/ingestion_manager.py --sweep --json
python tools/rag/ingestion_manager.py --status --json
python tools/rag/ingestion_manager.py --daemon --json

# Retrieval
python tools/rag/retriever.py --query "FedRAMP AC-2" --json

# Retention
python tools/rag/retention_manager.py --migrate --json
python tools/rag/retention_manager.py --status --json
```

## 7. Dashboard

**Page:** `/knowledge-search`

- Stat grid: Total chunks, source types, active tiers, backend, online/offline status
- Search controls: text input, source filter dropdown, top-K selector
- Search results: content preview, source attribution, relevance score, tier badge
- Source distribution chart: SVG bar chart by source type
- Recent searches table: from rag_retrieval_log
- Example queries: clickable links for common searches

## 8. Architecture Decisions

| ID | Decision |
|----|----------|
| D-RAG-1 | VectorStoreProvider ABC with SQLite/ChromaDB/FAISS (D66 pattern) |
| D-RAG-2 | RAG context injected into _draft_request() system prompt, not user message |
| D-RAG-3 | Two-stage retrieval: vector top-50 -> qwen3 re-rank -> top-5 |
| D-RAG-4 | Adaptive chunking: <500 tok whole, >2000 tok overlap (deterministic) |
| D-RAG-5 | Content hash (SHA-256) dedup on ingest |
| D-RAG-6 | Tiered retention: hot(30d)/warm(365d,float16)/cold(archive) |
| D-RAG-7 | Multi-tenant isolation via namespacing (mirrors D60) |
| D-RAG-8 | Full PROV-AGENT provenance per retrieval (NIST AU-3) |
| D-RAG-9 | Hybrid ingestion: real-time hooks (D261) + batch sweep |
| D-RAG-10 | Embeddings via existing get_embedding_provider() |
| D-RAG-11 | Append-only logs (D6) added to APPEND_ONLY_TABLES |
| D-RAG-12 | BM25/time-decay reuse from tools/memory/ (zero duplication) |
| D-RAG-13 | Child app 3-tier RAG: local, parent-federated, hybrid |
| D-RAG-14 | Child queries logged with agent_id="child:{child_id}" |

## 9. Testing

```bash
pytest tests/test_rag_vector_stores.py -v    # Vector store backends
pytest tests/test_rag_chunker.py -v           # Adaptive chunking
pytest tests/test_rag_retriever.py -v         # Retrieval pipeline
pytest tests/test_rag_reranker.py -v          # Re-ranking
pytest tests/test_rag_ingestion.py -v         # Ingestion manager
pytest tests/test_rag_retention.py -v         # Tier migration
pytest tests/test_rag_two_tier.py -v          # LLM integration
pytest tests/test_rag_child_app.py -v         # Child app RAG
```

## 10. Security Considerations

- **Content tracing:** Query content stored as SHA-256 hash only (D282). Plaintext logging requires `ICDEV_CONTENT_TRACING_ENABLED=true`.
- **Multi-tenant isolation:** Separate vector store instances per tenant. SQLite: per-tenant DB. ChromaDB: tenant-prefixed collections. FAISS: per-tenant index directories.
- **Append-only audit:** rag_ingestion_log and rag_retrieval_log are append-only (D6). Protected by APPEND_ONLY_TABLES in pre_tool_use.py.
- **RAG injection safety:** RAG context goes into system prompt only, never user-controllable input. Function denylist prevents injection into vision tasks.
- **Provenance chain:** Every retrieval creates PROV-AGENT entities/activities/relations for full traceability.
