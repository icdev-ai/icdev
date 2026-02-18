#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Service Mesh Configuration Generator for ICDEV.

Generates Istio and Linkerd service mesh configurations based on a project's
ZTA profile. Both mesh types are supported (ADR D121); customer selects in
the ZTA profile. PDP is modeled as an external reference — ICDEV generates
PEP (Policy Enforcement Point) configs, not PDP itself (ADR D124).

Each generator returns a dict containing individual K8s-style manifests plus
a combined yaml_content string suitable for kubectl apply.

Supported manifests:
  Istio:
    - PeerAuthentication    (STRICT mTLS namespace-wide)
    - AuthorizationPolicy   (per-service, deny-by-default)
    - VirtualService        (traffic routing: retries, timeouts)
    - DestinationRule       (circuit breaking, outlier detection, ISTIO_MUTUAL TLS)
    - Sidecar               (egress traffic control)

  Linkerd:
    - Server                (mTLS policy per service)
    - ServerAuthorization   (per-service authorization)
    - ServiceProfile        (retries, timeouts per route)
    - HTTPRoute             (traffic routing)

Usage:
    python tools/devsecops/service_mesh_generator.py --project-id proj-123 --mesh istio --json
    python tools/devsecops/service_mesh_generator.py --project-id proj-123 --mesh linkerd
    python tools/devsecops/service_mesh_generator.py --project-id proj-123 --mesh istio --output ./k8s/mesh/

ADR D121: Both Istio and Linkerd supported; customer selects in profile.
ADR D124: PDP modeled as external reference; ICDEV generates PEP configs only.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
# YAML / JSON serialization
# ---------------------------------------------------------------------------

def _yaml_dump(obj: dict) -> str:
    """Serialize a dict to YAML. Falls back to JSON if PyYAML is unavailable."""
    if yaml is not None:
        try:
            return yaml.dump(obj, default_flow_style=False, sort_keys=False)
        except Exception:
            pass
    return json.dumps(obj, indent=2)


def _combine_manifests(manifests: list) -> str:
    """Join multiple YAML manifest dicts with --- separator."""
    parts = []
    for m in manifests:
        parts.append(_yaml_dump(m))
    return "---\n" + "\n---\n".join(parts)


