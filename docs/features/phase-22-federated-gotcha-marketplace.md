# Phase 22 — Federated GOTCHA Asset Marketplace

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 22 |
| Title | Federated GOTCHA Asset Marketplace |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 21 (SaaS Multi-Tenancy) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Government and defense software teams frequently build the same compliance skills, security scanning goals, CUI marking templates, and deployment hardprompts independently. Without a shared catalog, every tenant organization re-invents identical GOTCHA framework assets from scratch, wasting engineering cycles and introducing inconsistencies across programs of record. The problem is compounded by classification boundaries: sharing assets between IL4, IL5, and IL6 environments requires rigorous security scanning, digital signing, and human review to prevent unauthorized data movement.

Existing package managers (npm, PyPI, Helm Hub) were designed for open-source software distribution and lack the classification-aware, compliance-validated, governance-enforced publishing pipeline required for DoD/IC environments. They have no concept of Impact Level compatibility, CUI marking enforcement, SBOM attestation, or mandatory ISSO review for cross-organization sharing. A purpose-built federated marketplace that reuses the Phase 21 SaaS infrastructure (authentication, RBAC, tenant isolation) fills this gap.

The marketplace also closes the loop on the GOTCHA framework itself: skills, goals, hardprompts, context files, args configurations, and compliance extensions become first-class shareable artifacts with full provenance tracking, enabling a community-driven ecosystem that accelerates ATO timelines across the enterprise.

---

## 2. Goals

1. Enable customer developer communities to **publish, search, install, and review** GOTCHA framework assets (skills, goals, hardprompts, context, args, compliance extensions) through a federated marketplace
2. Enforce a **7-gate automated security pipeline** for all published assets covering SAST, secret detection, dependency audit, CUI markings, SBOM generation, supply chain provenance, and digital signing
3. Implement a **3-tier federated catalog** (tenant-local, cross-tenant review, central vetted registry) with promotion workflows and mandatory human ISSO review for cross-tenant sharing
4. Enforce **IL-aware compatibility** so assets marked at a given Impact Level cannot be consumed by lower-IL tenants without classification filtering
5. Provide **semantic search** (BM25 + vector embeddings) for asset discovery, with Ollama nomic-embed-text support for air-gapped environments
6. Track full **supply chain provenance** for every asset version, including publisher identity, scan results, review decisions, and installation history
7. Support **community ratings** and feedback per asset to surface quality signals across the ecosystem
8. Maintain an **append-only audit trail** for all marketplace operations (publish, install, review, rate) per NIST AU compliance

---

## 3. Architecture

### 3.1 Federated Three-Tier Catalog

```
CENTRAL VETTED REGISTRY (platform-level, curated, human-approved)
    ^  Promote (human ISSO review required)
    |
CROSS-TENANT REVIEW QUEUE (pending human approval)
    ^  Submit for cross-tenant sharing
    |
TENANT-LOCAL CATALOG (per-org private catalog)
    ^  Publish (7-gate automated scanning)
    |
DEVELOPER WORKSPACE (local asset development)
```

### 3.2 Shareable GOTCHA Asset Types

| Type | Format | Primary File |
|------|--------|-------------|
| Skill | SKILL.md + scripts/ + references/ + assets/ | SKILL.md |
| Goal | Workflow definition | goal.md |
| Hard Prompt | LLM instruction template | prompt.md |
| Context | Reference material | context.json / context.yaml |
| Args | Behavior settings | config.yaml |
| Compliance | Framework extensions / overlays | controls.json |

### 3.3 7-Gate Security Pipeline

```
Asset submitted
  -> Gate 1: SAST scan (bandit/ruff for Python, language-specific)
    -> Gate 2: Secret detection (pattern matching)
      -> Gate 3: Dependency audit (pip-audit, npm audit)
        -> Gate 4: CUI marking validation
          -> Gate 5: SBOM generation (CycloneDX)
            -> Gate 6: Supply chain provenance check
              -> Gate 7: Digital signature (RSA-SHA256)
                -> PUBLISHED (tenant-local) or QUEUED (cross-tenant)
```

---

## 4. Requirements

### 4.1 Publishing

#### REQ-22-001: Asset Publish Pipeline
The system SHALL validate, scan, and publish GOTCHA assets through a 7-gate security pipeline before making them available in the tenant-local catalog.

#### REQ-22-002: Cross-Tenant Promotion
The system SHALL require mandatory ISSO/security officer human review before promoting assets from a tenant-local catalog to the central vetted registry.

#### REQ-22-003: SBOM Generation
The system SHALL generate a CycloneDX SBOM for all executable assets (skills with scripts) at publish time.

### 4.2 Discovery and Installation

#### REQ-22-004: Hybrid Search
The system SHALL provide hybrid BM25 + semantic vector search for asset discovery, with Ollama nomic-embed-text support for air-gapped deployments.

#### REQ-22-005: IL Compatibility Check
The system SHALL enforce Impact Level compatibility during installation: consumer IL rank must be greater than or equal to asset IL rank (IL2=0 < IL4=1 < IL5=2 < IL6=3).

#### REQ-22-006: Dependency Resolution
The system SHALL resolve asset dependencies during installation and warn when required dependencies are missing.

### 4.3 Governance

#### REQ-22-007: Conflict of Interest Prevention
The system SHALL prevent the publisher of an asset from serving as its cross-tenant reviewer.

