---
name: icdev-market
description: "Manage the ICDEV™ Federated FORGE Asset Marketplace — publish, install, search, and sync skills and compliance extensions. Use when publishing or installing marketplace assets."
context: fork
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite"]
---

# ICDEV™ Marketplace Manager

CUI // SP-CTI

## Overview
The ICDEV™ Marketplace is a federated FORGE asset registry where customer developer communities share skills, plugins, goals, hardprompts, context files, and compliance extensions — with mandatory security, compliance, and governance enforcement.

## Before Starting
1. Read `goals/marketplace.md` for the full workflow
2. Read `args/marketplace_config.yaml` for configuration
3. Ensure the ICDEV™ database is initialized (`python tools/db/init_icdev_db.py`)

## Available Operations

### Publish an Asset -- RETIRED (icx-ent-04), deleted (icx-ent-13)
`publish_pipeline.py` is deleted (icx-ent-13): there is no auto-publish path, and no agent publishes. A person
prepares a signed ICDEV[OS] store package on `/store-publisher/` and another person publishes it; read them with
`python -m tools.store_publisher.cli packages --json` (or the MCP tool `store_publisher_status`).
_Deleted at icx-ent-13: `tools/marketplace/publish_pipeline.py` no longer exists; a person prepares a signed store package with the store publisher (`/store-publisher/`, `python -m tools.store_publisher.cli`) and another person publishes it._

### Search the Marketplace
```bash
python tools/marketplace/search_engine.py --search "STIG compliance checker" --json
python tools/marketplace/search_engine.py --search "authentication" --asset-type skill --json
```

### Install an Asset
```bash
# Check compatibility first
python tools/marketplace/compatibility_checker.py \
    --asset-id "asset-abc" --consumer-il IL5 --json

# Install
python tools/marketplace/install_manager.py --install \
    --asset-id "asset-abc" --tenant-id "tenant-abc" \
    --project-id "proj-123" --json
```

### Review Queue (ISSO/Security Officer) -- RETIRED (icx-ent-04), deleted (icx-ent-13)
A store package is reviewed and certified in ICDEV[OS], by a person.
_Deleted at icx-ent-13: `tools/marketplace/review_queue.py` no longer exists; a store package is reviewed and certified in ICDEV[OS] (its `store-review` workflow), never here._

### Federation Sync
```bash
# Check sync status
python tools/marketplace/federation_sync.py --status --json

# Promote approved assets to central
python tools/marketplace/federation_sync.py --promote --tenant-id "tenant-abc" --json

# List available central assets for a tenant
python tools/marketplace/federation_sync.py --pull --tenant-id "tenant-abc" --consumer-il IL5 --json
```

### Security Scanning
```bash
python tools/marketplace/asset_scanner.py \
    --asset-id "asset-abc" --version-id "ver-abc" \
    --asset-path /path/to/asset --json
```

### Catalog Management
```bash
# List assets
python tools/marketplace/catalog_manager.py --list --json
python tools/marketplace/catalog_manager.py --list --asset-type skill --catalog-tier central_vetted --json

# Get asset details
python tools/marketplace/catalog_manager.py --get --slug "tenant-abc/my-skill" --json

# Deprecate an asset
python tools/marketplace/catalog_manager.py --deprecate --asset-id "asset-abc" --json
```

## Workflow Decision Tree

1. **User wants to publish** → a PERSON prepares a store package on /store-publisher/; ANOTHER person publishes it
   - The agent only reads (`store_publisher_status`); publish_pipeline.py is deleted (icx-ent-13)
2. **User wants to find assets** → Run search_engine.py
   - Hybrid BM25 + semantic search across name, description, tags
3. **User wants to install** → Run compatibility_checker.py first, then install_manager.py
   - Checks IL compatibility → Copies files → Records installation
4. **ISSO wants to review** → in ICDEV[OS] (its store-review workflow); review_queue.py is deleted (icx-ent-13)
5. **Admin wants sync status** → Run federation_sync.py --status
   - Shows catalog tiers, pending reviews, eligible promotions

## Security Gates (7 Automated)
1. SAST scan (0 critical/high)
2. Secret detection (0 findings)
3. Dependency audit (0 critical/high vulns)
4. CUI marking validation (all source files marked)
5. SBOM generation (for executable assets)
6. Supply chain provenance (version pinning check)
7. Digital signature (RSA-SHA256)

## Error Handling
- If publish fails on scanning: show which gate failed and specific findings
- If install fails on IL: show consumer IL vs asset IL with allowed levels
- If review is rejected: show rationale and suggest fixes
- If search returns no results: suggest broader query or different filters
