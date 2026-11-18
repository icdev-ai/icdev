---
id: D-KARL-8
title: RAG evaluation feedback loop with quality-triggered retraining
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
RAG evaluation feedback loop with quality-triggered retraining — monitor RAG metrics (NDCG, MRR, faithfulness) against configurable thresholds, record snapshots in `ft_quality_snapshots` (append-only), count consecutive failures, recommend retrain when N consecutive failures detected.
