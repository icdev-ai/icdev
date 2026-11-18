---
id: D-RCE-3
title: SQLite vectors default to float16 with a self-describing RVQ1 header
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
SQLite vectors default to **float16** with a self-describing `RVQ1` header (magic + dtype byte + payload). Reads decode float16, headered float32, and legacy headerless float32 — so the ~48% storage win needs no re-index. Fixes the latent `migrate_tier` warm-path bug where headerless float16 was mis-read as float32. Binary quantization + Hamming pre-filter (`sign_bits` column) is available but default OFF pending per-corpus recall validation.
