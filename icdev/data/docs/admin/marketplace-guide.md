# Marketplace Administration Guide

> CUI // SP-CTI

## Overview

The ICDEV Federated GOTCHA Marketplace (Phase 22) enables customer developer communities to share reusable assets across tenant organizations with mandatory security scanning, compliance validation, and governance enforcement. The marketplace operates entirely within air-gapped environments and integrates with the Phase 21 SaaS multi-tenancy infrastructure.

### Supported Asset Types

| Type | Description |
|------|-------------|
| `skill` | Claude Code slash commands and workflows |
| `goal` | GOTCHA goal workflow definitions |
| `hardprompt` | Reusable LLM instruction templates |
| `context` | Static reference material (tone rules, ICP descriptions, case studies) |
| `args` | YAML/JSON behavior configuration |
| `compliance` | Compliance framework extensions and catalog updates |

### Architecture: 3-Tier Federated Catalog

1. **Tenant-Local** -- Assets published within a single tenant organization. No external review required.
2. **Cross-Tenant Review** -- Assets promoted from a tenant catalog for sharing. Requires human ISSO/security officer review.
3. **Central Vetted Registry** -- Fully reviewed, signed assets available to all authorized tenants.

Each tier applies progressively stricter security scanning and governance gates.

---

## Publishing Assets

### 9-Gate Security Pipeline

Every asset passes through the following gates before publication:

| Gate | Check | Blocking |
|------|-------|----------|
| 1 | SAST scan (bandit for Python, eslint-security for JS/TS) | Yes -- 0 critical/high findings |
| 2 | Secret detection (detect-secrets) | Yes -- 0 secrets |
| 3 | Dependency vulnerability scan (pip-audit, npm audit) | Yes -- 0 critical/high vulns |
| 4 | CUI markings validation | Yes -- markings must be present |
| 5 | SBOM generation (CycloneDX) | Yes -- SBOM required |
| 6 | Digital signature | Yes -- must be signed |
| 7 | Human ISSO/security officer review (cross-tenant only) | Yes -- approval required |
| 8 | Prompt injection scan | Yes -- 0 high-confidence injection patterns |
| 9 | Behavioral sandbox | Warning -- 0 critical dangerous code patterns (eval, exec, os.system) |

### Publish a Skill

```bash
python tools/marketplace/publish_pipeline.py \
  --asset-path /path/to/skill \
  --asset-type skill \
  --tenant-id "tenant-abc" \
  --publisher-user "user@mil" \
  --json
```

### Publish a Compliance Extension

```bash
python tools/marketplace/publish_pipeline.py \
  --asset-path /path/to/compliance-ext \
  --asset-type compliance \
  --tenant-id "tenant-abc" \
  --publisher-user "isso@mil" \
  --json
```

### Publish Gate Results

The publish pipeline returns a JSON result with per-gate status:

```json
{
  "asset_id": "asset-abc-123",
  "version_id": "ver-001",
  "gates": {
    "sast": {"status": "passed", "findings": 0},
    "secrets": {"status": "passed", "findings": 0},
    "dependencies": {"status": "passed", "critical": 0, "high": 0},
    "cui_markings": {"status": "passed"},
    "sbom": {"status": "passed", "components": 12},
    "signature": {"status": "passed"},
    "isso_review": {"status": "not_required"},
    "prompt_injection": {"status": "passed", "patterns": 0},
    "behavioral_sandbox": {"status": "passed", "warnings": 0}
  },
  "overall": "published"
}
```

---

## Searching the Marketplace

### Semantic Search

The search engine uses `nomic-embed-text` embeddings via Ollama for air-gapped environments. Searches match against asset metadata, descriptions, and README content.

```bash
# Search by keyword/concept
python tools/marketplace/search_engine.py --search "STIG checker" --json

# Search by asset type
python tools/marketplace/search_engine.py --search "compliance" --asset-type compliance --json

# Search with tenant filter
python tools/marketplace/search_engine.py --search "deployment pipeline" --tenant-id "tenant-abc" --json
```

