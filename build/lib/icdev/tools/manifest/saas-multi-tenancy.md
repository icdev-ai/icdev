# SaaS Multi-Tenancy (Phase 21)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## SaaS Multi-Tenancy (Phase 21)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Platform DB | tools/saas/platform_db.py | Platform PostgreSQL/SQLite schema (tenants, users, api_keys, subscriptions, usage_records, audit_platform) | --init, --reset | Schema creation |
| Models | tools/saas/models.py | Pydantic models: Tenant, User, APIKey, Subscription, UsageRecord, enums, tier limits | — | — |
| Tenant Manager | tools/saas/tenant_manager.py | Tenant CRUD, provisioning lifecycle, DB creation, API key generation | --create, --list, --provision, --approve, --suspend, --delete | Tenant info |
| Auth Middleware | tools/saas/auth/middleware.py | Flask before_request middleware: credential extraction, tenant context, security headers | — | g.tenant_id, g.user_id |
| API Key Auth | tools/saas/auth/api_key_auth.py | API key validation: SHA-256 hash lookup, expiry/scope/status checks | Authorization header | Auth context |
| OAuth Auth | tools/saas/auth/oauth_auth.py | OAuth 2.0/OIDC JWT validation: decode, JWKS verify, tenant/user resolution | Authorization header | Auth context |
| CAC Auth | tools/saas/auth/cac_auth.py | CAC/PIV authentication: CN lookup from X-Client-Cert-CN header | Client cert header | Auth context |
| RBAC | tools/saas/auth/rbac.py | Role-based access control: 5 roles × 9 endpoint categories permission matrix | role, path, method | Allow/deny |
| API Gateway | tools/saas/api_gateway.py | Main Flask app: REST + MCP Streamable HTTP + auth + rate limiting + request logging | --port, --debug | Web server |
| REST API | tools/saas/rest_api.py | Flask Blueprint: tenants, users, keys, projects, compliance, security, builder, audit, usage; Phase 11 agents/workflows/authority endpoints | /api/v1/* | JSON responses |
| MCP Streamable HTTP | tools/saas/mcp_http.py | MCP Streamable HTTP transport (spec 2025-03-26): single endpoint, session-based | POST/GET/DELETE /mcp/v1/ | JSON + SSE |
| Rate Limiter | tools/saas/rate_limiter.py | Per-tenant rate limiting by subscription tier (in-memory, thread-safe) | tenant_id, tier | Allow/deny + headers |
| Request Logger | tools/saas/request_logger.py | Audit logging: every API call → usage_records + audit_platform | Flask hooks | Log entries |
| Tenant DB Adapter | tools/saas/tenant_db_adapter.py | Route existing tool DB calls to tenant's isolated database | tenant_id | DB path/connection |
| PG Schema | tools/saas/db/pg_schema.py | Full ICDEV™ schema (100+ tables) ported from SQLite to PostgreSQL DDL | --init | PG schema |
| DB Compat | tools/saas/db/db_compat.py | SQLite ↔ PostgreSQL compatibility: placeholder translation, row factory | engine type | DB connection |
| Connection Pool | tools/saas/db/connection_pool.py | Per-tenant PostgreSQL connection pooling (psycopg2 ThreadedConnectionPool) | tenant_id | Pooled connection |
| Delivery Engine | tools/saas/artifacts/delivery_engine.py | Push artifacts to tenant S3/Git/SFTP with audit trail | tenant_id, artifact_path | Delivery status |
| Artifact Signer | tools/saas/artifacts/signer.py | SHA-256 hash + RSA digital signature for compliance artifacts | file_path | Hash + signature |
| Bedrock Proxy | tools/saas/bedrock/bedrock_proxy.py | Route Bedrock LLM calls: BYOK (tenant's AWS) or ICDEV™ shared pool | tenant_id, prompt | LLM response |
| Token Metering | tools/saas/bedrock/token_metering.py | Track Bedrock token usage per tenant for billing/rate enforcement | tenant_id, tokens | Usage record |
| Tenant Portal | tools/saas/portal/app.py | Flask Blueprint: tenant admin web dashboard (login, dashboard, team, settings, keys) | /portal/* | Web UI |
| NS Provisioner | tools/saas/infra/namespace_provisioner.py | Create K8s namespace, network policies, resource quotas per tenant | --create, --slug, --il | Namespace YAML |
| Account Provisioner | tools/saas/infra/account_provisioner.py | Create AWS sub-accounts for IL5/IL6 tenants via Organizations | --provision, --slug | Account ID |
| License Validator | tools/saas/licensing/license_validator.py | Offline RSA-SHA256 license key validation (air-gap safe) | --validate, --info | License status |
| License Generator | tools/saas/licensing/license_generator.py | Admin tool: generate signed license keys for on-prem customers | --generate, --customer, --tier | License JSON |
| OpenAPI Spec | tools/saas/openapi_spec.py | OpenAPI 3.0.3 spec generator — 23 endpoints, 13 schemas (D153) | --output, --compact | OpenAPI JSON |
| Swagger UI | tools/saas/swagger_ui.py | Flask Blueprint: /api/v1/docs (Swagger UI) + /api/v1/openapi.json (D153) | /api/v1/docs | HTML + JSON |
| Metrics | tools/saas/metrics.py | Prometheus metrics collector — dual-backend: prometheus_client or stdlib fallback (D154) | (library) | MetricsCollector |
| Metrics Blueprint | tools/saas/metrics_blueprint.py | Flask Blueprint: GET /metrics — Prometheus text exposition (D154) | /metrics | text/plain |

