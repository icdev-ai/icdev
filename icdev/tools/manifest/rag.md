# RAG (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## RAG (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| PG Vector Store | tools/rag/pg_vector_store.py | PostgreSQL pgvector backend for RAG | (library) | VectorStore class |
| RAG-KG Bridge Ingester | tools/rag/rag_to_kg_ingester.py | Cursor-based backfill: reads rag_chunks, extracts entities/relationships, writes kg_nodes+kg_edges with source_chunk_id, tenant isolation, tier filter, no-LLM NER fallback | `--backfill [--tier warm]` or `--chunk-id ID` | JSON stats (pages, chunks, nodes, edges) |

