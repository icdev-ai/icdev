---
id: D-MKT-E2
title: Encryption key derived from token encryption_seed field
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
Encryption key derived from token `encryption_seed` field (hex string, stable across renewals) + module slug as HKDF salt
