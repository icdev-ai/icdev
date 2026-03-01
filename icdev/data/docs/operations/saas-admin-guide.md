# SaaS Multi-Tenancy Administration Guide

## Overview

ICDEV operates as a multi-tenant SaaS platform. The SaaS layer wraps existing deterministic tools (D58) without rewriting them. Each API request resolves the tenant, routes to their isolated database, invokes the existing Python tool, and returns the result.

**Key design principles:**
- Existing tools remain unchanged; SaaS is additive
- Per-tenant database isolation (strongest isolation model)
- Dual transport: REST API for generic clients, MCP Streamable HTTP for Claude Code
- Three authentication methods scaling with classification level
- Offline license validation for air-gapped on-premises deployments

---

## Platform Database Initialization

```bash
# Initialize the platform database (tenants, users, api_keys, subscriptions, usage_records, audit_platform)
python tools/saas/platform_db.py --init
```

This creates `data/platform.db` with 6 tables. The platform database is separate from operational databases (`data/icdev.db`, `data/memory.db`).

---

## Tenant Lifecycle

### Create a Tenant

```bash
python tools/saas/tenant_manager.py \
  --create \
  --name "ACME Federal" \
  --il IL4 \
  --tier professional \
  --admin-email admin@acme.gov
```

This registers the tenant in the platform database with status `pending_provision`.

### Provision a Tenant

```bash
python tools/saas/tenant_manager.py --provision --tenant-id "tenant-uuid"
```

Provisioning performs:
1. Creates a dedicated tenant database (`data/tenants/{slug}.db`) with the full ICDEV schema
2. Creates a K8s namespace (if K8s is available)
3. Sets tenant status to `active`

### Approve IL5/IL6 Tenants

IL5 and IL6 tenants require explicit approval before provisioning:

```bash
python tools/saas/tenant_manager.py \
  --approve \
  --tenant-id "tenant-uuid" \
  --approver-id "admin-uuid"
```

IL5 tenants receive a dedicated K8s node pool. IL6 tenants receive a dedicated AWS sub-account on SIPR.

### Add Users to a Tenant

```bash
python tools/saas/tenant_manager.py \
  --add-user \
  --tenant-id "tenant-uuid" \
  --email dev@acme.gov \
  --role developer
```

### List All Tenants

```bash
python tools/saas/tenant_manager.py --list --json
```

---

## API Gateway

### Starting the Gateway

```bash
# Development mode (port 8443, debug enabled)
python tools/saas/api_gateway.py --port 8443 --debug

# Production mode (gunicorn, 4 workers)
gunicorn -w 4 -b 0.0.0.0:8443 tools.saas.api_gateway:app
```

The API gateway runs on port 8443 and serves as the single entry point for all tenant API traffic.

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check (no auth required) |
| `/api/v1/*` | POST/GET | REST API (JSON) |
| `/mcp/v1/` | POST/GET/DELETE | MCP Streamable HTTP (JSON-RPC 2.0) |
| `/api/v1/docs` | GET | Swagger UI |
| `/api/v1/openapi.json` | GET | OpenAPI 3.0.3 specification |
| `/metrics` | GET | Prometheus metrics (no auth required) |

### REST API Transport

Standard HTTP JSON for generic clients. All endpoints are under `/api/v1/`:

```bash
# Example: list projects for a tenant
curl -H "Authorization: Bearer icdev_abc123..." \
     https://api.icdev.example.com/api/v1/projects

# Example: generate SSP
curl -X POST \
     -H "Authorization: Bearer icdev_abc123..." \
     -H "Content-Type: application/json" \
     -d '{"project_id": "proj-123"}' \
     https://api.icdev.example.com/api/v1/compliance/ssp
```

### MCP Streamable HTTP Transport

JSON-RPC 2.0 via Streamable HTTP (spec 2025-03-26) for Claude Code clients:

```bash
# Example: MCP tool invocation
curl -X POST \
     -H "Authorization: Bearer icdev_abc123..." \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"ssp_generate","arguments":{"project_id":"proj-123"}},"id":1}' \
     https://api.icdev.example.com/mcp/v1/
```

Session management is supported via MCP session headers for stateful interactions.

### Swagger UI and OpenAPI

- **Swagger UI:** Navigate to `/api/v1/docs` for interactive API exploration
- **OpenAPI Spec:** Download from `/api/v1/openapi.json` (23 documented endpoints)
- **Spec Generation:** `python tools/saas/openapi_spec.py --output spec.json`

The OpenAPI spec is generated programmatically from declarative schema dicts (D153). No flask-restx dependency.

---

## Authentication Methods

### Method 1: API Key

Simplest method. Available on all tiers.

```bash
# Request header format
Authorization: Bearer icdev_<key>
```

Keys are SHA-256 hashed before storage. The gateway looks up the hash in the `api_keys` table, resolves the associated user and tenant.

### Method 2: OAuth 2.0 / OIDC

Available on Professional and Enterprise tiers. Supports any OIDC-compliant identity provider (Keycloak, Azure AD, Okta).

```bash
# Request header format
Authorization: Bearer eyJ...  # JWT token
```

