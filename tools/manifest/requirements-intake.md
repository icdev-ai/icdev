# Requirements Intake (RICOAS Phase 1)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Requirements Intake (RICOAS Phase 1)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Intake Engine [DEPRECATED] | tools/requirements/intake_engine.py | Conversational requirements intake — create/resume sessions, process turns, extract requirements | --project-id, --session-id, --message, --resume, --export, --json | Session + requirements |
| Decomposition Engine | tools/requirements/decomposition_engine.py | SAFe hierarchy decomposition (Epic > Capability > Feature > Story > Enabler) with WSJF scoring | --session-id, --level, --generate-bdd, --json | SAFe items |
| Gap Detector | tools/requirements/gap_detector.py | AI-powered gap/ambiguity detection against NIST coverage patterns | --session-id, --check-security, --check-compliance, --json | Gaps + recommendations |
| Document Extractor | tools/requirements/document_extractor.py | Upload SOW/CDD/CONOPS/SRD, extract structured requirements (shall/must/should) | --session-id, --upload, --extract, --document-id, --json | Extracted requirements |
| Readiness Scorer | tools/requirements/readiness_scorer.py | 5-dimension scoring: completeness, clarity, feasibility, compliance, testability | --session-id, --threshold, --trend, --json | Readiness score + trend |
| MCP Requirements Server | tools/mcp/requirements_server.py | MCP server for requirements tools (10 tools: intake, gaps, readiness, decompose, documents) | stdio | JSON-RPC responses |

