# CUI // SP-CTI
"""Base Selector — Stage 2 of Connector Forge pipeline (D-CF-1).

Deterministic protocol → base class mapping.
No LLM, no I/O. Input: ForgeApiManifest. Output: BaseClassSelection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from tools.databridge.forge.spec_parser import ForgeApiManifest


@dataclass
class BaseClassSelection:
    """Result of base class selection for a connector."""

    base_class: str  # e.g. 'SaaSBaseConnector'
    base_module: str  # e.g. 'tools.databridge.connectors.saas_base'
    template_name: str  # e.g. 'rest_connector'
    connector_type: str  # e.g. 'saas_api'
    rationale: str = ""
    confidence: float = 1.0  # 1.0 = deterministic, <1.0 = heuristic


# Protocol → (base_class, base_module, template_name, connector_type)
_PROTOCOL_MAP: dict[str, Tuple[str, str, str, str]] = {
    "rest": (
        "SaaSBaseConnector",
        "tools.databridge.connectors.saas_base",
        "rest_connector",
        "saas_api",
    ),
    "graphql": (
        "SaaSBaseConnector",
        "tools.databridge.connectors.saas_base",
        "graphql_connector",
        "saas_api",
    ),
    "soap": (
        "SoapBaseConnector",
        "tools.databridge.connectors.soap_base",
        "soap_connector",
        "soap",
    ),
    "sql": (
        "SQLBaseConnector",
        "tools.databridge.connectors.sql_base",
        "sql_connector",
        "database",
    ),
    "odbc": (
        "SQLBaseConnector",
        "tools.databridge.connectors.sql_base",
        "sql_connector",
        "database",
    ),
    "ftp": (
        "FsspecBaseConnector",
        "tools.databridge.connectors.fsspec_base",
        "ftp_connector",
        "cloud_storage",
    ),
    "sftp": (
        "FsspecBaseConnector",
        "tools.databridge.connectors.fsspec_base",
        "ftp_connector",
        "cloud_storage",
    ),
    "messaging": (
        "MessagingBaseConnector",
        "tools.databridge.connectors.messaging_base",
        "messaging_connector",
        "messaging",
    ),
    "hl7": (
        "HealthBaseConnector",
        "tools.databridge.connectors.health_base",
        "health_connector",
        "health",
    ),
    "fhir": (
        "HealthBaseConnector",
        "tools.databridge.connectors.health_base",
        "health_connector",
        "health",
    ),
    "tcp": (
        "DataConnector",
        "tools.databridge.connector",
        "tcp_connector",
        "on_prem",
    ),
}


def select_base_class(manifest: ForgeApiManifest) -> BaseClassSelection:
    """Select the appropriate base class for a connector manifest.

    Uses deterministic protocol mapping with heuristic fallback.
    """
    proto = manifest.protocol.lower()

    # Direct protocol match
    if proto in _PROTOCOL_MAP:
        base_class, base_module, template, conn_type = _PROTOCOL_MAP[proto]
        return BaseClassSelection(
            base_class=base_class,
            base_module=base_module,
            template_name=template,
            connector_type=conn_type,
            rationale=f"Protocol '{proto}' maps deterministically to {base_class}",
            confidence=1.0,
        )

    # Heuristic: WSDL/operations hint
    if manifest.wsdl_url or manifest.soap_operations:
        base_class, base_module, template, conn_type = _PROTOCOL_MAP["soap"]
        return BaseClassSelection(
            base_class=base_class,
            base_module=base_module,
            template_name=template,
            connector_type=conn_type,
            rationale="WSDL URL or SOAP operations detected — selecting SOAP",
            confidence=0.9,
        )

    # Heuristic: FHIR resources hint
    if manifest.fhir_resources:
        base_class, base_module, template, conn_type = _PROTOCOL_MAP["fhir"]
        return BaseClassSelection(
            base_class=base_class,
            base_module=base_module,
            template_name=template,
            connector_type=conn_type,
            rationale="FHIR resources detected — selecting Health",
            confidence=0.9,
        )

    # Default: REST/SaaS
    base_class, base_module, template, conn_type = _PROTOCOL_MAP["rest"]
    return BaseClassSelection(
        base_class=base_class,
        base_module=base_module,
        template_name=template,
        connector_type=conn_type,
        rationale=f"Unknown protocol '{proto}' — defaulting to REST/SaaS",
        confidence=0.6,
    )
