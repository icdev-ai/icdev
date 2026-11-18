---
id: D-SYNC-1
title: 'SyncTargetProvider ABC with 3 implementations: Local'
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
`SyncTargetProvider` ABC with 3 implementations: Local (stdlib), SFTP (paramiko + subprocess ssh fallback), Cloud (wraps existing `StorageProvider`)
