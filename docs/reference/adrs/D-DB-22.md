---
id: D-DB-22
title: args/storage_config.yaml controls backend selection
version: '1'
status: accepted
section: D-DB — Database & Storage
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
`args/storage_config.yaml` controls backend selection; env vars override YAML (ICDEV_STORAGE_BACKEND, ICDEV_PG_*)
