---
id: D-VL-6
title: Formal verification gate uses deterministic checks
version: '1'
status: accepted
section: D-VL — Verification Loop (LeanStral-adapted)
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
Formal verification gate uses deterministic checks (SQL injection regex, dangerous patterns, invariant density, input validation AST) — no LLM required. Property-based test generation via hypothesis specs (advisory only)
