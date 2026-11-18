---
id: D-RES-15
title: YouTube Data API v3 requires YOUTUBE_API_KEY env var
version: '1'
status: accepted
section: D-RES — Industry Research Engine
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
YouTube Data API v3 requires `YOUTUBE_API_KEY` env var. Degrades gracefully without key to manual-only mode. Transcript extraction via `youtube-transcript-api` (no API key needed)
