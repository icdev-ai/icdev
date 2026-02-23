# Phase 21 — SaaS Multi-Tenancy

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 21 |
| Title | SaaS Multi-Tenancy |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 17 (Multi-Framework Compliance), Phase 20 (FIPS 199/200 Security Categorization) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV was originally designed as a single-tenant CLI tool running on a developer's local machine. As adoption grows across government programs, defense contractors, and compliance-focused organizations, each team must maintain its own separate ICDEV installation. This creates operational overhead (installation, updates, configuration), inconsistent environments, and no ability to share compliance artifacts, patterns, or tooling across organizational boundaries.

Transforming ICDEV into a multi-tenant SaaS platform requires solving multiple hard problems simultaneously: per-tenant data isolation that satisfies DoD classification requirements (IL2 through IL6), three distinct authentication methods (API keys, OAuth/OIDC, CAC/PIV), dual API transport (REST for generic clients and MCP Streamable HTTP for Claude Code users), subscription-based feature gating, and on-premises deployment for air-gapped environments via Helm charts.

The SaaS layer must wrap existing tools without rewriting them. Twenty phases of deterministic Python scripts must continue to function identically -- the API gateway adds auth, tenant resolution, and routing, while tools remain stateless and tenant-unaware. Each REST or MCP endpoint resolves the tenant, routes to their isolated database, calls the existing Python tool, and returns the result.

---

## 2. Goals

1. Expose ICDEV compliance automation tools as a multi-tenant SaaS platform via REST API and MCP Streamable HTTP transport
2. Implement 3 authentication methods: API key (SHA-256 hashed), OAuth 2.0/OIDC (JWT + JWKS), and CAC/PIV (X.509 certificate CN lookup)
3. Enforce per-tenant database isolation: dedicated SQLite (dev) or PostgreSQL (prod) per tenant, not row-level separation
4. Support 3 subscription tiers (Starter, Professional, Enterprise) with feature gating, rate limiting, and impact level restrictions
5. Provide a tenant admin portal for self-service management of projects, users, API keys, and compliance posture
6. Support on-premises deployment via Helm chart with RSA-SHA256 offline license validation for air-gapped environments
7. Implement RBAC with 5 roles (tenant_admin, developer, compliance_officer, auditor, viewer) across 9 permission categories
8. Scale tenant isolation by classification: shared K8s namespace for IL2-IL4, dedicated node pool for IL5, dedicated AWS sub-account for IL6

---

## 3. Architecture

```
                              +-------------------+
                              |  Load Balancer    |
                              |  (TLS termination)|
                              +--------+----------+
                                       |
                              +--------v----------+
                              |  API Gateway      |
                              |  (Flask, port 8443)|
                              +--------+----------+
                                       |
                    +------------------+------------------+
                    |                                     |
           +--------v----------+              +-----------v--------+
           |  REST API         |              |  MCP Streamable    |
           |  /api/v1/*        |              |  HTTP /mcp/v1/     |
           +--------+----------+              +-----------+--------+
                    |                                     |
                    +------------------+------------------+
                                       |
                              +--------v----------+
                              |  Auth Middleware   |
                              |  (API key/OAuth/   |
                              |   CAC/PIV)         |
                              +--------+----------+
                                       |
                              +--------v----------+
                              |  RBAC + Rate Limit |
                              +--------+----------+
                                       |
                              +--------v----------+
                              |  Tenant DB Adapter |
                              |  (route to tenant  |
                              |   database)        |
                              +--------+----------+
                                       |
                    +------------------+------------------+
                    |                  |                  |
           +--------v------+  +--------v------+  +-------v-------+
           | Tenant A DB   |  | Tenant B DB   |  | Tenant C DB   |
           | (full ICDEV   |  | (full ICDEV   |  | (full ICDEV   |
           |  schema)      |  |  schema)      |  |  schema)      |
           +---------------+  +---------------+  +---------------+
```

The SaaS architecture follows a strict layering principle:

1. **API Gateway** -- Single Flask application handling both REST and MCP Streamable HTTP transport
2. **Auth Middleware** -- Extracts credentials, validates via the appropriate auth module, resolves tenant_id + user_id + role
3. **RBAC** -- Checks role permissions against the requested operation category
4. **Rate Limiter** -- Enforces per-tenant limits based on subscription tier
5. **Tenant DB Adapter** -- Routes all database operations to the tenant's isolated database
6. **Existing Tools** -- Called unchanged; receive tenant-scoped DB connections transparently

---

## 4. Requirements

### 4.1 API Transport

