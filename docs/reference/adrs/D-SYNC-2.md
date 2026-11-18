---
id: D-SYNC-2
title: SHA-256 content hash for change detection
version: '1'
status: accepted
section: D-SYNC — File Sync
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
SHA-256 content hash for change detection. Fast-skip: if mtime+size unchanged vs cached state, skip expensive hash
