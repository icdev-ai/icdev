---
id: D-RCE-7
title: Qdrant — DEFER (not adopt)
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
**Qdrant — DEFER (not adopt).** Qdrant local mode exists and has built-in quantization, but it adds a Rust wheel dependency and breaks the "pure Python + SQL" fallback philosophy; the factory already auto-detects pgvector → ChromaDB → FAISS → SQLite. Revisit only if the SQLite fallback becomes a *measured* bottleneck (the float16/binary-quant work in D-RCE-3 targets that risk first).
