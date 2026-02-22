#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/cloud/ CSP abstraction layer.

Tests local implementations (always available), factory config loading,
and graceful degradation when cloud SDKs are not installed.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cloud.secrets_provider import LocalSecretsProvider
from tools.cloud.storage_provider import LocalStorageProvider
from tools.cloud.kms_provider import LocalKMSProvider


# ============================================================
# Local Secrets Provider
# ============================================================
class TestLocalSecrets:
    def test_provider_name(self, tmp_path):
        p = LocalSecretsProvider(env_file=str(tmp_path / ".env"))
        assert p.provider_name == "local"

    def test_put_and_get(self, tmp_path):
        env_file = tmp_path / ".env"
        p = LocalSecretsProvider(env_file=str(env_file))
        assert p.put_secret("MY_KEY", "my_value") is True
        assert p.get_secret("MY_KEY") == "my_value"

    def test_list_secrets(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=val1\nKEY2=val2\n")
        p = LocalSecretsProvider(env_file=str(env_file))
        secrets = p.list_secrets()
        assert "KEY1" in secrets
        assert "KEY2" in secrets

    def test_delete_secret(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=val1\nKEY2=val2\n")
        p = LocalSecretsProvider(env_file=str(env_file))
        assert p.delete_secret("KEY1") is True
        assert p.get_secret("KEY1") is None

    def test_availability(self, tmp_path):
        p = LocalSecretsProvider(env_file=str(tmp_path / ".env"))
        assert p.check_availability() is True

    def test_get_from_env_var(self, tmp_path):
        p = LocalSecretsProvider(env_file=str(tmp_path / ".env"))
        os.environ["_TEST_ICDEV_SECRET"] = "from_env"
        try:
            assert p.get_secret("_TEST_ICDEV_SECRET") == "from_env"
        finally:
            del os.environ["_TEST_ICDEV_SECRET"]

    def test_handles_comments_and_empty_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# Comment\n\nKEY1=val1\n# Another comment\nKEY2=val2\n")
        p = LocalSecretsProvider(env_file=str(env_file))
        assert len(p.list_secrets()) == 2


# ============================================================
# Local Storage Provider
# ============================================================
class TestLocalStorage:
    def test_provider_name(self, tmp_path):
        p = LocalStorageProvider(base_dir=str(tmp_path))
        assert p.provider_name == "local"

    def test_upload_and_download(self, tmp_path):
        p = LocalStorageProvider(base_dir=str(tmp_path))
        assert p.upload("mybucket", "mykey.txt", b"hello world") is True
        data = p.download("mybucket", "mykey.txt")
        assert data == b"hello world"

    def test_list_objects(self, tmp_path):
        p = LocalStorageProvider(base_dir=str(tmp_path))
        p.upload("bucket", "dir/file1.txt", b"data1")
        p.upload("bucket", "dir/file2.txt", b"data2")
        p.upload("bucket", "other.txt", b"data3")
        objects = p.list_objects("bucket", prefix="dir/")
        assert len(objects) == 2

    def test_delete(self, tmp_path):
        p = LocalStorageProvider(base_dir=str(tmp_path))
        p.upload("bucket", "key.txt", b"data")
        assert p.delete("bucket", "key.txt") is True
        assert p.download("bucket", "key.txt") is None

    def test_download_nonexistent(self, tmp_path):
        p = LocalStorageProvider(base_dir=str(tmp_path))
        assert p.download("nobucket", "nokey") is None

    def test_availability(self, tmp_path):
        p = LocalStorageProvider(base_dir=str(tmp_path))
        assert p.check_availability() is True

    def test_nested_keys(self, tmp_path):
        p = LocalStorageProvider(base_dir=str(tmp_path))
        p.upload("bucket", "a/b/c/deep.txt", b"deep data")
        data = p.download("bucket", "a/b/c/deep.txt")
        assert data == b"deep data"


# ============================================================
# Local KMS Provider
# ============================================================
class TestLocalKMS:
    def test_provider_name(self):
        p = LocalKMSProvider()
        assert p.provider_name == "local"

    def test_availability(self):
        p = LocalKMSProvider()
        # Should be available if cryptography is installed
        # (it's in our requirements)
        from tools.cloud.kms_provider import _HAS_FERNET
        assert p.check_availability() == _HAS_FERNET

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("cryptography"),
        reason="cryptography not installed"
    )
    def test_encrypt_decrypt(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        p = LocalKMSProvider(key=key)
        encrypted = p.encrypt(b"hello world")
        assert encrypted is not None
        decrypted = p.decrypt(encrypted)
        assert decrypted == b"hello world"

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("cryptography"),
        reason="cryptography not installed"
    )
    def test_generate_data_key(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        p = LocalKMSProvider(key=key)
        result = p.generate_data_key()
        assert result is not None
        plaintext_key, encrypted_key = result
        assert len(plaintext_key) > 0
        assert len(encrypted_key) > 0


# ============================================================
# CSP Provider Factory
# ============================================================
class TestCSPProviderFactory:
    def test_factory_defaults_to_local(self, tmp_path):
        # Write minimal config
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        assert factory.global_provider == "local"

    def test_factory_secrets_local(self, tmp_path):
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        provider = factory.get_secrets_provider()
        assert provider.provider_name == "local"

    def test_factory_storage_local(self, tmp_path):
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        provider = factory.get_storage_provider()
        assert provider.provider_name == "local"

    def test_factory_kms_local(self, tmp_path):
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        provider = factory.get_kms_provider()
        assert provider.provider_name == "local"

    def test_factory_health_check(self, tmp_path):
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        health = factory.health_check()
        assert health["global_provider"] == "local"
        assert "secrets" in health["services"]
        assert "storage" in health["services"]
        assert "kms" in health["services"]

    def test_factory_caching(self, tmp_path):
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        p1 = factory.get_secrets_provider()
        p2 = factory.get_secrets_provider()
        assert p1 is p2  # Same instance (cached)

    def test_factory_missing_config(self, tmp_path):
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(tmp_path / "nonexistent.yaml"))
        assert factory.global_provider == "local"

    def test_factory_aws_provider(self, tmp_path):
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: aws\n  region: us-gov-west-1\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        provider = factory.get_secrets_provider()
        assert provider.provider_name == "aws_secrets_manager"

    def test_factory_monitoring_local(self, tmp_path):
        """Factory exposes get_monitoring_provider returning local fallback."""
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        provider = factory.get_monitoring_provider()
        assert provider.provider_name == "local"

    def test_factory_iam_local(self, tmp_path):
        """Factory exposes get_iam_provider returning local fallback."""
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        provider = factory.get_iam_provider()
        assert provider.provider_name == "local"

    def test_factory_registry_local(self, tmp_path):
        """Factory exposes get_registry_provider returning local fallback."""
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        provider = factory.get_registry_provider()
        assert provider.provider_name == "local"

    def test_factory_health_check_all_six_services(self, tmp_path):
        """Health check includes all 6 service categories."""
        config = tmp_path / "cloud_config.yaml"
        config.write_text("cloud:\n  provider: local\n")
        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(config))
        health = factory.health_check()
        for svc in ("secrets", "storage", "kms", "monitoring", "iam", "registry"):
            assert svc in health["services"], f"Missing service: {svc}"


