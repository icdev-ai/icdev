# Strategos

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Strategos

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Tier Resolver | tools/strategos/tier_resolver.py | Resolves active OSINT and executor tiers at runtime. OSINT: INTERNET → GITLAB → FILE_INBOX → NONE. Executor: CLAUDE_CLI → GITLAB → OLLAMA_LOCAL. Caches 5 min; logs tier changes. Used by osint_harvester, kanban_scheduler, and external importers. | --json, --no-cache | TierStatus JSON (osint_tier, exec_tier, gitlab_reachable, file_inbox_count, ollama_reachable, resolved_at) |
