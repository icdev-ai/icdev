---
id: D-SYNC-7
title: sync_log is append-only
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
`sync_log` is append-only (NIST AU). `sync_jobs`/`sync_state`/`sync_conflicts` allow UPDATE
