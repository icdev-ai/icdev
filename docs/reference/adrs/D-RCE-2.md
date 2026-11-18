---
id: D-RCE-2
title: Contextual retrieval
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
Contextual retrieval (Anthropic pattern) — embed a ~50-100 token LLM context prefix + chunk, but **store and cite the original chunk** (`VectorChunk.text_for_embedding()`; `content_hash` computed on original for stable dedup). Preserves citation integrity while improving embedding recall. Default OFF; graceful no-op air-gapped; deterministic source_type/metadata heuristic fallback.
