---
id: D-KARL-7
title: Entity embedding and centrality enrichment
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
Entity embedding and centrality enrichment — compute degree + betweenness centrality (weighted average 0.6/0.4, BFS-based, deterministic) and persist to `kg_nodes.centrality`. Compute entity embeddings via nomic-embed-text (Ollama) and persist to `kg_nodes.embedding` BLOB. Enables semantic node search in GraphRAG. Config in `args/knowledge_graph_config.yaml`.