The gateway decodes the JWT, verifies the signature against the provider's JWKS endpoint, and resolves the tenant from token claims.

### Method 3: CAC/PIV Certificate

Enterprise tier only. For DoD environments with PKI infrastructure.

```bash
# Request header format (set by nginx/ALB TLS termination)
X-Client-Cert-CN: john.doe.1234567890
```

The gateway extracts the Common Name from the client certificate (presented during mutual TLS at the reverse proxy) and looks up the user by CN in the `users` table.

---

## RBAC: Role-Based Access Control

Five roles control API access across 9 resource categories:

| Role | Description |
|------|-------------|
| `tenant_admin` | Full tenant administration, user management, configuration |
| `developer` | Project CRUD, build/test/deploy, code generation |
| `compliance_officer` | Compliance artifacts, assessments, ATO management |
| `auditor` | Read-only access to all resources and audit trails |
| `viewer` | Read-only access to project status and dashboards |

### Permission Matrix (Abbreviated)

| Category | tenant_admin | developer | compliance_officer | auditor | viewer |
|----------|:---:|:---:|:---:|:---:|:---:|
| Projects | CRUD | CRUD | Read | Read | Read |
| Compliance | Full | Read | Full | Read | Read |
| Security | Full | Run scans | Read reports | Read | - |
| Infrastructure | Full | Deploy | - | Read | - |
| Users | Manage | - | - | Read | - |
| Audit Trail | Full | Read own | Read | Read | - |
| Agents | Manage | Read | Read | Read | Read |
| Marketplace | Full | Install | Review | Read | Read |
| Settings | Full | - | - | - | - |

Full RBAC definitions are in `tools/saas/auth/rbac.py`.

---

## Subscription Tiers

| Feature | Starter | Professional | Enterprise |
|---------|---------|-------------|------------|
| **Projects** | 5 | 25 | Unlimited |
| **Users** | 3 | 15 | Unlimited |
| **Impact Levels** | IL2, IL4 | IL2-IL5 | IL2-IL6 |
| **Auth Methods** | API key | API key + OAuth | API key + OAuth + CAC/PIV |
| **Compute** | Shared K8s NS | Dedicated K8s NS | Dedicated AWS account |
| **Rate Limit** | 60 req/min | 300 req/min | Unlimited |
| **CLI Capabilities** | scripted_intake only | All except container_execution | All 4 capabilities |
| **Marketplace** | Install only | Install + publish | Full (incl. cross-tenant) |
| **Support** | Community | Priority | Dedicated |

Rate limiting is enforced per tenant by the `tools/saas/rate_limiter.py` module. Backend options are in-memory (default) or Redis (`args/scaling_config.yaml`).

---

## Tenant Isolation by Impact Level

| IL | Compute | Database | Network |
|----|---------|----------|---------|
| IL2-IL4 | Dedicated K8s namespace | Dedicated SQLite DB (dev) or PostgreSQL | K8s NetworkPolicy isolation |
| IL5 | Dedicated K8s namespace + dedicated node pool | Dedicated RDS instance | VPC peering |
| IL6 | Dedicated AWS sub-account (SIPR) | Isolated VPC PostgreSQL | Air-gapped network |

### K8s Namespace Provisioning

```bash
# Create a tenant namespace with network policies and resource quotas
python tools/saas/infra/namespace_provisioner.py \
  --create \
  --slug acme \
  --il IL4 \
  --tier professional
```

This generates:
- K8s namespace `icdev-tenant-acme`
- Default-deny NetworkPolicy
- Resource quotas based on tier
- Service account with tenant-scoped RBAC

### Database Isolation

Each tenant receives a dedicated database at `data/tenants/{slug}.db` (SQLite) or a dedicated PostgreSQL database. The `tools/saas/tenant_db_adapter.py` module routes all tool DB calls to the correct tenant database based on the authenticated tenant context.

The `tools/saas/db/db_compat.py` module provides a SQLite-to-PostgreSQL compatibility layer for production deployments.

---

## On-Premises Deployment

### Helm Chart

```bash
# Standard Helm installation
helm install icdev deploy/helm/ --values deploy/helm/values.yaml

# With autoscaling enabled
helm install icdev deploy/helm/ --set autoscaling.enabled=true

# Per-CSP value overrides
helm install icdev deploy/helm/ --values deploy/helm/values-aws.yaml
helm install icdev deploy/helm/ --values deploy/helm/values-on-prem.yaml
```

The Helm chart is located at `deploy/helm/` and includes:
- `Chart.yaml` - Chart metadata
- `values.yaml` - Default values
- `templates/` - K8s manifest templates
- Per-CSP override files (`values-aws.yaml`, `values-azure.yaml`, `values-gcp.yaml`, `values-oci.yaml`, `values-ibm.yaml`, `values-on-prem.yaml`, `values-docker.yaml`)

### Air-Gapped Installation

```bash
# Using the offline installer
cd deploy/offline/
python install.py

# Or via shell script
bash install.sh
```

See `deploy/offline/README.md` for prerequisites and bundle preparation instructions.

### License Management