#### REQ-22-008: Community Ratings
The system SHALL support one rating per tenant per asset, enabling community-driven quality signals.

#### REQ-22-009: Immutable Versions
The system SHALL enforce version immutability: once published, a version cannot be modified; new changes require a new version.

#### REQ-22-010: Digital Signing
The system SHALL compute RSA-SHA256 digital signatures for all published assets to enable integrity verification at installation time.

### 4.4 Federation

#### REQ-22-011: Federation Sync
The system SHALL support bidirectional federation between tenant-local catalogs and the central vetted registry, including promote and pull operations.

#### REQ-22-012: Provenance Tracking
The system SHALL maintain a complete provenance chain for every asset version including publisher identity, scan results, review decisions, and installation history.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `marketplace_assets` | Core asset registry (slug, type, tenant, IL, status) |
| `marketplace_versions` | Immutable version history per asset |
| `marketplace_reviews` | Human review queue with append-only decisions |
| `marketplace_installations` | Per-tenant installation tracking |
| `marketplace_scan_results` | Security scan results per version (7 gates) |
| `marketplace_ratings` | Community ratings (one per tenant per asset) |
| `marketplace_embeddings` | Vector embeddings for semantic search |
| `marketplace_dependencies` | Asset dependency graph (adjacency list) |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/marketplace/catalog_manager.py` | CRUD for assets and versions |
| `tools/marketplace/asset_scanner.py` | 7-gate security scanning pipeline |
| `tools/marketplace/publish_pipeline.py` | Orchestrate scan, validate, sign, publish |
| `tools/marketplace/install_manager.py` | Install/update/uninstall with dependency resolution |
| `tools/marketplace/search_engine.py` | Hybrid BM25 + semantic search (Ollama air-gapped) |
| `tools/marketplace/review_queue.py` | Human review workflow management |
| `tools/marketplace/provenance_tracker.py` | Supply chain provenance tracking |
| `tools/marketplace/compatibility_checker.py` | IL + version + dependency compatibility |
| `tools/marketplace/federation_sync.py` | Tenant-local to central registry sync |
| `tools/mcp/marketplace_server.py` | MCP server (11 tools, 2 resources) |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D74 | Marketplace is a SaaS module (reuses Phase 21 auth, RBAC, tenant isolation) | Avoids duplicating authentication and tenant management infrastructure |
| D75 | Federated 3-tier catalog (local, shared, central) | Balances org-level autonomy with enterprise-wide sharing |
| D76 | 7-gate automated + mandatory human review for cross-tenant sharing | Automated scanning catches known issues; human review catches intent/context issues |
| D77 | Independent IL marking per asset with high-watermark consumption rule | Prevents lower-classification environments from consuming higher-classification assets |
| D78 | Ollama nomic-embed-text for air-gapped semantic search | Air-gap safe vector search without cloud dependency |
| D79 | Full GOTCHA asset sharing (skills, goals, hardprompts, context, args, compliance) | Maximizes reuse across the entire framework, not just skills |
| D80 | Append-only marketplace audit trail | NIST AU compliance; full traceability of all marketplace operations |
| D81 | Asset SBOM required for executable assets | Supply chain traceability for assets containing runnable code |

---

## 8. Security Gate

**Marketplace Publish Gate:**
- 0 critical/high SAST findings
- 0 secrets detected
- 0 critical/high dependency vulnerabilities
- CUI markings present on all source files
- SBOM generated for executable assets
- Digital signature computed

**Marketplace Cross-Tenant Gate:**
- All publish gate requirements met
- Human ISSO/security officer review completed
- Code review confirmed
- Compliance review confirmed

**IL Compatibility Gate:**
- Consumer IL rank >= asset IL rank
- Asset classification marking appropriate for consumer environment

---

## 9. Commands

```bash
# Publish a skill to tenant-local catalog
python tools/marketplace/publish_pipeline.py --asset-path /path --asset-type skill \
  --tenant-id "tenant-abc" --publisher-user "user@mil" --json

# Search the marketplace
python tools/marketplace/search_engine.py --search "STIG checker" --json

# Check IL compatibility
python tools/marketplace/compatibility_checker.py --asset-id "asset-abc" \
  --consumer-il IL5 --json

# Install an asset
python tools/marketplace/install_manager.py --install --asset-id "asset-abc" \
  --tenant-id "tenant-abc" --json

# Review queue (ISSO/security officer)
python tools/marketplace/review_queue.py --pending --json
python tools/marketplace/review_queue.py --review --review-id "rev-abc" \
  --reviewer-id "isso@mil" --decision approved --rationale "Passed review" --json

# Federation sync
python tools/marketplace/federation_sync.py --status --json
python tools/marketplace/federation_sync.py --promote --tenant-id "tenant-abc" --json
python tools/marketplace/federation_sync.py --pull --tenant-id "tenant-abc" \
  --consumer-il IL5 --json

# Security scanning
python tools/marketplace/asset_scanner.py --asset-id "asset-abc" \
  --version-id "ver-abc" --asset-path /path --json

# Catalog management
python tools/marketplace/catalog_manager.py --list --asset-type skill --json
python tools/marketplace/catalog_manager.py --get --slug "tenant-abc/my-skill" --json

# Provenance report
python tools/marketplace/provenance_tracker.py --report --asset-id "asset-abc" --json
```
