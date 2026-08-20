
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""ICDEV Infrastructure Design Canvas — Assessment Engine.

Pure deterministic functions for evaluating infrastructure designs against
compliance rules. No Flask dependency — takes graph data and returns results.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.infra_canvas.constants import INFRA_COMPLIANCE_RULES

logger = get_logger(__name__)

try:
    import yaml as _yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "infra_canvas_config.yaml"


def _load_config() -> dict:
    """Load IDC engine config from args/infra_canvas_config.yaml."""
    if not _HAS_YAML or not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
        return _yaml.safe_load(_f) or {}


_IDC_CONFIG = _load_config()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Helper: node/edge queries ────────────────────────────────────────────────


def _nodes_by_type_prefix(nodes: list, prefix: str) -> list:
    """Return nodes whose type starts with a given prefix."""
    return [n for n in nodes if n.get("type", "").startswith(prefix)]


def _has_node_type(nodes: list, type_id: str) -> bool:
    return any(n.get("type") == type_id for n in nodes)


def _has_any_node_prefix(nodes: list, prefixes: list) -> bool:
    return any(n.get("type", "").startswith(p) for n in nodes for p in prefixes)


def _nodes_connected_to(node_id: str, edges: list, nodes: list) -> list:
    """Return nodes connected to the given node via edges."""
    connected_ids = set()
    for e in edges:
        if e.get("source") == node_id:
            connected_ids.add(e.get("target"))
        elif e.get("target") == node_id:
            connected_ids.add(e.get("source"))
    return [n for n in nodes if n.get("id") in connected_ids]


# ── Check functions ──────────────────────────────────────────────────────────


def _check_storage_encrypted(nodes, edges, findings, rule):
    """All storage resources should have encryption (KMS node connected)."""
    storage_prefixes = [
        "aws-s3",
        "aws-ebs",
        "aws-efs",
        "aws-fsx",
        "az-blob",
        "az-disk",
        "az-files",
        "az-netapp",
        "gcp-gcs",
        "gcp-pd",
        "gcp-filestore",
        "oci-os",
        "oci-bv",
        "oci-fss",
        "ibm-cos",
        "ibm-block",
        "ibm-file",
        "op-san",
        "op-nas",
        "op-s3-compat",
    ]
    kms_types = [
        "aws-kms",
        "az-keyvault",
        "gcp-kms",
        "oci-vault",
        "ibm-kms",
        "iac-vault",
    ]
    storage_nodes = [n for n in nodes if any(n.get("type", "").startswith(p) for p in storage_prefixes)]
    if not storage_nodes:
        return
    has_kms = any(n.get("type") in kms_types for n in nodes)
    if not has_kms:
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": (
                    f"Found {len(storage_nodes)} storage resource(s) "
                    f"but no KMS/Key Vault service for encryption key management."
                ),
            }
        )


def _check_db_encrypted(nodes, edges, findings, rule):
    """All database services should have encryption."""
    db_prefixes = [
        "aws-rds",
        "aws-aurora",
        "aws-dynamodb",
        "aws-redshift",
        "aws-elasticache",
        "aws-documentdb",
        "aws-neptune",
        "aws-timestream",
        "aws-keyspaces",
        "aws-opensearch",
        "az-sql",
        "az-postgres",
        "az-mysql",
        "az-cosmos",
        "az-synapse",
        "az-redis",
        "az-search",
        "gcp-cloudsql",
        "gcp-spanner",
        "gcp-bigtable",
        "gcp-firestore",
        "gcp-bigquery",
        "gcp-memorystore",
        "gcp-alloydb",
        "oci-adb",
        "oci-mysql",
        "oci-nosql",
        "oci-postgresql",
        "ibm-db2",
        "ibm-postgres",
        "ibm-mongo",
        "ibm-redis",
        "ibm-elastic",
        "op-postgres",
        "op-mysql",
        "op-oracle",
        "op-sqlserver",
        "op-mongo",
        "op-redis",
    ]
    kms_types = [
        "aws-kms",
        "az-keyvault",
        "gcp-kms",
        "oci-vault",
        "ibm-kms",
        "iac-vault",
    ]
    db_nodes = [n for n in nodes if any(n.get("type", "").startswith(p) for p in db_prefixes)]
    if not db_nodes:
        return
    has_kms = any(n.get("type") in kms_types for n in nodes)
    if not has_kms:
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": (f"Found {len(db_nodes)} database(s) but no KMS/Key Vault for encryption key management."),
            }
        )


