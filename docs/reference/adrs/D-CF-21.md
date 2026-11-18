---
id: D-CF-21
title: DAG execution uses Kahn's algorithm
version: '1'
status: accepted
section: D-CF — Connector Forge & CloudForge
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
DAG execution uses Kahn's algorithm (topological sort) -- deterministic O(V+E), no LLM in critical path
