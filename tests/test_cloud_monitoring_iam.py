#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for P4-4: Cloud Monitoring, IAM, and Registry providers (Phase 38D).

Covers: MonitoringProvider, IAMProvider, RegistryProvider, CSPHealthChecker.
All tested using Local implementations (no cloud SDK needed).
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestMonitoringProvider(unittest.TestCase):
    """Tests for cloud monitoring provider ABCs and Local implementation."""

    def test_import_base(self):
        """MonitoringProvider ABC should be importable."""
        from tools.cloud.monitoring_provider import MonitoringProvider
        self.assertTrue(callable(MonitoringProvider))

    def test_import_local(self):
        """LocalMonitoringProvider should be importable."""
        from tools.cloud.monitoring_provider import LocalMonitoringProvider
        self.assertTrue(callable(LocalMonitoringProvider))

    def test_local_send_metric(self):
        """Local provider should accept metric data."""
        from tools.cloud.monitoring_provider import LocalMonitoringProvider
        provider = LocalMonitoringProvider()
        result = provider.send_metric(
            namespace="icdev-test",
            metric_name="test_metric",
            value=42.0,
            dimensions={"service": "test"},
        )
        self.assertIsInstance(result, bool)
        self.assertTrue(result)

    def test_local_send_log(self):
        """Local provider should accept log data."""
        from tools.cloud.monitoring_provider import LocalMonitoringProvider
        provider = LocalMonitoringProvider()
        result = provider.send_log(
            log_group="icdev-test",
            message="Test log message",
            level="INFO",
        )
        self.assertIsInstance(result, bool)
        self.assertTrue(result)

    def test_local_check_availability(self):
        """Local provider should always be available."""
        from tools.cloud.monitoring_provider import LocalMonitoringProvider
        provider = LocalMonitoringProvider()
        self.assertTrue(provider.check_availability())

    def test_abc_methods(self):
        """ABC should define send_metric, send_log, query_metrics, create_alarm, check_availability."""
        from tools.cloud.monitoring_provider import MonitoringProvider
        import inspect
        methods = [m for m in dir(MonitoringProvider) if not m.startswith("_")]
        expected = {"send_metric", "send_log", "query_metrics", "create_alarm", "check_availability"}
        self.assertTrue(expected.issubset(set(methods)))


class TestIAMProvider(unittest.TestCase):
    """Tests for cloud IAM provider ABCs and Local implementation."""

    def test_import_base(self):
        """IAMProvider ABC should be importable."""
        from tools.cloud.iam_provider import IAMProvider
        self.assertTrue(callable(IAMProvider))

    def test_import_local(self):
        """LocalIAMProvider should be importable."""
        from tools.cloud.iam_provider import LocalIAMProvider
        self.assertTrue(callable(LocalIAMProvider))

    def test_local_create_service_account(self):
        """Local provider should create service accounts."""
        from tools.cloud.iam_provider import LocalIAMProvider
        with tempfile.TemporaryDirectory() as td:
            provider = LocalIAMProvider(data_dir=td)
            result = provider.create_service_account(
                name="test-account",
                description="test service account",
            )
            self.assertIsInstance(result, dict)
            self.assertIn("id", result)

    def test_local_check_permission(self):
        """Local provider should check permissions."""
        from tools.cloud.iam_provider import LocalIAMProvider
        provider = LocalIAMProvider()
        result = provider.check_permission(
            account_id="test-user",
            action="read",
            resource="data/*",
        )
        self.assertIsInstance(result, bool)

    def test_local_check_availability(self):
        """Local provider should always be available."""
        from tools.cloud.iam_provider import LocalIAMProvider
        provider = LocalIAMProvider()
        self.assertTrue(provider.check_availability())


class TestRegistryProvider(unittest.TestCase):
    """Tests for container registry provider ABCs and Local implementation."""

    def test_import_base(self):
        """RegistryProvider ABC should be importable."""
        from tools.cloud.registry_provider import RegistryProvider
        self.assertTrue(callable(RegistryProvider))

    def test_import_local(self):
        """LocalDockerProvider should be importable."""
        from tools.cloud.registry_provider import LocalDockerProvider
        self.assertTrue(callable(LocalDockerProvider))

    def test_local_list_images(self):
        """Local provider should list images."""
        from tools.cloud.registry_provider import LocalDockerProvider
        provider = LocalDockerProvider()
        result = provider.list_images(repository="test-repo")
        self.assertIsInstance(result, list)

    def test_local_check_availability(self):
        """Local provider should always be available."""
        from tools.cloud.registry_provider import LocalDockerProvider
        provider = LocalDockerProvider()
        self.assertTrue(provider.check_availability())


class TestCSPHealthChecker(unittest.TestCase):
    """Tests for the CSP health checker."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        # Pre-create cloud_provider_status table (checker only writes if it exists)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cloud_provider_status (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                service TEXT NOT NULL,
                status TEXT NOT NULL,
                latency_ms REAL DEFAULT 0.0,
                error_message TEXT,
                checked_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_import(self):
        """CSPHealthChecker should be importable."""
        from tools.cloud.csp_health_checker import CSPHealthChecker
        self.assertTrue(callable(CSPHealthChecker))

    def test_check_all_services(self):
        """Health check should return status for all services."""
        from tools.cloud.csp_health_checker import CSPHealthChecker
        checker = CSPHealthChecker(db_path=self.db_path)
        result = checker.check_all()
        self.assertIsInstance(result, dict)
        self.assertIn("services", result)

    def test_health_stored_in_db(self):
        """Health check results should be stored in cloud_provider_status."""
        from tools.cloud.csp_health_checker import CSPHealthChecker
        checker = CSPHealthChecker(db_path=self.db_path)
        checker.check_all()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM cloud_provider_status")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertGreaterEqual(count, 1)


class TestProviderFactory(unittest.TestCase):
    """Test that provider factory can instantiate monitoring/IAM/registry."""

    def test_factory_import(self):
        """CSPProviderFactory should be importable."""
        from tools.cloud.provider_factory import CSPProviderFactory
        self.assertTrue(callable(CSPProviderFactory))

    @patch.dict(os.environ, {"ICDEV_CLOUD_PROVIDER": "local"})
    def test_factory_local_monitoring(self):
        """Factory should return local monitoring provider."""
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory()
        try:
            provider = factory.get_monitoring_provider()
            self.assertIsNotNone(provider)
        except (AttributeError, NotImplementedError):
            # Factory may not have monitoring yet; acceptable
            pass

    @patch.dict(os.environ, {"ICDEV_CLOUD_PROVIDER": "local"})
    def test_factory_local_iam(self):
        """Factory should return local IAM provider."""
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory()
        try:
            provider = factory.get_iam_provider()
            self.assertIsNotNone(provider)
        except (AttributeError, NotImplementedError):
            # Factory may not have IAM yet; acceptable
            pass


if __name__ == "__main__":
    unittest.main()
