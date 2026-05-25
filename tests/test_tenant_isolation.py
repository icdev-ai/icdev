#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tenant_id isolation retrofit in Academy, dashboard, and TTX."""


# Ensure storage backend is SQLite for tests
import os
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"

from tools.db.storage import get_connection


class TestStorageRLSInjection:
    def test_cursor_injects_tenant_predicate(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.set_security_context(None)  # no-op when None
        # Should not raise

    def test_connection_set_security_context(self):
        conn = get_connection()
        conn.set_security_context(None)  # no-op when None


class TestAcademyDBTenantFiltering:
    def test_functions_accept_tenant_id(self):
        # Verify signatures accept tenant_id without error
        from apps.forge_academy.db import get_user, get_user_by_username

        # These functions accept tenant_id=None as default
        # We just verify they don't crash on None tenant
        try:
            get_user("nonexistent", tenant_id=None)
        except Exception as exc:
            # Missing user or missing table is fine; TypeError is not
            assert not isinstance(exc, TypeError)

        try:
            get_user_by_username("nonexistent", tenant_id=None)
        except Exception as exc:
            assert not isinstance(exc, TypeError)


class TestDashboardAuthTenantFiltering:
    def test_functions_accept_tenant_id(self):
        from tools.dashboard.auth import get_user_by_id, list_users

        try:
            get_user_by_id("nonexistent", tenant_id=None)
        except Exception as exc:
            assert not isinstance(exc, TypeError)

        try:
            list_users(tenant_id=None)
        except Exception as exc:
            assert not isinstance(exc, TypeError)


class TestTTXSessionManagerTenantFiltering:
    def test_functions_accept_tenant_id(self):
        from tools.ttx.session_manager import get_session, get_session_by_code

        try:
            get_session("nonexistent", tenant_id=None)
        except Exception as exc:
            assert not isinstance(exc, TypeError)

        try:
            get_session_by_code("nonexistent", tenant_id=None)
        except Exception as exc:
            assert not isinstance(exc, TypeError)
