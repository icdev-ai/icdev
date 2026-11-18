---
id: D-PC-1
title: Declarative YAML prompt chains
version: '1'
status: accepted
section: D-PC — Prompt Chains
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
Declarative YAML prompt chains (D26 pattern) — sequential LLM-to-LLM reasoning with $INPUT/$ORIGINAL/$STEP{id} variable substitution, per-step agent routing via LLMRouter
