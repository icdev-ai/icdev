#!/usr/bin/env python3
"""ICDEV SaaS -- Namespace Provisioner.

CUI // SP-CTI

Creates K8s namespaces, network policies, resource quotas, and service
accounts for tenant isolation.  Isolation model:
  - IL2-IL4: Dedicated K8s namespace (icdev-tenant-{slug})
  - IL5:     Dedicated namespace + dedicated node pool + dedicated RDS
  - IL6:     Dedicated AWS sub-account (SIPR air-gapped)

Usage:
    # Create namespace for a tenant
    python tools/saas/infra/namespace_provisioner.py --create \\
        --slug acme-defense --il IL4 --tier starter

    # Delete namespace
    python tools/saas/infra/namespace_provisioner.py --delete --slug acme-defense

    # Check namespace status / resource usage
    python tools/saas/infra/namespace_provisioner.py --status --slug acme-defense

    # Generate YAML only (no kubectl)
    python tools/saas/infra/namespace_provisioner.py --yaml \\
        --slug acme-defense --il IL5 --tier enterprise
"""

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
TEMPLATE_DIR = BASE_DIR / "k8s" / "saas"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("namespace_provisioner")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAMESPACE_PREFIX = "icdev-tenant-"
PLATFORM_NAMESPACE = "icdev-platform"

TIER_QUOTAS = {
    "starter": {
        "requests.cpu": "2",
        "requests.memory": "4Gi",
        "limits.cpu": "4",
        "limits.memory": "8Gi",
        "pods": "10",
    },
    "professional": {
        "requests.cpu": "8",
        "requests.memory": "16Gi",
        "limits.cpu": "16",
        "limits.memory": "32Gi",
        "pods": "20",
    },
    "enterprise": {
        "requests.cpu": "32",
        "requests.memory": "64Gi",
        "limits.cpu": "64",
        "limits.memory": "128Gi",
        "pods": "50",
    },
}

VALID_IMPACT_LEVELS = {"IL2", "IL4", "IL5", "IL6"}
VALID_TIERS = {"starter", "professional", "enterprise"}


# ============================================================================
# YAML Generation
# ============================================================================

def _namespace_yaml(tenant_slug, impact_level, tier):
    """Generate Namespace resource YAML."""
    ns = NAMESPACE_PREFIX + tenant_slug
    lines = [
        "# CUI // SP-CTI",
        "apiVersion: v1",
        "kind: Namespace",
        "metadata:",
        "  name: {}".format(ns),
        "  labels:",
        "    app.kubernetes.io/part-of: icdev",
        "    app.kubernetes.io/managed-by: icdev-namespace-provisioner",
        '    icdev/tenant: "{}"'.format(tenant_slug),
        '    icdev/impact-level: "{}"'.format(impact_level),
        '    icdev/tier: "{}"'.format(tier),
        "    classification: CUI",
    ]
    return "\n".join(lines)


def _default_deny_yaml(tenant_slug):
    """Generate default-deny NetworkPolicy YAML."""
    ns = NAMESPACE_PREFIX + tenant_slug
    lines = [
        "apiVersion: networking.k8s.io/v1",
        "kind: NetworkPolicy",
        "metadata:",
        "  name: default-deny-all",
        "  namespace: {}".format(ns),
        "  labels:",
        "    app.kubernetes.io/part-of: icdev",
        "    classification: CUI",
        "spec:",
        "  podSelector: {}",
        "  policyTypes:",
        "    - Ingress",
        "    - Egress",
        "  ingress: []",
        "  egress: []",
    ]
    return "\n".join(lines)


def _allow_gateway_yaml(tenant_slug):
    """Generate allow-from-gateway NetworkPolicy YAML."""
    ns = NAMESPACE_PREFIX + tenant_slug
    lines = [
        "apiVersion: networking.k8s.io/v1",
        "kind: NetworkPolicy",
        "metadata:",
        "  name: allow-from-gateway",
        "  namespace: {}".format(ns),
        "  labels:",
        "    app.kubernetes.io/part-of: icdev",
        "    classification: CUI",
        "spec:",
        "  podSelector: {}",
        "  policyTypes:",
        "    - Ingress",
        "  ingress:",
        "    - from:",
        "        - namespaceSelector:",
        "            matchLabels:",
        "              kubernetes.io/metadata.name: {}".format(PLATFORM_NAMESPACE),
        "      ports:",
        "        - protocol: TCP",
        "          port: 8443",
        "        - protocol: TCP",
        "          port: 8444",
        "        - protocol: TCP",
        "          port: 8445",
    ]
    return "\n".join(lines)


