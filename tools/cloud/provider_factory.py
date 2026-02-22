#!/usr/bin/env python3
# CUI // SP-CTI
"""CSP Provider Factory — config-driven cloud service resolution.

Reads args/cloud_config.yaml and provides the correct cloud service
implementation based on global CSP setting or per-service overrides (D225).

Pattern: tools/llm/router.py (config-driven, lazy instantiation, fallback).
ADRs: D223, D224, D225.
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

try:
    import yaml
except ImportError:
    yaml = None

from tools.cloud.secrets_provider import (
    SecretsProvider, AWSSecretsProvider, AzureSecretsProvider,
    GCPSecretsProvider, OCISecretsProvider, IBMSecretsProvider,
    LocalSecretsProvider,
)
from tools.cloud.storage_provider import (
    StorageProvider, AWSS3Provider, AzureBlobProvider,
    GCSProvider, OCIObjectStorageProvider, IBMStorageProvider,
    LocalStorageProvider,
)
from tools.cloud.kms_provider import (
    KMSProvider, AWSKMSProvider, AzureKMSProvider,
    GCPKMSProvider, OCIKMSProvider, IBMKMSProvider, LocalKMSProvider,
)
from tools.cloud.monitoring_provider import (
    MonitoringProvider, AWSCloudWatchProvider, AzureMonitorProvider,
    GCPMonitoringProvider, OCIMonitoringProvider, IBMMonitoringProvider,
    LocalMonitoringProvider,
)
from tools.cloud.iam_provider import (
    IAMProvider, AWSIAMProvider, AzureEntraIDProvider,
    GCPCloudIAMProvider, OCIIAMProvider, IBMIAMProvider,
    LocalIAMProvider,
)
from tools.cloud.registry_provider import (
    RegistryProvider, AWSECRProvider, AzureACRProvider,
    GCPArtifactRegistryProvider, OCIOCIRProvider, IBMRegistryProvider,
    LocalDockerProvider,
)

logger = logging.getLogger("icdev.cloud.factory")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "args" / "cloud_config.yaml"


def _expand_env(value):
    """Expand ${VAR:-default} patterns in string values."""
    if not isinstance(value, str):
        return value
    pattern = r'\$\{([^}]+)\}'
    def replacer(match):
        expr = match.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            return os.environ.get(var, default)
        return os.environ.get(expr, match.group(0))
    return re.sub(pattern, replacer, value)


class CSPProviderFactory:
    """Config-driven Cloud Service Provider factory.

    Resolves secrets, storage, KMS, monitoring, IAM, and registry
    providers for 6 CSPs (AWS, Azure, GCP, OCI, IBM, Local) based on
    args/cloud_config.yaml with per-service overrides (D225).
    """

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._config: Dict = {}
        self._providers: Dict = {}
        self._load_config()

    def _load_config(self):
        """Load cloud_config.yaml."""
        if yaml is None:
            logger.warning("PyYAML not available — using defaults (local)")
            self._config = {"cloud": {"provider": "local"}}
            return
        if not self._config_path.exists():
            logger.warning("Cloud config not found at %s — using local", self._config_path)
            self._config = {"cloud": {"provider": "local"}}
            return
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
            logger.info("Cloud config loaded: provider=%s", self.global_provider)
        except Exception as exc:
            logger.error("Failed to load cloud config: %s", exc)
            self._config = {"cloud": {"provider": "local"}}

    @property
    def global_provider(self) -> str:
        return self._config.get("cloud", {}).get("provider", "local")

    @property
    def region(self) -> str:
        return self._config.get("cloud", {}).get("region", "")

    @property
    def impact_level(self) -> str:
        return self._config.get("cloud", {}).get("impact_level", "IL5")

    @property
    def air_gapped(self) -> bool:
        return self._config.get("cloud", {}).get("air_gapped", False)

    @property
    def cloud_mode(self) -> str:
        """Cloud mode: commercial, government, on_prem, air_gapped (D232)."""
        return self._config.get("cloud", {}).get("cloud_mode", "government")

    def _resolve_csp_for_service(self, service: str) -> str:
        """Resolve CSP for a specific service.

        Per-service override (D225) > global provider > 'local'.
        """
        services = self._config.get("cloud", {}).get("services", {})
        override = _expand_env(services.get(service, ""))
        if override:
            return override
        return self.global_provider

    def get_secrets_provider(self) -> SecretsProvider:
        """Get secrets provider (cached)."""
        if "secrets" in self._providers:
            return self._providers["secrets"]

        csp = self._resolve_csp_for_service("secrets")
        cloud = self._config.get("cloud", {})
        provider: SecretsProvider

        if csp == "aws":
            region = cloud.get("region", "us-gov-west-1")
            provider = AWSSecretsProvider(region=region)
        elif csp == "azure":
            vault_url = _expand_env(cloud.get("azure", {}).get("vault_url", ""))
            provider = AzureSecretsProvider(vault_url=vault_url)
        elif csp == "gcp":
            project_id = _expand_env(cloud.get("gcp", {}).get("project_id", ""))
            provider = GCPSecretsProvider(project_id=project_id)
        elif csp == "oci":
            compartment = _expand_env(cloud.get("oci", {}).get("compartment_ocid", ""))
            vault = _expand_env(cloud.get("oci", {}).get("vault_ocid", ""))
            provider = OCISecretsProvider(compartment_id=compartment, vault_id=vault)
        elif csp == "ibm":
            ibm_cfg = cloud.get("ibm", {})
            api_key = _expand_env(ibm_cfg.get("api_key", ""))
            region = ibm_cfg.get("region", "us-south")
            instance_id = _expand_env(ibm_cfg.get("secrets_manager_id", ""))
            provider = IBMSecretsProvider(
                api_key=api_key, region=region, instance_id=instance_id,
            )
        else:
            provider = LocalSecretsProvider()

        self._providers["secrets"] = provider
        logger.debug("Secrets provider: %s (%s)", csp, provider.provider_name)
        return provider

    def get_storage_provider(self) -> StorageProvider:
        """Get storage provider (cached)."""
        if "storage" in self._providers:
            return self._providers["storage"]

        csp = self._resolve_csp_for_service("storage")
        cloud = self._config.get("cloud", {})
        provider: StorageProvider

        if csp == "aws":
            region = cloud.get("region", "us-gov-west-1")
            provider = AWSS3Provider(region=region)
        elif csp == "azure":
            url = _expand_env(cloud.get("azure", {}).get("storage_account_url", ""))
            provider = AzureBlobProvider(account_url=url)
        elif csp == "gcp":
            project_id = _expand_env(cloud.get("gcp", {}).get("project_id", ""))
            provider = GCSProvider(project_id=project_id)
        elif csp == "oci":
            ns = _expand_env(cloud.get("oci", {}).get("namespace", ""))
            comp = _expand_env(cloud.get("oci", {}).get("compartment_ocid", ""))
            provider = OCIObjectStorageProvider(namespace=ns, compartment_id=comp)
        elif csp == "ibm":
            ibm_cfg = cloud.get("ibm", {})
            api_key = _expand_env(ibm_cfg.get("api_key", ""))
            instance_id = _expand_env(ibm_cfg.get("cos_instance_id", ""))
            region = ibm_cfg.get("region", "us-south")
            provider = IBMStorageProvider(
                api_key=api_key, instance_id=instance_id, region=region,
            )
        else:
            provider = LocalStorageProvider()

        self._providers["storage"] = provider
        logger.debug("Storage provider: %s (%s)", csp, provider.provider_name)
        return provider

    def get_kms_provider(self) -> KMSProvider:
        """Get KMS provider (cached)."""
        if "kms" in self._providers:
            return self._providers["kms"]

        csp = self._resolve_csp_for_service("kms")
        cloud = self._config.get("cloud", {})
        provider: KMSProvider

        if csp == "aws":
            region = cloud.get("region", "us-gov-west-1")
            key_id = _expand_env(cloud.get("aws", {}).get("kms_key_id", ""))
            provider = AWSKMSProvider(region=region, key_id=key_id)
        elif csp == "azure":
            vault_url = _expand_env(cloud.get("azure", {}).get("vault_url", ""))
            provider = AzureKMSProvider(vault_url=vault_url)
        elif csp == "gcp":
            project_id = _expand_env(cloud.get("gcp", {}).get("project_id", ""))
            provider = GCPKMSProvider(project_id=project_id)
        elif csp == "oci":
            vault = _expand_env(cloud.get("oci", {}).get("vault_ocid", ""))
            provider = OCIKMSProvider(vault_id=vault)
        elif csp == "ibm":
            ibm_cfg = cloud.get("ibm", {})
            api_key = _expand_env(ibm_cfg.get("api_key", ""))
            instance_id = _expand_env(ibm_cfg.get("key_protect_instance_id", ""))
            region = ibm_cfg.get("region", "us-south")
            provider = IBMKMSProvider(
                api_key=api_key, instance_id=instance_id, region=region,
            )
        else:
            provider = LocalKMSProvider()

        self._providers["kms"] = provider
        logger.debug("KMS provider: %s (%s)", csp, provider.provider_name)
        return provider

    def get_monitoring_provider(self) -> MonitoringProvider:
        """Get monitoring provider (cached)."""
        if "monitoring" in self._providers:
            return self._providers["monitoring"]

        csp = self._resolve_csp_for_service("monitoring")
        cloud = self._config.get("cloud", {})
        provider: MonitoringProvider

        if csp == "aws":
            region = cloud.get("region", "us-gov-west-1")
            provider = AWSCloudWatchProvider(region=region)
        elif csp == "azure":
            provider = AzureMonitorProvider()
        elif csp == "gcp":
            project_id = _expand_env(cloud.get("gcp", {}).get("project_id", ""))
            provider = GCPMonitoringProvider(project_id=project_id)
        elif csp == "oci":
            compartment = _expand_env(cloud.get("oci", {}).get("compartment_ocid", ""))
            provider = OCIMonitoringProvider(compartment_id=compartment)
        elif csp == "ibm":
            ibm_cfg = cloud.get("ibm", {})
            api_key = _expand_env(ibm_cfg.get("api_key", ""))
            region = ibm_cfg.get("region", "us-south")
            provider = IBMMonitoringProvider(api_key=api_key, region=region)
        else:
            provider = LocalMonitoringProvider()

        self._providers["monitoring"] = provider
        logger.debug("Monitoring provider: %s (%s)", csp, provider.provider_name)
        return provider

    def get_iam_provider(self) -> IAMProvider:
        """Get IAM provider (cached)."""
        if "iam" in self._providers:
            return self._providers["iam"]

        csp = self._resolve_csp_for_service("iam")
        cloud = self._config.get("cloud", {})
        provider: IAMProvider

        if csp == "aws":
            region = cloud.get("region", "us-gov-west-1")
            provider = AWSIAMProvider(region=region)
        elif csp == "azure":
            provider = AzureEntraIDProvider()
        elif csp == "gcp":
            project_id = _expand_env(cloud.get("gcp", {}).get("project_id", ""))
            provider = GCPCloudIAMProvider(project_id=project_id)
        elif csp == "oci":
            compartment = _expand_env(cloud.get("oci", {}).get("compartment_ocid", ""))
            provider = OCIIAMProvider(compartment_id=compartment)
        elif csp == "ibm":
            ibm_cfg = cloud.get("ibm", {})
            api_key = _expand_env(ibm_cfg.get("api_key", ""))
            provider = IBMIAMProvider(api_key=api_key)
        else:
            provider = LocalIAMProvider()

        self._providers["iam"] = provider
        logger.debug("IAM provider: %s (%s)", csp, provider.provider_name)
        return provider

    def get_registry_provider(self) -> RegistryProvider:
        """Get container registry provider (cached)."""
        if "registry" in self._providers:
            return self._providers["registry"]

        csp = self._resolve_csp_for_service("registry")
        cloud = self._config.get("cloud", {})
        provider: RegistryProvider

        if csp == "aws":
            region = cloud.get("region", "us-gov-west-1")
            provider = AWSECRProvider(region=region)
        elif csp == "azure":
            provider = AzureACRProvider()
        elif csp == "gcp":
            project_id = _expand_env(cloud.get("gcp", {}).get("project_id", ""))
            provider = GCPArtifactRegistryProvider(project_id=project_id)
        elif csp == "oci":
            provider = OCIOCIRProvider()
        elif csp == "ibm":
            ibm_cfg = cloud.get("ibm", {})
            api_key = _expand_env(ibm_cfg.get("api_key", ""))
            region = ibm_cfg.get("region", "us-south")
            provider = IBMRegistryProvider(api_key=api_key, region=region)
        else:
            provider = LocalDockerProvider()

        self._providers["registry"] = provider
        logger.debug("Registry provider: %s (%s)", csp, provider.provider_name)
        return provider

    def health_check(self) -> Dict:
        """Check health of all configured cloud services."""
        results = {}
        for service_name, getter in [
            ("secrets", self.get_secrets_provider),
            ("storage", self.get_storage_provider),
            ("kms", self.get_kms_provider),
            ("monitoring", self.get_monitoring_provider),
            ("iam", self.get_iam_provider),
            ("registry", self.get_registry_provider),
        ]:
            try:
                provider = getter()
                available = provider.check_availability()
                results[service_name] = {
                    "provider": provider.provider_name,
                    "csp": self._resolve_csp_for_service(service_name),
                    "available": available,
                }
            except Exception as e:
                results[service_name] = {
                    "provider": "error",
                    "csp": self._resolve_csp_for_service(service_name),
                    "available": False,
                    "error": str(e),
                }
        return {
            "global_provider": self.global_provider,
            "region": self.region,
            "impact_level": self.impact_level,
            "air_gapped": self.air_gapped,
            "cloud_mode": self.cloud_mode,
            "services": results,
        }
