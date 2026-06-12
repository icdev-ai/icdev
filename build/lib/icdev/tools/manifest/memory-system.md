# Memory System

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Memory System
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Init Memory DB | tools/memory/init_memory_db.py | Initialize data/memory.db with all memory tables (memory_entries, daily_logs, memory_access_log, memory_consolidation_log, memory_buffer) | --db-path, --json | Table list + status |
| Memory Read | tools/memory/memory_read.py | Load all memory (MEMORY.md + recent logs) | --format markdown | Formatted memory context |
| Memory Write | tools/memory/memory_write.py | Write to daily log + DB | --content, --type, --importance | Confirmation |
| Memory DB | tools/memory/memory_db.py | Keyword search on memory database | --action search, --query | Search results |
| Semantic Search | tools/memory/semantic_search.py | Vector similarity search (requires OpenAI key) | --query | Ranked results |
| Hybrid Search | tools/memory/hybrid_search.py | Combined keyword + semantic search, optional --time-decay flag for recency weighting | --query, --bm25-weight, --semantic-weight, --time-decay | Ranked results |
| Embed Memory | tools/memory/embed_memory.py | Generate embeddings for memory entries | --all | Confirmation |
| Time-Decay Scoring | tools/memory/time_decay.py | Exponential time-decay scoring for memory entries: per-type half-lives, importance resistance, combined relevance+recency+importance scoring (D147) | --score --entry-id, --rank --query, --top-k, --user-id, --json | Decay factors + ranked results |
| Auto-Capture | tools/memory/auto_capture.py | Auto-capture content from hooks into memory buffer with dedup (D181) | --content, --source, --type, --tool-name, --flush, --buffer-status, --user-id, --json | Capture/flush result |
| Maintenance Cron | tools/memory/maintenance_cron.py | Orchestrate memory maintenance: flush buffer, embed, prune, backup (D179-D182) | --all, --flush-buffer, --embed-unembedded, --prune-stale, --backup, --days, --json | Maintenance results |
| Auto-Consolidate | tools/memory/auto_consolidate.py | OPT-40 cron-style replacement for pre_compact hook — checks memory.db size, calls MemoryConsolidator.consolidate_all() when over threshold, writes audit row | --threshold-mb, --batch-size, --dry-run, --force, --no-audit, --json | Summary JSON (triggered, processed, actions) |
| Scoped Provider | tools/memory/scoped_provider.py | Per-agent isolated memory partitions with swappable backends (SQLite/InMemory) and controlled cross-agent transfer (D-MEM-10/11/12) | --agent-id, --project-id, --backend, --policy; subcommands: write, read, transfer, pull-inbox, pull-team, pull-broadcast, stats, list-partitions; --json --gate | Entry ID / entries / transfer result |

