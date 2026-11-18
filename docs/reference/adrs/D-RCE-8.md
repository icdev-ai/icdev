---
id: D-RCE-8
title: Domain-adapted embedding fine-tune — NO-GO / DEFER
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
**Domain-adapted embedding fine-tune — NO-GO / DEFER** (rce-eval-02; `docs/features/rce-eval-02-domain-embedding-feasibility.md`). The corpus holds ~0 in-domain (compliance/NIST) chunks of 1397, so there is nothing to fine-tune on; the low baseline is a corpus-coverage gap, not an embedding-quality gap. Embeddings run through the `get_embedding_provider()` abstraction (configured in `args/llm_config.yaml` `embeddings:`), so any future candidate is a new provider behind the ABC, not an Ollama/nomic code fork. Re-evaluate (via `tools/rag/embedding_feasibility.py`) after compliance-corpus ingestion; prefer contextual retrieval + RAPTOR + reranking first.
