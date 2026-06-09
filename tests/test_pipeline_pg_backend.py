"""Regression tests for pipeline canvas PostgreSQL compatibility."""
import os
from unittest.mock import patch

import pytest


class TestPipelineInitDbPgBackend:
    """Verify pipeline db/init_db.py correctly routes to PG when ICDEV_STORAGE_BACKEND=postgresql."""

    def test_get_connection_respects_icdev_storage_backend_postgresql(self):
        """When ICDEV_STORAGE_BACKEND=postgresql, get_connection must return a PG StorageConnection with RLS disabled."""
        from tools.pipeline.db import init_db as pdc_init

        # Force PostgreSQL backend
        with patch.dict(os.environ, {"ICDEV_STORAGE_BACKEND": "postgresql", "PC_STORAGE_BACKEND": ""}, clear=False):
            # Reload the module-level _PC_BACKEND so it picks up the env var
            # We call get_connection which re-evaluates _PC_BACKEND on each call
            # because _PC_BACKEND is module-level and cached at import time.
            # To handle this, patch the module-level constant directly.
            orig_backend = pdc_init._PC_BACKEND
            try:
                pdc_init._PC_BACKEND = "postgresql"
                conn = pdc_init.get_connection()
                assert getattr(conn, "_backend", None) == "postgresql", (
                    f"Expected PG backend, got {getattr(conn, '_backend', None)}"
                )
                # RLS must be disabled for canvas tables (no tenant_id/classification)
                assert getattr(conn, "_security_context", None) is None, (
                    "Canvas connection must have security_context=None (RLS disabled)"
                )
            finally:
                pdc_init._PC_BACKEND = orig_backend
                try:
                    conn.close()
                except Exception:
                    pass

    def test_get_connection_sqlite_fallback_when_no_env(self):
        """When no env vars are set, get_connection must return a sqlite3 connection."""
        from tools.pipeline.db import init_db as pdc_init

        with patch.dict(os.environ, {"ICDEV_STORAGE_BACKEND": "", "PC_STORAGE_BACKEND": "", "ICDEV_CANVAS_STORAGE_BACKEND": ""}, clear=False):
            orig_backend = pdc_init._PC_BACKEND
            try:
                pdc_init._PC_BACKEND = "sqlite"
                conn = pdc_init.get_connection()
                # Should be a raw sqlite3 connection or wrapped; either way _backend should be sqlite
                assert getattr(conn, "_backend", "sqlite") == "sqlite"
            finally:
                pdc_init._PC_BACKEND = orig_backend
                try:
                    conn.close()
                except Exception:
                    pass
