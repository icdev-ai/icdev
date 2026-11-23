# Marketplace (Phase 22)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Marketplace (Phase 22)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Catalog Manager | tools/marketplace/catalog_manager.py | CRUD for marketplace assets and versions (icx-ent-04: `--update-status --status published` and `--promote` are RETIRED and refuse) | --register/--list/--get/--add-version/--deprecate | Asset record JSON |
| Asset Scanner | tools/marketplace/asset_scanner.py | 7-gate security scanning pipeline (SAST, secrets, deps, CUI, SBOM, provenance, signature) | --asset-id, --version-id, --asset-path | Scan results JSON |
| Install Manager | tools/marketplace/install_manager.py | Install/update/uninstall assets with IL compatibility | --install/--uninstall/--update/--check-updates | Installation record |
| Search Engine | tools/marketplace/search_engine.py | Hybrid BM25 + semantic search (Ollama air-gapped) | --search query | Ranked results JSON |
| Provenance Tracker | tools/marketplace/provenance_tracker.py | Supply chain provenance recording and verification | --record/--get/--verify/--report | Provenance chain JSON |
| Compatibility Checker | tools/marketplace/compatibility_checker.py | IL + version + dependency compatibility checks | --asset-id, --consumer-il | Compatibility result |
| Federation Sync | tools/marketplace/federation_sync.py | Sync tenant-local ↔ central vetted registry (icx-ent-04: `--promote` is RETIRED and refuses) | --promote/--pull/--status | Sync result JSON |
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

## AAC Skill Pack (FORGE Marketplace)
| Spec / Tool | File | Description | Input | Output |
|-------------|------|-------------|-------|--------|
| AAC Skill Pack Spec | docs/marketplace/aac-skill-pack-spec.md | Bundle format spec for framework-specific AAC rule packs (manifest, Semgrep rules, pattern catalog, guardrails) | (documentation) | Format definition |
| AAC Pack Validator | tools/marketplace/aac_pack_validator.py | Validate AAC Skill Pack manifest, rules, and extensions | --input, --strict | Validation report JSON |
| AAC Pack Builder | tools/marketplace/aac_pack_builder.py | Sign and package AAC Skill Packs into `.aacsp` archives | --input, --sign, --output | Signed `.aacsp` file |

## Store Publisher (icx-ent-04; replaces the retired publish pipeline and review queue)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Store Publisher | tools/store_publisher/service.py | One signed `icos-store-package/v1` (ICDEV[OS]'s contract, vendored and pinned, `context/icos/store-package.schema.json`) per package declared in `args/store_publisher.yaml` -- an agent, crew, skill, MCP server or plugin -- with its publisher identity, an A2A Agent Card JWS-signed (ES384 over RFC 8785 JCS) by the publisher's OS-keyring key that binds the package's content digest (`icos.store/package_digest`) and whose skills are exactly its declared tools, its SBOM and a dependency report over exactly that SBOM, its ceilings and budget, the publisher's red-team report, and ICDEV's seven marketplace scanner gates run on it (`gates.py`; a gate that cannot run is unverified). A person prepares (never an agent); another person of the package's org publishes (`icos-store-publish`); `export` gives `{bundle, publication}` for ICDEV[OS]'s one shared bundle import only while that step is recorded. Append-only, hash-chained `sp_events` (`ledger.py`, on `tools/common/stream_chain.py`). Page `/store-publisher/`; MCP `store_publisher_status` (read). | (console) | icos-store-package/v1 + publication |
| Store Publisher CLI | tools/store_publisher/cli.py | `python -m tools.store_publisher.cli check-config\|packages\|seed-synthetic`. Never prepares, publishes or exports (a person takes the bundle out, from the console). | command, --json | JSON |
| Store Card Signer | tools/store_publisher/card.py | icx-ent-04. The publisher's P-384 Agent Card key from `keyring:<service>/<name>` (no key is refused, never an unsigned card) and the card's JWS exactly as ICDEV[OS]'s `icdev_os/a2a/card_sign.py` verifies it; every signature verified before it is returned; the key is never printed. | (import) | signed card |
| JCS (RFC 8785) | tools/common/jcs.py | icx-ent-04 review M4: the ONE canonical-JSON and base64url copy, used by the store card signer and `tools/modernization/icos_decisions.py`. | (import) | canonical JSON |
| Confined Reader | tools/common/confined_read.py | Read a file under a reviewed root: `openat` component by component with O_NOFOLLOW, O_NONBLOCK, a regular file with one link, size-capped, read once. | (import) | bytes |