def _check_kms_present(nodes, edges, findings, rule):
    kms_types = [
        "aws-kms",
        "az-keyvault",
        "gcp-kms",
        "oci-vault",
        "ibm-kms",
        "iac-vault",
    ]
    if not any(n.get("type") in kms_types for n in nodes):
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": "No KMS/Key Vault service found in the design.",
            }
        )


def _check_compute_behind_lb(nodes, edges, findings, rule):
    """Compute instances should have a load balancer or API gateway."""
    compute_prefixes = [
        "aws-ec2",
        "az-vm",
        "gcp-gce",
        "oci-compute",
        "ibm-vsi",
        "op-server",
        "op-vm",
    ]
    lb_prefixes = [
        "aws-elb",
        "aws-apigw",
        "az-app",
        "az-apim",
        "gcp-lb",
        "gcp-apigee",
        "oci-lb",
        "oci-apigw",
        "ibm-lb",
    ]
    compute_nodes = [n for n in nodes if any(n.get("type", "").startswith(p) for p in compute_prefixes)]
    if not compute_nodes:
        return
    has_lb = any(any(n.get("type", "").startswith(p) for p in lb_prefixes) for n in nodes)
    if not has_lb:
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": (f"Found {len(compute_nodes)} compute instance(s) with no load balancer or API gateway."),
            }
        )


def _check_autoscaling_configured(nodes, edges, findings, rule):
    asg_types = [
        "aws-ec2-asg",
        "az-vmss",
        "gcp-mig",
        "oci-instance-pool",
    ]
    compute_prefixes = [
        "aws-ec2",
        "az-vm",
        "gcp-gce",
        "oci-compute",
        "ibm-vsi",
    ]
    has_compute = _has_any_node_prefix(nodes, compute_prefixes)
    if not has_compute:
        return
    has_asg = any(n.get("type") in asg_types for n in nodes)
    if not has_asg:
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": "Compute instances found but no auto-scaling group.",
            }
        )


def _check_dedicated_for_compliance(nodes, edges, findings, rule):
    # Advisory only — if design has compliance-tagged nodes but no dedicated hosts
    dedicated_types = [
        "aws-ec2-dedicated",
        "az-dedicated",
        "gcp-sole",
        "oci-dedicated-host",
    ]
    if any(n.get("type") in dedicated_types for n in nodes):
        return  # Already using dedicated
    # Check if any node metadata mentions ITAR/EAR/CJIS
    for n in nodes:
        meta = n.get("metadata", {}) or {}
        tags = str(meta.get("tags", "")).lower()
        if any(kw in tags for kw in ("itar", "ear", "cjis")):
            findings.append(
                {
                    "rule_id": rule["id"],
                    "title": rule["title"],
                    "severity": rule["severity"],
                    "category": rule["category"],
                    "detail": (
                        f"Node '{n.get('label', n.get('type'))}' tagged with "
                        f"compliance keywords but no dedicated/sole-tenant host."
                    ),
                }
            )
            return