def _allow_egress_yaml(tenant_slug):
    """Generate controlled egress NetworkPolicy (DNS + platform + AWS endpoints)."""
    ns = NAMESPACE_PREFIX + tenant_slug
    lines = [
        "apiVersion: networking.k8s.io/v1",
        "kind: NetworkPolicy",
        "metadata:",
        "  name: allow-controlled-egress",
        "  namespace: {}".format(ns),
        "  labels:",
        "    app.kubernetes.io/part-of: icdev",
        "    classification: CUI",
        "spec:",
        "  podSelector: {}",
        "  policyTypes:",
        "    - Egress",
        "  egress:",
        "    # Allow DNS",
        "    - to: []",
        "      ports:",
        "        - protocol: UDP",
        "          port: 53",
        "        - protocol: TCP",
        "          port: 53",
        "    # Allow HTTPS to AWS endpoints",
        "    - to: []",
        "      ports:",
        "        - protocol: TCP",
        "          port: 443",
        "    # Allow platform namespace",
        "    - to:",
        "        - namespaceSelector:",
        "            matchLabels:",
        "              kubernetes.io/metadata.name: {}".format(PLATFORM_NAMESPACE),
    ]
    return "\n".join(lines)


def _resource_quota_yaml(tenant_slug, tier):
    """Generate ResourceQuota YAML based on subscription tier."""
    ns = NAMESPACE_PREFIX + tenant_slug
    quota = TIER_QUOTAS.get(tier, TIER_QUOTAS["starter"])
    lines = [
        "apiVersion: v1",
        "kind: ResourceQuota",
        "metadata:",
        "  name: tenant-quota",
        "  namespace: {}".format(ns),
        "  labels:",
        "    app.kubernetes.io/part-of: icdev",
        "    classification: CUI",
        "spec:",
        "  hard:",
    ]
    for key, value in quota.items():
        lines.append('    {}: "{}"'.format(key, value))
    return "\n".join(lines)


def _service_account_yaml(tenant_slug):
    """Generate ServiceAccount YAML for tenant agents."""
    ns = NAMESPACE_PREFIX + tenant_slug
    lines = [
        "apiVersion: v1",
        "kind: ServiceAccount",
        "metadata:",
        "  name: icdev-agent",
        "  namespace: {}".format(ns),
        "  labels:",
        "    app.kubernetes.io/part-of: icdev",
        "    classification: CUI",
        "automountServiceAccountToken: false",
    ]
    return "\n".join(lines)


def _node_affinity_patch_yaml(tenant_slug):
    """Generate node affinity patch for IL5+ dedicated node pools."""
    lines = [
        "# Node affinity patch for IL5+ tenants — apply to all pods in namespace",
        "# Requires node pool labeled: icdev/dedicated-tenant={}".format(tenant_slug),
        "apiVersion: v1",
        "kind: ConfigMap",
        "metadata:",
        "  name: node-affinity-config",
        "  namespace: {}".format(NAMESPACE_PREFIX + tenant_slug),
        "  labels:",
        "    app.kubernetes.io/part-of: icdev",
        "    classification: CUI",
        "  annotations:",
        "    icdev/note: >-",
        "      Use a mutating webhook or PodPreset to inject nodeSelector",
        "      into all pods in this namespace.",
        "data:",
        "  node-selector: |",
        "    icdev/dedicated-tenant: {}".format(tenant_slug),
        "  toleration: |",
        "    - key: icdev/dedicated-tenant",
        '      value: "{}"'.format(tenant_slug),
        '      effect: "NoSchedule"',
    ]
    return "\n".join(lines)


