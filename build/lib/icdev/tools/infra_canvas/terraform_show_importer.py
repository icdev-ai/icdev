# CUI // SP-CTI
# Classification: CUI — Controlled Unclassified Information
"""IDC IaC Twin Phase 1 — Terraform Show / Plan Importer.

Parses the JSON output of:
  - `terraform show -json`   → import_terraform_show(data)
  - `terraform plan -json`   → import_terraform_plan(data)

Returns an IDC graph dict: {"nodes": [...], "edges": [...]}
Compatible with infra_engine.assess_infra_design().

NIST 800-53: CM-8 (Component Inventory), CM-3 (Config Change Control)
"""
import hashlib
from typing import Any

# ---------------------------------------------------------------------------
# Resource type → IDC node type mapping
# Ordered: most-specific prefix first.
# ---------------------------------------------------------------------------
_TF_TYPE_MAP: list[tuple[str, str]] = [
    # AWS Compute
    ("aws_instance", "aws-ec2"),
    ("aws_launch_template", "aws-ec2"),
    ("aws_launch_configuration", "aws-ec2"),
    ("aws_autoscaling_group", "aws-ec2-asg"),
    ("aws_spot_instance_request", "aws-ec2-spot"),
    ("aws_dedicated_host", "aws-ec2-dedicated"),
    ("aws_lightsail_instance", "aws-lightsail"),
    ("aws_batch_job_definition", "aws-batch"),
    # AWS Containers
    ("aws_eks_cluster", "aws-eks"),
    ("aws_ecs_cluster", "aws-ecs"),
    ("aws_ecs_service", "aws-ecs"),
    ("aws_ecs_task_definition", "aws-ecs"),
    ("aws_ecr_repository", "aws-ecr"),
    ("aws_apprunner_service", "aws-apprunner"),
    # AWS Storage
    ("aws_s3_bucket", "aws-s3"),
    ("aws_ebs_volume", "aws-ebs"),
    ("aws_efs_file_system", "aws-efs"),
    ("aws_fsx_", "aws-fsx"),
    ("aws_backup_", "aws-backup"),
    ("aws_storage_gateway_", "aws-storage-gw"),
    # AWS Databases
    ("aws_db_instance", "aws-rds"),
    ("aws_db_cluster", "aws-aurora"),
    ("aws_rds_cluster", "aws-aurora"),
    ("aws_dynamodb_table", "aws-dynamodb"),
    ("aws_redshift_cluster", "aws-redshift"),
    ("aws_elasticache_cluster", "aws-elasticache"),
    ("aws_elasticache_replication_group", "aws-elasticache"),
    ("aws_docdb_cluster", "aws-documentdb"),
    ("aws_neptune_cluster", "aws-neptune"),
    ("aws_timestream_table", "aws-timestream"),
    ("aws_keyspaces_table", "aws-keyspaces"),
    ("aws_opensearch_domain", "aws-opensearch"),
    # AWS Security / Identity
    ("aws_kms_key", "aws-kms"),
    ("aws_kms_alias", "aws-kms"),
    ("aws_iam_role", "aws-iam"),
    ("aws_iam_policy", "aws-iam"),
    ("aws_iam_user", "aws-iam"),
    ("aws_iam_group", "aws-iam"),
    ("aws_iam_instance_profile", "aws-iam"),
    ("aws_secretsmanager_secret", "aws-secrets"),
    ("aws_cognito_user_pool", "aws-cognito"),
    ("aws_cognito_identity_pool", "aws-cognito"),
    ("aws_config_config_rule", "aws-config"),
    ("aws_securityhub_", "aws-securityhub"),
    ("aws_guardduty_", "aws-securityhub"),
    # AWS Serverless / Messaging
    ("aws_lambda_function", "aws-lambda"),
    ("aws_sfn_state_machine", "aws-step-fn"),
    ("aws_cloudwatch_event_rule", "aws-eventbridge"),
    ("aws_cloudwatch_event_target", "aws-eventbridge"),
    ("aws_sqs_queue", "aws-sqs"),
    ("aws_sns_topic", "aws-sns"),
    ("aws_api_gateway_", "aws-apigw"),
    ("aws_apigatewayv2_", "aws-apigw"),
    # AWS Networking / LB
    ("aws_elb", "aws-elb"),
    ("aws_alb", "aws-elb"),
    ("aws_lb", "aws-elb"),
    ("aws_vpc", "aws-ec2"),
    ("aws_subnet", "aws-ec2"),
    ("aws_security_group", "aws-ec2"),
    # AWS AI/ML
    ("aws_sagemaker_", "aws-sagemaker"),
    ("aws_bedrock_", "aws-bedrock"),
    # IaC
    ("terraform_", "iac-terraform"),
    # Azure Compute
    ("azurerm_virtual_machine", "az-vm"),
    ("azurerm_linux_virtual_machine", "az-vm"),
    ("azurerm_windows_virtual_machine", "az-vm"),
    ("azurerm_virtual_machine_scale_set", "az-vmss"),
    ("azurerm_kubernetes_cluster", "az-aks"),
    ("azurerm_container_group", "az-aci"),
    ("azurerm_container_registry", "az-acr"),
    ("azurerm_container_app", "az-aca"),
    # Azure Storage
    ("azurerm_storage_account", "az-blob"),
    ("azurerm_managed_disk", "az-disk"),
    ("azurerm_storage_share", "az-files"),
    ("azurerm_netapp_account", "az-netapp"),
    ("azurerm_backup_", "az-backup"),
    # Azure Databases
    ("azurerm_sql_server", "az-sql"),
    ("azurerm_mssql_server", "az-sql"),
    ("azurerm_postgresql_flexible_server", "az-postgres"),
    ("azurerm_mysql_flexible_server", "az-mysql"),
    ("azurerm_cosmosdb_account", "az-cosmos"),
    ("azurerm_redis_cache", "az-redis"),
    # Azure Security / Identity
    ("azurerm_key_vault", "az-keyvault"),
    ("azurerm_active_directory_domain_service", "az-entra"),
    ("azurerm_security_center_", "az-defender"),
    ("azurerm_sentinel_", "az-sentinel-sec"),
    # GCP
    ("google_compute_instance", "gcp-gce"),
    ("google_compute_region_instance_group_manager", "gcp-mig"),
    ("google_container_cluster", "gcp-gke"),
    ("google_cloud_run_service", "gcp-cloudrun"),
    ("google_artifact_registry_repository", "gcp-gar"),
    ("google_storage_bucket", "gcp-gcs"),
    ("google_sql_database_instance", "gcp-cloudsql"),
    ("google_spanner_instance", "gcp-spanner"),
    ("google_bigtable_instance", "gcp-bigtable"),
    ("google_firestore_database", "gcp-firestore"),
    ("google_bigquery_dataset", "gcp-bigquery"),
    ("google_bigquery_table", "gcp-bigquery"),
    ("google_pubsub_topic", "gcp-pubsub"),
    ("google_cloudfunctions_function", "gcp-functions"),
    ("google_kms_crypto_key", "gcp-kms"),
    ("google_kms_key_ring", "gcp-kms"),
    ("google_secret_manager_secret", "gcp-secret"),
    ("google_security_center_", "gcp-scc"),
    # OCI
    ("oci_core_instance", "oci-compute"),
    ("oci_containerengine_cluster", "oci-oke"),
    ("oci_objectstorage_bucket", "oci-os"),
    ("oci_database_autonomous_database", "oci-adb"),
    ("oci_mysql_mysql_db_system", "oci-mysql"),
    ("oci_kms_key", "oci-vault"),
    ("oci_identity_", "oci-iam"),
    # Hashicorp / IaC tools
    ("vault_", "iac-vault"),
    ("helm_release", "iac-helm"),
    ("kubernetes_deployment", "op-k8s"),
    ("kubernetes_service", "op-k8s"),
    ("kubernetes_", "op-k8s"),
    ("helm_", "iac-helm"),
    ("argocd_", "iac-argocd"),
    ("flux_", "iac-flux"),
    ("crossplane_", "iac-crossplane"),
]

