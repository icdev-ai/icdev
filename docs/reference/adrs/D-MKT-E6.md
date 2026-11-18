---
id: D-MKT-E6
title: Per-module encryption keys
version: '1'
status: accepted
section: D-MKT-E — Marketplace Encryption
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
Per-module encryption keys (different slug = different HKDF salt = different key) -- per-child-app isolation via per-license `encryption_seed`
