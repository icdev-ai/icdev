---
id: D-MKT-E3
title: 'File format: [4B "IENC"][2B version][12B nonce][NB ciphertext+GCM tag] -- GCM provides both confidentiality
  and integrity'
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
File format: `[4B "IENC"][2B version][12B nonce][NB ciphertext+GCM tag]` -- GCM provides both confidentiality and integrity (no separate HMAC)
