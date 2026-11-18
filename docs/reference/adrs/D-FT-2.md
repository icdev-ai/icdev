---
id: D-FT-2
title: Unsloth as sole local QLoRA engine
version: '1'
status: accepted
section: D-FT — Fine-Tuning
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
Unsloth as sole local QLoRA engine (MIT license, air-gap safe). Subprocess invocation. GGUF export via `save_pretrained_gguf()` with Q4_K_M