### Search Configuration

Search weights are configured in `args/marketplace_config.yaml`:

```yaml
search:
  weights:
    name_match: 0.3
    description_match: 0.25
    semantic_similarity: 0.25
    rating: 0.1
    downloads: 0.1
```

---

## Installing Assets

### Install an Asset

```bash
python tools/marketplace/install_manager.py \
  --install \
  --asset-id "asset-abc" \
  --tenant-id "tenant-abc" \
  --json
```

### Uninstall an Asset

```bash
python tools/marketplace/install_manager.py \
  --uninstall \
  --asset-id "asset-abc" \
  --tenant-id "tenant-abc" \
  --json
```

### Installation Rules

- Assets are installed into the appropriate GOTCHA layer directory (`goals/`, `tools/`, `context/`, etc.)
- IL compatibility is enforced at install time (see below)
- Dependency resolution is automatic -- if Asset B depends on Asset A, Asset A is installed first
- Installed assets are recorded in the `marketplace_installations` table with full provenance

---

## IL Compatibility

The marketplace enforces the **high-watermark consumption rule** (D77): an asset marked at a given Impact Level can only be installed by tenants at that level or higher.

### Check Compatibility

```bash
python tools/marketplace/compatibility_checker.py \
  --asset-id "asset-abc" \
  --consumer-il IL5 \
  --json
```

### IL Compatibility Matrix

| Asset IL | Consumer IL2 | Consumer IL4 | Consumer IL5 | Consumer IL6 |
|----------|:---:|:---:|:---:|:---:|
| IL2 | Yes | Yes | Yes | Yes |
| IL4 | No | Yes | Yes | Yes |
| IL5 | No | No | Yes | Yes |
| IL6 | No | No | No | Yes |

---

## Review Queue

Cross-tenant asset sharing requires mandatory human review by an ISSO or security officer. The review queue tracks all pending reviews.

### List Pending Reviews

```bash
python tools/marketplace/review_queue.py --pending --json
```

### Approve an Asset

```bash
python tools/marketplace/review_queue.py \
  --review \
  --review-id "rev-abc" \
  --reviewer-id "isso@mil" \
  --decision approved \
  --rationale "Passed security review. No CUI leakage. SAST clean." \
  --json
```

### Reject an Asset

```bash
python tools/marketplace/review_queue.py \
  --review \
  --review-id "rev-abc" \
  --reviewer-id "isso@mil" \
  --decision rejected \
  --rationale "Hardcoded credentials detected in context file line 42." \
  --json
```

### Cross-Tenant Gate Requirements

All publish gate requirements (Gates 1-6, 8-9) must pass, plus:
- Human ISSO/security officer review completed and approved (Gate 7)
- Code review confirmed by reviewer

---

## Federation Sync

Federation enables assets to move between tenant catalogs and the central vetted registry.

### Check Federation Status

```bash
python tools/marketplace/federation_sync.py --status --json
```

### Promote an Asset to Cross-Tenant Catalog

```bash
python tools/marketplace/federation_sync.py \
  --promote \
  --tenant-id "tenant-abc" \
  --json
```

### Pull Assets from Central Registry

```bash
python tools/marketplace/federation_sync.py \
  --pull \
  --tenant-id "tenant-abc" \
  --consumer-il IL5 \
  --json
```

### Sync Workflow

1. Tenant publishes asset locally (passes Gates 1-6, 8-9).
2. Tenant admin promotes asset to cross-tenant catalog.
3. ISSO/security officer reviews and approves (Gate 7).
4. Asset becomes available in central vetted registry.
5. Other tenants pull from registry, subject to IL compatibility.

---

## Asset Scanning

Run security scans against a specific asset version at any time.

