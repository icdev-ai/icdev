#!/usr/bin/env python3
"""ICDEV SaaS — Role-Based Access Control (RBAC).
CUI // SP-CTI
"""
import logging
from typing import Optional

logger = logging.getLogger("saas.auth.rbac")


# Permission matrix: {category: {role: permission_level}}
# R = read, W = write, RW = read+write, OWN = own resources only, - = denied
PERMISSIONS = {
    "projects": {
        "tenant_admin": "RW",
        "developer": "RW",
        "compliance_officer": "R",
        "auditor": "R",
        "viewer": "R",
    },
    "code_generation": {
        "tenant_admin": "RW",
        "developer": "RW",
        "compliance_officer": "-",
        "auditor": "-",
        "viewer": "-",
    },
    "compliance": {
        "tenant_admin": "RW",
        "developer": "RW",
        "compliance_officer": "RW",
        "auditor": "R",
        "viewer": "R",
    },
    "security": {
        "tenant_admin": "RW",
        "developer": "RW",
        "compliance_officer": "R",
        "auditor": "R",
        "viewer": "R",
    },
    "audit": {
        "tenant_admin": "R",
        "developer": "R",
        "compliance_officer": "R",
        "auditor": "R",
        "viewer": "R",
    },
    "team": {
        "tenant_admin": "RW",
        "developer": "-",
        "compliance_officer": "-",
        "auditor": "-",
        "viewer": "-",
    },
    "api_keys": {
        "tenant_admin": "RW",
        "developer": "OWN",
        "compliance_officer": "OWN",
        "auditor": "OWN",
        "viewer": "OWN",
    },
    "billing": {
        "tenant_admin": "R",
        "developer": "-",
        "compliance_officer": "-",
        "auditor": "-",
        "viewer": "-",
    },
    "tenant_settings": {
        "tenant_admin": "RW",
        "developer": "R",
        "compliance_officer": "R",
        "auditor": "R",
        "viewer": "R",
    },
}

# Map REST endpoint prefixes to permission categories
ENDPOINT_CATEGORY_MAP = {
    "/api/v1/projects": "projects",
    "/api/v1/projects/{id}/scaffold": "code_generation",
    "/api/v1/projects/{id}/generate": "code_generation",
    "/api/v1/projects/{id}/test": "code_generation",
    "/api/v1/projects/{id}/ssp": "compliance",
    "/api/v1/projects/{id}/poam": "compliance",
    "/api/v1/projects/{id}/stig": "compliance",
    "/api/v1/projects/{id}/sbom": "compliance",
    "/api/v1/projects/{id}/fips199": "compliance",
    "/api/v1/projects/{id}/fips200": "compliance",
    "/api/v1/projects/{id}/crosswalk": "compliance",
    "/api/v1/projects/{id}/fedramp": "compliance",
    "/api/v1/projects/{id}/cmmc": "compliance",
    "/api/v1/projects/{id}/scan": "security",
    "/api/v1/projects/{id}/audit": "audit",
    "/api/v1/projects/{id}/artifacts": "compliance",
    "/api/v1/users": "team",
    "/api/v1/keys": "api_keys",
    "/api/v1/usage": "billing",
    "/api/v1/tenants": "tenant_settings",
    "/mcp/v1": "compliance",  # MCP tools default to compliance category
}


def get_endpoint_category(path: str) -> str:
    """Resolve the permission category for a request path."""
    # Try exact match first, then prefix match (longest first)
    for pattern in sorted(ENDPOINT_CATEGORY_MAP.keys(), key=len, reverse=True):
        # Normalize pattern: replace {id} with wildcard matching
        normalized = pattern.replace("{id}", "")
        if path.startswith(normalized.rstrip("/")):
            return ENDPOINT_CATEGORY_MAP[pattern]
    return "projects"  # default


def check_permission(role: str, category: str, method: str = "GET",
                     user_id: Optional[str] = None,
                     resource_owner_id: Optional[str] = None) -> bool:
    """Check if a role has permission for the given category and HTTP method.

    Args:
        role: User role (tenant_admin, developer, etc.)
        category: Permission category (projects, compliance, etc.)
        method: HTTP method (GET, POST, PUT, PATCH, DELETE)
        user_id: Current user's ID (for OWN checks)
        resource_owner_id: Owner of the resource being accessed (for OWN checks)

    Returns:
        True if allowed, False if denied.
    """
    if category not in PERMISSIONS:
        logger.warning("Unknown category: %s -- denying", category)
        return False

    perm = PERMISSIONS[category].get(role, "-")

    if perm == "-":
        return False

    if perm == "R":
        return method in ("GET", "HEAD", "OPTIONS")

    if perm == "RW":
        return True

    if perm == "OWN":
        # For OWN, allow read of own resources, write of own resources
        if user_id and resource_owner_id:
            return user_id == resource_owner_id
        # If no resource_owner_id provided, allow GET (list own), deny others
        return method in ("GET", "HEAD", "OPTIONS", "POST")  # POST = create own

    return False


def require_permission(role: str, path: str, method: str = "GET",
                       user_id: Optional[str] = None,
                       resource_owner_id: Optional[str] = None) -> bool:
    """Convenience: resolve category from path, then check permission."""
    category = get_endpoint_category(path)
    return check_permission(role, category, method, user_id, resource_owner_id)
