---
id: D-RES-19
title: Each prediction has confidence
version: '1'
status: accepted
section: D-RES — Industry Research Engine
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
Each prediction has confidence (0-1), surprise_score (0-1), composite_rank = confidence × surprise_score. Top 5 returned sorted by composite_rank