def _check_registry_private(nodes, edges, findings, rule):
    registry_types = [
        "aws-ecr",
        "az-acr",
        "gcp-gar",
        "oci-ocir",
        "ibm-cr",
        "op-harbor",
    ]
    k8s_prefixes = [
        "aws-eks",
        "aws-ecs",
        "aws-fargate",
        "az-aks",
        "az-aci",
        "az-aca",
        "gcp-gke",
        "gcp-cloudrun",
        "oci-oke",
        "ibm-iks",
        "ibm-openshift",
        "op-k8s",
        "op-openshift",
        "op-docker",
        "op-bigbang",
    ]
    has_containers = _has_any_node_prefix(nodes, k8s_prefixes)
    if not has_containers:
        return
    has_registry = any(n.get("type") in registry_types for n in nodes)
    if not has_registry:
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": "Container workloads present but no private registry.",
            }
        )


def _check_k8s_rbac(nodes, edges, findings, rule):
    # Advisory — K8s clusters should exist with RBAC
    k8s_types = [
        "aws-eks",
        "az-aks",
        "gcp-gke",
        "oci-oke",
        "ibm-iks",
        "ibm-openshift",
        "op-k8s",
        "op-openshift",
        "op-bigbang",
    ]
    iam_prefixes = ["aws-iam", "az-entra", "gcp-iam", "oci-iam", "ibm-iam"]
    k8s_nodes = [n for n in nodes if n.get("type") in k8s_types]
    if not k8s_nodes:
        return
    has_iam = _has_any_node_prefix(nodes, iam_prefixes)
    if not has_iam:
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": (f"Found {len(k8s_nodes)} K8s cluster(s) but no IAM/IdP service for RBAC integration."),
            }
        )


def _check_iam_present(nodes, edges, findings, rule):
    iam_types = [
        "aws-iam",
        "aws-cognito",
        "az-entra",
        "gcp-iam",
        "oci-iam",
        "ibm-iam",
    ]
    if not any(n.get("type") in iam_types for n in nodes):
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": "No IAM or identity provider service in the design.",
            }
        )


def _check_secrets_managed(nodes, edges, findings, rule):
    secrets_types = [
        "aws-secrets",
        "az-keyvault",
        "gcp-secret",
        "oci-vault",
        "ibm-secrets",
        "iac-vault",
    ]
    if not any(n.get("type") in secrets_types for n in nodes):
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": "No secrets manager found — secrets must not be hardcoded.",
            }
        )


def _check_backup_configured(nodes, edges, findings, rule):
    backup_types = [
        "aws-backup",
        "az-backup",
        "gcp-backup",
    ]
    db_prefixes = [
        "aws-rds",
        "aws-aurora",
        "az-sql",
        "az-postgres",
        "gcp-cloudsql",
        "gcp-spanner",
        "oci-adb",
    ]
    has_db = _has_any_node_prefix(nodes, db_prefixes)
    if not has_db:
        return
    has_backup = any(n.get("type") in backup_types for n in nodes)
    if not has_backup:
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": "Database services present but no backup service.",
            }
        )


def _check_cspm_present(nodes, edges, findings, rule):
    cspm_types = [
        "aws-securityhub",
        "aws-config",
        "az-defender",
        "gcp-scc",
        "oci-cspm",
        "ibm-scc",
    ]
    if not any(n.get("type") in cspm_types for n in nodes):
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": "No CSPM/security posture service in the design.",
            }
        )


def _check_iac_present(nodes, edges, findings, rule):
    iac_prefixes = ["iac-"]
    if not _has_any_node_prefix(nodes, iac_prefixes):
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "detail": "No IaC tool (Terraform/Pulumi/Crossplane) in design.",
            }
        )


# Map check names to functions
_CHECK_FUNCTIONS = {
    "storage_encrypted": _check_storage_encrypted,
    "db_encrypted": _check_db_encrypted,
    "kms_present": _check_kms_present,
    "compute_behind_lb": _check_compute_behind_lb,
    "autoscaling_configured": _check_autoscaling_configured,
    "dedicated_for_compliance": _check_dedicated_for_compliance,
    "registry_private": _check_registry_private,
    "k8s_rbac": _check_k8s_rbac,
    "iam_present": _check_iam_present,
    "secrets_managed": _check_secrets_managed,
    "backup_configured": _check_backup_configured,
    "cspm_present": _check_cspm_present,
    "iac_present": _check_iac_present,
}


