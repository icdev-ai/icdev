# Marketplace (Phase 22)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Marketplace (Phase 22)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Catalog Manager | tools/marketplace/catalog_manager.py | CRUD for marketplace assets and versions | --register/--list/--get/--add-version/--deprecate | Asset record JSON |
| Asset Scanner | tools/marketplace/asset_scanner.py | 7-gate security scanning pipeline (SAST, secrets, deps, CUI, SBOM, provenance, signature) | --asset-id, --version-id, --asset-path | Scan results JSON |
| Publish Pipeline [DEPRECATED] | tools/marketplace/publish_pipeline.py | Orchestrate validate → scan → sign → publish/review | --asset-path, --asset-type, --tenant-id | Pipeline result JSON |
| Install Manager | tools/marketplace/install_manager.py | Install/update/uninstall assets with IL compatibility | --install/--uninstall/--update/--check-updates | Installation record |
| Search Engine | tools/marketplace/search_engine.py | Hybrid BM25 + semantic search (Ollama air-gapped) | --search query | Ranked results JSON |
| Review Queue [DEPRECATED] | tools/marketplace/review_queue.py | Human review workflow for cross-tenant sharing | --submit/--review/--pending | Review record JSON |
| Provenance Tracker | tools/marketplace/provenance_tracker.py | Supply chain provenance recording and verification | --record/--get/--verify/--report | Provenance chain JSON |
| Compatibility Checker | tools/marketplace/compatibility_checker.py | IL + version + dependency compatibility checks | --asset-id, --consumer-il | Compatibility result |
| Federation Sync | tools/marketplace/federation_sync.py | Sync tenant-local ↔ central vetted registry | --promote/--pull/--status | Sync result JSON |
| SkillHub Connector | tools/databridge/connectors/skillhub_connector.py | DataBridge connector for SkillHub API — vector search, skill detail, zip download | --search/--get/--download/--list/--health | Skill data JSON |
| OpenClaw ScriptGen | tools/marketplace/openclaw_scriptgen.py | Generate Python companion scripts for actionable skill steps (LLM-agnostic) | --generate/--analyze | Script + analysis JSON |
| OpenClaw Enricher | tools/marketplace/openclaw_enricher.py | 3-engine skill enrichment (Innovation + Creative + Research) with merge discovery | --enrich/--discover-similar | Enrichment result JSON |
| OpenClaw Compat | tools/marketplace/openclaw_compat.py | Compatibility checker & translator for OpenClaw → ICDEV™ skills | --check/--translate/--full, --output | Compat report / translated SKILL.md |
| OpenClaw Bridge | tools/marketplace/openclaw_bridge.py | Zero-trust import/export for SkillHub (skillhub.ai) skills with 10-gate scanning, quarantine, provenance | --import/--export/--promote/--reject/--list-quarantine/--list-exports/--health/--gate | Import/export/scan JSON |
| Marketplace MCP | tools/mcp/marketplace_server.py | MCP server (17 tools, 2 resources) for marketplace | stdio | JSON-RPC 2.0 |


## Marketplace (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Asset Installer | tools/marketplace/asset_installer.py | Install marketplace assets into project | --install, --json | Installation result |
| License Client | tools/marketplace/license_client.py | Offline license sync/verify/renew (D-MKT-S4) | (library) | License status |
| Module Runtime | tools/marketplace/module_runtime.py | Module gating runtime (D-MKT-S4) | (library) | is_module_enabled() |
| Token Store | tools/marketplace/token_store.py | Local JSON token cache (D-MKT-S4) | (library) | Token management |