# ============================================================
# IBM Provider Imports (graceful degradation)
# ============================================================
class TestIBMProviderImports:
    """Verify all IBM provider classes are importable."""

    def test_import_ibm_secrets(self):
        from tools.cloud.secrets_provider import IBMSecretsProvider
        assert IBMSecretsProvider is not None

    def test_import_ibm_storage(self):
        from tools.cloud.storage_provider import IBMStorageProvider
        assert IBMStorageProvider is not None

    def test_import_ibm_kms(self):
        from tools.cloud.kms_provider import IBMKMSProvider
        assert IBMKMSProvider is not None

    def test_import_ibm_monitoring(self):
        from tools.cloud.monitoring_provider import IBMMonitoringProvider
        assert IBMMonitoringProvider is not None

    def test_import_ibm_iam(self):
        from tools.cloud.iam_provider import IBMIAMProvider
        assert IBMIAMProvider is not None

    def test_import_ibm_registry(self):
        from tools.cloud.registry_provider import IBMRegistryProvider
        assert IBMRegistryProvider is not None

    def test_import_ibm_watsonx(self):
        from tools.llm.ibm_watsonx_provider import IBMWatsonxProvider
        assert IBMWatsonxProvider is not None

    def test_ibm_providers_degrade_gracefully(self):
        """All IBM providers return False for availability without SDK."""
        from tools.cloud.secrets_provider import IBMSecretsProvider
        from tools.cloud.storage_provider import IBMStorageProvider
        from tools.cloud.kms_provider import IBMKMSProvider
        from tools.cloud.monitoring_provider import IBMMonitoringProvider
        from tools.cloud.iam_provider import IBMIAMProvider
        from tools.cloud.registry_provider import IBMRegistryProvider

        for cls in (IBMSecretsProvider, IBMStorageProvider, IBMKMSProvider,
                    IBMMonitoringProvider, IBMIAMProvider, IBMRegistryProvider):
            p = cls()
            assert p.check_availability() is False, (
                f"{cls.__name__} should not be available without IBM SDK"
            )
