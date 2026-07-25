# Universal RAG Subsystem (Phase 64)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Universal RAG Subsystem (Phase 64)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Vector Store Provider | tools/rag/vector_store_provider.py | ABC + dataclasses (VectorChunk, SearchResult) for pluggable vector store backends (D-RAG-1) | (import) | ABC |
| SQLite Vector Store | tools/rag/sqlite_vector_store.py | Default vector store backend using BLOB embeddings with cosine similarity (numpy + pure-Python fallback) | (import) | VectorStoreProvider |
| ChromaDB Vector Store | tools/rag/chroma_vector_store.py | Optional ChromaDB backend with persistent collections and tenant-namespaced isolation | (import) | VectorStoreProvider |
| FAISS Vector Store | tools/rag/faiss_vector_store.py | Optional FAISS backend (faiss-cpu) with IndexFlatIP for fast approximate nearest neighbor search | (import) | VectorStoreProvider |
| Vector Store Factory | tools/rag/vector_store_factory.py | Config-driven backend selection: auto-detect ChromaDB → FAISS → SQLite fallback | (import) | VectorStoreProvider instance |
| Adaptive Chunker | tools/rag/chunker.py | Adaptive chunking: <500 tok whole, >2000 tok sliding window with 10% overlap at sentence boundaries (D-RAG-4) | text, source_type | List[VectorChunk] |
| Source Registry | tools/rag/source_registry.py | Declarative SOURCE_REGISTRY mapping 20+ source types to tables, columns, priority, chunk strategy | (import) | Registry dict |
| Ingestion Manager | tools/rag/ingestion_manager.py | Real-time + batch ingestion pipeline with content hash dedup, watermarking, CLI (D-RAG-9) | --ingest, --sweep, --status, --daemon, --json | Ingestion stats JSON |
| RAG Retriever | tools/rag/retriever.py | Two-stage retrieval: vector top-50 → BM25 boost → time-decay → qwen3 re-rank → top-5 (D-RAG-3) | --query, --json | Ranked results JSON |
| Adaptive Router | tools/rag/adaptive_router.py | agx-rag-01 complexity pre-routing: cheap-tier classifier emits a 3-value complexity ENUM {none/simple/complex} (deterministic-picker, heuristic fallback); Python `decide_route` composes skip/single_pass/decompose. `none`/skip is enforced-unavailable on citation surfaces (TRUST safety, in code). Opt-in via rag.adaptive_routing (default off = current behavior); `measure_savings()` reports skip-rate + routing accuracy | AdaptiveRetriever.retrieve(query, requires_citations), classify_complexity, decide_route, measure_savings | {route, complexity, results, retrieved} |
| Retriever Common | tools/rag/retriever_common.py | Shared helpers for tenant-scoped RAGRetriever construction + search invocation (run_rag_search) and unit-interval score clamp (clamp_unit); used by Cortex search_rag adapter and the rag_search MCP handler | (import) | run_rag_search, clamp_unit |
| Re-ranker | tools/rag/reranker.py | qwen3 re-ranking via LLM router scanner_function (D-RAG-3) | query, chunks | Ranked chunk IDs |
| Retention Manager | tools/rag/retention_manager.py | Hot/warm/cold tier migration with float16 compression (D-RAG-6) | --migrate, --status, --json | Migration stats JSON |
| RAG Ingestion Hook | tools/extensions/builtins/020_rag_ingestion.py | Extension hook at TOOL_EXECUTE_AFTER for real-time ingestion (D-RAG-9) | (hook) | Auto-ingest |
| RAG MCP Server | tools/mcp/rag_server.py | 9 MCP tool handlers: search, ingest, status, chunk_info, delete, retention, reindex, history, providers | (MCP stdio) | JSON-RPC responses |
| RAG Config | args/rag_config.yaml | All RAG settings: vector store, embedding, chunking, retrieval, rerank, injection, ingestion, retention, provenance | (data) | YAML config |
| RAG Re-rank Prompt | hardprompts/rag_rerank.md | Re-ranking prompt template for qwen3 scanner_function | (hardprompt) | Prompt template |
| Source Mappings | context/rag/source_mappings.json | Declarative source type → table/column mappings (D26 pattern) | (data) | JSON mappings |
| Knowledge Search Page | tools/dashboard/templates/rag/knowledge_search.html | Dashboard page: stat grid, NLQ search, results with scores, source distribution chart, recent searches | (template) | HTML |

