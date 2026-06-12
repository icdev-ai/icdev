# CUI // SP-CTI
"""Parse ``terraform show -json`` output into SnapshotRow list.

Supports format_version 0.1 and 1.0.  Resources in child modules are
included; data sources (mode == "data") are skipped.

Usage::

    import json
    from tools.infra_canvas.importers.tf_state import parse

    with open("terraform.tfstate.json") as f:
        rows = parse(json.load(f), project_id="proj-govcloud-east")
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.infra_canvas.importers import SnapshotRow

# ── Type mapping: Terraform resource type → IDC canonical type ────────────────

_TF_TYPE_MAP: dict[str, str] = {
    # AWS Compute
    "aws_instance": "aws-ec2",
    "aws_autoscaling_group": "aws-ec2-asg",
    "aws_spot_instance_request": "aws-ec2-spot",
    "aws_launch_template": "aws-ec2",
    # AWS Containers
    "aws_eks_cluster": "aws-eks",
    "aws_ecs_cluster": "aws-ecs",
    "aws_ecs_service": "aws-ecs",
    "aws_ecr_repository": "aws-ecr",
    "aws_apprunner_service": "aws-apprunner",
    # AWS Serverless
    "aws_lambda_function": "aws-lambda",
    "aws_sfn_state_machine": "aws-step-fn",
    # AWS Networking
    "aws_vpc": "aws-vpc",
    "aws_subnet": "aws-subnet",
    "aws_security_group": "aws-sg",
    "aws_nat_gateway": "aws-nat",
    "aws_internet_gateway": "aws-igw",
    "aws_route_table": "aws-route-table",
    "aws_vpc_endpoint": "aws-vpc-endpoint",
    "aws_lb": "aws-elb",
    "aws_alb": "aws-elb",
    "aws_elb": "aws-elb",
    "aws_wafv2_web_acl": "aws-waf",
    "aws_cloudfront_distribution": "aws-cdn",
    "aws_api_gateway_rest_api": "aws-apigw",
    "aws_apigatewayv2_api": "aws-apigw",
    "aws_direct_connect_connection": "aws-dx",
    "aws_dx_connection": "aws-dx",
    # AWS Storage
    "aws_s3_bucket": "aws-s3",
    "aws_ebs_volume": "aws-ebs",
    "aws_efs_file_system": "aws-efs",
    "aws_fsx_lustre_file_system": "aws-fsx",
    "aws_backup_plan": "aws-backup",
    # AWS Database
    "aws_db_instance": "aws-rds",
    "aws_rds_cluster": "aws-aurora",
    "aws_dynamodb_table": "aws-dynamodb",
    "aws_elasticache_cluster": "aws-elasticache",
    "aws_elasticache_replication_group": "aws-elasticache",
    "aws_redshift_cluster": "aws-redshift",
    "aws_docdb_cluster": "aws-documentdb",
    "aws_neptune_cluster": "aws-neptune",
    "aws_opensearch_domain": "aws-opensearch",
    # AWS IAM & Security
    "aws_iam_role": "aws-iam",
    "aws_iam_user": "aws-iam",
    "aws_iam_policy": "aws-iam",
    "aws_iam_group": "aws-iam",
    "aws_kms_key": "aws-kms",
    "aws_cloudtrail": "aws-cloudtrail",
    "aws_securityhub_account": "aws-securityhub",
    "aws_guardduty_detector": "aws-guardduty",
    # AWS Messaging
    "aws_sqs_queue": "aws-sqs",
    "aws_sns_topic": "aws-sns",
    "aws_kinesis_stream": "aws-kinesis",
    "aws_msk_cluster": "aws-msk",
    # Azure Compute
    "azurerm_linux_virtual_machine": "az-vm",
    "azurerm_windows_virtual_machine": "az-vm",
    "azurerm_virtual_machine": "az-vm",
    "azurerm_virtual_machine_scale_set": "az-vmss",
    # Azure Containers
    "azurerm_kubernetes_cluster": "az-aks",
    "azurerm_container_registry": "az-acr",
    "azurerm_container_group": "az-aci",
    "azurerm_container_app": "az-aca",
    # Azure Networking
    "azurerm_virtual_network": "az-vnet",
    "azurerm_subnet": "az-subnet",
    "azurerm_network_security_group": "az-nsg",
    "azurerm_lb": "az-lb",
    "azurerm_application_gateway": "az-appgw",
    "azurerm_firewall": "az-fw",
    # Azure Storage
    "azurerm_storage_account": "az-blob",
    "azurerm_storage_container": "az-blob",
    "azurerm_managed_disk": "az-disk",
    "azurerm_storage_share": "az-files",
    "azurerm_backup_policy_vm": "az-backup",
    # Azure Database
    "azurerm_sql_database": "az-sql",
    "azurerm_mssql_database": "az-sql",
    "azurerm_postgresql_flexible_server": "az-postgres",
    "azurerm_mysql_flexible_server": "az-mysql",
    "azurerm_redis_cache": "az-redis",
    "azurerm_cosmosdb_account": "az-cosmos",
    "azurerm_synapse_workspace": "az-synapse",
    "azurerm_cognitive_account": "az-openai",
    # Azure IAM & Security
    "azurerm_key_vault": "az-keyvault",
    # GCP Compute
    "google_compute_instance": "gcp-gce",
    "google_compute_instance_group_manager": "gcp-mig",
    # GCP Containers
    "google_container_cluster": "gcp-gke",
    "google_artifact_registry_repository": "gcp-gar",
    "google_cloud_run_service": "gcp-cloudrun",
    "google_cloud_run_v2_service": "gcp-cloudrun",
    # GCP Networking
    "google_compute_network": "gcp-vpc",
    "google_compute_subnetwork": "gcp-subnet",
    "google_compute_firewall": "gcp-firewall",
    "google_compute_forwarding_rule": "gcp-lb",
    # GCP Storage
    "google_storage_bucket": "gcp-gcs",
    "google_compute_disk": "gcp-pd",
    "google_filestore_instance": "gcp-filestore",
    # GCP Database
    "google_sql_database_instance": "gcp-cloudsql",
    "google_spanner_instance": "gcp-spanner",
    "google_bigtable_instance": "gcp-bigtable",
    "google_firestore_document": "gcp-firestore",
    # GCP IAM & Security
    "google_kms_key_ring": "gcp-kms",
    "google_kms_crypto_key": "gcp-kms",
    # OCI Compute
    "oci_core_instance": "oci-compute",
    # OCI Containers
    "oci_containerengine_cluster": "oci-oke",
    "oci_artifacts_container_repository": "oci-ocir",
    # OCI Networking
    "oci_core_vcn": "oci-vcn",
    "oci_core_subnet": "oci-subnet",
    "oci_core_security_list": "oci-sl",
    # OCI Storage
    "oci_objectstorage_bucket": "oci-os",
    "oci_core_boot_volume": "oci-bv",
    # OCI Database
    "oci_database_autonomous_database": "oci-adb",
    # OCI IAM & Security
    "oci_vault_vault": "oci-vault",
    "oci_kms_vault": "oci-vault",
}

# Provider registry path → CSP
_TF_PROVIDER_CSP: dict[str, str] = {
    "hashicorp/aws": "aws",
    "hashicorp/awscc": "aws",
    "hashicorp/azurerm": "azure",
    "hashicorp/azuread": "azure",
    "hashicorp/google": "gcp",
    "hashicorp/google-beta": "gcp",
    "hashicorp/oci": "oci",
    "ibm-cloud/ibm": "ibm",
}

_TF_TYPE_PREFIX_CSP: list[tuple[str, str]] = [
    ("aws_", "aws"),
    ("azurerm_", "azure"),
    ("azuread_", "azure"),
    ("google_", "gcp"),
    ("oci_", "oci"),
    ("ibm_", "ibm"),
]

_REDACT_KEYS: frozenset[str] = frozenset({
    "password", "secret_key", "private_key", "secret", "token",
    "access_key", "certificate", "private_pem", "key_pem",
    "client_secret", "primary_access_key", "secondary_access_key",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_csp(resource: dict[str, Any]) -> str:
    provider = resource.get("provider_name", "")
    for suffix, csp in _TF_PROVIDER_CSP.items():
        if provider.endswith(suffix):
            return csp
    rtype = resource.get("type", "")
    for prefix, csp in _TF_TYPE_PREFIX_CSP:
        if rtype.startswith(prefix):
            return csp
    return "unknown"


def _map_type(tf_type: str) -> str:
    return _TF_TYPE_MAP.get(tf_type, f"unknown-{tf_type}")


def _extract_region(values: dict[str, Any], csp: str) -> str:
    for key in ("region", "location", "availability_zone"):
        val = values.get(key)
        if isinstance(val, str) and val:
            # Normalize: strip AZ suffix (e.g. "us-east-1a" → "us-east-1")
            if csp in ("aws", "aws_gov") and val[-1].isalpha() and val.count("-") >= 2:
                return val[:-1]
            return val
    # Parse from ARN
    arn = values.get("arn", "")
    if isinstance(arn, str) and arn.startswith("arn:"):
        parts = arn.split(":")
        if len(parts) >= 5 and parts[3]:
            return parts[3]
    return "unknown"


def _extract_tags(values: dict[str, Any]) -> dict[str, str]:
    tags = values.get("tags") or values.get("Tags") or values.get("labels") or {}
    if not isinstance(tags, dict):
        return {}
    return {str(k): str(v) for k, v in tags.items()}


def _redact(values: dict[str, Any]) -> dict[str, Any]:
    return {
        k: "[REDACTED]" if k.lower() in _REDACT_KEYS else v
        for k, v in values.items()
    }


def _collect_resources(module: dict[str, Any]) -> list[dict[str, Any]]:
    """Recurse into child_modules to collect all managed resources."""
    result: list[dict[str, Any]] = []
    for res in module.get("resources", []):
        if res.get("mode") != "data":
            result.append(res)
    for child in module.get("child_modules", []):
        result.extend(_collect_resources(child))
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def parse(
    state: dict[str, Any],
    *,
    snapshot_id: str | None = None,
    project_id: str = "",
    classification: str = "CUI",
    taken_at: str | None = None,
) -> list[SnapshotRow]:
    """Parse ``terraform show -json`` output into a list of SnapshotRow.

    Args:
        state: Parsed JSON from ``terraform show -json``.
        snapshot_id: UUIDv4 hex for the batch; generated if omitted.
        project_id: ICDEV project identifier.
        classification: Default classification for all rows.
        taken_at: ISO 8601 UTC timestamp; defaults to now.

    Returns:
        List of SnapshotRow, one per managed resource.
    """
    sid = snapshot_id or uuid.uuid4().hex
    ts = taken_at or datetime.now(timezone.utc).isoformat()

    root = state.get("values", {}).get("root_module", {})
    resources = _collect_resources(root)

    rows: list[SnapshotRow] = []
    for res in resources:
        values = res.get("values") or {}
        csp = _detect_csp(res)
        resource_id = str(values.get("id") or values.get("arn") or res.get("address", ""))
        rows.append(SnapshotRow(
            snapshot_id=sid,
            project_id=project_id,
            csp=csp,
            region=_extract_region(values, csp),
            resource_type=_map_type(res.get("type", "")),
            resource_id=resource_id,
            config_json=json.dumps(_redact(values), default=str),
            classification=classification,
            tags_json=json.dumps(_extract_tags(values)),
            taken_at=ts,
        ))
    return rows
