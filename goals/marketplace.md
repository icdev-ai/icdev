# CUI // SP-CTI
# Goal: Federated GOTCHA Asset Marketplace (Phase 22)

## Purpose
Enable customer developer communities to share skills, plugins, goals, hardprompts,
context files, and compliance extensions through a federated marketplace with
mandatory security, compliance, and governance enforcement.

## Architecture

### Federated Three-Tier Catalog
```
CENTRAL VETTED REGISTRY (platform-level, curated, human-approved)
    ↑ Promote (human review required)
TENANT-LOCAL CATALOG (per-org private catalog)
    ↑ Publish (automated scanning)
DEVELOPER WORKSPACE (local asset development)
```

### Shareable GOTCHA Asset Types
| Type | Format | Primary File |
|------|--------|-------------|
| Skill | SKILL.md + scripts/ + references/ + assets/ | SKILL.md |
| Goal | Workflow definition | goal.md |
| Hard Prompt | LLM instruction template | prompt.md |
| Context | Reference material | context.json / context.yaml |
| Args | Behavior settings | config.yaml |
| Compliance | Framework extensions / overlays | controls.json |

### Key Decisions
- **D74:** Marketplace is SaaS module (reuse Phase 21 auth, RBAC, tenant isolation)
- **D75:** Federated 3-tier catalog (local → shared → central)
- **D76:** 7-gate automated + human review for cross-tenant sharing
- **D77:** Independent IL marking per asset with high-watermark consumption
- **D78:** Ollama nomic-embed-text for air-gapped semantic search
- **D79:** Full GOTCHA asset sharing (not just skills)
- **D80:** Append-only marketplace audit (NIST AU compliance)
- **D81:** Asset SBOM required for executable assets

## Workflow

### Publishing (Tenant-Local)
1. Developer prepares asset directory with required structure
2. Run `python tools/marketplace/publish_pipeline.py --asset-path <path> --asset-type <type> --tenant-id <id>`
3. Pipeline validates structure and parses metadata
4. Pipeline runs 7-gate security scanning:
   - Gate 1: SAST scan (bandit/ruff for Python)
   - Gate 2: Secret detection (pattern matching)
   - Gate 3: Dependency audit (pip-audit)
   - Gate 4: CUI marking validation
   - Gate 5: SBOM generation (CycloneDX)
   - Gate 6: Supply chain provenance check
   - Gate 7: Digital signature readiness (RSA-SHA256)
5. If all blocking gates pass → auto-publish to tenant-local catalog
6. If any blocking gate fails → reject with detailed findings

### Cross-Tenant Sharing (Central Registry)
1. Publish with `--target-tier central_vetted`
2. All 7 gates must pass
3. Asset enters review queue
4. ISSO/security officer reviews:
   - Scan results verified
   - Code reviewed
   - Compliance validated
5. Reviewer approves/rejects/conditionals
6. On approval → promoted to central_vetted catalog
7. Available to all tenants (subject to IL compatibility)

### Installation
1. Search: `python tools/marketplace/search_engine.py --search "query"`
2. Check compatibility: `python tools/marketplace/compatibility_checker.py --asset-id <id> --consumer-il IL5`
3. Install: `python tools/marketplace/install_manager.py --install --asset-id <id> --tenant-id <id>`
4. Pipeline checks IL compatibility, copies files, records installation

### Federation Sync
1. `python tools/marketplace/federation_sync.py --status` — check sync state
2. `python tools/marketplace/federation_sync.py --promote --tenant-id <id>` — promote approved assets
3. `python tools/marketplace/federation_sync.py --pull --tenant-id <id> --consumer-il IL5` — discover available assets

## Tools

| Tool | Purpose |
|------|---------|
| `tools/marketplace/catalog_manager.py` | CRUD for assets and versions |
| `tools/marketplace/asset_scanner.py` | 7-gate security scanning pipeline |
| `tools/marketplace/publish_pipeline.py` | Orchestrate scan + validate + sign + publish |
| `tools/marketplace/install_manager.py` | Install/update/uninstall with dependency resolution |
| `tools/marketplace/search_engine.py` | Hybrid BM25 + semantic search (Ollama air-gapped) |
| `tools/marketplace/review_queue.py` | Human review workflow management |
| `tools/marketplace/provenance_tracker.py` | Supply chain provenance tracking |
| `tools/marketplace/compatibility_checker.py` | IL + version + dependency compatibility |
| `tools/marketplace/federation_sync.py` | Tenant-local ↔ central registry sync |
| `tools/mcp/marketplace_server.py` | MCP server (11 tools, 2 resources) |

## Database Tables (8 new in icdev.db)

| Table | Purpose |
|-------|---------|
| `marketplace_assets` | Core asset registry |
| `marketplace_versions` | Immutable version history |
| `marketplace_reviews` | Human review queue (append-only decisions) |
| `marketplace_installations` | Per-tenant installation tracking |
| `marketplace_scan_results` | Security scan results per version |
| `marketplace_ratings` | Community ratings (one per tenant per asset) |
| `marketplace_embeddings` | Vector embeddings for semantic search |
| `marketplace_dependencies` | Asset dependency graph (adjacency list) |

## Security Gates

### Marketplace Publish Gate
- 0 critical/high SAST findings
- 0 secrets detected
- 0 critical/high dependency vulnerabilities
- CUI markings present on all source files
- SBOM generated for executable assets
- Digital signature computed

### Marketplace Cross-Tenant Gate
- All publish gate requirements met
- Human ISSO/security officer review completed
- Code review confirmed
- Compliance review confirmed

### IL Compatibility Gate
- Consumer IL rank >= asset IL rank (IL2=0 < IL4=1 < IL5=2 < IL6=3)
- Asset classification marking appropriate for consumer environment

## Configuration
- `args/marketplace_config.yaml` — Scan thresholds, approval policies, federation settings
- `context/marketplace/asset_schema.json` — Extended asset metadata schema with Gov/DoD fields

## Integration Points
- SaaS API Gateway (Phase 21) — `/api/v1/marketplace/*` endpoints
- RBAC — publisher, reviewer, consumer roles
- Artifact Signer — RSA-SHA256 digital signatures
- Security Pipeline — SAST, deps, secrets reused as-is
- Classification Manager — CUI markings at publish time
- Crosswalk Engine — Compliance validation
- Supply Chain Tools — Dependency graph and SCRM
- Audit Logger — Immutable marketplace audit trail
- Approval Manager — Human review workflow
- Memory Search — BM25 + semantic search pattern

## Error Handling
- If scanning fails mid-pipeline: asset stays in 'draft', retry scanning
- If review is rejected: asset stays in 'draft' with rationale
- If installation fails IL check: clear error message with allowed ILs
- If dependency not found: list missing deps with resolution hints
- If federation sync fails: retry with exponential backoff

## Edge Cases
- Asset with same slug already exists → error with existing asset details
- Version already published → immutable, must publish new version
- Reviewer is also the publisher → reject (conflict of interest)
- Asset deprecated but still installed → notify tenants, don't auto-uninstall
- IL6/SECRET asset → requires SIPR network, restricted to IL6 tenants only
