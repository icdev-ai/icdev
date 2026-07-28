# Memory System

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Memory System
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Init Memory DB | tools/memory/init_memory_db.py | Initialize data/memory.db with all memory tables (memory_entries, daily_logs, memory_access_log, memory_consolidation_log, memory_buffer) | --db-path, --json | Table list + status |
| Memory Read | tools/memory/memory_read.py | Load all memory (MEMORY.md + recent logs) | --format markdown | Formatted memory context |
| Memory Tier Measure | tools/memory/memory_tier_measure.py | oss2-meas-01. Measures whether the memory tier earns its keep before building anything mem0-shaped: scans `memory_entries` pairwise with the **consolidator's own** keyword/Jaccard similarity to quantify the semantic redundancy exact-hash dedup misses (what the repaired consolidation would merge), plus baseline stats (type mix, embedding coverage). Refuses a verdict below 30 entries (`insufficient_data`) so it can't launder a claim from too little data. Live result (98 entries): 40–64% redundancy → consolidation earns its keep; 0% embedding coverage flagged. See [docs/features/oss2-memory-tier-measurement.md](../../docs/features/oss2-memory-tier-measurement.md). | `--limit`, `--threshold`, `--json` | baseline + consolidation report w/ verdict |
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
| Wiki Lint Reflex | tools/genesis/reflexes/wiki_lint.py | Karpathy LLM Wiki health checks: orphan files (not in MEMORY.md), broken [[slug]] links, stale current-state language, MEMORY.md overflow — emits oracle_predictions → kanban suggested-cards | --lint, --full, --json | Findings summary JSON |
| Cross-Reference Update | tools/memory/memory_write.py::update_crossrefs | Add [[slug]] back-links to related existing memory files on ingest; ≥2 shared keywords triggers a back-link | slug, content, memory_dir | List of updated file paths |
| Session Indexer | tools/memory/session_indexer.py | FTS5-backed session history indexer: index_session_turn() writes to memory_entries + refreshes memory_fts; search_history() uses FTS5 (SQLite) or ILIKE (PG fallback); reindex_all() rebuilds from scratch. fts5_search() wired into hybrid_search.py as third backend (weight 0.3, activates for queries >2 tokens) (adapt-hermes-01/02/03). | --search QUERY, --reindex, --index-turn CONTENT, --json | Ranked results / reindex summary |
| Navigate Wiki Query | tools/memory/wiki_tool_query.py | ANVIL Navigate phase wiki pre-step: BM25 keyword search over memory wiki files before grepping tools manifest; surfaces institutional tool know-how; also called by runner.py as step 0 (Karpathy-wiki Item 3). | --query TEXT, --top-k N, --memory-dir DIR, --json | Ranked wiki entry list |

