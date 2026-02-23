# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools.saas.openapi_spec — OpenAPI 3.0.3 specification generator.

Validates the generated OpenAPI spec structure, schemas, security schemes,
endpoint documentation, and JSON serialization.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.saas.openapi_spec import (
    ENDPOINT_DOCS,
    OPENAPI_BASE,
    SCHEMAS,
    generate_openapi_spec,
)


# ============================================================================
# OPENAPI_BASE structure tests
# ============================================================================

class TestOpenAPIBase:
    """Tests for the OPENAPI_BASE skeleton dict."""

    def test_base_has_openapi_key(self):
        """OPENAPI_BASE must contain the 'openapi' version key."""
        assert "openapi" in OPENAPI_BASE

    def test_base_has_info_key(self):
        """OPENAPI_BASE must contain the 'info' metadata block."""
        assert "info" in OPENAPI_BASE

    def test_base_openapi_version_is_303(self):
        """OpenAPI version must be 3.0.3 per ADR D153."""
        assert OPENAPI_BASE["openapi"] == "3.0.3"

    def test_base_has_servers(self):
        """OPENAPI_BASE must define at least one server."""
        assert "servers" in OPENAPI_BASE
        assert len(OPENAPI_BASE["servers"]) >= 1

    def test_base_has_security(self):
        """OPENAPI_BASE must define a top-level security requirement."""
        assert "security" in OPENAPI_BASE
        assert len(OPENAPI_BASE["security"]) >= 1

    def test_base_has_tags(self):
        """OPENAPI_BASE must define categorization tags."""
        assert "tags" in OPENAPI_BASE
        assert len(OPENAPI_BASE["tags"]) > 0


# ============================================================================
# SCHEMAS tests
# ============================================================================

class TestSchemas:
    """Tests for the reusable SCHEMAS dict."""

    def test_schemas_has_error_response(self):
        """SCHEMAS must include the ErrorResponse schema."""
        assert "ErrorResponse" in SCHEMAS

    def test_schemas_has_tenant_response(self):
        """SCHEMAS must include the TenantResponse schema."""
        assert "TenantResponse" in SCHEMAS

    def test_schemas_has_project_response(self):
        """SCHEMAS must include the ProjectResponse schema."""
        assert "ProjectResponse" in SCHEMAS

    def test_error_response_has_required_fields(self):
        """ErrorResponse schema must require 'error' and 'code'."""
        err = SCHEMAS["ErrorResponse"]
        assert "required" in err
        assert "error" in err["required"]
        assert "code" in err["required"]


# ============================================================================
# generate_openapi_spec() tests
# ============================================================================

class TestGenerateSpec:
    """Tests for the generate_openapi_spec() function."""

    @pytest.fixture(autouse=True)
    def _spec(self):
        self.spec = generate_openapi_spec()

    def test_returns_dict(self):
        """generate_openapi_spec must return a dict."""
        assert isinstance(self.spec, dict)

    def test_spec_has_info_title(self):
        """Generated spec must have an info.title field."""
        assert "info" in self.spec
        assert "title" in self.spec["info"]
        assert len(self.spec["info"]["title"]) > 0

    def test_spec_has_security_schemes(self):
        """Generated spec must have all three security schemes."""
        schemes = self.spec["components"]["securitySchemes"]
        assert "ApiKeyAuth" in schemes
        assert "OAuthBearer" in schemes
        assert "CACAuth" in schemes

    def test_spec_has_all_23_endpoint_paths(self):
        """Generated spec paths must cover all 23 documented endpoints."""
        # ENDPOINT_DOCS has 23 (method, path) tuples
        assert len(ENDPOINT_DOCS) == 23
        # All paths should be present in the spec
        for (_method, path) in ENDPOINT_DOCS:
            assert path in self.spec["paths"], (
                f"Path '{path}' missing from generated spec"
            )

    def test_spec_paths_have_correct_http_methods(self):
        """Each documented (method, path) must appear under the correct method."""
        for (method, path), _doc in ENDPOINT_DOCS.items():
            assert method in self.spec["paths"][path], (
                f"Method '{method}' missing for path '{path}'"
            )

    def test_get_tenants_me_exists(self):
        """GET /tenants/me endpoint must exist in the spec."""
        assert "/tenants/me" in self.spec["paths"]
        assert "get" in self.spec["paths"]["/tenants/me"]

    def test_post_projects_has_request_body(self):
        """POST /projects must include a requestBody definition."""
        post_doc = self.spec["paths"]["/projects"]["post"]
        assert "requestBody" in post_doc

    def test_get_health_exists(self):
        """GET /health endpoint must exist in the spec."""
        assert "/health" in self.spec["paths"]
        assert "get" in self.spec["paths"]["/health"]

    def test_tags_list_populated(self):
        """The spec tags list must contain at least one tag."""
        assert "tags" in self.spec
        assert len(self.spec["tags"]) >= 1

    def test_spec_serializable_to_json(self):
        """The complete spec must be serializable to JSON without errors."""
        json_str = json.dumps(self.spec, ensure_ascii=False)
        assert len(json_str) > 0
        # Round-trip parse must succeed
        parsed = json.loads(json_str)
        assert parsed["openapi"] == "3.0.3"
