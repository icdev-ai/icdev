---
id: D-DB-27
title: Eliminated 8 direct SELECT last_insert_rowid() SQL calls
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
Eliminated 8 direct `SELECT last_insert_rowid()` SQL calls — replaced with `cursor.lastrowid` property (cross-DB via StorageCursor wrapper).