# ── Public API ───────────────────────────────────────────────────────────────


def assess_infra_design(graph_data: dict) -> dict:
    """Run all infrastructure compliance rules against a design.

    Args:
        graph_data: {"nodes": [...], "edges": [...]}

    Returns:
        {
            "assessed_at": ISO timestamp,
            "findings": [...],
            "summary": {"total": N, "by_severity": {...}, "by_category": {...}},
            "score": float  (0-100, higher = more compliant),
        }
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    findings: list[dict[str, Any]] = []

    for rule in INFRA_COMPLIANCE_RULES:
        check_name = rule.get("check")
        check_fn = _CHECK_FUNCTIONS.get(check_name)
        if check_fn:
            try:
                check_fn(nodes, edges, findings, rule)
            except Exception as exc:
                logger.warning("Check %s failed: %s", check_name, exc)

    # Summarize
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for f in findings:
        sev = f.get("severity", "CAT3")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        cat = f.get("category", "other")
        by_category[cat] = by_category.get(cat, 0) + 1

    total_rules = len(INFRA_COMPLIANCE_RULES)
    passed = total_rules - len(findings)
    # NOT ASSESSED, never 100.0 (rem-hyg-13). An empty rulebook means no check
    # ran, which is the strongest possible reason NOT to publish a compliance
    # score. INFRA_COMPLIANCE_RULES is non-empty today so this arm is unreached,
    # and it is corrected anyway: the branch is what a future change filtering
    # the rulebook by profile or by IL would land on, and it would then start
    # returning a perfect score for a design nothing was checked against.
    score = round((passed / total_rules) * 100, 1) if total_rules else None

    return {
        "assessed_at": _utcnow_iso(),
        "findings": findings,
        "summary": {
            "total": len(findings),
            "rules_checked": total_rules,
            "passed": passed,
            "by_severity": by_severity,
            "by_category": by_category,
        },
        "score": score,
    }


def compute_csp_coverage(graph_data: dict) -> dict:
    """Compute which CSPs are represented in the design."""
    nodes = graph_data.get("nodes", [])
    csps = set()
    for n in nodes:
        ntype = n.get("type", "")
        if ntype.startswith("aws-"):
            csps.add("aws")
        elif ntype.startswith("az-"):
            csps.add("azure")
        elif ntype.startswith("gcp-"):
            csps.add("gcp")
        elif ntype.startswith("oci-"):
            csps.add("oci")
        elif ntype.startswith("ibm-"):
            csps.add("ibm")
        elif ntype.startswith("op-"):
            csps.add("onprem")
    return {
        "csps": sorted(csps),
        "count": len(csps),
        "multi_cloud": len(csps) > 1,
    }


def suggest_equivalents(node_type: str, target_csp: str) -> list:
    """Suggest equivalent services on a different CSP.

    Args:
        node_type: Current node type (e.g., "aws-eks")
        target_csp: Target CSP (e.g., "azure")

    Returns:
        List of equivalent node types on the target CSP.
    """
    from tools.infra_canvas.constants import CSP_EQUIVALENCE

    suggestions = []
    for _concept, mapping in CSP_EQUIVALENCE.items():
        if node_type in mapping.values():
            target_type = mapping.get(target_csp)
            if target_type and target_type != node_type:
                suggestions.append(target_type)
    return suggestions


def detect_infra_gaps(assessment_result: dict) -> list:
    """Identify missing controls from assessment findings."""
    gaps = []
    for finding in assessment_result.get("findings", []):
        gaps.append(
            {
                "rule_id": finding["rule_id"],
                "title": finding["title"],
                "severity": finding["severity"],
                "recommendation": finding.get("detail", ""),
            }
        )
    return gaps
