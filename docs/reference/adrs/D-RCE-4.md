---
id: D-RCE-4
title: RAPTOR summary hierarchy
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
RAPTOR summary hierarchy — `rag_chunk_summaries` (level 0 leaves → level 1 sibling summaries → level 2 root), co-located with `rag_chunks` in the vector-store DB (not `icdev.db`, so no conftest churn), carrying `tenant_id` + `classification` (RLS parity, **not** append-only). Retrieval merges summary tiers with leaves and dedups by lineage (leaf/finer granularity wins; orphan summaries kept as weak-retrieval fallback). Summaries are tagged `is_summary` and never surfaced as citation sources — citations resolve to leaf chunks. Default OFF.
