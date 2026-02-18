#!/usr/bin/env python3
# CUI // SP-CTI
"""PDP/PEP Configuration Generator — Policy Decision Point and Policy Enforcement Point configs for ZTA.

Generates PEP configurations (Istio AuthorizationPolicy, Linkerd ServerAuthorization) that point
to external Policy Decision Points. ICDEV does NOT implement PDP logic itself.

ADR D124: PDP modeled as external reference (Zscaler, Palo Alto, DISA ICAM, CrowdStrike) —
ICDEV generates PEP configs (Istio AuthorizationPolicy) but does NOT implement PDP itself.

Usage:
    python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --pdp-type disa_icam --json
    python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --pdp-type zscaler --json
    python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --mesh istio --pdp-type disa_icam --json
    python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --mesh linkerd --pdp-type crowdstrike --json
    python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --device-trust --mdm-type crowdstrike --json
    python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --device-trust --mdm-type microsoft_intune --json
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml

    def _to_yaml(data: dict) -> str:
        return yaml.dump(data, default_flow_style=False, sort_keys=False)

except ImportError:
    yaml = None

    def _to_yaml(data: dict) -> str:
        return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Config and DB helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load ZTA config from args/zta_config.yaml (reads pdp_references section)."""
    config_path = BASE_DIR / "args" / "zta_config.yaml"
    if yaml and config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    # Minimal fallback matching zta_config.yaml structure
    return {
        "pdp_references": [
            {
                "id": "disa_icam",
                "name": "DISA ICAM/IDAM",
                "type": "identity",
                "description": "DoD Identity, Credential, and Access Management",
                "integration": "SAML/OIDC federation to Istio/Linkerd",
            },
            {
                "id": "zscaler",
                "name": "Zscaler Private Access",
                "type": "network",
                "description": "Zero Trust Network Access (ZTNA)",
                "integration": "Connector deployment in K8s, policy push via API",
            },
            {
                "id": "palo_alto_prisma",
                "name": "Palo Alto Prisma Access",
                "type": "network",
                "description": "Cloud-delivered security platform",
                "integration": "GlobalProtect agent, Prisma Cloud Defender",
            },
            {
                "id": "crowdstrike",
                "name": "CrowdStrike Falcon",
                "type": "device",
                "description": "Endpoint detection and response (EDR)",
                "integration": "Falcon sensor DaemonSet, device posture API",
            },
            {
                "id": "microsoft_entra",
                "name": "Microsoft Entra ID (Azure AD)",
                "type": "identity",
                "description": "Cloud identity and access management",
                "integration": "OIDC/SAML federation, conditional access policies",
            },
            {
                "id": "custom",
                "name": "Customer-provided PDP",
                "type": "custom",
                "description": "Customer's existing policy decision point",
                "integration": "Customer provides integration spec",
            },
        ]
    }


def _get_db():
    """Get database connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_profile(project_id: str) -> dict:
    """Retrieve DevSecOps profile for a project."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM devsecops_profiles WHERE project_id = ?",
            (project_id,)
        ).fetchone()
        if not row:
            return {}
        return {
            "maturity_level": row["maturity_level"],
            "active_stages": json.loads(row["active_stages"] or "[]"),
            "stage_configs": json.loads(row["stage_configs"] or "{}"),
        }
    finally:
        conn.close()


