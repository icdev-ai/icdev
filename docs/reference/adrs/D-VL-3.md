---
id: D-VL-3
title: Air-gap safe
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
Air-gap safe — repair uses local Ollama models (`qwen3-local`) when `ICDEV_AIR_GAPPED=true`; cloud models (LeanStral, Claude) optional
