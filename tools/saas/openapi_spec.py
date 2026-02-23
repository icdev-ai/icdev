#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV SaaS -- OpenAPI 3.0.3 Specification Generator.

Generates a complete OpenAPI 3.0.3 specification for the ICDEV SaaS REST API.
All 23 endpoints documented with request/response schemas, security schemes,
and CUI classification metadata.

Architecture Decision:
    ADR D153 -- OpenAPI spec generated programmatically from Python dicts
    rather than static YAML to enable runtime validation and ensure spec
    stays synchronized with actual endpoint implementations.

Usage:
    from tools.saas.openapi_spec import generate_openapi_spec
    spec = generate_openapi_spec()  # Returns complete OpenAPI dict

    # Or run directly to dump JSON:
    python tools/saas/openapi_spec.py
    python tools/saas/openapi_spec.py --output /path/to/openapi.json

References:
    - OpenAPI 3.0.3: https://spec.openapis.org/oas/v3.0.3
    - ICDEV REST API: tools/saas/rest_api.py
    - API Gateway: tools/saas/api_gateway.py
"""

import copy
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ============================================================================
# OPENAPI BASE SKELETON
# ============================================================================

OPENAPI_BASE = {
    "openapi": "3.0.3",
    "info": {
        "title": "ICDEV SaaS API",
        "version": "1.0.0",
        "description": (
            "CUI // SP-CTI -- ICDEV Intelligent Coding Development platform "
            "REST API for multi-tenant SaaS operations.  All endpoints require "
            "authentication via API key, OAuth 2.0 bearer token, or CAC/PIV "
            "client certificate.  Responses include CUI classification headers "
            "(X-Classification).  Rate limits are enforced per subscription "
            "tier (Starter: 60/min, Professional: 300/min, Enterprise: unlimited)."
        ),
        "contact": {
            "name": "ICDEV System Administrator",
            "email": "admin@icdev.mil",
        },
        "license": {
            "name": "Government Purpose Rights",
        },
    },
    "servers": [
        {
            "url": "/api/v1",
            "description": "REST API v1",
        },
    ],
    "security": [
        {"ApiKeyAuth": []},
    ],
    "tags": [
        {
            "name": "Tenants",
            "description": "Tenant information and settings management.",
        },
        {
            "name": "Users",
            "description": "User and team management within a tenant.",
        },
        {
            "name": "API Keys",
            "description": "API key lifecycle management.",
        },
        {
            "name": "Projects",
            "description": "Project CRUD and lifecycle operations.",
        },
        {
            "name": "Compliance",
            "description": (
                "Compliance artifact generation (SSP, POAM, STIG, SBOM, "
                "FIPS 199/200).  Delegates to deterministic ICDEV tools."
            ),
        },
        {
            "name": "Security",
            "description": (
                "Security scanning operations (SAST, dependency audit).  "
                "Delegates to deterministic ICDEV security tools."
            ),
        },
        {
            "name": "Audit",
            "description": (
                "Append-only audit trail queries.  NIST 800-53 AU compliant "
                "(no UPDATE/DELETE operations)."
            ),
        },
        {
            "name": "Usage",
            "description": "Tenant usage statistics and billing data.",
        },
        {
            "name": "Health",
            "description": "Gateway and service health checks.",
        },
    ],
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "icdev_*",
                "description": (
                    "API key authentication.  Pass the full API key as a "
                    "Bearer token: `Authorization: Bearer icdev_...`.  "
                    "Key is SHA-256 hashed for lookup in the api_keys table."
                ),
            },
            "OAuthBearer": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": (
                    "OAuth 2.0 / OIDC bearer token.  JWT is decoded and "
                    "verified against the tenant's configured JWKS endpoint.  "
                    "Tenant is resolved from the `tenant_id` claim."
                ),
            },
            "CACAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Client-Cert-CN",
                "description": (
                    "CAC/PIV certificate authentication.  The TLS termination "
                    "proxy (nginx/ALB) extracts the client certificate CN and "
                    "passes it via the X-Client-Cert-CN header.  CN is looked "
                    "up in the users table."
                ),
            },
        },
    },
}


# ============================================================================
# REUSABLE SCHEMAS
# ============================================================================

SCHEMAS = {
    "ErrorResponse": {
        "type": "object",
        "required": ["error", "code"],
        "properties": {
            "error": {
                "type": "string",
                "description": "Human-readable error message.",
                "example": "Tenant not found",
            },
            "code": {
                "type": "string",
                "description": "Machine-readable error code.",
                "example": "NOT_FOUND",
            },
            "details": {
                "type": "string",
                "description": "Additional error context when available.",
                "example": "The requested resource does not exist.",
            },
        },
    },
    "TenantResponse": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "format": "uuid",
                "description": "Unique tenant identifier.",
                "example": "550e8400-e29b-41d4-a716-446655440000",
            },
            "name": {
                "type": "string",
                "description": "Tenant display name.",
                "example": "ACME Defense Corp",
            },
            "slug": {
                "type": "string",
                "description": "URL-safe tenant slug.",
                "example": "acme-defense",
            },
            "tier": {
                "type": "string",
                "enum": ["starter", "professional", "enterprise"],
                "description": "Subscription tier.",
                "example": "professional",
            },
            "impact_level": {
                "type": "string",
                "enum": ["IL2", "IL4", "IL5", "IL6"],
                "description": "DoD Impact Level.",
                "example": "IL4",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "provisioning", "active", "suspended",
                         "deprovisioning", "archived"],
                "description": "Tenant lifecycle status.",
                "example": "active",
            },
            "created_at": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 creation timestamp.",
                "example": "2026-01-15T10:30:00Z",
            },
            "settings": {
                "type": "object",
                "description": "Tenant-specific settings (free-form JSON).",
                "additionalProperties": True,
            },
        },
    },
    "UserResponse": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "format": "uuid",
                "description": "Unique user identifier.",
            },
            "email": {
                "type": "string",
                "format": "email",
                "description": "User email address.",
                "example": "jane.smith@acme.gov",
            },
            "role": {
                "type": "string",
                "enum": ["tenant_admin", "isso", "developer", "viewer",
                         "auditor"],
                "description": "User role within the tenant.",
                "example": "developer",
            },
            "tenant_id": {
                "type": "string",
                "format": "uuid",
                "description": "Parent tenant identifier.",
            },
            "created_at": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 creation timestamp.",
            },
            "last_login": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 last login timestamp.  Null if never.",
                "nullable": True,
            },
        },
    },
    "UserCreateRequest": {
        "type": "object",
        "required": ["email"],
        "properties": {
            "email": {
                "type": "string",
                "format": "email",
                "description": "Email address for the new user.",
                "example": "john.doe@acme.gov",
            },
            "role": {
                "type": "string",
                "enum": ["tenant_admin", "isso", "developer", "viewer",
                         "auditor"],
                "description": "Role to assign. Defaults to 'developer'.",
                "default": "developer",
            },
            "display_name": {
                "type": "string",
                "description": (
                    "Display name. Defaults to email local part."
                ),
                "example": "John Doe",
            },
        },
    },
    "ProjectResponse": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Project identifier (proj-XXXX format).",
                "example": "proj-a1b2c3d4",
            },
            "name": {
                "type": "string",
                "description": "Project name.",
                "example": "mission-planner",
            },
            "type": {
                "type": "string",
                "description": "Project type (microservice, monolith, library).",
                "example": "microservice",
            },
            "classification": {
                "type": "string",
                "description": "Classification marking.",
                "example": "CUI // SP-CTI",
            },
            "status": {
                "type": "string",
                "enum": ["planning", "active", "testing", "deployed",
                         "archived"],
                "description": "Project lifecycle status.",
                "example": "active",
            },
            "impact_level": {
                "type": "string",
                "enum": ["IL2", "IL4", "IL5", "IL6"],
                "description": "DoD Impact Level.",
                "example": "IL5",
            },
            "created_at": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 creation timestamp.",
            },
        },
    },
    "ProjectCreateRequest": {
        "type": "object",
        "required": ["name", "type"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Project name (alphanumeric + hyphens).",
                "example": "mission-planner",
            },
            "type": {
                "type": "string",
                "description": "Project type.",
                "example": "microservice",
            },
            "classification": {
                "type": "string",
                "description": "Classification marking.  Defaults to CUI.",
                "default": "CUI // SP-CTI",
            },
            "impact_level": {
                "type": "string",
                "enum": ["IL2", "IL4", "IL5", "IL6"],
                "description": "DoD Impact Level.  Defaults to tenant IL.",
            },
            "directory_path": {
                "type": "string",
                "description": (
                    "Optional filesystem path for the project directory."
                ),
            },
        },
    },
    "APIKeyResponse": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "format": "uuid",
                "description": "API key identifier.",
            },
            "name": {
                "type": "string",
                "description": "Human-readable key name.",
                "example": "ci-cd-pipeline",
            },
            "key_prefix": {
                "type": "string",
                "description": "First 8 characters of the key for identification.",
                "example": "icdev_ab",
            },
            "scopes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Authorized scopes for this key.",
                "example": ["projects:read", "compliance:write"],
            },
            "expires_at": {
                "type": "string",
                "format": "date-time",
                "description": "Key expiration timestamp.  Null for non-expiring.",
                "nullable": True,
            },
            "created_at": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 creation timestamp.",
            },
        },
    },
    "APIKeyCreateRequest": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Human-readable name for the API key.",
                "example": "ci-cd-pipeline",
            },
            "scopes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Scopes to grant.  Defaults to all scopes.",
                "example": ["projects:read", "projects:write"],
            },
            "expires_in_days": {
                "type": "integer",
                "description": (
                    "Number of days until expiration.  "
                    "Omit for non-expiring key."
                ),
                "example": 365,
            },
        },
    },
    "ComplianceResult": {
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "Project identifier.",
                "example": "proj-a1b2c3d4",
            },
            "artifact_type": {
                "type": "string",
                "description": "Type of compliance artifact generated.",
                "example": "ssp",
            },
            "status": {
                "type": "string",
                "enum": ["pass", "fail", "warning", "generated"],
                "description": "Overall result status.",
                "example": "generated",
            },
            "findings_count": {
                "type": "integer",
                "description": "Number of findings identified.",
                "example": 3,
            },
            "generated_at": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 generation timestamp.",
            },
        },
    },
    "ScanResult": {
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "Project identifier.",
                "example": "proj-a1b2c3d4",
            },
            "scan_type": {
                "type": "string",
                "enum": ["sast", "dependency_audit", "secret_detection",
                         "container_scan"],
                "description": "Type of security scan performed.",
                "example": "sast",
            },
            "status": {
                "type": "string",
                "enum": ["pass", "fail", "warning"],
                "description": "Overall scan result.",
                "example": "pass",
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "severity": {"type": "string"},
                        "message": {"type": "string"},
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                    },
                },
                "description": "List of individual findings.",
            },
            "severity_counts": {
                "type": "object",
                "properties": {
                    "critical": {"type": "integer", "example": 0},
                    "high": {"type": "integer", "example": 1},
                    "medium": {"type": "integer", "example": 3},
                    "low": {"type": "integer", "example": 5},
                    "info": {"type": "integer", "example": 2},
                },
                "description": "Finding counts by severity level.",
            },
        },
    },
    "AuditEntry": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "format": "uuid",
                "description": "Unique audit entry identifier.",
            },
            "event_type": {
                "type": "string",
                "description": "Audit event type.",
                "example": "code.commit",
            },
            "actor": {
                "type": "string",
                "description": "Entity that performed the action.",
                "example": "builder-agent",
            },
            "action": {
                "type": "string",
                "description": "Human-readable action description.",
                "example": "Committed authentication module",
            },
            "timestamp": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 event timestamp.",
            },
            "classification": {
                "type": "string",
                "description": "Classification marking for this entry.",
                "example": "CUI // SP-CTI",
            },
        },
    },
    "UsageRecord": {
        "type": "object",
        "properties": {
            "tenant_id": {
                "type": "string",
                "format": "uuid",
                "description": "Tenant identifier.",
            },
            "period": {
                "type": "string",
                "description": "Billing period (YYYY-MM format).",
                "example": "2026-02",
            },
            "api_calls": {
                "type": "integer",
                "description": "Total API calls during the period.",
                "example": 12450,
            },
            "storage_bytes": {
                "type": "integer",
                "description": "Storage consumed in bytes.",
                "example": 524288000,
            },
            "compute_seconds": {
                "type": "number",
                "description": "Compute time consumed in seconds.",
                "example": 3600.5,
            },
        },
    },
    "HealthResponse": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["healthy", "degraded", "unhealthy"],
                "description": "Overall gateway health status.",
                "example": "healthy",
            },
            "service": {
                "type": "string",
                "description": "Service identifier.",
                "example": "icdev-saas-gateway",
            },
            "version": {
                "type": "string",
                "description": "Gateway version.",
                "example": "1.0.0",
            },
            "uptime_seconds": {
                "type": "number",
                "description": "Seconds since gateway started.",
                "example": 86400.0,
            },
            "components": {
                "type": "object",
                "description": "Health status of individual components.",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
                "example": {
                    "platform_db": {"status": "ok", "tenant_count": 5},
                    "mcp": {"status": "ok"},
                },
            },
        },
    },
}


# ============================================================================
# COMMON RESPONSE HELPERS
# ============================================================================

def _ref(schema_name):
    """Return a JSON Schema $ref pointer to a component schema."""
    return {"$ref": "#/components/schemas/{}".format(schema_name)}


def _json_response(schema_ref, description, status="200"):
    """Build a standard JSON response entry."""
    return {
        status: {
            "description": description,
            "content": {
                "application/json": {
                    "schema": schema_ref,
                },
            },
        },
    }


def _error_responses(*codes):
    """Build common error response entries for given HTTP status codes."""
    error_map = {
        "400": "Bad request -- missing or invalid parameters.",
        "401": "Unauthorized -- missing or invalid credentials.",
        "403": "Forbidden -- insufficient role permissions.",
        "404": "Resource not found.",
        "429": "Rate limit exceeded for current subscription tier.",
        "500": "Internal server error.",
    }
    result = {}
    for code in codes:
        code_str = str(code)
        result[code_str] = {
            "description": error_map.get(code_str, "Error"),
            "content": {
                "application/json": {
                    "schema": _ref("ErrorResponse"),
                },
            },
        }
    return result


def _project_id_param():
    """Return the common project_id path parameter."""
    return {
        "name": "project_id",
        "in": "path",
        "required": True,
        "description": "Project identifier (proj-XXXX format).",
        "schema": {"type": "string", "example": "proj-a1b2c3d4"},
    }


def _user_id_param():
    """Return the common user_id path parameter."""
    return {
        "name": "user_id",
        "in": "path",
        "required": True,
        "description": "User identifier (UUID).",
        "schema": {"type": "string", "format": "uuid"},
    }


def _key_id_param():
    """Return the common key_id path parameter."""
    return {
        "name": "key_id",
        "in": "path",
        "required": True,
        "description": "API key identifier (UUID).",
        "schema": {"type": "string", "format": "uuid"},
    }


# ============================================================================
# ENDPOINT DOCUMENTATION
# ============================================================================

ENDPOINT_DOCS = {
    # ------------------------------------------------------------------
    # TENANTS
    # ------------------------------------------------------------------
    ("get", "/tenants/me"): {
        "summary": "Get current tenant info",
        "description": (
            "Returns full details for the authenticated tenant, including "
            "name, slug, subscription tier, impact level, status, and "
            "custom settings."
        ),
        "tags": ["Tenants"],
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "tenant": _ref("TenantResponse"),
                }},
                "Current tenant details.",
            ),
            **_error_responses(401, 404, 500),
        },
    },
    ("patch", "/tenants/me"): {
        "summary": "Update tenant settings",
        "description": (
            "Update the current tenant's settings, artifact configuration, "
            "Bedrock configuration, IdP configuration, or display name.  "
            "Requires tenant_admin role."
        ),
        "tags": ["Tenants"],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Updated tenant name.",
                            },
                            "settings": {
                                "type": "object",
                                "description": "Tenant settings object.",
                            },
                            "artifact_config": {
                                "type": "object",
                                "description": "Artifact delivery config.",
                            },
                            "bedrock_config": {
                                "type": "object",
                                "description": "Bedrock LLM configuration.",
                            },
                            "idp_config": {
                                "type": "object",
                                "description": "Identity provider config.",
                            },
                        },
                    },
                },
            },
        },
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "tenant": _ref("TenantResponse"),
                }},
                "Updated tenant details.",
            ),
            **_error_responses(400, 401, 403, 500),
        },
    },

    # ------------------------------------------------------------------
    # USERS
    # ------------------------------------------------------------------
    ("get", "/users"): {
        "summary": "List users for tenant",
        "description": (
            "Returns all users belonging to the authenticated tenant."
        ),
        "tags": ["Users"],
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "users": {
                        "type": "array",
                        "items": _ref("UserResponse"),
                    },
                    "total": {"type": "integer"},
                }},
                "List of tenant users.",
            ),
            **_error_responses(401, 500),
        },
    },
    ("post", "/users"): {
        "summary": "Create user",
        "description": (
            "Add a new user to the current tenant.  Requires tenant_admin "
            "role.  The user's email must be unique within the tenant."
        ),
        "tags": ["Users"],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _ref("UserCreateRequest"),
                },
            },
        },
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "user": _ref("UserResponse"),
                }},
                "Newly created user.",
                status="201",
            ),
            **_error_responses(400, 401, 403, 409, 500),
        },
    },
    ("patch", "/users/{user_id}"): {
        "summary": "Update user",
        "description": (
            "Update a user's role or display name.  Requires tenant_admin "
            "role.  Cannot demote the last tenant_admin."
        ),
        "tags": ["Users"],
        "parameters": [_user_id_param()],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "role": {
                                "type": "string",
                                "enum": ["tenant_admin", "isso", "developer",
                                         "viewer", "auditor"],
                            },
                            "display_name": {"type": "string"},
                        },
                    },
                },
            },
        },
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "user": _ref("UserResponse"),
                }},
                "Updated user details.",
            ),
            **_error_responses(400, 401, 403, 404, 500),
        },
    },
    ("delete", "/users/{user_id}"): {
        "summary": "Delete user",
        "description": (
            "Remove a user from the current tenant.  Requires tenant_admin "
            "role.  Cannot delete the last tenant_admin."
        ),
        "tags": ["Users"],
        "parameters": [_user_id_param()],
        "responses": {
            "200": {
                "description": "User deleted successfully.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "deleted": {"type": "boolean", "example": True},
                                "user_id": {"type": "string"},
                            },
                        },
                    },
                },
            },
            **_error_responses(401, 403, 404, 500),
        },
    },

    # ------------------------------------------------------------------
    # API KEYS
    # ------------------------------------------------------------------
    ("get", "/keys"): {
        "summary": "List API keys",
        "description": (
            "Returns all API keys for the authenticated tenant.  Key values "
            "are never returned -- only the key_prefix for identification."
        ),
        "tags": ["API Keys"],
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "keys": {
                        "type": "array",
                        "items": _ref("APIKeyResponse"),
                    },
                    "total": {"type": "integer"},
                }},
                "List of API keys.",
            ),
            **_error_responses(401, 500),
        },
    },
    ("post", "/keys"): {
        "summary": "Create API key",
        "description": (
            "Generate a new API key for the tenant.  The full key value is "
            "returned ONLY in this response -- store it securely.  "
            "Requires tenant_admin role."
        ),
        "tags": ["API Keys"],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _ref("APIKeyCreateRequest"),
                },
            },
        },
        "responses": {
            "201": {
                "description": "API key created.  Store the key value securely.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "key": _ref("APIKeyResponse"),
                                "key_value": {
                                    "type": "string",
                                    "description": (
                                        "Full API key value.  Only returned "
                                        "once at creation time."
                                    ),
                                    "example": "icdev_a1b2c3d4e5f6...",
                                },
                            },
                        },
                    },
                },
            },
            **_error_responses(400, 401, 403, 500),
        },
    },
    ("delete", "/keys/{key_id}"): {
        "summary": "Revoke API key",
        "description": (
            "Revoke (delete) an API key.  The key becomes immediately "
            "unusable.  Requires tenant_admin role."
        ),
        "tags": ["API Keys"],
        "parameters": [_key_id_param()],
        "responses": {
            "200": {
                "description": "API key revoked successfully.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "revoked": {"type": "boolean", "example": True},
                                "key_id": {"type": "string"},
                            },
                        },
                    },
                },
            },
            **_error_responses(401, 403, 404, 500),
        },
    },

    # ------------------------------------------------------------------
    # PROJECTS
    # ------------------------------------------------------------------
    ("post", "/projects"): {
        "summary": "Create project",
        "description": (
            "Create a new project within the tenant.  Initializes the "
            "project in the tenant database with compliance scaffolding "
            "appropriate to the specified impact level."
        ),
        "tags": ["Projects"],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _ref("ProjectCreateRequest"),
                },
            },
        },
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "project": _ref("ProjectResponse"),
                }},
                "Newly created project.",
                status="201",
            ),
            **_error_responses(400, 401, 403, 500),
        },
    },
    ("get", "/projects"): {
        "summary": "List projects",
        "description": (
            "Returns all projects belonging to the authenticated tenant.  "
            "Results are ordered by creation date descending."
        ),
        "tags": ["Projects"],
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "projects": {
                        "type": "array",
                        "items": _ref("ProjectResponse"),
                    },
                    "total": {"type": "integer"},
                }},
                "List of tenant projects.",
            ),
            **_error_responses(401, 500),
        },
    },
    ("get", "/projects/{project_id}"): {
        "summary": "Get project details",
        "description": (
            "Returns full details for a specific project, including "
            "status, classification, compliance posture summary, and "
            "recent activity."
        ),
        "tags": ["Projects"],
        "parameters": [_project_id_param()],
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "project": _ref("ProjectResponse"),
                }},
                "Project details.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },

    # ------------------------------------------------------------------
    # COMPLIANCE
    # ------------------------------------------------------------------
    ("post", "/projects/{project_id}/ssp"): {
        "summary": "Generate SSP",
        "description": (
            "Generate a System Security Plan (SSP) for the project.  "
            "Maps NIST 800-53 controls based on the project's FIPS 199 "
            "categorization and impact level.  The generated SSP includes "
            "CUI markings appropriate to the classification."
        ),
        "tags": ["Compliance"],
        "parameters": [_project_id_param()],
        "responses": {
            **_json_response(
                _ref("ComplianceResult"),
                "SSP generated successfully.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },
    ("post", "/projects/{project_id}/poam"): {
        "summary": "Generate POAM",
        "description": (
            "Generate a Plan of Action and Milestones (POAM) for the "
            "project.  Identifies open findings from STIG checks, SAST "
            "scans, and compliance assessments, and generates remediation "
            "milestones."
        ),
        "tags": ["Compliance"],
        "parameters": [_project_id_param()],
        "responses": {
            **_json_response(
                _ref("ComplianceResult"),
                "POAM generated successfully.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },
    ("post", "/projects/{project_id}/stig"): {
        "summary": "Run STIG check",
        "description": (
            "Run Security Technical Implementation Guide (STIG) checks "
            "against the project.  Evaluates CAT I, CAT II, and CAT III "
            "findings.  The merge gate blocks on any CAT I findings."
        ),
        "tags": ["Compliance"],
        "parameters": [_project_id_param()],
        "responses": {
            **_json_response(
                _ref("ComplianceResult"),
                "STIG check completed.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },
    ("post", "/projects/{project_id}/sbom"): {
        "summary": "Generate SBOM",
        "description": (
            "Generate a Software Bill of Materials (SBOM) in CycloneDX "
            "format for the project.  Enumerates all dependencies with "
            "version, license, and vulnerability information."
        ),
        "tags": ["Compliance"],
        "parameters": [_project_id_param()],
        "responses": {
            **_json_response(
                _ref("ComplianceResult"),
                "SBOM generated successfully.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },
    ("post", "/projects/{project_id}/fips199"): {
        "summary": "Run FIPS 199 categorization",
        "description": (
            "Run FIPS 199 security categorization for the project.  Uses "
            "SP 800-60 information types with high watermark algorithm.  "
            "CNSSI 1253 overlay auto-applies for IL6/SECRET projects.  "
            "Categorization is required before ATO artifacts can be generated."
        ),
        "tags": ["Compliance"],
        "parameters": [_project_id_param()],
        "responses": {
            **_json_response(
                _ref("ComplianceResult"),
                "FIPS 199 categorization completed.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },
    ("post", "/projects/{project_id}/fips200"): {
        "summary": "Run FIPS 200 validation",
        "description": (
            "Validate all 17 FIPS 200 minimum security requirement areas "
            "for the project.  Requires prior FIPS 199 categorization.  "
            "The gate blocks on any not_satisfied requirement area."
        ),
        "tags": ["Compliance"],
        "parameters": [_project_id_param()],
        "responses": {
            **_json_response(
                _ref("ComplianceResult"),
                "FIPS 200 validation completed.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },

    # ------------------------------------------------------------------
    # SECURITY
    # ------------------------------------------------------------------
    ("post", "/projects/{project_id}/scan/sast"): {
        "summary": "Run SAST scan",
        "description": (
            "Run Static Application Security Testing (SAST) against the "
            "project source code.  Uses Bandit for Python, SpotBugs for "
            "Java, eslint-security for JavaScript/TypeScript, gosec for "
            "Go, cargo-audit for Rust, and SecurityCodeScan for C#."
        ),
        "tags": ["Security"],
        "parameters": [_project_id_param()],
        "responses": {
            **_json_response(
                _ref("ScanResult"),
                "SAST scan completed.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },
    ("post", "/projects/{project_id}/scan/deps"): {
        "summary": "Scan dependencies",
        "description": (
            "Scan project dependencies for known vulnerabilities.  Uses "
            "pip-audit for Python, OWASP Dependency-Check for Java, "
            "npm audit for JavaScript/TypeScript, govulncheck for Go, "
            "cargo-audit for Rust, and dotnet list for C#.  The merge "
            "gate blocks on critical or high severity vulnerabilities."
        ),
        "tags": ["Security"],
        "parameters": [_project_id_param()],
        "responses": {
            **_json_response(
                _ref("ScanResult"),
                "Dependency scan completed.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },

    # ------------------------------------------------------------------
    # AUDIT
    # ------------------------------------------------------------------
    ("get", "/projects/{project_id}/audit"): {
        "summary": "Get audit trail",
        "description": (
            "Query the append-only audit trail for a specific project.  "
            "Supports pagination via offset/limit query parameters and "
            "filtering by event_type.  The audit trail is immutable "
            "(NIST 800-53 AU compliant -- no UPDATE/DELETE)."
        ),
        "tags": ["Audit"],
        "parameters": [
            _project_id_param(),
            {
                "name": "event_type",
                "in": "query",
                "required": False,
                "description": "Filter by event type (e.g. 'code.commit').",
                "schema": {"type": "string"},
            },
            {
                "name": "limit",
                "in": "query",
                "required": False,
                "description": "Maximum entries to return (default 50, max 500).",
                "schema": {"type": "integer", "default": 50, "maximum": 500},
            },
            {
                "name": "offset",
                "in": "query",
                "required": False,
                "description": "Number of entries to skip for pagination.",
                "schema": {"type": "integer", "default": 0},
            },
        ],
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "entries": {
                        "type": "array",
                        "items": _ref("AuditEntry"),
                    },
                    "total": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                }},
                "Paginated audit trail entries.",
            ),
            **_error_responses(401, 403, 404, 500),
        },
    },

    # ------------------------------------------------------------------
    # USAGE
    # ------------------------------------------------------------------
    ("get", "/usage"): {
        "summary": "Get usage statistics",
        "description": (
            "Returns usage statistics for the authenticated tenant, "
            "including API call counts, storage consumption, and "
            "compute time for the current and previous billing periods."
        ),
        "tags": ["Usage"],
        "parameters": [
            {
                "name": "period",
                "in": "query",
                "required": False,
                "description": (
                    "Billing period in YYYY-MM format.  Defaults to current."
                ),
                "schema": {"type": "string", "example": "2026-02"},
            },
        ],
        "responses": {
            **_json_response(
                {"type": "object", "properties": {
                    "usage": _ref("UsageRecord"),
                }},
                "Tenant usage statistics.",
            ),
            **_error_responses(401, 500),
        },
    },

    # ------------------------------------------------------------------
    # HEALTH
    # ------------------------------------------------------------------
    ("get", "/health"): {
        "summary": "Health check",
        "description": (
            "Returns the health status of the API gateway and its "
            "dependent services (platform database, MCP transport, "
            "agent mesh).  This endpoint does not require authentication."
        ),
        "tags": ["Health"],
        "security": [],
        "responses": {
            **_json_response(
                _ref("HealthResponse"),
                "Gateway health status.",
            ),
            **_error_responses(500),
        },
    },
}


# ============================================================================
# SPEC GENERATOR
# ============================================================================

def generate_openapi_spec():
    """Build the complete OpenAPI 3.0.3 specification.

    Merges the base skeleton, reusable schemas, and endpoint documentation
    into a single spec dict ready for JSON serialization.

    Returns:
        dict: Complete OpenAPI 3.0.3 specification.
    """
    spec = copy.deepcopy(OPENAPI_BASE)

    # -- Inject schemas into components -----------------------------------
    spec["components"]["schemas"] = copy.deepcopy(SCHEMAS)

    # -- Build paths from ENDPOINT_DOCS -----------------------------------
    paths = {}
    for (method, path), doc in ENDPOINT_DOCS.items():
        if path not in paths:
            paths[path] = {}
        paths[path][method] = copy.deepcopy(doc)

    spec["paths"] = paths

    return spec


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def main():
    """Dump the OpenAPI spec as formatted JSON."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate ICDEV SaaS OpenAPI 3.0.3 specification.",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file path.  Prints to stdout if omitted.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Output compact (non-indented) JSON.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    spec = generate_openapi_spec()
    indent = None if args.compact else 2
    spec_json = json.dumps(spec, indent=indent, ensure_ascii=False)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(spec_json + "\n", encoding="utf-8")
        print("OpenAPI spec written to: {}".format(output_path))
    else:
        print(spec_json)


if __name__ == "__main__":
    main()