def generate_namespace_yaml(tenant_slug, impact_level, tier):
    """Generate the full multi-document YAML for a tenant namespace.

    Args:
        tenant_slug: URL-safe tenant identifier
        impact_level: IL2, IL4, IL5, or IL6
        tier: starter, professional, or enterprise

    Returns:
        str: Multi-document YAML string
    """
    _validate_inputs(tenant_slug, impact_level, tier)

    documents = [
        _namespace_yaml(tenant_slug, impact_level, tier),
        _default_deny_yaml(tenant_slug),
        _allow_gateway_yaml(tenant_slug),
        _allow_egress_yaml(tenant_slug),
        _resource_quota_yaml(tenant_slug, tier),
        _service_account_yaml(tenant_slug),
    ]

    # IL5+ gets dedicated node affinity configuration
    if impact_level in ("IL5", "IL6"):
        documents.append(_node_affinity_patch_yaml(tenant_slug))

    return "\n---\n".join(documents) + "\n"


# ============================================================================
# Validation
# ============================================================================

def _validate_inputs(tenant_slug, impact_level, tier):
    """Validate provisioning inputs."""
    if not tenant_slug or not tenant_slug.strip():
        raise ValueError("tenant_slug must be a non-empty string.")
    if impact_level not in VALID_IMPACT_LEVELS:
        raise ValueError(
            "Invalid impact_level: {}. Must be one of: {}".format(
                impact_level, sorted(VALID_IMPACT_LEVELS)))
    if tier not in VALID_TIERS:
        raise ValueError(
            "Invalid tier: {}. Must be one of: {}".format(
                tier, sorted(VALID_TIERS)))


# ============================================================================
# kubectl Helpers
# ============================================================================

def _kubectl_available():
    """Check if kubectl is available on the system PATH."""
    try:
        result = subprocess.run(
            ["kubectl", "version", "--client", "--short"],
            capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _kubectl_apply(yaml_str):
    """Apply YAML via kubectl apply -f -.

    Returns:
        (success: bool, output: str)
    """
    try:
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=yaml_str, capture_output=True, text=True, timeout=60)
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode == 0, output
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, "kubectl error: {}".format(exc)


def _kubectl_delete_namespace(namespace):
    """Delete a K8s namespace via kubectl.

    Returns:
        (success: bool, output: str)
    """
    try:
        result = subprocess.run(
            ["kubectl", "delete", "namespace", namespace, "--wait=false"],
            capture_output=True, text=True, timeout=30)
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode == 0, output
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, "kubectl error: {}".format(exc)


def _kubectl_get_quota(namespace):
    """Get resource quota usage for a namespace.

    Returns:
        dict or None
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", "resourcequota", "tenant-quota",
             "-n", namespace, "-o", "json"],
            capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _kubectl_namespace_exists(namespace):
    """Check if a namespace exists."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "namespace", namespace],
            capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ============================================================================
# Public API
# ============================================================================

def provision_namespace(tenant_slug, impact_level, tier):
    """Create a K8s namespace with network policies, quotas, and service account.

    If kubectl is available, applies YAML directly.  Otherwise generates
    YAML to stdout for manual application.

    Args:
        tenant_slug: URL-safe tenant identifier (e.g., "acme-defense")
        impact_level: IL2, IL4, IL5, or IL6
        tier: starter, professional, or enterprise

    Returns:
        dict with keys: namespace, impact_level, tier, policies_created,
        quota, service_account, applied, yaml_generated, timestamp
    """
    _validate_inputs(tenant_slug, impact_level, tier)

    namespace = NAMESPACE_PREFIX + tenant_slug
    yaml_str = generate_namespace_yaml(tenant_slug, impact_level, tier)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    policies = ["default-deny-all", "allow-from-gateway", "allow-controlled-egress"]
    extras = []
    if impact_level in ("IL5", "IL6"):
        extras.append("node-affinity-config")

    result = {
        "namespace": namespace,
        "impact_level": impact_level,
        "tier": tier,
        "policies_created": policies,
        "quota": TIER_QUOTAS.get(tier, TIER_QUOTAS["starter"]),
        "service_account": "icdev-agent",
        "dedicated_nodes": impact_level in ("IL5", "IL6"),
        "extra_resources": extras,
        "applied": False,
        "yaml_generated": True,
        "timestamp": now,
    }

    if _kubectl_available():
        if _kubectl_namespace_exists(namespace):
            logger.warning("Namespace %s already exists — applying updates.", namespace)
        success, output = _kubectl_apply(yaml_str)
        result["applied"] = success
        result["kubectl_output"] = output
        if success:
            logger.info("Namespace %s provisioned successfully.", namespace)
        else:
            logger.error("Failed to apply namespace %s: %s", namespace, output)
    else:
        logger.warning(
            "kubectl not available. YAML generated but not applied. "
            "Pipe output to kubectl apply -f -")
        result["yaml"] = yaml_str

    return result


