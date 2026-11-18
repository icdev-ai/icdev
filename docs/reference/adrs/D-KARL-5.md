---
id: D-KARL-5
title: Automated RAG-to-FT pipeline
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
Automated RAG-to-FT pipeline — detect new RAG chunks since last run, generate Q&A pairs via pair_generator, quality-filter with Bayesian ranking, auto-approve above threshold (0.8), check retrain trigger. Tracked in `ft_pipeline_runs` (append-only). Configurable source types, schedule, and max pairs per run via `args/finetune_config.yaml`.
