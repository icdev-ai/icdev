---
id: D-KARL-10
title: Compliance crosswalk as knowledge graph
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
Compliance crosswalk as knowledge graph — models NIST 800-53 controls, control families, and frameworks (FedRAMP, CMMC, NIST 800-171, CJIS, HIPAA, etc.) as `kg_nodes` with `satisfies`, `belongs_to`, and `overlaps_with` edges. Deterministic content-hash IDs for idempotent rebuilds. BFS crosswalk path traversal and per-framework coverage queries.