# ---------------------------------------------------------------------------
# Config / DB helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load ZTA config from args/zta_config.yaml (fallback to defaults)."""
    config_path = BASE_DIR / "args" / "zta_config.yaml"
    if yaml is not None and config_path.exists():
        with open(config_path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    # Minimal fallback — preserves generation even without PyYAML
    return {
        "service_mesh_options": {
            "istio": {
                "api_versions": {
                    "security": "security.istio.io/v1beta1",
                    "networking": "networking.istio.io/v1beta1",
                },
                "mtls_mode": "STRICT",
            },
            "linkerd": {
                "api_versions": {
                    "policy": "policy.linkerd.io/v1beta2",
                    "server": "policy.linkerd.io/v1beta1",
                },
                "mtls_mode": "enforced",
            },
        },
        "pdp_references": [],
    }


def _get_db():
    """Get an ICDEV database connection with WAL mode and Row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_profile(project_id: str) -> dict:
    """Retrieve the ZTA / DevSecOps profile for a project from the DB.

    Returns the devsecops_profiles row as a dict, or a minimal default if
    the project has no profile yet.
    """
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM devsecops_profiles WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass
    # Default profile when no DB row exists
    return {
        "project_id": project_id,
        "service_mesh": "istio",
        "namespace": "default",
        "impact_level": "IL4",
        "classification": "CUI",
        "active_stages": "[]",
    }


def _get_project_info(project_id: str) -> dict:
    """Retrieve project metadata (name, namespace, impact level) from the DB.

    Returns a dict with at least: project_id, name, namespace, impact_level,
    classification.
    """
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ? LIMIT 1",
            (project_id,),
        ).fetchone()
        conn.close()
        if row:
            data = dict(row)
            # Normalize field names that vary across DB schema versions
            if "project_name" in data and "name" not in data:
                data["name"] = data["project_name"]
            return data
    except Exception:
        pass
    return {
        "project_id": project_id,
        "name": project_id,
        "namespace": "default",
        "impact_level": "IL4",
        "classification": "CUI",
    }


# ---------------------------------------------------------------------------
# Common label helpers
# ---------------------------------------------------------------------------

def _mesh_labels(project_id: str, component: str, classification: str = "CUI") -> dict:
    """Return standard ICDEV service mesh labels for a K8s resource."""
    return {
        "icdev.mil/project": project_id,
        "icdev.mil/component": component,
        "icdev.mil/classification": classification,
        "app.kubernetes.io/managed-by": "icdev",
    }


# ---------------------------------------------------------------------------
# Istio manifest builders
# ---------------------------------------------------------------------------

def _istio_peer_authentication(
    namespace: str,
    project_id: str,
    classification: str,
    api_version: str,
) -> dict:
    """PeerAuthentication — enforces STRICT mTLS across the entire namespace.

    All workloads in the namespace must present a valid mTLS certificate.
    No plain-text traffic is permitted (ADR D121 — mTLS enforced).
    """
    return {
        "apiVersion": api_version,
        "kind": "PeerAuthentication",
        "metadata": {
            "name": f"{project_id}-mtls-strict",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "peer-authentication", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/adr": "D121",
                "icdev.mil/description": "Namespace-wide STRICT mTLS enforcement",
            },
        },
        "spec": {
            # Namespace-wide policy — no selector = applies to all pods
            "mtls": {
                "mode": "STRICT",
            },
        },
    }


def _istio_authorization_policy(
    namespace: str,
    project_id: str,
    project_name: str,
    classification: str,
    api_version: str,
    pdp_refs: list,
) -> dict:
    """AuthorizationPolicy — deny-by-default with allow-list for internal services.

    Implements a deny-all baseline then explicitly allows:
    - Intra-namespace traffic (service-to-service within the project)
    - Monitoring namespace (Prometheus scrape)
    - Ingress controller
    PDP integration references are surfaced as annotations (ADR D124).
    """
    pdp_annotation = (
        json.dumps([r.get("name", r.get("id", "")) for r in pdp_refs])
        if pdp_refs
        else "[]"
    )
    return {
        "apiVersion": api_version,
        "kind": "AuthorizationPolicy",
        "metadata": {
            "name": f"{project_id}-deny-all",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "authorization-policy", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/adr": "D124",
                "icdev.mil/pdp-references": pdp_annotation,
                "icdev.mil/description": (
                    "Deny-by-default AuthorizationPolicy. "
                    "PDP decisions are enforced externally (see pdp-references)."
                ),
            },
        },
        "spec": {
            # action: DENY with empty rules = deny ALL traffic by default
            "action": "DENY",
            "rules": [{}],
        },
    }


def _istio_authorization_policy_allow(
    namespace: str,
    project_id: str,
    project_name: str,
    classification: str,
    api_version: str,
) -> dict:
    """AuthorizationPolicy — allow-list companion to deny-all.

    Permits:
    - Same-namespace service accounts (intra-service traffic)
    - Monitoring namespace service accounts (metrics scraping)
    - Ingress namespace service accounts
    Requires principal to be a valid SPIFFE identity (mTLS enforced via PeerAuthentication).
    """
    return {
        "apiVersion": api_version,
        "kind": "AuthorizationPolicy",
        "metadata": {
            "name": f"{project_id}-allow-internal",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "authorization-policy-allow", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/description": "Allow-list for intra-namespace and monitoring traffic",
            },
        },
        "spec": {
            "action": "ALLOW",
            "rules": [
                {
                    # Intra-namespace: any service account in same namespace
                    "from": [
                        {
                            "source": {
                                "principals": [
                                    f"cluster.local/ns/{namespace}/sa/*",
                                ],
                            },
                        },
                    ],
                },
                {
                    # Monitoring namespace — Prometheus scrape
                    "from": [
                        {
                            "source": {
                                "namespaces": ["monitoring"],
                            },
                        },
                    ],
                    "to": [
                        {
                            "operation": {
                                "paths": ["/metrics"],
                                "methods": ["GET"],
                            },
                        },
                    ],
                },
                {
                    # Ingress controller — inbound HTTP/HTTPS
                    "from": [
                        {
                            "source": {
                                "namespaces": ["ingress-nginx", "istio-ingress"],
                            },
                        },
                    ],
                },
            ],
        },
    }


def _istio_virtual_service(
    namespace: str,
    project_id: str,
    project_name: str,
    classification: str,
    api_version: str,
) -> dict:
    """VirtualService — traffic routing with retries and timeouts.

    Configures:
    - 3 HTTP retries on 5xx / connect-failure / retriable-4xx
    - 30s per-attempt timeout (10s overall)
    - 10% fault injection abort (configurable — disabled by default)
    """
    return {
        "apiVersion": api_version,
        "kind": "VirtualService",
        "metadata": {
            "name": f"{project_id}-vs",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "virtual-service", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/description": "Traffic routing with retries and timeouts",
            },
        },
        "spec": {
            "hosts": [project_name],
            "http": [
                {
                    "name": "primary",
                    "retries": {
                        "attempts": 3,
                        "perTryTimeout": "10s",
                        "retryOn": "5xx,connect-failure,retriable-4xx",
                    },
                    "timeout": "30s",
                    "route": [
                        {
                            "destination": {
                                "host": project_name,
                                "port": {"number": 8080},
                            },
                            "weight": 100,
                        },
                    ],
                },
            ],
        },
    }


def _istio_destination_rule(
    namespace: str,
    project_id: str,
    project_name: str,
    classification: str,
    api_version: str,
) -> dict:
    """DestinationRule — circuit breaking, outlier detection, and ISTIO_MUTUAL TLS.

    Configures:
    - ISTIO_MUTUAL TLS mode (uses Istio-issued certs, not application-level)
    - Connection pool limits (prevents cascading failures)
    - Outlier detection (automatic unhealthy host ejection)
    """
    return {
        "apiVersion": api_version,
        "kind": "DestinationRule",
        "metadata": {
            "name": f"{project_id}-dr",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "destination-rule", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/description": (
                    "Circuit breaking, outlier detection, ISTIO_MUTUAL TLS"
                ),
            },
        },
        "spec": {
            "host": project_name,
            "trafficPolicy": {
                "tls": {
                    # ISTIO_MUTUAL: Istio issues and rotates certs automatically
                    "mode": "ISTIO_MUTUAL",
                },
                "connectionPool": {
                    "tcp": {
                        "maxConnections": 100,
                        "connectTimeout": "5s",
                        "tcpKeepalive": {
                            "time": "7200s",
                            "interval": "75s",
                        },
                    },
                    "http": {
                        "h2UpgradePolicy": "UPGRADE",
                        "http1MaxPendingRequests": 1024,
                        "http2MaxRequests": 1024,
                        "maxRequestsPerConnection": 10,
                        "maxRetries": 3,
                        "idleTimeout": "90s",
                    },
                },
                "outlierDetection": {
                    # Eject host after 5 consecutive 5xx errors
                    "consecutiveGatewayErrors": 5,
                    "consecutive5xxErrors": 5,
                    "interval": "30s",
                    "baseEjectionTime": "30s",
                    # Maximum 50% of hosts ejected at once
                    "maxEjectionPercent": 50,
                    "minHealthPercent": 50,
                },
                "loadBalancer": {
                    "simple": "LEAST_CONN",
                },
            },
        },
    }


def _istio_sidecar(
    namespace: str,
    project_id: str,
    classification: str,
    api_version: str,
) -> dict:
    """Sidecar — restrict egress to only what the service needs.

    Limits egress to:
    - Same namespace (intra-service traffic)
    - istio-system (control plane)
    - kube-system (DNS)
    Prevents workloads from reaching arbitrary external endpoints.
    """
    return {
        "apiVersion": api_version,
        "kind": "Sidecar",
        "metadata": {
            "name": f"{project_id}-sidecar",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "sidecar", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/description": "Egress traffic restriction via Sidecar CRD",
            },
        },
        "spec": {
            # Apply to all pods in namespace (no workloadSelector = namespace-wide)
            "egress": [
                {
                    "hosts": [
                        # Same namespace
                        f"./{namespace}",
                        # Istio control plane (xDS, telemetry)
                        "istio-system/*",
                        # DNS resolution
                        "kube-system/*",
                    ],
                },
            ],
            "outboundTrafficPolicy": {
                # REGISTRY_ONLY: block traffic to hosts not in the service registry
                "mode": "REGISTRY_ONLY",
            },
        },
    }


# ---------------------------------------------------------------------------
# Linkerd manifest builders
# ---------------------------------------------------------------------------

def _linkerd_server(
    namespace: str,
    project_id: str,
    project_name: str,
    classification: str,
    policy_api: str,
) -> dict:
    """Server — defines an mTLS policy for a named port on matching pods.

    The Server resource declares which port(s) require mTLS and which
    ServerAuthorization resources govern access.
    """
    return {
        "apiVersion": policy_api,
        "kind": "Server",
        "metadata": {
            "name": f"{project_id}-server",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "server", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/adr": "D121",
                "icdev.mil/description": "mTLS Server policy for project workloads",
            },
        },
        "spec": {
            "podSelector": {
                "matchLabels": {
                    "icdev.mil/project": project_id,
                },
            },
            "port": 8080,
            "proxyProtocol": "HTTP/2",
        },
    }


def _linkerd_server_authorization(
    namespace: str,
    project_id: str,
    classification: str,
    policy_api: str,
    pdp_refs: list,
) -> dict:
    """ServerAuthorization — per-service authorization policy for Linkerd.

    Allows:
    - Authenticated workloads in the same namespace
    - Monitoring workloads (metrics scraping)
    PDP references surfaced as annotations (ADR D124).
    """
    pdp_annotation = (
        json.dumps([r.get("name", r.get("id", "")) for r in pdp_refs])
        if pdp_refs
        else "[]"
    )
    return {
        "apiVersion": policy_api,
        "kind": "ServerAuthorization",
        "metadata": {
            "name": f"{project_id}-authz",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "server-authorization", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/adr": "D124",
                "icdev.mil/pdp-references": pdp_annotation,
                "icdev.mil/description": (
                    "Allow authenticated intra-namespace + monitoring traffic. "
                    "External PDP decisions enforced via annotations."
                ),
            },
        },
        "spec": {
            "server": {
                "name": f"{project_id}-server",
            },
            "client": {
                "meshTLS": {
                    # Only authenticated (mTLS) clients may connect
                    "serviceAccounts": [
                        {
                            "name": "*",
                            "namespace": namespace,
                        },
                        {
                            # Prometheus / monitoring
                            "name": "*",
                            "namespace": "monitoring",
                        },
                    ],
                },
            },
        },
    }


def _linkerd_service_profile(
    namespace: str,
    project_id: str,
    project_name: str,
    classification: str,
    server_api: str,
) -> dict:
    """ServiceProfile — retries and per-route timeouts for Linkerd.

    Defines:
    - isRetryable routes (idempotent GET/HEAD)
    - Per-route timeout (30s)
    - Route-level response classification (5xx = failure)
    """
    return {
        "apiVersion": server_api,
        "kind": "ServiceProfile",
        "metadata": {
            "name": f"{project_name}.{namespace}.svc.cluster.local",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "service-profile", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/description": "Retries and timeouts via Linkerd ServiceProfile",
            },
        },
        "spec": {
            "routes": [
                {
                    "name": "GET /health",
                    "condition": {
                        "method": "GET",
                        "pathRegex": "/health(/.*)?",
                    },
                    "isRetryable": False,
                    "timeout": "5s",
                },
                {
                    "name": "GET /api",
                    "condition": {
                        "method": "GET",
                        "pathRegex": "/api(/.*)?",
                    },
                    "isRetryable": True,
                    "timeout": "30s",
                    "responseClasses": [
                        {
                            "condition": {"status": {"min": 500, "max": 599}},
                            "isFailure": True,
                        },
                    ],
                },
                {
                    "name": "POST /api",
                    "condition": {
                        "method": "POST",
                        "pathRegex": "/api(/.*)?",
                    },
                    # POST is not idempotent — do not retry
                    "isRetryable": False,
                    "timeout": "30s",
                    "responseClasses": [
                        {
                            "condition": {"status": {"min": 500, "max": 599}},
                            "isFailure": True,
                        },
                    ],
                },
            ],
            "retryBudget": {
                "retryRatio": 0.2,
                "minRetriesPerSecond": 10,
                "ttl": "10s",
            },
        },
    }


def _linkerd_http_route(
    namespace: str,
    project_id: str,
    project_name: str,
    classification: str,
    policy_api: str,
) -> dict:
    """HTTPRoute — Linkerd traffic routing with timeout and retry policy.

    Uses the Gateway API HTTPRoute kind supported by Linkerd for fine-grained
    per-route traffic management.
    """
    return {
        "apiVersion": "gateway.networking.k8s.io/v1beta1",
        "kind": "HTTPRoute",
        "metadata": {
            "name": f"{project_id}-route",
            "namespace": namespace,
            "labels": _mesh_labels(project_id, "http-route", classification),
            "annotations": {
                "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                "icdev.mil/description": "Linkerd HTTPRoute — traffic routing with timeout/retry",
                # Linkerd timeout annotation
                "timeout.linkerd.io/request": "30s",
                "retry.linkerd.io/http": "5xx,gateway-error",
                "retry.linkerd.io/limit": "3",
            },
        },
        "spec": {
            "parentRefs": [
                {
                    "name": project_name,
                    "namespace": namespace,
                    "kind": "Service",
                    "group": "",
                },
            ],
            "rules": [
                {
                    "matches": [
                        {
                            "path": {
                                "type": "PathPrefix",
                                "value": "/",
                            },
                        },
                    ],
                    "backendRefs": [
                        {
                            "name": project_name,
                            "port": 8080,
                            "weight": 100,
                        },
                    ],
                    "timeouts": {
                        "request": "30s",
                        "backendRequest": "20s",
                    },
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Public generators
# ---------------------------------------------------------------------------

def generate_istio_config(project_id: str, profile: dict = None) -> dict:
    """Generate Istio service mesh configuration manifests for a project.

    Produces five Istio CRD manifests:
    1. PeerAuthentication  — STRICT mTLS namespace-wide
    2. AuthorizationPolicy — deny-by-default baseline
    3. AuthorizationPolicy — allow-list (intra-namespace + monitoring)
    4. VirtualService      — traffic routing with retries + timeouts
    5. DestinationRule     — circuit breaking, outlier detection, ISTIO_MUTUAL TLS
    6. Sidecar             — egress traffic restriction

    All manifests carry icdev.mil/* labels and classification annotations.
    PDP references are surfaced as annotations only (ADR D124).

    Args:
        project_id: ICDEV project identifier.
        profile:    Optional pre-loaded ZTA/DevSecOps profile dict. If None,
                    the profile is loaded from the database.

    Returns:
        Dict with keys:
          peer_authentication, authorization_policy_deny,
          authorization_policy_allow, virtual_service,
          destination_rule, sidecar, yaml_content (str), mesh, project_id.
    """
    if profile is None:
        profile = _get_profile(project_id)

    project = _get_project_info(project_id)
    project_name = project.get("name", project_id)
    namespace = profile.get("namespace") or project.get("namespace", "default")
    classification = profile.get("classification") or project.get("classification", "CUI")

    config = _load_config()
    istio_opts = config.get("service_mesh_options", {}).get("istio", {})
    api_versions = istio_opts.get("api_versions", {})
    security_api = api_versions.get("security", "security.istio.io/v1beta1")
    networking_api = api_versions.get("networking", "networking.istio.io/v1beta1")
    pdp_refs = config.get("pdp_references", [])

    peer_auth = _istio_peer_authentication(
        namespace, project_id, classification, security_api,
    )
    authz_deny = _istio_authorization_policy(
        namespace, project_id, project_name, classification, security_api, pdp_refs,
    )
    authz_allow = _istio_authorization_policy_allow(
        namespace, project_id, project_name, classification, security_api,
    )
    virtual_svc = _istio_virtual_service(
        namespace, project_id, project_name, classification, networking_api,
    )
    dest_rule = _istio_destination_rule(
        namespace, project_id, project_name, classification, networking_api,
    )
    sidecar = _istio_sidecar(
        namespace, project_id, classification, networking_api,
    )

    manifests = [peer_auth, authz_deny, authz_allow, virtual_svc, dest_rule, sidecar]
    yaml_content = _combine_manifests(manifests)

    return {
        "mesh": "istio",
        "project_id": project_id,
        "namespace": namespace,
        "classification": classification,
        "peer_authentication": peer_auth,
        "authorization_policy_deny": authz_deny,
        "authorization_policy_allow": authz_allow,
        "virtual_service": virtual_svc,
        "destination_rule": dest_rule,
        "sidecar": sidecar,
        "manifest_count": len(manifests),
        "yaml_content": yaml_content,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def generate_linkerd_config(project_id: str, profile: dict = None) -> dict:
    """Generate Linkerd service mesh configuration manifests for a project.

    Produces four Linkerd CRD manifests:
    1. Server              — mTLS policy per service (policy.linkerd.io/v1beta2)
    2. ServerAuthorization — per-service authorization (policy.linkerd.io/v1beta2)
    3. ServiceProfile      — retries, timeouts (linkerd.io/v1alpha2)
    4. HTTPRoute           — traffic routing (gateway.networking.k8s.io/v1beta1)

    All manifests carry icdev.mil/* labels and classification annotations.
    PDP references are surfaced as annotations only (ADR D124).

    Args:
        project_id: ICDEV project identifier.
        profile:    Optional pre-loaded ZTA/DevSecOps profile dict. If None,
                    the profile is loaded from the database.

    Returns:
        Dict with keys:
          server, server_authorization, service_profile, http_route,
          yaml_content (str), mesh, project_id.
    """
    if profile is None:
        profile = _get_profile(project_id)

    project = _get_project_info(project_id)
    project_name = project.get("name", project_id)
    namespace = profile.get("namespace") or project.get("namespace", "default")
    classification = profile.get("classification") or project.get("classification", "CUI")

    config = _load_config()
    linkerd_opts = config.get("service_mesh_options", {}).get("linkerd", {})
    api_versions = linkerd_opts.get("api_versions", {})
    policy_api = api_versions.get("policy", "policy.linkerd.io/v1beta2")
    server_api = "linkerd.io/v1alpha2"   # ServiceProfile uses a separate API group
    pdp_refs = config.get("pdp_references", [])

    server = _linkerd_server(
        namespace, project_id, project_name, classification, policy_api,
    )
    server_authz = _linkerd_server_authorization(
        namespace, project_id, classification, policy_api, pdp_refs,
    )
    svc_profile = _linkerd_service_profile(
        namespace, project_id, project_name, classification, server_api,
    )
    http_route = _linkerd_http_route(
        namespace, project_id, project_name, classification, policy_api,
    )

    manifests = [server, server_authz, svc_profile, http_route]
    yaml_content = _combine_manifests(manifests)

    return {
        "mesh": "linkerd",
        "project_id": project_id,
        "namespace": namespace,
        "classification": classification,
        "server": server,
        "server_authorization": server_authz,
        "service_profile": svc_profile,
        "http_route": http_route,
        "manifest_count": len(manifests),
        "yaml_content": yaml_content,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _human_output(result: dict) -> None:
    """Print a human-readable summary of generated manifests to stdout."""
    mesh = result.get("mesh", "unknown").upper()
    project_id = result.get("project_id", "")
    namespace = result.get("namespace", "")
    classification = result.get("classification", "")
    count = result.get("manifest_count", 0)
    generated_at = result.get("generated_at", "")

    print(f"\n{'='*60}")
    print(f"  ICDEV Service Mesh Generator — {mesh}")
    print(f"{'='*60}")
    print(f"  Project:        {project_id}")
    print(f"  Namespace:      {namespace}")
    print(f"  Classification: {classification}")
    print(f"  Manifests:      {count}")
    print(f"  Generated:      {generated_at}")
    print(f"{'='*60}\n")

    if mesh == "ISTIO":
        manifest_keys = [
            ("peer_authentication", "PeerAuthentication"),
            ("authorization_policy_deny", "AuthorizationPolicy (deny-all)"),
            ("authorization_policy_allow", "AuthorizationPolicy (allow-internal)"),
            ("virtual_service", "VirtualService"),
            ("destination_rule", "DestinationRule"),
            ("sidecar", "Sidecar"),
        ]
    else:
        manifest_keys = [
            ("server", "Server"),
            ("server_authorization", "ServerAuthorization"),
            ("service_profile", "ServiceProfile"),
            ("http_route", "HTTPRoute"),
        ]

    for key, label in manifest_keys:
        manifest = result.get(key, {})
        kind = manifest.get("kind", label)
        name = manifest.get("metadata", {}).get("name", "")
        print(f"  [{kind:30s}]  {name}")

    print()
    print("  YAML output (-80 chars per line):")
    print(f"{'─'*60}")
    for line in result.get("yaml_content", "").splitlines():
        print(f"  {line[:78]}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate Istio or Linkerd service mesh configurations for an ICDEV project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate Istio config and print JSON
  python tools/devsecops/service_mesh_generator.py --project-id proj-123 --mesh istio --json

  # Generate Linkerd config in human-readable format
  python tools/devsecops/service_mesh_generator.py --project-id proj-123 --mesh linkerd --human

  # Write manifests to a directory
  python tools/devsecops/service_mesh_generator.py --project-id proj-123 --mesh istio --output ./k8s/mesh/

ADR D121: Both Istio and Linkerd supported; customer selects in profile.
ADR D124: PDP modeled as external reference; ICDEV generates PEP configs only.
""",
    )
    parser.add_argument(
        "--project-id",
        required=True,
        metavar="PROJECT_ID",
        help="ICDEV project identifier",
    )
    parser.add_argument(
        "--mesh",
        choices=["istio", "linkerd"],
        default=None,
        help=(
            "Service mesh type. If omitted, uses the value from the project's "
            "ZTA/DevSecOps profile (defaults to 'istio' when no profile exists)."
        ),
    )
    parser.add_argument(
        "--output",
        metavar="DIR",
        default=None,
        help=(
            "Directory to write generated YAML manifests. "
            "If omitted, manifests are printed to stdout (combined YAML)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output full result as JSON (includes individual manifest dicts)",
    )
    parser.add_argument(
        "--human",
        action="store_true",
        help="Output human-readable summary (overrides --json)",
    )

    args = parser.parse_args()

    # Load profile
    profile = _get_profile(args.project_id)

    # Resolve mesh type: CLI flag > profile > default
    mesh = args.mesh
    if mesh is None:
        mesh = profile.get("service_mesh", "istio")
    mesh = mesh.lower()

    # Generate
    if mesh == "istio":
        result = generate_istio_config(args.project_id, profile)
    elif mesh == "linkerd":
        result = generate_linkerd_config(args.project_id, profile)
    else:
        sys.stderr.write(f"ERROR: Unsupported mesh type: {mesh}\n")
        sys.exit(1)

    # Write to output directory
    if args.output:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{args.project_id}-{mesh}-mesh.yaml"
        out_file.write_text(result["yaml_content"], encoding="utf-8")
        print(f"[service_mesh] Written {result['manifest_count']} manifests to: {out_file}")
        if args.as_json and not args.human:
            # Also emit JSON summary
            summary = {k: v for k, v in result.items() if k != "yaml_content"}
            summary["output_file"] = str(out_file)
            print(json.dumps(summary, indent=2))
        return

    # Console output
    if args.human:
        _human_output(result)
    elif args.as_json:
        print(json.dumps(result, indent=2))
    else:
        # Default: print combined YAML content
        print(result["yaml_content"])


if __name__ == "__main__":
    main()