_GENERIC_NODE_TYPE = "op-server"  # fallback for unknown resource types


def _uid_from_address(address: str) -> str:
    """Deterministic short ID from Terraform resource address."""
    return hashlib.sha256(address.encode()).hexdigest()[:10]


def _map_tf_type(tf_type: str) -> str:
    """Map a Terraform resource type string to an IDC node type."""
    for prefix, idc_type in _TF_TYPE_MAP:
        if tf_type.startswith(prefix):
            return idc_type
    return _GENERIC_NODE_TYPE


def _build_node(address: str, tf_type: str, values: dict) -> dict:
    """Build an IDC graph node from a Terraform resource."""
    idc_type = _map_tf_type(tf_type)
    name = address.split(".")[-1] if "." in address else address
    label = f"{name} ({tf_type})"
    tags = values.get("tags") or {}
    return {
        "id": f"tf-{_uid_from_address(address)}",
        "type": idc_type,
        "label": label,
        "x": 0,
        "y": 0,
        "metadata": {
            "tf_address": address,
            "tf_type": tf_type,
            "tags": tags,
        },
    }


def import_terraform_show(data: dict) -> dict:
    """Parse `terraform show -json` output into an IDC graph.

    Args:
        data: Parsed JSON from `terraform show -json`.

    Returns:
        {"nodes": [...], "edges": []}  — edges are not inferrable from show output.
    """
    nodes: list[dict[str, Any]] = []
    resources = (
        data.get("values", {})
        .get("root_module", {})
        .get("resources", [])
    )
    for res in resources:
        address = res.get("address", "")
        tf_type = res.get("type", "")
        values = res.get("values", {}) or {}
        if not address or not tf_type:
            continue
        nodes.append(_build_node(address, tf_type, values))
    return {"nodes": nodes, "edges": []}


def import_terraform_plan(data: dict) -> dict:
    """Parse `terraform plan -json` output into an IDC graph.

    Only includes resources being created or updated (not deleted).

    Args:
        data: Parsed JSON from `terraform plan -json` or `terraform show -json <planfile>`.

    Returns:
        {"nodes": [...], "edges": []}
    """
    nodes: list[dict[str, Any]] = []
    resource_changes = data.get("resource_changes", [])
    for change in resource_changes:
        actions = change.get("change", {}).get("actions", [])
        # Skip delete-only changes
        if actions == ["delete"] or actions == ["no-op"]:
            continue
        # Skip if 'after' state is None (pure destroy)
        after = change.get("change", {}).get("after")
        if after is None:
            continue
        address = change.get("address", "")
        tf_type = change.get("type", "")
        if not address or not tf_type:
            continue
        nodes.append(_build_node(address, tf_type, after))
    return {"nodes": nodes, "edges": []}