def delete_namespace(tenant_slug):
    """Delete a tenant namespace.

    Args:
        tenant_slug: URL-safe tenant identifier

    Returns:
        bool: True if deletion was initiated successfully
    """
    if not tenant_slug or not tenant_slug.strip():
        raise ValueError("tenant_slug must be a non-empty string.")

    namespace = NAMESPACE_PREFIX + tenant_slug

    if not _kubectl_available():
        logger.error(
            "kubectl not available. To delete manually: "
            "kubectl delete namespace %s", namespace)
        return False

    if not _kubectl_namespace_exists(namespace):
        logger.warning("Namespace %s does not exist.", namespace)
        return False

    success, output = _kubectl_delete_namespace(namespace)
    if success:
        logger.info("Namespace %s deletion initiated.", namespace)
    else:
        logger.error("Failed to delete namespace %s: %s", namespace, output)
    return success


def get_namespace_status(tenant_slug):
    """Get resource usage and status for a tenant namespace.

    Args:
        tenant_slug: URL-safe tenant identifier

    Returns:
        dict with namespace, exists, quota_usage, timestamp
    """
    if not tenant_slug or not tenant_slug.strip():
        raise ValueError("tenant_slug must be a non-empty string.")

    namespace = NAMESPACE_PREFIX + tenant_slug
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = {
        "namespace": namespace,
        "exists": False,
        "quota_usage": None,
        "timestamp": now,
    }

    if not _kubectl_available():
        result["error"] = "kubectl not available"
        return result

    result["exists"] = _kubectl_namespace_exists(namespace)

    if result["exists"]:
        quota_data = _kubectl_get_quota(namespace)
        if quota_data and "status" in quota_data:
            status = quota_data["status"]
            result["quota_usage"] = {
                "hard": status.get("hard", {}),
                "used": status.get("used", {}),
            }

    return result


# ============================================================================
# CLI
# ============================================================================

def _print_result(data, as_json=False):
    """Print result to stdout."""
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        for key, value in data.items():
            if isinstance(value, dict):
                print("  {}:".format(key))
                for k, v in value.items():
                    print("    {}: {}".format(k, v))
            elif isinstance(value, list):
                print("  {}: {}".format(key, ", ".join(str(v) for v in value)))
            elif key == "yaml":
                print("\n--- Generated YAML ---\n")
                print(value)
            else:
                print("  {}: {}".format(key, value))


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI -- ICDEV SaaS Namespace Provisioner",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--create", action="store_true",
        help="Create namespace with policies and quotas")
    action.add_argument(
        "--delete", action="store_true",
        help="Delete a tenant namespace")
    action.add_argument(
        "--status", action="store_true",
        help="Get namespace resource usage")
    action.add_argument(
        "--yaml", action="store_true",
        help="Generate YAML only (do not apply)")

    parser.add_argument(
        "--slug", type=str, required=True,
        help="Tenant slug (e.g., acme-defense)")
    parser.add_argument(
        "--il", type=str, default="IL4",
        help="Impact level: IL2, IL4, IL5, IL6 (default: IL4)")
    parser.add_argument(
        "--tier", type=str, default="starter",
        help="Subscription tier: starter, professional, enterprise (default: starter)")
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Output as JSON")

    args = parser.parse_args()

    try:
        if args.create:
            result = provision_namespace(args.slug, args.il.upper(), args.tier.lower())
            _print_result(result, args.as_json)

        elif args.delete:
            success = delete_namespace(args.slug)
            result = {"slug": args.slug, "deleted": success}
            _print_result(result, args.as_json)

        elif args.status:
            result = get_namespace_status(args.slug)
            _print_result(result, args.as_json)

        elif args.yaml:
            yaml_str = generate_namespace_yaml(
                args.slug, args.il.upper(), args.tier.lower())
            if args.as_json:
                print(json.dumps({"yaml": yaml_str}, indent=2))
            else:
                print(yaml_str)

    except ValueError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print("FATAL: {}".format(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
