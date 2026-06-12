# CUI // SP-CTI
"""Parse ``pulumi stack export`` JSON into SnapshotRow list.

Supports Pulumi deployment version 3 (the current stable format).
Internal Pulumi meta-resources (``pulumi:pulumi:*``, ``pulumi:providers:*``) and
component/non-custom resources are skipped — only custom resources with a
physical cloud ID are imported.

Usage::

    import json
    from tools.infra_canvas.importers.pulumi_state import parse

    with open("stack-export.json") as f:
        rows = parse(json.load(f), project_id="proj-govcloud-east")
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.infra_canvas.importers import SnapshotRow

# ── Type mapping: Pulumi resource type → IDC canonical type ──────────────────

_PULUMI_TYPE_MAP: dict[str, str] = {
    # AWS Compute
    "aws:ec2/instance:Instance": "aws-ec2",
    "aws:ec2/launchTemplate:LaunchTemplate": "aws-ec2",
    "aws:autoscaling/group:Group": "aws-ec2-asg",
    "aws:lightsail/instance:Instance": "aws-lightsail",
    # AWS Containers
    "aws:eks/cluster:Cluster": "aws-eks",
    "aws:ecs/cluster:Cluster": "aws-ecs",
    "aws:ecs/service:Service": "aws-ecs",
    "aws:ecr/repository:Repository": "aws-ecr",
    "aws:apprunner/service:Service": "aws-apprunner",
    # AWS Serverless
    "aws:lambda/function:Function": "aws-lambda",
    "aws:sfn/stateMachine:StateMachine": "aws-step-fn",
    # AWS Networking
    "aws:ec2/vpc:Vpc": "aws-vpc",
    "aws:ec2/subnet:Subnet": "aws-subnet",
    "aws:ec2/securityGroup:SecurityGroup": "aws-sg",
    "aws:ec2/natGateway:NatGateway": "aws-nat",
    "aws:ec2/internetGateway:InternetGateway": "aws-igw",
    "aws:ec2/routeTable:RouteTable": "aws-route-table",
    "aws:ec2/vpcEndpoint:VpcEndpoint": "aws-vpc-endpoint",
    "aws:lb/loadBalancer:LoadBalancer": "aws-elb",
    "aws:alb/loadBalancer:LoadBalancer": "aws-elb",
    "aws:wafv2/webAcl:WebAcl": "aws-waf",
    "aws:cloudfront/distribution:Distribution": "aws-cdn",
    "aws:apigateway/restApi:RestApi": "aws-apigw",
    "aws:apigatewayv2/api:Api": "aws-apigw",
    "aws:directconnect/connection:Connection": "aws-dx",
    # AWS Storage
    "aws:s3/bucket:Bucket": "aws-s3",
    "aws:s3/bucketV2:BucketV2": "aws-s3",
    "aws:ebs/volume:Volume": "aws-ebs",
    "aws:efs/fileSystem:FileSystem": "aws-efs",
    "aws:fsx/lustreFileSystem:LustreFileSystem": "aws-fsx",
    "aws:backup/plan:Plan": "aws-backup",
    # AWS Database
    "aws:rds/instance:Instance": "aws-rds",
    "aws:rds/cluster:Cluster": "aws-aurora",
    "aws:dynamodb/table:Table": "aws-dynamodb",
    "aws:elasticache/cluster:Cluster": "aws-elasticache",
    "aws:elasticache/replicationGroup:ReplicationGroup": "aws-elasticache",
    "aws:redshift/cluster:Cluster": "aws-redshift",
    "aws:docdb/cluster:Cluster": "aws-documentdb",
    "aws:neptune/cluster:Cluster": "aws-neptune",
    "aws:opensearch/domain:Domain": "aws-opensearch",
    # AWS IAM & Security
    "aws:iam/role:Role": "aws-iam",
    "aws:iam/user:User": "aws-iam",
    "aws:iam/policy:Policy": "aws-iam",
    "aws:iam/group:Group": "aws-iam",
    "aws:kms/key:Key": "aws-kms",
    "aws:cloudtrail/trail:Trail": "aws-cloudtrail",
    "aws:securityhub/account:Account": "aws-securityhub",
    "aws:guardduty/detector:Detector": "aws-guardduty",
    # AWS Messaging
    "aws:sqs/queue:Queue": "aws-sqs",
    "aws:sns/topic:Topic": "aws-sns",
    "aws:kinesis/stream:Stream": "aws-kinesis",
    "aws:msk/cluster:Cluster": "aws-msk",
    # Azure Compute
    "azure-native:compute:VirtualMachine": "az-vm",
    "azure-native:compute:VirtualMachineScaleSet": "az-vmss",
    "azure:compute/virtualMachine:VirtualMachine": "az-vm",
    # Azure Containers
    "azure-native:containerservice:ManagedCluster": "az-aks",
    "azure-native:containerregistry:Registry": "az-acr",
    "azure-native:containerinstance:ContainerGroup": "az-aci",
    "azure-native:app:ContainerApp": "az-aca",
    # Azure Networking
    "azure-native:network:VirtualNetwork": "az-vnet",
    "azure-native:network:Subnet": "az-subnet",
    "azure-native:network:NetworkSecurityGroup": "az-nsg",
    "azure-native:network:LoadBalancer": "az-lb",
    "azure-native:network:ApplicationGateway": "az-appgw",
    "azure-native:network:AzureFirewall": "az-fw",
    # Azure Storage
    "azure-native:storage:StorageAccount": "az-blob",
    "azure-native:storage:BlobContainer": "az-blob",
    "azure-native:compute:Disk": "az-disk",
    "azure-native:storage:FileShare": "az-files",
    "azure-native:recoveryservices:ProtectionPolicy": "az-backup",
    # Azure Database
    "azure-native:sql:Database": "az-sql",
    "azure-native:sql:Server": "az-sql",
    "azure-native:dbforpostgresql:FlexibleServer": "az-postgres",
    "azure-native:dbformysql:FlexibleServer": "az-mysql",
    "azure-native:cache:Redis": "az-redis",
    "azure-native:documentdb:DatabaseAccount": "az-cosmos",
    "azure-native:synapse:Workspace": "az-synapse",
    "azure-native:cognitiveservices:Account": "az-openai",
    # Azure IAM & Security
    "azure-native:keyvault:Vault": "az-keyvault",
    # GCP Compute
    "gcp:compute/instance:Instance": "gcp-gce",
    "gcp:compute/instanceGroupManager:InstanceGroupManager": "gcp-mig",
    # GCP Containers
    "gcp:container/cluster:Cluster": "gcp-gke",
    "gcp:artifactregistry/repository:Repository": "gcp-gar",
    "gcp:cloudrun/service:Service": "gcp-cloudrun",
    "gcp:cloudrunv2/service:Service": "gcp-cloudrun",
    # GCP Networking
    "gcp:compute/network:Network": "gcp-vpc",
    "gcp:compute/subnetwork:Subnetwork": "gcp-subnet",
    "gcp:compute/firewall:Firewall": "gcp-firewall",
    "gcp:compute/forwardingRule:ForwardingRule": "gcp-lb",
    # GCP Storage
    "gcp:storage/bucket:Bucket": "gcp-gcs",
    "gcp:compute/disk:Disk": "gcp-pd",
    "gcp:filestore/instance:Instance": "gcp-filestore",
    # GCP Database
    "gcp:sql/databaseInstance:DatabaseInstance": "gcp-cloudsql",
    "gcp:spanner/instance:Instance": "gcp-spanner",
    "gcp:bigtable/instance:Instance": "gcp-bigtable",
    # GCP IAM & Security
    "gcp:kms/keyRing:KeyRing": "gcp-kms",
    "gcp:kms/cryptoKey:CryptoKey": "gcp-kms",
    # OCI Compute
    "oci:Core/instance:Instance": "oci-compute",
    # OCI Containers
    "oci:ContainerEngine/cluster:Cluster": "oci-oke",
    "oci:Artifacts/containerRepository:ContainerRepository": "oci-ocir",
    # OCI Networking
    "oci:Core/vcn:Vcn": "oci-vcn",
    "oci:Core/subnet:Subnet": "oci-subnet",
    "oci:Core/securityList:SecurityList": "oci-sl",
    # OCI Storage
    "oci:ObjectStorage/bucket:Bucket": "oci-os",
    "oci:Core/bootVolume:BootVolume": "oci-bv",
    # OCI Database
    "oci:Database/autonomousDatabase:AutonomousDatabase": "oci-adb",
    # OCI IAM & Security
    "oci:Vault/vault:Vault": "oci-vault",
    "oci:KeyManagement/vault:Vault": "oci-vault",
}

# Pulumi type namespace prefix → CSP
_PULUMI_CSP_PREFIX: dict[str, str] = {
    "aws": "aws",
    "aws-native": "aws",
    "azure-native": "azure",
    "azure": "azure",
    "azuread": "azure",
    "gcp": "gcp",
    "google-native": "gcp",
    "oci": "oci",
    "ibm": "ibm",
}

# Known internal Pulumi type prefixes to skip
_PULUMI_SKIP_PREFIXES: tuple[str, ...] = (
    "pulumi:pulumi:",
    "pulumi:providers:",
)

_REDACT_KEYS: frozenset[str] = frozenset({
    "password", "secret_key", "private_key", "secret", "token",
    "access_key", "certificate", "private_pem", "key_pem",
    "client_secret", "primary_access_key", "secondary_access_key",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pulumi_csp(resource_type: str) -> str:
    prefix = resource_type.split(":")[0]
    return _PULUMI_CSP_PREFIX.get(prefix, "unknown")


def _idc_type(resource_type: str) -> str:
    return _PULUMI_TYPE_MAP.get(resource_type, f"unknown-{resource_type}")


def _extract_region(outputs: dict[str, Any], inputs: dict[str, Any]) -> str:
    for cfg in (outputs, inputs):
        for key in ("region", "location"):
            val = cfg.get(key)
            if isinstance(val, str) and val:
                return val
    # Parse from ARN in outputs then inputs
    for cfg in (outputs, inputs):
        arn = cfg.get("arn", "")
        if isinstance(arn, str) and arn.startswith("arn:"):
            parts = arn.split(":")
            if len(parts) >= 5 and parts[3]:
                return parts[3]
    return "unknown"


def _extract_tags(inputs: dict[str, Any], outputs: dict[str, Any]) -> dict[str, str]:
    for cfg in (inputs, outputs):
        tags = cfg.get("tags") or cfg.get("Tags") or cfg.get("labels")
        if isinstance(tags, dict):
            return {str(k): str(v) for k, v in tags.items()}
    return {}


def _build_config(inputs: dict[str, Any], outputs: dict[str, Any]) -> str:
    merged = {**inputs, **outputs}
    redacted = {
        k: "[REDACTED]" if k.lower() in _REDACT_KEYS else v
        for k, v in merged.items()
    }
    return json.dumps(redacted, default=str)


# ── Public API ────────────────────────────────────────────────────────────────

def parse(
    export: dict[str, Any],
    *,
    snapshot_id: str | None = None,
    project_id: str = "",
    classification: str = "CUI",
    taken_at: str | None = None,
) -> list[SnapshotRow]:
    """Parse ``pulumi stack export`` JSON into a list of SnapshotRow.

    Args:
        export: Parsed JSON from ``pulumi stack export --json``.
        snapshot_id: UUIDv4 hex for the batch; generated if omitted.
        project_id: ICDEV project identifier.
        classification: Default classification marking for all rows.
        taken_at: ISO 8601 UTC timestamp; defaults to manifest time or now.

    Returns:
        List of SnapshotRow, one per custom cloud resource.
    """
    sid = snapshot_id or uuid.uuid4().hex
    manifest = export.get("deployment", {}).get("manifest", {})
    ts = taken_at or manifest.get("time") or datetime.now(timezone.utc).isoformat()

    rows: list[SnapshotRow] = []
    for resource in export.get("deployment", {}).get("resources", []):
        rtype = resource.get("type", "")

        # Skip internal Pulumi meta-resources
        if any(rtype.startswith(skip) for skip in _PULUMI_SKIP_PREFIXES):
            continue
        # Skip component/logical resources (no physical cloud ID)
        if not resource.get("custom", True):
            continue

        inputs: dict[str, Any] = resource.get("inputs") or {}
        outputs: dict[str, Any] = resource.get("outputs") or {}

        resource_id = str(resource.get("id") or outputs.get("id") or resource.get("urn", ""))

        rows.append(SnapshotRow(
            snapshot_id=sid,
            project_id=project_id,
            csp=_pulumi_csp(rtype),
            region=_extract_region(outputs, inputs),
            resource_type=_idc_type(rtype),
            resource_id=resource_id,
            config_json=_build_config(inputs, outputs),
            classification=classification,
            tags_json=json.dumps(_extract_tags(inputs, outputs)),
            taken_at=ts,
        ))
    return rows
