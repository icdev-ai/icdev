---
id: D-KARL-6
title: KG community-based fine-tuning pair generation
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
KG community-based fine-tuning pair generation — three deterministic strategies: (1) entity-relationship pairs from edge templates, (2) community cluster pairs from BFS components, (3) compliance crosswalk pairs from control↔standard edges. Template-based (air-gap safe), optional scanner-tier refinement. Deduplicates by content hash before storage.
