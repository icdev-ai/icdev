---
id: D-MKT-E4
title: Custom sys.meta_path import hook
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
Custom `sys.meta_path` import hook (`EncryptedModuleFinder`) transparently decrypts `.py.enc` files to memory on import -- never writes plaintext to disk
