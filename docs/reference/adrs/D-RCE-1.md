---
id: D-RCE-1
title: Baseline-first
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
Baseline-first — no RCE change lands unmeasured. `tools/rag/rag_benchmark.py` scores a compliance/NIST golden query set (`args/rag/golden_query_set.yaml`) for recall@k, MRR, citation-hit-rate, ndcg, reusing `evaluator.mrr`/`ndcg_at_k` (no re-implemented scoring); the committed `data/rag/rce_baseline.json` is the reference for `--compare` deltas. Ground-truth is expressed as content substrings (not only chunk IDs) so it survives the re-indexing that contextual retrieval and RAPTOR perform.
