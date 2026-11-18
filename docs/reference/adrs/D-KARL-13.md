---
id: D-KARL-13
title: Entity disambiguation
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
Entity disambiguation — find duplicate nodes via exact/normalized label matching and embedding cosine similarity. Merge entities (re-point edges, merge properties, add aliases). Resolve ambiguous labels with context-aware scoring. Deterministic, no LLM required.
