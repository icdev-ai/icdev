---
id: D-FT-1
title: FineTuneProvider ABC
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
`FineTuneProvider` ABC (D66 pattern) with 4 implementations: `UnslothLocalProvider`, `OpenAIFineTuneProvider`, `BedrockFineTuneProvider`, `AzureOpenAIFineTuneProvider`. Graceful `ImportError` on missing SDKs