def _get_project_info(project_id: str) -> dict:
    """Retrieve project metadata."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT name, classification, impact_level FROM projects WHERE id = ?",
            (project_id,)
        ).fetchone()
        if row:
            return dict(row)
        return {"name": "unknown", "classification": "CUI", "impact_level": "IL4"}
    finally:
        conn.close()


def _find_pdp_reference(config: dict, pdp_type: str) -> dict:
    """Look up a PDP reference entry from config by id."""
    for ref in config.get("pdp_references", []):
        if ref.get("id") == pdp_type:
            return ref
    return {}


# ---------------------------------------------------------------------------
# PDP reference generation (D124: ICDEV documents but does NOT implement PDP)
# ---------------------------------------------------------------------------

def generate_pdp_reference(project_id: str, pdp_type: str) -> dict:
    """Document external PDP integration point for a project.

    ICDEV does not implement PDP logic. This function generates documentation
    and integration configuration describing how the project's PEP will connect
    to the specified external PDP. The customer is responsible for deploying
    and operating the PDP.

    ADR D124: PDP is an external reference. ICDEV generates PEP configs only.

    Args:
        project_id: Project identifier.
        pdp_type: One of disa_icam, zscaler, palo_alto_prisma, crowdstrike,
                  microsoft_entra, custom.

    Returns:
        Dict with integration_config (connection details, endpoints),
        documentation, deployment_notes.
    """
    valid_types = [
        "disa_icam", "zscaler", "palo_alto_prisma",
        "crowdstrike", "microsoft_entra", "custom",
    ]
    if pdp_type not in valid_types:
        return {
            "error": f"Invalid pdp_type: {pdp_type}",
            "valid_types": valid_types,
        }

    config = _load_config()
    ref = _find_pdp_reference(config, pdp_type)
    project = _get_project_info(project_id)
    now = datetime.now(timezone.utc).isoformat()

    # Build type-specific integration config
    integration_config = _build_integration_config(pdp_type, project)

    # Build documentation
    documentation = _build_pdp_documentation(pdp_type, ref, project)

    # Build deployment notes
    deployment_notes = _build_deployment_notes(pdp_type, ref, project)

    return {
        "project_id": project_id,
        "pdp_type": pdp_type,
        "pdp_name": ref.get("name", pdp_type),
        "pdp_category": ref.get("type", "unknown"),
        "description": ref.get("description", ""),
        "integration_method": ref.get("integration", ""),
        "integration_config": integration_config,
        "documentation": documentation,
        "deployment_notes": deployment_notes,
        "adr_reference": "ADR D124: PDP is external — ICDEV generates PEP configs only",
        "generated_at": now,
        "status": "reference_documented",
    }


def _build_integration_config(pdp_type: str, project: dict) -> dict:
    """Build type-specific integration connection details."""
    impact_level = project.get("impact_level", "IL4")

    configs = {
        "disa_icam": {
            "protocol": "OIDC/SAML 2.0",
            "endpoints": {
                "authorization": "https://icam.mil/oauth2/authorize",
                "token": "https://icam.mil/oauth2/token",
                "jwks": "https://icam.mil/.well-known/jwks.json",
                "userinfo": "https://icam.mil/oauth2/userinfo",
            },
            "connection_details": {
                "auth_method": "CAC/PIV + OIDC federation",
                "mfa_required": True,
                "phishing_resistant_mfa": True,
                "federation_type": "SAML 2.0 / OIDC",
                "audience": f"icdev-{project.get('name', 'app')}.mil",
            },
            "k8s_integration": {
                "ext_authz_provider": "ext-authz-grpc",
                "grpc_service": "disa-icam-ext-authz.icam-system.svc.cluster.local:9001",
                "timeout_ms": 5000,
                "failure_mode": "DENY",
            },
            "nist_controls": ["IA-2", "IA-8", "AC-2", "AC-3"],
        },
        "zscaler": {
            "protocol": "ZTNA/HTTPS",
            "endpoints": {
                "cloud_portal": "https://admin.zscaler.net",
                "api_base": "https://zsapi.zscaler.net/api/v1",
                "connector_mgmt": "https://connector.zscaler.net",
            },
            "connection_details": {
                "auth_method": "Zscaler App Connector (K8s DaemonSet)",
                "tunnel_type": "Zscaler Tunnel 2.0 (ZT2)",
                "policy_enforcement": "Zscaler cloud — policies defined in ZPA admin portal",
                "app_segment": f"{project.get('name', 'app')}-{impact_level.lower()}",
            },
            "k8s_integration": {
                "connector_image": "zscaler/zpa-connector:latest",
                "deployment_type": "DaemonSet",
                "namespace": "zscaler-system",
                "secret_name": "zpa-connector-secret",
                "required_secret_keys": ["CONNECTOR_NAME", "PROVISIONING_KEY", "ZPA_CLOUD"],
            },
            "nist_controls": ["AC-3", "AC-4", "SC-7", "SC-8"],
        },
        "palo_alto_prisma": {
            "protocol": "ZTNA/IPSec/SSL-VPN",
            "endpoints": {
                "cloud_portal": "https://panorama.paloaltonetworks.com",
                "api_base": "https://api.prismaaccess.com/api",
                "cspm": "https://api2.prismacloud.io",
            },
            "connection_details": {
                "auth_method": "GlobalProtect Agent + Prisma Cloud Defender",
                "tunnel_type": "IPSec / SSL",
                "policy_enforcement": "Prisma Access cloud — NGFW policies in Panorama",
                "defender_mode": "DaemonSet (container runtime protection)",
            },
            "k8s_integration": {
                "defender_image": "paloaltonetworks/prisma-cloud-compute-defender:latest",
                "deployment_type": "DaemonSet",
                "namespace": "prisma-system",
                "secret_name": "prisma-defender-secret",
                "required_secret_keys": ["PRISMA_CLOUD_URL", "PRISMA_ACCESS_KEY", "PRISMA_SECRET_KEY"],
            },
            "nist_controls": ["AC-3", "AC-4", "SC-7", "SI-4"],
        },
        "crowdstrike": {
            "protocol": "REST API / Falcon Sensor",
            "endpoints": {
                "api_base": "https://api.crowdstrike.com",
                "device_posture": "https://api.crowdstrike.com/zero-trust-assessment/v1",
                "oauth2": "https://api.crowdstrike.com/oauth2/token",
            },
            "connection_details": {
                "auth_method": "CrowdStrike Falcon Sensor (DaemonSet) + OAuth2 API",
                "posture_endpoint": "/zero-trust-assessment/v1/assessments",
                "check_frequency_seconds": 30,
                "minimum_ztascore": 75,
            },
            "k8s_integration": {
                "sensor_image": "falcon-sensor/falcon-sensor:latest",
                "deployment_type": "DaemonSet",
                "namespace": "crowdstrike-system",
                "secret_name": "falcon-api-secret",
                "required_secret_keys": ["FALCON_CLIENT_ID", "FALCON_CLIENT_SECRET", "FALCON_CID"],
                "posture_sidecar": "falcon-sidecar-injector",
            },
            "nist_controls": ["CM-8", "IA-3", "SI-7", "SI-4"],
        },
        "microsoft_entra": {
            "protocol": "OIDC/OAuth2 / SAML 2.0",
            "endpoints": {
                "authorization": "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize",
                "token": "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                "jwks": "https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
                "userinfo": "https://graph.microsoft.com/oidc/userinfo",
            },
            "connection_details": {
                "auth_method": "OIDC/SAML federation with conditional access policies",
                "mfa_required": True,
                "phishing_resistant_mfa": True,
                "conditional_access": "Require compliant device + MFA + risk-based",
                "audience": f"api://{project.get('name', 'app')}-{impact_level.lower()}",
            },
            "k8s_integration": {
                "ext_authz_provider": "ext-authz-grpc",
                "grpc_service": "entra-ext-authz.entra-system.svc.cluster.local:9001",
                "timeout_ms": 5000,
                "failure_mode": "DENY",
            },
            "nist_controls": ["IA-2", "IA-8", "AC-2", "AC-3"],
        },
        "custom": {
            "protocol": "Customer-defined",
            "endpoints": {
                "ext_authz_grpc": "PLACEHOLDER: customer-pdp.pdp-system.svc.cluster.local:9001",
                "api_base": "PLACEHOLDER: https://pdp.customer.internal/api",
            },
            "connection_details": {
                "auth_method": "Customer-defined — update before deployment",
                "integration_spec": "Customer must provide gRPC ext_authz service implementation",
                "protocol_reference": "Envoy ext_authz v3 API (envoy.service.auth.v3.Authorization)",
            },
            "k8s_integration": {
                "ext_authz_provider": "ext-authz-grpc",
                "grpc_service": "PLACEHOLDER: customer-pdp.pdp-system.svc.cluster.local:9001",
                "timeout_ms": 5000,
                "failure_mode": "DENY",
            },
            "nist_controls": ["AC-3", "IA-2"],
        },
    }

    return configs.get(pdp_type, configs["custom"])


def _build_pdp_documentation(pdp_type: str, ref: dict, project: dict) -> dict:
    """Build human-readable documentation for the PDP integration."""
    docs = {
        "disa_icam": {
            "summary": "DISA ICAM (Identity, Credential, and Access Management) provides DoD-wide identity services. "
                       "ICDEV generates Istio/Linkerd PEP configurations that delegate authorization decisions "
                       "to the DISA ICAM ext_authz gRPC service. The DISA ICAM service evaluates OIDC tokens "
                       "and CAC/PIV certificates to grant or deny access.",
            "customer_responsibilities": [
                "Deploy DISA ICAM ext_authz gRPC sidecar or service in the cluster",
                "Configure OIDC relying party registration with DISA ICAM",
                "Provide CAC/PIV certificate authority trust anchors",
                "Define access policies in DISA ICAM admin console",
                "Maintain ICAM service availability (SLA per DoD policy)",
            ],
            "icdev_responsibilities": [
                "Generate Istio AuthorizationPolicy pointing to DISA ICAM ext_authz provider",
                "Generate PeerAuthentication for mTLS enforcement",
                "Configure service account bindings for PEP identity",
            ],
            "references": [
                "DoD Identity, Credential, and Access Management (ICAM) Reference Design",
                "NIST SP 800-63 (Digital Identity Guidelines)",
                "DoDI 8520.02 (PKI and PKE for DoD)",
            ],
        },
        "zscaler": {
            "summary": "Zscaler Private Access (ZPA) provides Zero Trust Network Access. "
                       "ICDEV generates K8s Connector DaemonSet manifests and PEP network policies "
                       "that route traffic through Zscaler's cloud enforcement points. "
                       "Policy decisions occur in the Zscaler cloud — not within the cluster.",
            "customer_responsibilities": [
                "Provision Zscaler ZPA tenant and configure app segments",
                "Generate provisioning key for App Connector",
                "Define access policies in ZPA admin portal",
                "Maintain Zscaler connector licensing",
                "Configure user identity integration (IdP federation to Zscaler)",
            ],
            "icdev_responsibilities": [
                "Generate App Connector DaemonSet K8s manifest",
                "Generate K8s Secret template for provisioning key",
                "Generate NetworkPolicy allowing Zscaler connector egress",
            ],
            "references": [
                "Zscaler Private Access Deployment Guide",
                "Zscaler Zero Trust Exchange Architecture",
                "NIST SP 800-207 (Zero Trust Architecture)",
            ],
        },
        "palo_alto_prisma": {
            "summary": "Palo Alto Prisma Access combines ZTNA and cloud-delivered NGFW. "
                       "Prisma Cloud Defender provides runtime container security. "
                       "ICDEV generates Defender DaemonSet manifests and PEP policies. "
                       "Policy enforcement occurs in Prisma Access cloud.",
            "customer_responsibilities": [
                "Provision Palo Alto Prisma Access tenant",
                "Configure application onboarding in Panorama",
                "Deploy and license Prisma Cloud Compute (Defender)",
                "Define security policies in Panorama/Prisma Cloud console",
                "Configure user identity integration (GlobalProtect + IdP)",
            ],
            "icdev_responsibilities": [
                "Generate Prisma Cloud Defender DaemonSet manifest",
                "Generate K8s Secret template for Prisma API credentials",
                "Generate Kyverno/OPA policies enforcing Prisma security baselines",
            ],
            "references": [
                "Palo Alto Prisma Access Deployment Guide",
                "Prisma Cloud Compute Administrator Guide",
                "NIST SP 800-207 (Zero Trust Architecture)",
            ],
        },
        "crowdstrike": {
            "summary": "CrowdStrike Falcon provides device trust and endpoint detection. "
                       "ICDEV generates Falcon Sensor DaemonSet manifests and device posture "
                       "check configurations. The Falcon API is called at admission time to "
                       "verify device ZTA score before granting access.",
            "customer_responsibilities": [
                "Provision CrowdStrike Falcon subscription with Zero Trust Assessment module",
                "Generate API credentials (Client ID + Secret) for posture checks",
                "Deploy Falcon sensor to endpoint devices (BYOD/GFE)",
                "Define device posture policies in Falcon console",
                "Maintain Falcon CID configuration in K8s secrets",
            ],
            "icdev_responsibilities": [
                "Generate Falcon Sensor DaemonSet manifest for K8s nodes",
                "Generate device posture ext_authz integration config",
                "Generate K8s Secret template for Falcon API credentials",
            ],
            "references": [
                "CrowdStrike Falcon Sensor Deployment Guide for Kubernetes",
                "CrowdStrike Zero Trust Assessment API Reference",
                "NIST SP 800-207 (Zero Trust Architecture)",
            ],
        },
        "microsoft_entra": {
            "summary": "Microsoft Entra ID (formerly Azure AD) provides cloud identity and conditional access. "
                       "ICDEV generates Istio/Linkerd PEP configurations that validate Entra ID JWT tokens "
                       "and enforce conditional access policies. Phishing-resistant MFA and device compliance "
                       "checks are enforced through Entra conditional access — not by ICDEV.",
            "customer_responsibilities": [
                "Register application in Microsoft Entra ID tenant",
                "Configure conditional access policies (MFA, device compliance, risk-based)",
                "Set up phishing-resistant authentication (FIDO2/Windows Hello)",
                "Provide tenant ID and application client ID/secret",
                "Configure Entra ext_authz gRPC adapter in the cluster",
            ],
            "icdev_responsibilities": [
                "Generate Istio AuthorizationPolicy pointing to Entra ext_authz provider",
                "Generate PeerAuthentication for mTLS enforcement",
                "Generate K8s Secret template for Entra application credentials",
            ],
            "references": [
                "Microsoft Entra ID Documentation",
                "Microsoft Zero Trust Deployment Guide",
                "NIST SP 800-63 (Digital Identity Guidelines)",
            ],
        },
        "custom": {
            "summary": "Customer-provided PDP integration. ICDEV generates PEP configurations "
                       "with placeholder endpoints that the customer must update. The PDP must "
                       "implement the Envoy ext_authz v3 gRPC API to integrate with Istio/Linkerd.",
            "customer_responsibilities": [
                "Implement or deploy a PDP that exposes Envoy ext_authz v3 gRPC API",
                "Update placeholder endpoint in generated Istio AuthorizationPolicy",
                "Define and maintain authorization policies in the PDP",
                "Ensure PDP high availability (SLA per organizational policy)",
                "Document PDP integration for ATO artifacts (SSP)",
            ],
            "icdev_responsibilities": [
                "Generate Istio AuthorizationPolicy template with placeholder PDP endpoint",
                "Generate Linkerd ServerAuthorization template with placeholder reference",
                "Provide integration checklist for customer PDP onboarding",
            ],
            "references": [
                "Envoy ext_authz v3 API Reference (envoy.service.auth.v3.Authorization)",
                "Istio External Authorization Documentation",
                "NIST SP 800-207 (Zero Trust Architecture)",
            ],
        },
    }

    return docs.get(pdp_type, docs["custom"])


def _build_deployment_notes(pdp_type: str, ref: dict, project: dict) -> list:
    """Build ordered deployment notes for the PDP integration."""
    impact_level = project.get("impact_level", "IL4")

    common_notes = [
        f"CLASSIFICATION: All PDP integration credentials must be stored in AWS Secrets Manager "
        f"(not K8s ConfigMaps or plaintext files) — required for {impact_level}.",
        "AUDIT: PDP authorization decisions must be forwarded to SIEM (Splunk/ELK) per NIST AU-2/AU-12.",
        "FAILSAFE: Configure failure_mode=DENY on all ext_authz providers — never ALLOW on PDP unavailability.",
        "mTLS: Ensure Istio/Linkerd mTLS is STRICT before enabling ext_authz — prevent bypass via non-mesh traffic.",
    ]

    type_notes = {
        "disa_icam": [
            "Step 1: Submit DoD ICAM relying party registration request (est. 2-4 weeks lead time).",
            "Step 2: Obtain DISA ext_authz gRPC service endpoint and client certificate from DISA ICAM team.",
            "Step 3: Deploy DISA ICAM ext_authz adapter into cluster (DISA-provided container image).",
            "Step 4: Apply generated Istio AuthorizationPolicy — verify ext_authz provider name matches adapter.",
            "Step 5: Test with CAC/PIV + OIDC token end-to-end before ATO submission.",
        ],
        "zscaler": [
            "Step 1: Work with Zscaler account team to provision ZPA tenant and app segments.",
            "Step 2: Generate App Connector provisioning key from ZPA admin portal.",
            "Step 3: Store provisioning key in AWS Secrets Manager, reference in K8s ExternalSecret.",
            "Step 4: Apply generated App Connector DaemonSet manifest to cluster.",
            "Step 5: Verify connector registration in ZPA portal before enabling user access.",
            "Step 6: Configure user access policies in ZPA — map to project app segment.",
        ],
        "palo_alto_prisma": [
            "Step 1: Provision Prisma Access tenant and configure app onboarding in Panorama.",
            "Step 2: Generate Prisma Cloud API access key from Prisma Cloud console.",
            "Step 3: Store API credentials in AWS Secrets Manager, reference in K8s ExternalSecret.",
            "Step 4: Apply generated Prisma Defender DaemonSet manifest to cluster.",
            "Step 5: Verify Defender registration in Prisma Cloud console.",
            "Step 6: Configure runtime defense policies in Prisma Cloud for this project.",
        ],
        "crowdstrike": [
            "Step 1: Confirm Zero Trust Assessment (ZTA) module is included in Falcon subscription.",
            "Step 2: Generate API client credentials (Client ID + Secret) with ZTA read scope.",
            "Step 3: Store Falcon credentials in AWS Secrets Manager, reference in K8s ExternalSecret.",
            "Step 4: Apply generated Falcon Sensor DaemonSet manifest to cluster nodes.",
            "Step 5: Verify sensor enrollment in Falcon console — check ZTA score availability.",
            "Step 6: Configure minimum ZTA score threshold (recommended: 75) in integration config.",
        ],
        "microsoft_entra": [
            "Step 1: Register application in Microsoft Entra ID tenant (App Registration).",
            "Step 2: Configure conditional access policy: require MFA + device compliance + risk-based.",
            "Step 3: Generate client secret or certificate for ext_authz adapter.",
            "Step 4: Store Entra credentials in AWS Secrets Manager, reference in K8s ExternalSecret.",
            "Step 5: Deploy Entra ext_authz gRPC adapter into cluster.",
            "Step 6: Apply generated Istio AuthorizationPolicy — update tenant_id placeholder.",
        ],
        "custom": [
            "Step 1: Implement or procure a PDP that exposes Envoy ext_authz v3 gRPC API.",
            "Step 2: Deploy PDP into cluster or as an external service reachable from the mesh.",
            "Step 3: Update PLACEHOLDER endpoint in generated AuthorizationPolicy YAML.",
            "Step 4: Test ext_authz integration — verify DENY on invalid credentials.",
            "Step 5: Document PDP implementation in SSP AC-3 and IA-2 control responses.",
        ],
    }

    return type_notes.get(pdp_type, type_notes["custom"]) + common_notes


# ---------------------------------------------------------------------------
# PEP config generation (Istio and Linkerd)
# ---------------------------------------------------------------------------

def generate_pep_config(project_id: str, mesh: str = "istio", pdp_type: str = "disa_icam") -> dict:
    """Generate PEP (Policy Enforcement Point) configurations for the service mesh.

    For Istio: generates AuthorizationPolicy YAML pointing to external authz provider.
    For Linkerd: generates ServerAuthorization with external policy reference.

    The PEP enforces decisions made by the external PDP — ICDEV does not implement
    the PDP itself (ADR D124).

    Args:
        project_id: Project identifier.
        mesh: Service mesh type — 'istio' or 'linkerd'.
        pdp_type: External PDP to reference.

    Returns:
        Dict with yaml_content, pep_type, integration_notes.
    """
    valid_meshes = ["istio", "linkerd"]
    if mesh not in valid_meshes:
        return {
            "error": f"Invalid mesh: {mesh}",
            "valid_meshes": valid_meshes,
        }

    valid_pdp_types = [
        "disa_icam", "zscaler", "palo_alto_prisma",
        "crowdstrike", "microsoft_entra", "custom",
    ]
    if pdp_type not in valid_pdp_types:
        return {
            "error": f"Invalid pdp_type: {pdp_type}",
            "valid_types": valid_pdp_types,
        }

    project = _get_project_info(project_id)
    now = datetime.now(timezone.utc).isoformat()

    if mesh == "istio":
        result = _generate_istio_pep(project_id, pdp_type, project)
    else:
        result = _generate_linkerd_pep(project_id, pdp_type, project)

    result["project_id"] = project_id
    result["mesh"] = mesh
    result["pdp_type"] = pdp_type
    result["generated_at"] = now
    result["adr_reference"] = "ADR D124: PEP generated by ICDEV; PDP is external"

    return result


def _generate_istio_pep(project_id: str, pdp_type: str, project: dict) -> dict:
    """Generate Istio AuthorizationPolicy for external PDP."""
    project.get("name", "app")
    namespace = f"icdev-{project_id[:8]}"

    # Determine ext_authz provider name based on PDP type
    provider_map = {
        "disa_icam": "ext-authz-disa-icam",
        "zscaler": "ext-authz-zscaler",
        "palo_alto_prisma": "ext-authz-prisma",
        "crowdstrike": "ext-authz-crowdstrike",
        "microsoft_entra": "ext-authz-entra",
        "custom": "ext-authz-grpc",
    }
    provider_name = provider_map.get(pdp_type, "ext-authz-grpc")

    # AuthorizationPolicy (ext_authz CUSTOM action)
    authz_policy = {
        "apiVersion": "security.istio.io/v1beta1",
        "kind": "AuthorizationPolicy",
        "metadata": {
            "name": f"ext-authz-pdp-{pdp_type.replace('_', '-')}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "icdev",
                "icdev.mil/project-id": project_id,
                "icdev.mil/classification": project.get("classification", "CUI"),
                "icdev.mil/pdp-type": pdp_type,
            },
            "annotations": {
                "icdev.mil/adr": "D124 — PDP is external; this policy is the PEP",
                "icdev.mil/generated-at": datetime.now(timezone.utc).isoformat(),
            },
        },
        "spec": {
            "action": "CUSTOM",
            "provider": {
                "name": provider_name,
            },
            "rules": [
                {
                    "to": [
                        {
                            "operation": {
                                "paths": ["/*"],
                            }
                        }
                    ]
                }
            ],
        },
    }

    # PeerAuthentication (enforce mTLS STRICT — required before ext_authz)
    peer_auth = {
        "apiVersion": "security.istio.io/v1beta1",
        "kind": "PeerAuthentication",
        "metadata": {
            "name": f"mtls-strict-{project_id[:8]}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "icdev",
                "icdev.mil/project-id": project_id,
            },
        },
        "spec": {
            "mtls": {
                "mode": "STRICT",
            }
        },
    }

    # Mesh config extension (ext_authz provider registration in MeshConfig)
    # This goes in istio-system ConfigMap — shown as a reference snippet
    ext_authz_provider_snippet = {
        "# Add to istio MeshConfig extensionProviders": None,
        "extensionProviders": [
            {
                "name": provider_name,
                "envoyExtAuthzGrpc": {
                    "service": _get_grpc_service(pdp_type),
                    "port": "9001",
                    "timeout": "5s",
                    "failOpen": False,
                },
            }
        ],
    }

    yaml_docs = [
        f"# CUI // SP-CTI\n# ADR D124: PEP config — delegates to external PDP: {pdp_type}\n---\n{_to_yaml(peer_auth)}",
        f"# CUI // SP-CTI\n# Istio AuthorizationPolicy (CUSTOM ext_authz action)\n---\n{_to_yaml(authz_policy)}",
        f"# CUI // SP-CTI\n# MeshConfig extensionProviders snippet (add to istio-system/istio ConfigMap)\n# ---\n# {json.dumps(ext_authz_provider_snippet, indent=2).replace(chr(10), chr(10) + '# ')}",
    ]

    integration_notes = [
        f"Provider '{provider_name}' must be registered in Istio MeshConfig.extensionProviders before applying this policy.",
        "PeerAuthentication STRICT mode must be applied before AuthorizationPolicy to prevent plaintext bypass.",
        f"The ext_authz gRPC service endpoint is: {_get_grpc_service(pdp_type)}",
        "failOpen is set to FALSE — traffic is denied if the PDP is unreachable (ZTA requirement).",
        "Apply PeerAuthentication first, verify mTLS health, then apply AuthorizationPolicy.",
    ]

    return {
        "pep_type": "istio_authorization_policy",
        "yaml_content": "\n".join(yaml_docs),
        "policies_generated": [
            f"PeerAuthentication: mtls-strict-{project_id[:8]}",
            f"AuthorizationPolicy: ext-authz-pdp-{pdp_type.replace('_', '-')}",
        ],
        "meshconfig_snippet_included": True,
        "integration_notes": integration_notes,
    }


def _generate_linkerd_pep(project_id: str, pdp_type: str, project: dict) -> dict:
    """Generate Linkerd ServerAuthorization with external policy reference."""
    project_name = project.get("name", "app")
    namespace = f"icdev-{project_id[:8]}"

    # Linkerd Server CRD — defines the protected server
    server = {
        "apiVersion": "policy.linkerd.io/v1beta1",
        "kind": "Server",
        "metadata": {
            "name": f"{project_name}-server",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "icdev",
                "icdev.mil/project-id": project_id,
                "icdev.mil/classification": project.get("classification", "CUI"),
            },
        },
        "spec": {
            "podSelector": {
                "matchLabels": {
                    "app.kubernetes.io/part-of": project_name,
                }
            },
            "port": 8080,
            "proxyProtocol": "HTTP/2",
        },
    }

    # ServerAuthorization — allows traffic only from authorized clients
    # In Linkerd, external authz requires a custom Auth Policy extension
    server_authz = {
        "apiVersion": "policy.linkerd.io/v1beta2",
        "kind": "ServerAuthorization",
        "metadata": {
            "name": f"ext-policy-{pdp_type.replace('_', '-')}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "icdev",
                "icdev.mil/project-id": project_id,
                "icdev.mil/pdp-type": pdp_type,
            },
            "annotations": {
                "icdev.mil/adr": "D124 — PDP is external; this is the PEP config",
                "icdev.mil/pdp-reference": _get_grpc_service(pdp_type),
                "icdev.mil/generated-at": datetime.now(timezone.utc).isoformat(),
            },
        },
        "spec": {
            "server": {
                "name": f"{project_name}-server",
            },
            "client": {
                "meshTLS": {
                    "serviceAccounts": [
                        {
                            "name": f"{project_name}-client-sa",
                            "namespace": namespace,
                        }
                    ]
                }
            },
        },
    }

    # Linkerd AuthPolicy (external policy — Linkerd 2.13+ AuthPolicy CRD)
    auth_policy = {
        "apiVersion": "policy.linkerd.io/v1alpha1",
        "kind": "AuthorizationPolicy",
        "metadata": {
            "name": f"ext-authz-{pdp_type.replace('_', '-')}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "icdev",
                "icdev.mil/project-id": project_id,
            },
            "annotations": {
                "icdev.mil/adr": "D124 — external PDP reference",
            },
        },
        "spec": {
            "targetRef": {
                "group": "policy.linkerd.io",
                "kind": "Server",
                "name": f"{project_name}-server",
            },
            "requiredAuthenticationRefs": [
                {
                    "group": "policy.linkerd.io",
                    "kind": "MeshTLSAuthentication",
                    "name": f"mesh-tls-auth-{project_id[:8]}",
                }
            ],
        },
    }

    # MeshTLSAuthentication — require mesh identity
    mesh_tls_auth = {
        "apiVersion": "policy.linkerd.io/v1alpha1",
        "kind": "MeshTLSAuthentication",
        "metadata": {
            "name": f"mesh-tls-auth-{project_id[:8]}",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "icdev",
            },
        },
        "spec": {
            "identities": [f"*.{namespace}.serviceaccount.identity.linkerd.cluster.local"],
        },
    }

    yaml_docs = [
        f"# CUI // SP-CTI\n# ADR D124: Linkerd PEP config — external PDP reference: {pdp_type}\n---\n{_to_yaml(server)}",
        f"# CUI // SP-CTI\n---\n{_to_yaml(mesh_tls_auth)}",
        f"# CUI // SP-CTI\n---\n{_to_yaml(auth_policy)}",
        f"# CUI // SP-CTI\n---\n{_to_yaml(server_authz)}",
    ]

    integration_notes = [
        f"Linkerd's native policy enforces mTLS identity — external PDP ({pdp_type}) handles authorization decisions.",
        f"External PDP gRPC endpoint: {_get_grpc_service(pdp_type)} — must be deployed before applying policies.",
        "Linkerd AuthorizationPolicy (v1alpha1) requires Linkerd 2.13+ with policy extension enabled.",
        "MeshTLSAuthentication enforces mutual TLS between all services — non-mesh traffic is denied by default.",
        "For full ext_authz support in Linkerd, consider deploying an Envoy proxy as a policy sidecar.",
    ]

    return {
        "pep_type": "linkerd_server_authorization",
        "yaml_content": "\n".join(yaml_docs),
        "policies_generated": [
            f"Server: {project_name}-server",
            f"MeshTLSAuthentication: mesh-tls-auth-{project_id[:8]}",
            f"AuthorizationPolicy: ext-authz-{pdp_type.replace('_', '-')}",
            f"ServerAuthorization: ext-policy-{pdp_type.replace('_', '-')}",
        ],
        "integration_notes": integration_notes,
    }


def _get_grpc_service(pdp_type: str) -> str:
    """Return the expected gRPC service address for a PDP type."""
    services = {
        "disa_icam": "disa-icam-ext-authz.icam-system.svc.cluster.local:9001",
        "zscaler": "zscaler-ext-authz.zscaler-system.svc.cluster.local:9001",
        "palo_alto_prisma": "prisma-ext-authz.prisma-system.svc.cluster.local:9001",
        "crowdstrike": "falcon-ext-authz.crowdstrike-system.svc.cluster.local:9001",
        "microsoft_entra": "entra-ext-authz.entra-system.svc.cluster.local:9001",
        "custom": "PLACEHOLDER: customer-pdp.pdp-system.svc.cluster.local:9001",
    }
    return services.get(pdp_type, services["custom"])


# ---------------------------------------------------------------------------
# Device trust config generation
# ---------------------------------------------------------------------------

def generate_device_trust_config(project_id: str, mdm_type: str = "crowdstrike") -> dict:
    """Generate device posture checking integration config.

    Documents how device trust is enforced via an external MDM/EDR solution.
    ICDEV generates the K8s manifests and configuration references — the
    actual device posture decisions are made by the external MDM/EDR service.

    Args:
        project_id: Project identifier.
        mdm_type: MDM/EDR solution — crowdstrike, microsoft_intune, jamf, custom.

    Returns:
        Dict with config describing device trust integration points.
    """
    valid_mdm_types = ["crowdstrike", "microsoft_intune", "jamf", "custom"]
    if mdm_type not in valid_mdm_types:
        return {
            "error": f"Invalid mdm_type: {mdm_type}",
            "valid_types": valid_mdm_types,
        }

    project = _get_project_info(project_id)
    now = datetime.now(timezone.utc).isoformat()

    # Build MDM-specific config
    mdm_config = _build_mdm_config(mdm_type, project)

    # Build K8s manifests for device trust enforcement
    k8s_manifests = _build_device_trust_manifests(project_id, mdm_type, project)

    # Build posture check policy
    posture_policy = _build_posture_policy(mdm_type, project)

    return {
        "project_id": project_id,
        "mdm_type": mdm_type,
        "mdm_name": _get_mdm_name(mdm_type),
        "device_trust_pillar": "ZTA Pillar: Device (weight 0.15)",
        "nist_controls": ["CM-8", "IA-3", "SC-17", "SI-7"],
        "mdm_config": mdm_config,
        "k8s_manifests": k8s_manifests,
        "posture_policy": posture_policy,
        "enforcement_model": (
            "Device posture decisions are made by the external MDM/EDR. "
            "ICDEV generates K8s admission webhook configs and PEP policies "
            "that query the MDM API at admission time."
        ),
        "adr_reference": "ADR D124: device trust PDP is external — ICDEV generates PEP integration configs",
        "generated_at": now,
    }


def _get_mdm_name(mdm_type: str) -> str:
    """Return display name for MDM type."""
    names = {
        "crowdstrike": "CrowdStrike Falcon Zero Trust Assessment",
        "microsoft_intune": "Microsoft Intune (Endpoint Manager)",
        "jamf": "JAMF Pro (macOS/iOS MDM)",
        "custom": "Customer-provided MDM/EDR",
    }
    return names.get(mdm_type, mdm_type)


def _build_mdm_config(mdm_type: str, project: dict) -> dict:
    """Build MDM-specific configuration."""
    impact_level = project.get("impact_level", "IL4")

    configs = {
        "crowdstrike": {
            "product": "CrowdStrike Falcon",
            "modules_required": ["Falcon Prevent (AV)", "Falcon Insight (EDR)", "Zero Trust Assessment (ZTA)"],
            "api_integration": {
                "base_url": "https://api.crowdstrike.com",
                "zta_endpoint": "/zero-trust-assessment/v1/assessments",
                "oauth2_token": "/oauth2/token",
                "required_scopes": ["zero-trust-assessment:read"],
            },
            "posture_check": {
                "field": "assessment.overall",
                "minimum_score": 75,
                "check_interval_seconds": 30,
                "cache_ttl_seconds": 60,
            },
            "k8s_deployment": {
                "sensor_daemonset": True,
                "namespace": "crowdstrike-system",
                "image": "falcon-sensor/falcon-sensor:latest",
                "secret_name": "falcon-credentials",
                "secret_keys": ["FALCON_CLIENT_ID", "FALCON_CLIENT_SECRET", "FALCON_CID"],
            },
            "node_coverage": f"All {impact_level} worker nodes must have Falcon sensor installed",
        },
        "microsoft_intune": {
            "product": "Microsoft Intune (Endpoint Manager)",
            "modules_required": ["Intune Device Compliance", "Conditional Access"],
            "api_integration": {
                "base_url": "https://graph.microsoft.com/v1.0",
                "device_compliance_endpoint": "/deviceManagement/managedDevices",
                "oauth2_token": "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                "required_scopes": ["DeviceManagementManagedDevices.Read.All"],
            },
            "posture_check": {
                "field": "complianceState",
                "required_value": "compliant",
                "check_interval_seconds": 60,
                "cache_ttl_seconds": 120,
            },
            "k8s_deployment": {
                "sensor_daemonset": False,
                "admission_webhook": True,
                "namespace": "intune-system",
                "secret_name": "intune-credentials",
                "secret_keys": ["TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"],
            },
            "node_coverage": f"All {impact_level} workloads require device compliance token in JWT",
        },
        "jamf": {
            "product": "JAMF Pro",
            "modules_required": ["JAMF Pro MDM", "JAMF Connect (optional, for OIDC)"],
            "api_integration": {
                "base_url": "https://{jamf_instance}.jamfcloud.com/api/v1",
                "device_check_endpoint": "/computers/{device_id}",
                "oauth2_token": "https://{jamf_instance}.jamfcloud.com/api/oauth/token",
                "required_scopes": ["Read Computers"],
            },
            "posture_check": {
                "field": "managementStatus.enrolled",
                "required_value": True,
                "additional_checks": ["extensionAttributes.patch_compliance", "extensionAttributes.disk_encrypted"],
                "check_interval_seconds": 60,
                "cache_ttl_seconds": 120,
            },
            "k8s_deployment": {
                "sensor_daemonset": False,
                "admission_webhook": True,
                "namespace": "jamf-system",
                "secret_name": "jamf-credentials",
                "secret_keys": ["JAMF_INSTANCE_URL", "JAMF_CLIENT_ID", "JAMF_CLIENT_SECRET"],
            },
            "node_coverage": f"All {impact_level} macOS/iOS devices must be JAMF-enrolled",
        },
        "custom": {
            "product": "Customer-provided MDM/EDR",
            "modules_required": ["Customer-defined — document in SSP CM-8 control response"],
            "api_integration": {
                "base_url": "PLACEHOLDER: https://mdm.customer.internal/api",
                "device_check_endpoint": "PLACEHOLDER: /v1/devices/{device_id}/posture",
                "auth_method": "PLACEHOLDER: Bearer token / API key / mTLS client cert",
            },
            "posture_check": {
                "field": "PLACEHOLDER: posture.compliant",
                "required_value": True,
                "check_interval_seconds": 60,
                "cache_ttl_seconds": 120,
            },
            "k8s_deployment": {
                "sensor_daemonset": False,
                "admission_webhook": True,
                "namespace": "mdm-system",
                "secret_name": "mdm-credentials",
                "secret_keys": ["MDM_API_URL", "MDM_API_KEY"],
            },
            "node_coverage": "PLACEHOLDER: Document MDM coverage requirements in SSP",
        },
    }

    return configs.get(mdm_type, configs["custom"])


def _build_device_trust_manifests(project_id: str, mdm_type: str, project: dict) -> dict:
    """Build K8s manifests for device trust enforcement."""
    namespace = f"icdev-{project_id[:8]}"

    # Kyverno policy: deny requests without device trust header/annotation
    kyverno_device_policy = {
        "apiVersion": "kyverno.io/v1",
        "kind": "ClusterPolicy",
        "metadata": {
            "name": f"device-trust-{mdm_type.replace('_', '-')}",
            "annotations": {
                "policies.kyverno.io/title": "Device Trust Enforcement",
                "policies.kyverno.io/category": "ZTA/Device",
                "policies.kyverno.io/severity": "high",
                "policies.kyverno.io/description": (
                    f"Require device trust annotation from {mdm_type} before allowing pod scheduling"
                ),
                "icdev.mil/adr": "D124 — device trust PDP is external",
            },
            "labels": {
                "app.kubernetes.io/managed-by": "icdev",
                "icdev.mil/project-id": project_id,
            },
        },
        "spec": {
            "validationFailureAction": "Enforce",
            "background": False,
            "rules": [
                {
                    "name": "require-device-trust-annotation",
                    "match": {"any": [{"resources": {"kinds": ["Pod"], "namespaces": [namespace]}}]},
                    "validate": {
                        "message": (
                            f"Pod must have device trust annotation from {mdm_type} "
                            f"(icdev.mil/device-trust-verified: 'true')"
                        ),
                        "pattern": {
                            "metadata": {
                                "annotations": {
                                    "icdev.mil/device-trust-verified": "true",
                                    "icdev.mil/device-trust-source": f"{mdm_type}",
                                }
                            }
                        },
                    },
                }
            ],
        },
    }

    # Secret template (values must be populated from AWS Secrets Manager)
    secret_template = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": _build_mdm_config(mdm_type, project).get("k8s_deployment", {}).get("secret_name", "mdm-secret"),
            "namespace": _build_mdm_config(mdm_type, project).get("k8s_deployment", {}).get("namespace", "mdm-system"),
            "annotations": {
                "icdev.mil/secret-source": "aws-secrets-manager",
                "icdev.mil/do-not-commit": "true — populate from AWS Secrets Manager ExternalSecret",
            },
        },
        "type": "Opaque",
        "stringData": {
            k: "PLACEHOLDER — retrieve from AWS Secrets Manager"
            for k in _build_mdm_config(mdm_type, project).get("k8s_deployment", {}).get("secret_keys", [])
        },
    }

    yaml_docs = [
        f"# CUI // SP-CTI\n# ADR D124: Device trust PEP policy — external MDM: {mdm_type}\n---\n{_to_yaml(kyverno_device_policy)}",
        f"# CUI // SP-CTI\n# Secret template — populate from AWS Secrets Manager (do NOT commit values)\n---\n{_to_yaml(secret_template)}",
    ]

    return {
        "yaml_content": "\n".join(yaml_docs),
        "manifests_generated": [
            f"ClusterPolicy: device-trust-{mdm_type.replace('_', '-')}",
            f"Secret template: {secret_template['metadata']['name']}",
        ],
    }


def _build_posture_policy(mdm_type: str, project: dict) -> dict:
    """Build device posture evaluation policy."""
    return {
        "enforcement_point": "K8s admission webhook + Istio ext_authz",
        "evaluation_trigger": "Every new pod admission + periodic re-evaluation (JWT expiry)",
        "check_sequence": [
            "1. Extract device identifier from client certificate CN or JWT claim",
            f"2. Query {_get_mdm_name(mdm_type)} API for device posture",
            "3. Evaluate posture against minimum compliance threshold",
            "4. Annotate pod with device trust result (icdev.mil/device-trust-verified)",
            "5. Allow or deny based on Kyverno policy",
        ],
        "failure_behavior": "DENY — device posture failures result in access denial (ZTA: deny by default)",
        "audit_logging": "All device posture decisions logged to SIEM per NIST AU-2/AU-12",
        "cache_policy": (
            "Device posture results cached per device ID with TTL — "
            "balance between security (short TTL) and PDP load (longer TTL)"
        ),
        "nist_800_53_evidence": {
            "CM-8": "Device inventory maintained in MDM",
            "IA-3": "Device authentication via certificate/sensor enrollment",
            "SC-17": "PKI certificates used for device identity",
            "SI-7": "Software integrity verified by EDR sensor",
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="PDP/PEP Configuration Generator for ZTA (ADR D124)"
    )
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument(
        "--pdp-type",
        choices=["disa_icam", "zscaler", "palo_alto_prisma", "crowdstrike", "microsoft_entra", "custom"],
        default="disa_icam",
        help="External PDP type",
    )
    parser.add_argument(
        "--mesh",
        choices=["istio", "linkerd"],
        default="istio",
        help="Service mesh for PEP config generation",
    )
    parser.add_argument(
        "--device-trust",
        action="store_true",
        help="Generate device trust config instead of PDP/PEP config",
    )
    parser.add_argument(
        "--mdm-type",
        choices=["crowdstrike", "microsoft_intune", "jamf", "custom"],
        default="crowdstrike",
        help="MDM/EDR type for device trust config",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.device_trust:
        result = generate_device_trust_config(args.project_id, mdm_type=args.mdm_type)
    elif hasattr(args, "pdp_type") and not hasattr(args, "mesh"):
        result = generate_pdp_reference(args.project_id, pdp_type=args.pdp_type)
    else:
        # Default: generate both PDP reference doc and PEP config
        pdp_ref = generate_pdp_reference(args.project_id, pdp_type=args.pdp_type)
        pep_cfg = generate_pep_config(args.project_id, mesh=args.mesh, pdp_type=args.pdp_type)
        result = {
            "project_id": args.project_id,
            "pdp_reference": pdp_ref,
            "pep_config": pep_cfg,
        }

    if args.json or not args.human:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}")
            return

        if args.device_trust:
            print(f"Project:    {result['project_id']}")
            print(f"MDM Type:   {result['mdm_name']}")
            print(f"Pillar:     {result['device_trust_pillar']}")
            print(f"Controls:   {', '.join(result['nist_controls'])}")
            print(f"Manifests:  {', '.join(result['k8s_manifests'].get('manifests_generated', []))}")
            print(f"ADR:        {result['adr_reference']}")
        else:
            pdp = result.get("pdp_reference", result)
            pep = result.get("pep_config", {})
            print(f"Project:    {result.get('project_id', args.project_id)}")
            if pdp:
                print(f"PDP Type:   {pdp.get('pdp_name', args.pdp_type)}")
                print(f"Category:   {pdp.get('pdp_category', 'N/A')}")
                print(f"Status:     {pdp.get('status', 'N/A')}")
            if pep:
                print(f"PEP Type:   {pep.get('pep_type', 'N/A')}")
                print(f"Mesh:       {pep.get('mesh', args.mesh)}")
                for policy in pep.get("policies_generated", []):
                    print(f"  Policy:   {policy}")
                for note in pep.get("integration_notes", [])[:3]:
                    print(f"  Note:     {note}")
            print("ADR:        ADR D124 — PDP is external; ICDEV generates PEP configs only")


if __name__ == "__main__":
    main()
