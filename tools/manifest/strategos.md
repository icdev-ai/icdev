# Strategos

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Strategos

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Tier Resolver | tools/strategos/tier_resolver.py | Resolves active OSINT and executor tiers at runtime. OSINT: INTERNET → GITLAB → FILE_INBOX → NONE. Executor: CLAUDE_CLI → GITLAB → OLLAMA_LOCAL. Caches 5 min; logs tier changes. Used by osint_harvester, kanban_scheduler, and external importers. | --json, --no-cache | TierStatus JSON (osint_tier, exec_tier, gitlab_reachable, file_inbox_count, ollama_reachable, resolved_at) |
| OSINT Harvester | tools/genesis/reflexes/strategos/osint_harvester.py | Three-tier OSINT signal harvester (INTERNET→GITLAB→FILE_INBOX→NONE). Deduplicates via sha256. Writes sg_raw_signals + sg_raw_signals_audit. Handles RSS, Telegram, snscrape, GitLab CI artifacts, and file inbox JSON/TXT. | --json | {success, metric_value, details{tier, signals_harvested, duplicates_skipped, errors}} |
| GDELT Importer | tools/strategos/gdelt_importer.py | GDELT 2.0 narrative-events bulk importer. Downloads export CSV ZIPs, filters to Ukraine/Taiwan AOIs (country code + lat/lon bbox), and upserts into sg_conflict_events (event_type=narrative_event, source=gdelt). Deduplicates via (source, external_id) unique index. | --file, --date YYYYMMDD, --days N, --dry-run, --json | {dry_run, files_attempted, files_ok, total_aoi_rows, by_aoi, inserted, skipped_duplicates} |