#### REQ-21-001: REST API
The system SHALL expose ICDEV tools via REST API at `/api/v1/*` endpoints using standard HTTP JSON for generic clients, with Swagger UI at `/api/v1/docs` and OpenAPI 3.0.3 spec at `/api/v1/openapi.json`.

#### REQ-21-002: MCP Streamable HTTP
The system SHALL expose ICDEV tools via MCP Streamable HTTP transport at `/mcp/v1/` using JSON-RPC 2.0 per the MCP specification (2025-03-26) for Claude Code clients.

### 4.2 Authentication

#### REQ-21-003: API Key Authentication
The system SHALL authenticate requests via `Authorization: Bearer icdev_<key>` header, validating against SHA-256 hashed keys stored in the `api_keys` table. Plaintext keys SHALL never be stored.

#### REQ-21-004: OAuth 2.0/OIDC Authentication
The system SHALL authenticate requests via JWT bearer tokens, performing JWKS verification with 1-hour key cache for provider resilience.

#### REQ-21-005: CAC/PIV Authentication
The system SHALL authenticate requests via `X-Client-Cert-CN` header (from nginx/ALB TLS termination), performing CN lookup in the users table.

### 4.3 Tenant Isolation

#### REQ-21-006: Dedicated Database Per Tenant
The system SHALL provision a dedicated database per tenant (SQLite for dev, PostgreSQL for prod) containing the full ICDEV schema. No row-level tenant filtering (D60).

#### REQ-21-007: Classification-Scaled Isolation
Tenant compute isolation SHALL scale with classification: shared K8s namespace (IL2-IL4), dedicated K8s namespace + node pool (IL5), dedicated AWS sub-account on SIPR (IL6).

#### REQ-21-008: Network Policy Isolation
Each tenant K8s namespace SHALL have default-deny network policies, allowing traffic only from the API gateway.

### 4.4 Subscription and Rate Limiting

#### REQ-21-009: Subscription Tiers
The system SHALL enforce 3 tiers: Starter (5 projects, 3 users, IL2/IL4, 60 req/min), Professional (25 projects, 15 users, IL2-IL5, 300 req/min), Enterprise (unlimited, IL2-IL6, unlimited req/min).

#### REQ-21-010: Rate Limiting
The system SHALL enforce per-tenant rate limits with 429 responses including Retry-After headers and reset times.

### 4.5 On-Premises Deployment

#### REQ-21-011: Helm Chart Deployment
The system SHALL support on-premises deployment via Helm chart (`deploy/helm/`) with configurable values for all components.

#### REQ-21-012: Offline License Validation
The system SHALL validate on-premises licenses using RSA-SHA256 cryptographic signatures, air-gap safe with no license server required (D64).

#### REQ-21-013: Expired License Handling
On license expiration, the system SHALL allow read-only access for 30 days, then block write operations.

### 4.6 Tenant Lifecycle

#### REQ-21-014: Provisioning Pipeline
Tenant provisioning SHALL follow the pipeline: pending -> provisioning -> active, including: dedicated DB creation, full ICDEV schema application, K8s namespace creation, network policy application, admin API key generation, and webhook notification.

