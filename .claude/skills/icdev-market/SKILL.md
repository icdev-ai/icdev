---
name: icdev-market
description: "Manage the ICDEV Federated GOTCHA Asset Marketplace — publish, install, search, review, and sync skills, goals, hardprompts, context, args, and compliance extensions across tenant organizations."
context: fork
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite"]
---

# ICDEV Marketplace Manager

CUI // SP-CTI

## Overview
The ICDEV Marketplace is a federated GOTCHA asset registry where customer developer communities share skills, plugins, goals, hardprompts, context files, and compliance extensions — with mandatory security, compliance, and governance enforcement.

## Before Starting
1. Read `goals/marketplace.md` for the full workflow
2. Read `args/marketplace_config.yaml` for configuration
3. Ensure the ICDEV database is initialized (`python tools/db/init_icdev_db.py`)

## Available Operations

### Publish an Asset
```bash
# Publish to tenant-local catalog (auto-approved on passing gates)
python tools/marketplace/publish_pipeline.py \
    --asset-path /path/to/asset \
    --asset-type skill \
    --tenant-id "tenant-abc" \
    --publisher-user "user@mil" --json

# Publish to central registry (requires human review)
python tools/marketplace/publish_pipeline.py \
    --asset-path /path/to/asset \
    --asset-type skill \
    --tenant-id "tenant-abc" \
    --target-tier central_vetted --json
```

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

### Review Queue (ISSO/Security Officer)
```bash
# List pending reviews
python tools/marketplace/review_queue.py --pending --json

# Complete a review
python tools/marketplace/review_queue.py --review \
    --review-id "rev-abc" --reviewer-id "isso@mil" \
    --decision approved --rationale "Code reviewed, gates passed" --json
```

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

### Provenance
```bash
python tools/marketplace/provenance_tracker.py --report --asset-id "asset-abc" --json
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

1. **User wants to publish** → Run publish_pipeline.py
   - Validates structure → Scans 7 gates → Signs → Publishes or submits for review
2. **User wants to find assets** → Run search_engine.py
   - Hybrid BM25 + semantic search across name, description, tags
3. **User wants to install** → Run compatibility_checker.py first, then install_manager.py
   - Checks IL compatibility → Copies files → Records installation
4. **ISSO wants to review** → Run review_queue.py --pending, then --review
   - Lists pending → Reviews scan results + code → Approves/rejects
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
