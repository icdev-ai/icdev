# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for ICDEV SaaS RBAC (tools/saas/auth/rbac.py).

Validates the 5-role permission matrix, endpoint-to-category mapping,
OWN permission logic, and the require_permission convenience function.
"""

import pytest

try:
    from tools.saas.auth.rbac import (
        PERMISSIONS,
        ENDPOINT_CATEGORY_MAP,
        check_permission,
        get_endpoint_category,
        require_permission,
    )
    _IMPORT_OK = True
except ImportError:
    _IMPORT_OK = False

pytestmark = pytest.mark.skipif(not _IMPORT_OK, reason="tools.saas.auth.rbac not available")


# ---------------------------------------------------------------------------
# All five roles
# ---------------------------------------------------------------------------
ALL_ROLES = ["tenant_admin", "developer", "compliance_officer", "auditor", "viewer"]
READ_METHODS = ("GET", "HEAD", "OPTIONS")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


# ---------------------------------------------------------------------------
# Role x Category permission matrix tests
# ---------------------------------------------------------------------------

class TestPermissionMatrix:
    """Verify the PERMISSIONS matrix for each role and category."""

    # ---- projects ----
    def test_tenant_admin_projects_rw(self):
        assert check_permission("tenant_admin", "projects", "POST")
        assert check_permission("tenant_admin", "projects", "GET")

    def test_developer_projects_rw(self):
        assert check_permission("developer", "projects", "POST")
        assert check_permission("developer", "projects", "GET")

    def test_compliance_officer_projects_read_only(self):
        assert check_permission("compliance_officer", "projects", "GET")
        assert not check_permission("compliance_officer", "projects", "POST")

    def test_auditor_projects_read_only(self):
        assert check_permission("auditor", "projects", "GET")
        assert not check_permission("auditor", "projects", "DELETE")

    def test_viewer_projects_read_only(self):
        assert check_permission("viewer", "projects", "GET")
        assert not check_permission("viewer", "projects", "PUT")

    # ---- code_generation ----
    def test_developer_code_generation_rw(self):
        assert check_permission("developer", "code_generation", "POST")

    def test_compliance_officer_code_generation_denied(self):
        assert not check_permission("compliance_officer", "code_generation", "GET")

    def test_auditor_code_generation_denied(self):
        assert not check_permission("auditor", "code_generation", "GET")

    def test_viewer_code_generation_denied(self):
        assert not check_permission("viewer", "code_generation", "GET")

    # ---- compliance ----
    def test_compliance_officer_compliance_rw(self):
        assert check_permission("compliance_officer", "compliance", "POST")
        assert check_permission("compliance_officer", "compliance", "GET")

    def test_auditor_compliance_read(self):
        assert check_permission("auditor", "compliance", "GET")
        assert not check_permission("auditor", "compliance", "POST")

    # ---- team ----
    def test_tenant_admin_team_rw(self):
        assert check_permission("tenant_admin", "team", "POST")

    def test_developer_team_denied(self):
        assert not check_permission("developer", "team", "GET")

    def test_viewer_team_denied(self):
        assert not check_permission("viewer", "team", "GET")

    # ---- billing ----
    def test_tenant_admin_billing_read(self):
        assert check_permission("tenant_admin", "billing", "GET")
        assert not check_permission("tenant_admin", "billing", "POST")

    def test_developer_billing_denied(self):
        assert not check_permission("developer", "billing", "GET")

    # ---- tenant_settings ----
    def test_tenant_admin_settings_rw(self):
        assert check_permission("tenant_admin", "tenant_settings", "PUT")

    def test_developer_settings_read_only(self):
        assert check_permission("developer", "tenant_settings", "GET")
        assert not check_permission("developer", "tenant_settings", "PUT")


# ---------------------------------------------------------------------------
# llm_keys category (Phase 31)
# ---------------------------------------------------------------------------

class TestLLMKeysCategory:
    """Verify the llm_keys permission category."""

    def test_llm_keys_in_permissions(self):
        assert "llm_keys" in PERMISSIONS

    def test_tenant_admin_llm_keys_rw(self):
        assert check_permission("tenant_admin", "llm_keys", "POST")
        assert check_permission("tenant_admin", "llm_keys", "GET")

    def test_developer_llm_keys_read_only(self):
        assert check_permission("developer", "llm_keys", "GET")
        assert not check_permission("developer", "llm_keys", "POST")

    def test_compliance_officer_llm_keys_denied(self):
        assert not check_permission("compliance_officer", "llm_keys", "GET")

    def test_auditor_llm_keys_denied(self):
        assert not check_permission("auditor", "llm_keys", "GET")

    def test_viewer_llm_keys_denied(self):
        assert not check_permission("viewer", "llm_keys", "GET")


# ---------------------------------------------------------------------------
# OWN permission logic
# ---------------------------------------------------------------------------

class TestOWNPermission:
    """Verify OWN-scoped access for api_keys category."""

    def test_own_allows_matching_user(self):
        assert check_permission(
            "developer", "api_keys", "DELETE",
            user_id="user-1", resource_owner_id="user-1",
        )

    def test_own_denies_mismatched_user(self):
        assert not check_permission(
            "developer", "api_keys", "DELETE",
            user_id="user-1", resource_owner_id="user-2",
        )

    def test_own_allows_get_without_resource_owner(self):
        # When no resource_owner_id is provided, GET is allowed (list own)
        assert check_permission("developer", "api_keys", "GET", user_id="user-1")

    def test_own_allows_post_without_resource_owner(self):
        # POST = create own resource
        assert check_permission("developer", "api_keys", "POST", user_id="user-1")

    def test_own_denies_delete_without_resource_owner(self):
        # DELETE without resource_owner_id is denied (can't prove ownership)
        assert not check_permission("developer", "api_keys", "DELETE", user_id="user-1")


# ---------------------------------------------------------------------------
# Endpoint-to-category mapping
# ---------------------------------------------------------------------------

class TestEndpointCategoryMap:
    """Verify REST endpoints resolve to the correct permission category."""

    def test_projects_endpoint(self):
        assert get_endpoint_category("/api/v1/projects") == "projects"

    def test_scaffold_endpoint_pattern(self):
        """Endpoint patterns with {id} are in the map."""
        assert "/api/v1/projects/{id}/scaffold" in ENDPOINT_CATEGORY_MAP
        assert ENDPOINT_CATEGORY_MAP["/api/v1/projects/{id}/scaffold"] == "code_generation"

    def test_ssp_endpoint_pattern(self):
        assert "/api/v1/projects/{id}/ssp" in ENDPOINT_CATEGORY_MAP
        assert ENDPOINT_CATEGORY_MAP["/api/v1/projects/{id}/ssp"] == "compliance"

    def test_scan_endpoint_pattern(self):
        assert "/api/v1/projects/{id}/scan" in ENDPOINT_CATEGORY_MAP
        assert ENDPOINT_CATEGORY_MAP["/api/v1/projects/{id}/scan"] == "security"

    def test_audit_endpoint_pattern(self):
        assert "/api/v1/projects/{id}/audit" in ENDPOINT_CATEGORY_MAP
        assert ENDPOINT_CATEGORY_MAP["/api/v1/projects/{id}/audit"] == "audit"

    def test_users_endpoint(self):
        assert get_endpoint_category("/api/v1/users") == "team"

    def test_keys_endpoint(self):
        assert get_endpoint_category("/api/v1/keys") == "api_keys"

    def test_llm_keys_endpoint(self):
        assert get_endpoint_category("/api/v1/llm-keys") == "llm_keys"

    def test_usage_endpoint(self):
        assert get_endpoint_category("/api/v1/usage") == "billing"

    def test_tenants_endpoint(self):
        assert get_endpoint_category("/api/v1/tenants") == "tenant_settings"

    def test_mcp_endpoint(self):
        assert get_endpoint_category("/mcp/v1") == "compliance"

    def test_unknown_endpoint_defaults_to_projects(self):
        cat = get_endpoint_category("/api/v1/completely-unknown")
        assert cat == "projects"


# ---------------------------------------------------------------------------
# Unknown category denial
# ---------------------------------------------------------------------------

class TestUnknownCategory:
    """Verify unknown categories are denied."""

    def test_unknown_category_denied_for_admin(self):
        assert not check_permission("tenant_admin", "nonexistent_category", "GET")

    def test_unknown_category_denied_for_developer(self):
        assert not check_permission("developer", "nonexistent_category", "POST")


# ---------------------------------------------------------------------------
# require_permission convenience function
# ---------------------------------------------------------------------------

class TestRequirePermission:
    """Verify the require_permission helper resolves path and checks access."""

    def test_admin_can_post_projects(self):
        assert require_permission("tenant_admin", "/api/v1/projects", "POST")

    def test_viewer_cannot_post_projects(self):
        assert not require_permission("viewer", "/api/v1/projects", "POST")

    def test_developer_can_get_compliance(self):
        assert require_permission(
            "developer", "/api/v1/projects/proj-1/ssp", "GET"
        )

    def test_auditor_cannot_post_scan(self):
        assert not require_permission(
            "auditor", "/api/v1/projects/proj-1/scan", "POST"
        )


# [TEMPLATE: CUI // SP-CTI]