#### REQ-21-015: IL5+ Admin Approval
Tenants requesting IL5 or IL6 impact levels SHALL require explicit admin approval before provisioning proceeds.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `tenants` | Tenant records: id, name, slug, impact_level, tier, status, provisioned_at |
| `users` | Tenant users: id, tenant_id, email, name, role, status |
| `api_keys` | API key storage: id, user_id, tenant_id, key_hash (SHA-256), prefix, expires_at |
| `subscriptions` | Subscription details: tenant_id, tier, starts_at, expires_at, limits_json |
| `usage_records` | API usage tracking: tenant_id, user_id, endpoint, method, timestamp, response_code |
| `audit_platform` | Platform-level audit trail: event_type, actor, tenant_id, action, timestamp |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/saas/platform_db.py` | Platform schema initialization (tenants, users, api_keys, subscriptions, usage, audit) |
| `tools/saas/tenant_manager.py` | Tenant lifecycle: create, provision, approve, suspend, add-user, list |
| `tools/saas/api_gateway.py` | Flask API gateway: REST + MCP Streamable HTTP + auth + rate limiting |
| `tools/saas/rest_api.py` | Flask Blueprint with all REST v1 endpoints |
| `tools/saas/mcp_http.py` | MCP Streamable HTTP transport (JSON-RPC 2.0, session-based) |
| `tools/saas/auth/middleware.py` | Authentication middleware: credential extraction, validation, tenant resolution |
| `tools/saas/auth/rbac.py` | Role-based access control: 5 roles x 9 permission categories |
| `tools/saas/tenant_db_adapter.py` | Route tool DB calls to tenant-specific database |
| `tools/saas/rate_limiter.py` | Per-tenant rate limiting by subscription tier |
| `tools/saas/db/db_compat.py` | SQLite-PostgreSQL compatibility layer |
| `tools/saas/db/pg_schema.py` | Full ICDEV schema ported to PostgreSQL DDL |
| `tools/saas/artifacts/delivery_engine.py` | Push artifacts to tenant S3/Git/SFTP |
| `tools/saas/bedrock/bedrock_proxy.py` | Route LLM calls to BYOK or shared Bedrock pool |
| `tools/saas/licensing/license_generator.py` | Generate RSA-SHA256 signed offline license keys |
| `tools/saas/licensing/license_validator.py` | Validate on-premises license (air-gap safe) |
| `tools/saas/portal/app.py` | Tenant admin portal web dashboard |
| `tools/saas/infra/namespace_provisioner.py` | Create per-tenant K8s namespace with network policies |
| `tools/saas/openapi_spec.py` | Generate OpenAPI 3.0.3 specification |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D58 | SaaS layer wraps existing tools, does not rewrite | Preserves 20 phases of deterministic Python scripts; API gateway is additive |
| D59 | PostgreSQL for all SaaS databases (prod) | Concurrent writes, MVCC, RLS capability, RDS managed; SQLite fallback for dev |
| D60 | Separate database per tenant (not row-level) | Strongest isolation, simplest compliance, easy per-tenant backup/restore |
| D61 | API gateway as thin routing layer | Auth + tenant resolution + routing; tools stay deterministic and tenant-unaware |
| D62 | MCP Streamable HTTP alongside REST | Supports Claude Code users (MCP) and generic HTTP clients (REST) simultaneously |
| D63 | Per-tenant K8s namespace (IL2-4), per-tenant AWS sub-account (IL5-6) | Isolation scales with classification sensitivity |
| D64 | Offline license keys with RSA-SHA256 signatures | Air-gap safe, no license server needed for on-prem deployment |
| D65 | Helm chart for on-prem deployment | Standard K8s packaging; customer uses their own infrastructure |

---

## 8. Security Gate

**SaaS Platform Gate:**
- API keys stored as SHA-256 hashes only (never plaintext)
- Per-tenant database isolation (not row-level) -- no cross-tenant data access
- Network policies enforce default-deny between tenant namespaces
- All traffic TLS-encrypted; mTLS within K8s cluster
- CUI banners on all portal pages and API responses
- Every API call audited to usage_records (append-only)
- Rate limiting enforced per subscription tier
- License validation is cryptographic (RSA-SHA256)
- IL5/IL6 tenants require admin approval before provisioning

---

## 9. Commands

```bash
# Initialize platform database
python tools/saas/platform_db.py --init

# Create tenant
python tools/saas/tenant_manager.py --create --name "ACME" --il IL4 --tier professional --admin-email admin@acme.gov

# Approve IL5/IL6 tenant
python tools/saas/tenant_manager.py --approve --tenant-id "tenant-uuid" --approver-id "admin-uuid"

# Provision tenant (create DB, K8s namespace)
python tools/saas/tenant_manager.py --provision --tenant-id "tenant-uuid"

# Add user to tenant
python tools/saas/tenant_manager.py --add-user --tenant-id "tenant-uuid" --email dev@acme.gov --role developer

# List tenants
python tools/saas/tenant_manager.py --list --json

# Start API gateway (development)
python tools/saas/api_gateway.py --port 8443 --debug

# Start API gateway (production)
gunicorn -w 4 -b 0.0.0.0:8443 "tools.saas.api_gateway:create_app()"

# Generate OpenAPI spec
python tools/saas/openapi_spec.py --output spec.json

# Generate license key (admin)
python tools/saas/licensing/license_generator.py --generate --customer "ACME" --tier enterprise --expires-in-days 365 --private-key /path/key.pem

# Validate on-prem license
python tools/saas/licensing/license_validator.py --validate --json

# Deploy via Helm
helm install icdev deploy/helm/ --values deploy/helm/values.yaml

# Air-gapped deployment
python deploy/offline/install.py --namespace icdev --values values-custom.yaml --license license.json

# Create K8s namespace for tenant
python tools/saas/infra/namespace_provisioner.py --create --slug acme --il IL4 --tier professional
```
