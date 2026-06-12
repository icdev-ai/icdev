# Industry Research Engine (Phase 63)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Industry Research Engine (Phase 63)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Research Engine | tools/research/research_engine.py | Main orchestrator: 8-stage pipeline (SCOPE→DOSSIER), session lifecycle, daemon mode | --run, --run-stage, --status, --daemon, --json | Pipeline results JSON |
| Session Manager | tools/research/session_manager.py | Session CRUD, lifecycle management, vertical loading | --create, --list, --get, --advance, --json | Session data JSON |
| Vertical Loader | tools/research/vertical_loader.py | Load/validate vertical configs from JSON, store in DB | --load, --list, --get, --validate, --json | Vertical config JSON |
| Source Scanner | tools/research/source_scanner.py | 8-stream scanning: forums, reviews, academic, regulatory, OSS, SaaS, news, patents | --scan, --list-sources, --status, --json | Signal data JSON |
| Challenge Scorer | tools/research/challenge_scorer.py | 6-dimension weighted scoring: market, regulatory, technical, competition, readiness, compliance | --cluster, --score, --score-one, --top, --json | Challenge scores JSON |
| Regulatory Mapper | tools/research/regulatory_mapper.py | Map regulations to ICDEV™ crosswalk frameworks | --map, --landscape, --json | Regulatory mapping JSON |
| Capability Mapper | tools/research/capability_mapper.py | Map challenges to ICDEV™ capability catalog via keyword overlap | --map, --map-one, --coverage, --json | Capability mapping JSON |
| Build/Buy Analyzer | tools/research/build_buy_analyzer.py | Build/buy/partner decision matrix per challenge | --analyze, --analyze-one, --matrix, --json | Decision matrix JSON |
| Trend Detector | tools/research/trend_detector.py | Cross-session trend analysis with velocity/acceleration | --detect, --trends, --report, --json | Trend data JSON |
| Dossier Generator | tools/research/dossier_generator.py | Template-based Markdown dossier generation (no LLM, air-gap safe) | --generate, --list, --get, --review, --json | Dossier Markdown |
| Research MCP Server | tools/mcp/research_server.py | MCP server handlers for 10 research tools | (MCP stdio) | JSON-RPC responses |
| Research Config | args/research_config.yaml | Engine configuration: pipeline, sources, scoring, dossier, scheduling | (data) | YAML config |