```bash
python tools/marketplace/asset_scanner.py \
  --asset-id "asset-abc" \
  --version-id "ver-abc" \
  --asset-path /path/to/asset \
  --json
```

Scan results are stored in the `marketplace_scan_results` table and linked to the asset version.

### Re-scan After Update

When an asset is updated, re-scanning is mandatory before the new version is published. The publish pipeline handles this automatically.

---

## Catalog Management

### List Assets by Type

```bash
python tools/marketplace/catalog_manager.py --list --asset-type skill --json
```

### Get Asset Details

```bash
python tools/marketplace/catalog_manager.py --get --slug "tenant-abc/my-skill" --json
```

### List All Assets

```bash
python tools/marketplace/catalog_manager.py --list --json
```

---

## Provenance Tracking

Every marketplace action (publish, install, review, rate) is recorded in an append-only audit trail per D6/D80.

### Generate Provenance Report

```bash
python tools/marketplace/provenance_tracker.py --report --asset-id "asset-abc" --json
```

The report includes:
- Full publication history (who, when, which gates passed)
- All installations across tenants
- Review decisions with rationale
- Rating history
- Federation sync events

---

## Configuration Reference

### args/marketplace_config.yaml

| Key | Description | Default |
|-----|-------------|---------|
| `scan_gates.sast_max_critical` | Max critical SAST findings | `0` |
| `scan_gates.sast_max_high` | Max high SAST findings | `0` |
| `scan_gates.secrets_max` | Max secrets detected | `0` |
| `scan_gates.dep_max_critical` | Max critical dep vulns | `0` |
| `approval_policies.cross_tenant_review` | Require ISSO review for cross-tenant | `true` |
| `federation.sync_interval_minutes` | Federation sync interval | `60` |
| `search.weights` | Search scoring weights | See above |
| `il_compatibility.high_watermark` | Enforce IL high-watermark | `true` |
| `community_ratings.min_reviews_for_featured` | Min reviews to feature | `3` |

### Database Tables

| Table | Purpose |
|-------|---------|
| `marketplace_assets` | Asset metadata, type, tenant, status |
| `marketplace_versions` | Versioned asset content, checksums |
| `marketplace_reviews` | ISSO review decisions and rationale |
| `marketplace_installations` | Installation records per tenant |
| `marketplace_scan_results` | Security scan results per version |
| `marketplace_ratings` | Community ratings and feedback |
| `marketplace_embeddings` | Semantic search embeddings |
| `marketplace_dependencies` | Asset dependency graph |

### Security Gates Reference

| Gate | Config Key in security_gates.yaml |
|------|-----------------------------------|
| Marketplace Publish | `marketplace_publish` |
| Marketplace Cross-Tenant | `marketplace_cross_tenant` |
| Prompt Injection (Gate 8) | `marketplace_prompt_injection` |
| Behavioral Sandbox (Gate 9) | `marketplace_behavioral_sandbox` |

---

## Operational Procedures

### Adding a New Asset Type

1. Add the type definition to `args/marketplace_config.yaml`.
2. Update the publish pipeline to handle the new type.
3. Update the install manager to place assets in the correct directory.
4. Add the type to `tools/manifest.md`.

### Handling Rejected Assets

1. Publisher receives rejection rationale via review queue output.
2. Publisher fixes the identified issues.
3. Publisher re-submits via `publish_pipeline.py` (new version created).
4. New version enters the review queue.

### Emergency Asset Revocation

If a published asset is found to contain a vulnerability post-publication:

1. Check all installations: `catalog_manager.py --get --slug "tenant/asset"`.
2. Notify all consuming tenants via the audit trail.
3. Uninstall from affected tenants: `install_manager.py --uninstall`.
4. Remove from federation: update asset status in catalog.

### Audit Compliance

All marketplace operations are recorded in the append-only audit trail. For NIST AU compliance reporting, query the audit trail:

```bash
python tools/audit/audit_query.py --project "marketplace" --format json
```