On-premises deployments use offline RSA-SHA256 signed license keys (D64). No license server is required.

```bash
# Generate a license (issuer side)
python tools/saas/licensing/license_generator.py \
  --generate \
  --customer "ACME Federal" \
  --tier enterprise \
  --expires-in-days 365 \
  --private-key /path/to/key.pem

# Validate a license (customer side)
python tools/saas/licensing/license_validator.py --validate --json
```

License validation checks:
- RSA-SHA256 signature integrity
- Expiration date
- Tier entitlements
- Feature flags

---

## Tenant Portal

```bash
# Start the tenant portal (separate from main dashboard)
python tools/saas/portal/app.py
```

The tenant portal provides a self-service web interface for tenant administrators to:
- View tenant status and resource usage
- Manage users within their tenant
- View subscription details and limits
- Access compliance posture summary
- Monitor project health

Portal UX follows the same patterns as the main dashboard: glossary tooltips, breadcrumbs, skip-to-content links, and ARIA accessibility (D97).

---

## Prometheus Metrics

The `/metrics` endpoint exposes Prometheus-format metrics (exempt from authentication):

| Metric | Type | Description |
|--------|------|-------------|
| `icdev_http_requests_total` | Counter | Total HTTP requests by method, path, status |
| `icdev_http_request_duration_seconds` | Histogram | Request duration |
| `icdev_errors_total` | Counter | Application errors by type |
| `icdev_rate_limit_hits_total` | Counter | Rate limit rejections by tenant |
| `icdev_circuit_breaker_state` | Gauge | Circuit breaker state per service |
| `icdev_uptime_seconds` | Gauge | Process uptime |
| `icdev_active_tenants` | Gauge | Number of active tenants |
| `icdev_active_sessions` | Gauge | Number of active API sessions |

Implementation uses optional `prometheus_client` with stdlib text-format fallback (D154).

```bash
# Scrape metrics
curl http://localhost:8443/metrics
```

Configure your Prometheus instance to scrape this endpoint at the desired interval.

---

## Key Components Reference

| Component | File | Purpose |
|-----------|------|---------|
| Platform DB | `tools/saas/platform_db.py` | Schema for tenants, users, keys, subscriptions |
| Tenant Manager | `tools/saas/tenant_manager.py` | Tenant CRUD and provisioning lifecycle |
| Auth Middleware | `tools/saas/auth/middleware.py` | Credential extraction and validation |
| RBAC | `tools/saas/auth/rbac.py` | Role-based access control enforcement |
| API Gateway | `tools/saas/api_gateway.py` | Main Flask app: REST + MCP + auth + rate limiting |
| REST API | `tools/saas/rest_api.py` | Flask Blueprint with all v1 endpoints |
| MCP HTTP | `tools/saas/mcp_http.py` | MCP Streamable HTTP transport |
| Tenant DB Adapter | `tools/saas/tenant_db_adapter.py` | Route DB calls to tenant database |
| Rate Limiter | `tools/saas/rate_limiter.py` | Per-tenant rate limiting by tier |
| DB Compat | `tools/saas/db/db_compat.py` | SQLite and PostgreSQL compatibility |
| PG Schema | `tools/saas/db/pg_schema.py` | Full ICDEV schema in PostgreSQL DDL |
| Artifact Delivery | `tools/saas/artifacts/delivery_engine.py` | Push artifacts to tenant S3/Git/SFTP |
| Bedrock Proxy | `tools/saas/bedrock/bedrock_proxy.py` | Route LLM calls to BYOK or shared pool |
| License Generator | `tools/saas/licensing/license_generator.py` | RSA-SHA256 offline license generation |
| License Validator | `tools/saas/licensing/license_validator.py` | Offline license validation |
| Tenant Portal | `tools/saas/portal/app.py` | Web dashboard for tenant admin |
| NS Provisioner | `tools/saas/infra/namespace_provisioner.py` | Per-tenant K8s namespace creation |

---

## Troubleshooting

### Tenant provisioning fails

```bash
# Verify platform DB exists
python tools/saas/platform_db.py --init

# Check tenant status
python tools/saas/tenant_manager.py --list --json | python -m json.tool
```

### API returns 403

Verify the API key is valid, the user has the required role, and the tenant is in `active` status. Check `dashboard_auth_log` for denied requests.

### Rate limit errors (429)

Rate limits are per-tenant based on subscription tier. Upgrade the tier or reduce request frequency. Current limits:
- Starter: 60/min
- Professional: 300/min
- Enterprise: Unlimited

### Database migration for new tenants

When ICDEV is upgraded with new schema, existing tenant databases need migration:

```bash
# Apply migrations to all tenant databases
python tools/db/migrate.py --up --all-tenants
```

---

## Related Configuration

| File | Purpose |
|------|---------|
| `args/scaling_config.yaml` | HPA profiles, rate limiter backend selection |
| `args/resilience_config.yaml` | Circuit breaker and retry settings for external services |
| `args/cli_config.yaml` | CLI capability toggles and tenant ceiling |
| `args/cloud_config.yaml` | CSP selection and cloud mode |
| `args/db_config.yaml` | Database migration and backup settings |
