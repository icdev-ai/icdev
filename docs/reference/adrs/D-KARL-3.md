---
id: D-KARL-3
title: Parallel multi-strategy retrieval
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
Parallel multi-strategy retrieval (GraphRAG + corrective RAG + source registry) via ThreadPoolExecutor with generative aggregation — scanner-tier LLM synthesizes unified context from N independent retrieval paths
