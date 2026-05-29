#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for G-04: API key scope enforcement in tools.saas.auth.middleware."""
import os
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

from tools.saas.auth.middleware import _check_api_key_scopes


class TestCheckApiKeyScopes:
    """Unit tests for the scope-checking helper (no Flask required)."""

    # ------------------------------------------------------------------
    # Permissive cases
    # ------------------------------------------------------------------

    def test_empty_scopes_allows_all(self):
        assert _check_api_key_scopes([], "GET", "/api/v1/projects") is True

    def test_none_scopes_allows_all(self):
        assert _check_api_key_scopes(None, "POST", "/api/v1/projects") is True

    def test_wildcard_allows_all(self):
        assert _check_api_key_scopes(["*"], "DELETE", "/api/v1/projects") is True

    def test_wildcard_colon_star_allows_all(self):
        assert _check_api_key_scopes(["*:*"], "POST", "/api/v1/compliance") is True

    # ------------------------------------------------------------------
    # Read / write level matching
    # ------------------------------------------------------------------

    def test_read_scope_permits_get(self):
        assert _check_api_key_scopes(["projects:read"], "GET", "/api/v1/projects") is True

    def test_read_scope_denies_post(self):
        assert _check_api_key_scopes(["projects:read"], "POST", "/api/v1/projects") is False

    def test_write_scope_permits_get(self):
        # write >= read, so GET allowed
        assert _check_api_key_scopes(["projects:write"], "GET", "/api/v1/projects") is True

    def test_write_scope_permits_post(self):
        assert _check_api_key_scopes(["projects:write"], "POST", "/api/v1/projects") is True

    def test_admin_scope_permits_delete(self):
        assert _check_api_key_scopes(["projects:admin"], "DELETE", "/api/v1/projects") is True

    def test_read_scope_denies_put(self):
        assert _check_api_key_scopes(["projects:read"], "PUT", "/api/v1/projects/123") is False

    def test_read_scope_denies_patch(self):
        assert _check_api_key_scopes(["projects:read"], "PATCH", "/api/v1/projects/123") is False

    def test_read_scope_denies_delete(self):
        assert _check_api_key_scopes(["projects:read"], "DELETE", "/api/v1/projects/123") is False

    # ------------------------------------------------------------------
    # Canvas matching
    # ------------------------------------------------------------------

    def test_wrong_canvas_denied(self):
        assert _check_api_key_scopes(["compliance:read"], "GET", "/api/v1/projects") is False

    def test_correct_canvas_allowed(self):
        assert _check_api_key_scopes(["compliance:read"], "GET", "/api/v1/compliance") is True

    def test_multiple_scopes_first_match_wins(self):
        scopes = ["compliance:read", "projects:write"]
        assert _check_api_key_scopes(scopes, "POST", "/api/v1/projects") is True
        assert _check_api_key_scopes(scopes, "POST", "/api/v1/compliance") is False

    def test_hyphen_normalised_to_underscore(self):
        # Scope uses underscore, path segment uses hyphen
        assert _check_api_key_scopes(["ai_gameday:read"], "GET", "/api/v1/ai-gameday") is True

    # ------------------------------------------------------------------
    # Path parsing edge cases
    # ------------------------------------------------------------------

    def test_path_without_api_prefix(self):
        # Non-API path — canvas defaults to first segment
        assert _check_api_key_scopes(["projects:read"], "GET", "/projects") is True

    def test_deep_path_uses_first_canvas_segment(self):
        # /api/v1/projects/uuid/tasks — canvas is 'projects'
        assert _check_api_key_scopes(["projects:write"], "POST", "/api/v1/projects/abc/tasks") is True

    def test_empty_path_empty_canvas(self):
        # Edge: empty path → canvas = '' → no match unless wildcard
        assert _check_api_key_scopes(["projects:read"], "GET", "/") is False

    # ------------------------------------------------------------------
    # HTTP method edge cases
    # ------------------------------------------------------------------

    def test_head_is_read(self):
        assert _check_api_key_scopes(["projects:read"], "HEAD", "/api/v1/projects") is True

    def test_options_is_read(self):
        assert _check_api_key_scopes(["projects:read"], "OPTIONS", "/api/v1/projects") is True

    def test_unknown_method_requires_write(self):
        # Unknown HTTP methods default to 'write' level
        assert _check_api_key_scopes(["projects:read"], "PROPFIND", "/api/v1/projects") is False
        assert _check_api_key_scopes(["projects:write"], "PROPFIND", "/api/v1/projects") is True
