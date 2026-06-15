# Innovation Adaptation (Phase 44 — D257-D279)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Innovation Adaptation (Phase 44 — D257-D279)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Chat Manager | tools/dashboard/chat_manager.py | Multi-stream parallel chat: thread-per-context, max 5/user, message queue, mid-stream intervention (D257-D260, D265-D267) | (library) | ChatManager class |
| Chat API | tools/dashboard/api/chat.py | Flask Blueprint: create/list/send/intervene/resume/delete chat contexts | /api/chat/* | JSON chat data |
| Chat JS | tools/dashboard/static/js/chat.js | Unified multi-stream + RICOAS chat UI with intervention controls and real-time updates | (browser) | Chat UI |
| State Tracker | tools/dashboard/state_tracker.py | Dirty-tracking state push: per-client version counters, debounced SSE, incremental updates (D268-D270) | (library) | StateTracker class |
| Phase Loader | tools/dashboard/phase_loader.py | Load and render phase registry data for dashboard phases page | (library) | Phase data |
| Extension Manager | tools/extensions/extension_manager.py | Active extension hook system: 10 hook points, behavioral/observational tiers, layered override (project > tenant > default) (D261-D264) | (library) | ExtensionManager class |
| History Compressor | tools/memory/history_compressor.py | 3-tier history compression: current topic 50%, historical 30%, bulk 20%, topic boundary detection, LLM/truncation fallback (D271-D274) | --context-id, --budget, --json | Compressed history |
| Memory Consolidation | tools/memory/memory_consolidation.py | AI-driven memory consolidation: hybrid search → LLM decision (MERGE/REPLACE/KEEP_SEPARATE/UPDATE/SKIP), Jaccard fallback (D276) | --consolidate, --dry-run, --json | Consolidation log |
| Code Pattern Scanner | tools/security/code_pattern_scanner.py | Dangerous pattern detection across 6 languages (Python, Java, Go, Rust, C#, TypeScript), declarative YAML patterns (D278) | --scan, --project-dir, --language, --gate, --json | Pattern findings + gate |
| Register External Patterns | tools/innovation/register_external_patterns.py | Register Agent Zero + InsForge patterns as innovation signals with 5-dimension scoring (D279) | --register-all, --status, --score-all, --json | Registration results |
| Shared Schemas | tools/schemas/ | stdlib dataclass models (ProjectStatus, AgentHealth, AuditEvent, etc.) with validate_output() and wrap_mcp_response() (D275) | (library) | Schema classes |
| Innovation Signal Schema | tools/schemas/innovation.py | InnovationSignal dataclass — innovation pipeline signal model (source, scoring, triage result, FORGE layer, boundary tier, effort) (D275/Phase 44) | (library) | InnovationSignal class |
| Chat Schemas | tools/schemas/chat.py | ChatMessage and ChatContext dataclass models for multi-stream parallel chat (D257/D275). Used by dashboard API and SaaS portal. Supports compression tiers (current/historical/bulk), CUI classification, role types (user/assistant/system/intervention). | (library) | ChatMessage, ChatContext |
| Schema Validation | tools/schemas/validation.py | Schema validation utilities (Phase 44 — D275). Validates tool output dicts against shared dataclass models via validate_output(); backward compatible with plain dict returns. | (library) | SchemaValidationError, validate_output() |
| Core Schemas | tools/schemas/core.py | Core domain dataclass schemas (ProjectStatus, AgentHealth, AuditEvent) shared across MCP servers, dashboard, and CLI tools (D275) | (library) | Core dataclass models |
| Compliance Schemas | tools/schemas/compliance.py | Dataclass schema models for multi-framework compliance results and unified security scan findings across SAST, dependencies, secrets, and containers (D275) | (library) | Compliance + scan schema models |
| Context Indexer | tools/mcp/context_indexer.py | CLAUDE.md section indexer by ## headers for semantic layer MCP delivery (D277) | (library) | Section index |
| Platform Connectors | tools/platform_connectors/ | Agent Reach-inspired unified adapter registry: PlatformAdapter protocol, AdapterRegistry, get_adapter(name), list_adapters(). Built-in adapters: GitHubAdapter (repos+issues search), RedditAdapter (/search.json), HackerNewsAdapter (Algolia). Shared _safe_get() HTTP helper with rate-limit/403/error handling. All adapters return normalized {title, url, author, score, description, metadata} dicts (adapt-conn-01–04). | get_adapter(name).fetch(query, limit=30) | list[dict] |

