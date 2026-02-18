# SaaS Multi-Tenancy Goal

> CUI // SP-CTI

## Purpose
Transform ICDEV from a single-tenant CLI tool into a multi-tenant SaaS platform exposing compliance automation tools via REST API and MCP Streamable HTTP to external developers/teams while maintaining per-tenant data isolation and classification-aware security.

## When to Use
- Setting up ICDEV as a service for multiple teams/developers
- Onboarding new tenants (organizations/teams)
- Managing tenant lifecycle (create, provision, suspend, delete)
- Configuring API access (keys, OAuth, CAC/PIV)
- Deploying ICDEV on-premises at customer sites
- Managing subscription tiers and rate limits

## Workflow

### 1. Platform Initialization
```bash
# Initialize platform database (run once)
python tools/saas/platform_db.py --init
```
Creates: tenants, users, api_keys, subscriptions, usage_records, audit_platform tables.

### 2. Tenant Onboarding
```bash
# Self-service (Starter/Professional, IL2-IL4)
python tools/saas/tenant_manager.py --create \
  --name "ACME Defense" --il IL4 --tier professional \
  --admin-email admin@acme.gov

# Admin-approved (Enterprise, IL5-IL6)
python tools/saas/tenant_manager.py --create \
  --name "Mission Critical Inc" --il IL6 --tier enterprise \
  --admin-email admin@mci.gov
# Then: --approve --tenant-id <uuid> --approver-id <admin-uuid>
```

### 3. Tenant Provisioning (Automatic)
```
Status: pending → provisioning → active
  a. Create dedicated database (SQLite dev / PostgreSQL prod)
  b. Run full ICDEV schema against tenant DB (100+ tables)
  c. Create K8s namespace (icdev-tenant-{slug})
  d. Apply network policies (default deny, allow from gateway)
  e. For IL5+: create AWS sub-account, VPC, RDS
  f. Generate initial admin API key
  g. Notify tenant via webhook
```

### 4. API Access
Tenants access ICDEV via two transports:

**REST API:**
```bash
curl -H "Authorization: Bearer icdev_<key>" \
  https://gateway:8443/api/v1/projects
```

**MCP Streamable HTTP:**
```bash
curl -H "Authorization: Bearer icdev_<key>" \
  -H "Content-Type: application/json" \
  -X POST https://gateway:8443/mcp/v1/ \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"project_list","arguments":{}}}'
```

### 5. Start API Gateway
```bash
# Development
python tools/saas/api_gateway.py --port 8443 --debug

# Production
gunicorn -w 4 -b 0.0.0.0:8443 "tools.saas.api_gateway:create_app()"
```

### 6. On-Premises Deployment
```bash
# Generate license key (admin)
python tools/saas/licensing/license_generator.py --generate \
  --customer "ACME" --tier enterprise --expires-days 365 \
  --private-key /path/to/key.pem

# Deploy via Helm
helm install icdev deploy/helm/ \
  --values deploy/helm/values.yaml \
  --set license.key=<base64-license>

# Air-gapped deployment
python deploy/offline/install.py --namespace icdev \
  --values values-custom.yaml --license license.json
```

## Architecture

### Authentication Flow
```
Request → middleware.py:
  1. Extract credentials (API key / OAuth JWT / CAC cert)
  2. Validate via auth module (api_key_auth / oauth_auth / cac_auth)
  3. Resolve tenant_id + user_id + role
  4. Set g.tenant_id, g.user_id, g.user_role
  5. RBAC permission check
  6. Rate limit check
  7. Pass to handler
```

### RBAC Roles
| Role | Projects | Compliance | Security | Team | Keys |
|------|:---:|:---:|:---:|:---:|:---:|
| tenant_admin | RW | RW | RW | RW | RW |
| developer | RW | RW | RW | - | Own |
| compliance_officer | R | RW | R | - | Own |
| auditor | R | R | R | - | Own |
| viewer | R | R | R | - | Own |

### Tenant DB Routing
```
API request → auth middleware → g.tenant_id
  → tenant_db_adapter.get_tenant_db_path(tenant_id)
  → data/tenants/{slug}.db  (or PostgreSQL in production)
  → existing tool function receives tenant-scoped DB connection
  → results returned to tenant only
```

## Edge Cases
- **Missing tenant DB**: Re-provision, return 503 with retry-after
- **Expired API key**: Return 401 with clear error, suggest key rotation
- **Rate limit exceeded**: Return 429 with Retry-After header and reset time
- **IL mismatch**: Block higher-IL operations if tenant subscription doesn't allow
- **OAuth provider down**: Cache JWKS for 1 hour, degrade gracefully
- **On-prem license expired**: Allow read-only access for 30 days, then block writes

## Security Considerations
- API keys stored as SHA-256 hashes only (never plaintext)
- Per-tenant database isolation (not row-level) — D60
- Network policies enforce default-deny between tenant namespaces
- All traffic TLS-encrypted, mTLS within K8s cluster
- CUI banners on all portal pages and API responses
- Audit every API call to usage_records (append-only)
- Rate limiting prevents abuse and ensures fair resource allocation
- License validation is cryptographic (RSA-SHA256), air-gap safe

## Tools Used
| Tool | Purpose |
|------|---------|
| `tools/saas/platform_db.py` | Platform schema initialization |
| `tools/saas/tenant_manager.py` | Tenant lifecycle management |
| `tools/saas/api_gateway.py` | API gateway (REST + MCP Streamable HTTP) |
| `tools/saas/auth/middleware.py` | Authentication middleware |
| `tools/saas/auth/rbac.py` | Role-based access control |
| `tools/saas/rate_limiter.py` | Rate limiting |
| `tools/saas/tenant_db_adapter.py` | Tenant DB routing |
| `tools/saas/artifacts/delivery_engine.py` | Artifact delivery |
| `tools/saas/bedrock/bedrock_proxy.py` | Bedrock LLM proxy |
| `tools/saas/licensing/license_validator.py` | License validation |
| `tools/saas/infra/namespace_provisioner.py` | K8s provisioning |
| `tools/saas/portal/app.py` | Tenant admin portal |

## Expected Outputs
- Platform database with tenant records
- Per-tenant isolated databases with full ICDEV schema
- Running API gateway accepting REST and MCP Streamable HTTP requests
- Tenant admin portal at /portal/
- K8s namespaces with network policies per tenant
- Signed license keys for on-prem deployments
- Helm chart for self-hosted deployment
