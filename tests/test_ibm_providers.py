#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for IBM Cloud provider implementations (D237).

Tests IBM providers across all 6 cloud service categories.
All tests work without IBM SDKs installed (graceful degradation).
"""

import os
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


class TestIBMSecretsProvider:
    """Test IBMSecretsProvider class."""

    def test_import(self):
        from tools.cloud.secrets_provider import IBMSecretsProvider
        assert IBMSecretsProvider is not None

    def test_provider_name(self):
        from tools.cloud.secrets_provider import IBMSecretsProvider
        p = IBMSecretsProvider()
        assert p.provider_name == "ibm_secrets_manager"

    def test_check_availability_no_sdk(self):
        from tools.cloud.secrets_provider import IBMSecretsProvider
        p = IBMSecretsProvider()
        # Without IBM SDK and credentials, should return False
        assert p.check_availability() is False

    def test_get_secret_no_sdk(self):
        from tools.cloud.secrets_provider import IBMSecretsProvider
        p = IBMSecretsProvider()
        assert p.get_secret("test") is None

    def test_list_secrets_no_sdk(self):
        from tools.cloud.secrets_provider import IBMSecretsProvider
        p = IBMSecretsProvider()
        assert p.list_secrets() == []


class TestIBMStorageProvider:
    """Test IBMStorageProvider class."""

    def test_import(self):
        from tools.cloud.storage_provider import IBMStorageProvider
        assert IBMStorageProvider is not None

    def test_provider_name(self):
        from tools.cloud.storage_provider import IBMStorageProvider
        p = IBMStorageProvider()
        assert p.provider_name == "ibm_cos"

    def test_check_availability_no_sdk(self):
        from tools.cloud.storage_provider import IBMStorageProvider
        p = IBMStorageProvider()
        assert p.check_availability() is False

    def test_upload_no_sdk(self):
        from tools.cloud.storage_provider import IBMStorageProvider
        p = IBMStorageProvider()
        assert p.upload("bucket", "key", b"data") is False

    def test_download_no_sdk(self):
        from tools.cloud.storage_provider import IBMStorageProvider
        p = IBMStorageProvider()
        assert p.download("bucket", "key") is None


class TestIBMKMSProvider:
    """Test IBMKMSProvider class."""

    def test_import(self):
        from tools.cloud.kms_provider import IBMKMSProvider
        assert IBMKMSProvider is not None

    def test_provider_name(self):
        from tools.cloud.kms_provider import IBMKMSProvider
        p = IBMKMSProvider()
        assert p.provider_name == "ibm_key_protect"

    def test_check_availability_no_sdk(self):
        from tools.cloud.kms_provider import IBMKMSProvider
        p = IBMKMSProvider()
        assert p.check_availability() is False

    def test_encrypt_no_sdk(self):
        from tools.cloud.kms_provider import IBMKMSProvider
        p = IBMKMSProvider()
        assert p.encrypt(b"data") is None


class TestIBMMonitoringProvider:
    """Test IBMMonitoringProvider class."""

    def test_import(self):
        from tools.cloud.monitoring_provider import IBMMonitoringProvider
        assert IBMMonitoringProvider is not None

    def test_provider_name(self):
        from tools.cloud.monitoring_provider import IBMMonitoringProvider
        p = IBMMonitoringProvider()
        assert p.provider_name == "ibm_cloud_monitoring"

    def test_check_availability_no_creds(self):
        from tools.cloud.monitoring_provider import IBMMonitoringProvider
        p = IBMMonitoringProvider()
        assert p.check_availability() is False

    def test_query_metrics_stub(self):
        from tools.cloud.monitoring_provider import IBMMonitoringProvider
        p = IBMMonitoringProvider()
        assert p.query_metrics("test", "cpu_usage") == []


class TestIBMIAMProvider:
    """Test IBMIAMProvider class."""

    def test_import(self):
        from tools.cloud.iam_provider import IBMIAMProvider
        assert IBMIAMProvider is not None

    def test_provider_name(self):
        from tools.cloud.iam_provider import IBMIAMProvider
        p = IBMIAMProvider()
        assert p.provider_name == "ibm_iam"

    def test_check_availability_no_sdk(self):
        from tools.cloud.iam_provider import IBMIAMProvider
        p = IBMIAMProvider()
        assert p.check_availability() is False

    def test_list_accounts_no_sdk(self):
        from tools.cloud.iam_provider import IBMIAMProvider
        p = IBMIAMProvider()
        assert p.list_service_accounts() == []


class TestIBMRegistryProvider:
    """Test IBMRegistryProvider class."""

    def test_import(self):
        from tools.cloud.registry_provider import IBMRegistryProvider
        assert IBMRegistryProvider is not None

    def test_provider_name(self):
        from tools.cloud.registry_provider import IBMRegistryProvider
        p = IBMRegistryProvider()
        assert p.provider_name == "ibm_container_registry"

    def test_check_availability_no_creds(self):
        from tools.cloud.registry_provider import IBMRegistryProvider
        p = IBMRegistryProvider()
        assert p.check_availability() is False

    def test_login_command(self):
        from tools.cloud.registry_provider import IBMRegistryProvider
        p = IBMRegistryProvider()
        assert p.get_login_command() == "ibmcloud cr login"

    def test_list_repos_no_creds(self):
        from tools.cloud.registry_provider import IBMRegistryProvider
        p = IBMRegistryProvider()
        assert p.list_repositories() == []


class TestIBMWatsonxProvider:
    """Test IBMWatsonxProvider class."""

    def test_import(self):
        from tools.llm.ibm_watsonx_provider import IBMWatsonxProvider
        assert IBMWatsonxProvider is not None

    def test_provider_name(self):
        from tools.llm.ibm_watsonx_provider import IBMWatsonxProvider
        p = IBMWatsonxProvider()
        assert p.provider_name == "ibm_watsonx"

    def test_check_availability_no_sdk(self):
        from tools.llm.ibm_watsonx_provider import IBMWatsonxProvider
        p = IBMWatsonxProvider()
        assert p.check_availability() is False

    def test_messages_to_prompt(self):
        from tools.llm.ibm_watsonx_provider import IBMWatsonxProvider
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        prompt = IBMWatsonxProvider._messages_to_prompt(messages)
        assert "<|system|>" in prompt
        assert "You are helpful." in prompt
        assert "<|user|>" in prompt
        assert "Hello" in prompt
        assert prompt.endswith("<|assistant|>\n")

    def test_messages_to_prompt_empty(self):
        from tools.llm.ibm_watsonx_provider import IBMWatsonxProvider
        assert IBMWatsonxProvider._messages_to_prompt([]) == ""


class TestProviderFactoryIBM:
    """Test CSPProviderFactory IBM resolution."""

    def test_factory_ibm_secrets(self, tmp_path):
        """Test factory resolves IBM secrets provider."""
        import yaml
        config = {
            "cloud": {
                "provider": "ibm",
                "ibm": {
                    "api_key": "test-key",
                    "region": "us-south",
                    "secrets_manager_id": "sm-id",
                },
                "services": {},
            }
        }
        cfg_file = tmp_path / "cloud_config.yaml"
        cfg_file.write_text(yaml.dump(config))

        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(cfg_file))
        provider = factory.get_secrets_provider()
        assert provider.provider_name == "ibm_secrets_manager"

    def test_factory_ibm_storage(self, tmp_path):
        """Test factory resolves IBM storage provider."""
        import yaml
        config = {
            "cloud": {
                "provider": "ibm",
                "ibm": {
                    "api_key": "test-key",
                    "region": "us-south",
                    "cos_instance_id": "cos-id",
                },
                "services": {},
            }
        }
        cfg_file = tmp_path / "cloud_config.yaml"
        cfg_file.write_text(yaml.dump(config))

        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(cfg_file))
        provider = factory.get_storage_provider()
        assert provider.provider_name == "ibm_cos"

    def test_factory_ibm_kms(self, tmp_path):
        """Test factory resolves IBM KMS provider."""
        import yaml
        config = {
            "cloud": {
                "provider": "ibm",
                "ibm": {
                    "api_key": "test-key",
                    "region": "us-south",
                    "key_protect_instance_id": "kp-id",
                },
                "services": {},
            }
        }
        cfg_file = tmp_path / "cloud_config.yaml"
        cfg_file.write_text(yaml.dump(config))

        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(cfg_file))
        provider = factory.get_kms_provider()
        assert provider.provider_name == "ibm_key_protect"

    def test_factory_cloud_mode(self, tmp_path):
        """Test factory exposes cloud_mode property."""
        import yaml
        config = {
            "cloud": {
                "provider": "local",
                "cloud_mode": "air_gapped",
                "services": {},
            }
        }
        cfg_file = tmp_path / "cloud_config.yaml"
        cfg_file.write_text(yaml.dump(config))

        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(cfg_file))
        assert factory.cloud_mode == "air_gapped"

    def test_factory_monitoring_provider(self, tmp_path):
        """Test factory has get_monitoring_provider method."""
        import yaml
        config = {"cloud": {"provider": "local", "services": {}}}
        cfg_file = tmp_path / "cloud_config.yaml"
        cfg_file.write_text(yaml.dump(config))

        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(cfg_file))
        provider = factory.get_monitoring_provider()
        assert provider.provider_name == "local"

    def test_factory_iam_provider(self, tmp_path):
        """Test factory has get_iam_provider method."""
        import yaml
        config = {"cloud": {"provider": "local", "services": {}}}
        cfg_file = tmp_path / "cloud_config.yaml"
        cfg_file.write_text(yaml.dump(config))

        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(cfg_file))
        provider = factory.get_iam_provider()
        assert provider.provider_name == "local"

    def test_factory_registry_provider(self, tmp_path):
        """Test factory has get_registry_provider method."""
        import yaml
        config = {"cloud": {"provider": "local", "services": {}}}
        cfg_file = tmp_path / "cloud_config.yaml"
        cfg_file.write_text(yaml.dump(config))

        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(cfg_file))
        provider = factory.get_registry_provider()
        assert provider.provider_name == "local"

    def test_factory_health_check_all_services(self, tmp_path):
        """Test health check covers all 6 services."""
        import yaml
        config = {"cloud": {"provider": "local", "services": {}}}
        cfg_file = tmp_path / "cloud_config.yaml"
        cfg_file.write_text(yaml.dump(config))

        from tools.cloud.provider_factory import CSPProviderFactory
        factory = CSPProviderFactory(config_path=str(cfg_file))
        result = factory.health_check()
        assert "cloud_mode" in result
        services = result["services"]
        assert "secrets" in services
        assert "storage" in services
        assert "kms" in services
        assert "monitoring" in services
        assert "iam" in services
        assert "registry" in services
