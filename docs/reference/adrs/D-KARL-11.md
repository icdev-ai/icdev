---
id: D-KARL-11
title: Hyperparameter search orchestrator
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
Hyperparameter search orchestrator — grid/random search over LoRA hyperparameters (rank, learning rate, epochs, batch size). Non-blocking design: `run_next_trial()` queues training job, external scheduler checks completion. Config-driven from `args/finetune_config.yaml` `hyperparam_search` section. DB tables: `ft_hp_searches`, `ft_hp_trials`.
