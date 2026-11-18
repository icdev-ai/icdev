---
id: D-KARL-9
title: GraphRAG semantic search
version: '1'
status: accepted
section: D-KARL — Knowledge-Augmented Retrieval & Learning
format: legacy
supersedes: []
superseded_by: ''
links:
  requirements: []
  standards: []
context: ''
consequences: ''
recorded:
  by: bootstrap:icx-coe-02-adr-migration
  kind: bootstrap
  step_id: ''
  note: migrated from docs/reference/adrs.md at bbd0b5df6
---
GraphRAG semantic search — cosine similarity on `kg_nodes.embedding` BLOB (nomic-embed-text 768-dim float32) augments keyword LIKE matching. Additive 0.2x embedding similarity scoring bonus. Graceful degradation when Ollama unavailable or embeddings not populated. Uses `urllib.request` for embedding query (no external deps).
