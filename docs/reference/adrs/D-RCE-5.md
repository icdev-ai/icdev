---
id: D-RCE-5
title: TurboQuant — SKIP
version: '1'
status: accepted
section: Phase 74 — RCE (RAG Context Engineering)
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
**TurboQuant — SKIP.** A standalone FAISS-compatible quantization library with no pgvector integration; pgvector's HNSW already gives O(log n) ANN, and the SQLite fallback does pure-numpy/Python cosine that TurboQuant does not plug into. Its KV-cache-compression benefit is an inference-provider concern, not a retrieval-layer one. (Recorded so it is not re-litigated.)
