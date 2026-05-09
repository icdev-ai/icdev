# Requirements Intake (RICOAS Phase 1)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Requirements Intake (RICOAS Phase 1)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Intake Engine [DEPRECATED 2026-05-09, remove after 2026-08-01] | tools/requirements/intake_engine.py | DEPRECATED — use intake_api_client.py instead | — | — |
| Intake API Client | tools/requirements/intake_api_client.py | HTTP client for dashboard intake API — create sessions, process turns, score readiness, decompose | --create, --turn, --readiness, --gaps, --decompose, --export, --get, --session-id, --json | Session data / SAFe items |
| SAFe → Kanban Promoter | tools/requirements/intake_kanban_promoter.py | Promote safe_decomposition items → kanban_tasks(status='suggested') with WSJF→priority mapping | --session-id, --dry-run, --list, --list-all, --json | Inserted task IDs |
| Chat CLI Bridge | tools/chat/cli_bridge.py | HTTP client for multi-stream chat API — create/manage contexts, send/poll messages, link intake sessions | --create, --send, --poll, --messages, --url, --link-intake, --close, --persist-key, --json | Context dict / messages |
| Decomposition Engine | tools/requirements/decomposition_engine.py | SAFe hierarchy decomposition (Epic > Capability > Feature > Story > Enabler) with WSJF scoring | --session-id, --level, --generate-bdd, --json | SAFe items |
| Gap Detector | tools/requirements/gap_detector.py | AI-powered gap/ambiguity detection against NIST coverage patterns | --session-id, --check-security, --check-compliance, --json | Gaps + recommendations |
| Document Extractor | tools/requirements/document_extractor.py | Upload SOW/CDD/CONOPS/SRD, extract structured requirements (shall/must/should) | --session-id, --upload, --extract, --document-id, --json | Extracted requirements |
| Readiness Scorer | tools/requirements/readiness_scorer.py | 5-dimension scoring: completeness, clarity, feasibility, compliance, testability | --session-id, --threshold, --trend, --json | Readiness score + trend |
| MCP Requirements Server | tools/mcp/requirements_server.py | MCP server for requirements tools (10 tools: intake, gaps, readiness, decompose, documents) | stdio | JSON-RPC responses |

