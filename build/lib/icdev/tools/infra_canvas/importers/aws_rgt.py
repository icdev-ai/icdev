# CUI // SP-CTI
"""Parse AWS resourcegroupstaggingapi get-resources output into SnapshotRow list.

Uses boto3 when available; falls back to a deterministic stub for air-gap or
test environments where boto3 is not installed.

Usage::

    # Live fetch (requires boto3 + AWS credentials)
    from tools.infra_canvas.importers.aws_rgt import fetch_and_parse
    rows = fetch_and_parse(region="us-gov-west-1", project_id="proj-govcloud")

    # Parse pre-fetched API response
    from tools.infra_canvas.importers.aws_rgt import parse
    rows = parse(resource_tag_mappings, project_id="proj-govcloud")
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.infra_canvas.importers import SnapshotRow

# ── ARN service:resource-type → IDC canonical type ────────────────────────────

_ARN_TYPE_MAP: dict[str, str] = {
    # EC2 / Networking
    "ec2:instance": "aws-ec2",
    "ec2:security-group": "aws-sg",
    "ec2:vpc": "aws-vpc",
    "ec2:subnet": "aws-subnet",
    "ec2:internet-gateway": "aws-igw",
    "ec2:nat-gateway": "aws-nat",
    "ec2:route-table": "aws-route-table",
    "ec2:volume": "aws-ebs",
    # Containers
    "eks:cluster": "aws-eks",
    "ecs:cluster": "aws-ecs",
    "ecs:service": "aws-ecs",
    "ecr:repository": "aws-ecr",
    # Serverless
    "lambda:function": "aws-lambda",
    "states:stateMachine": "aws-step-fn",
    # Storage (S3 ARNs carry no resource-type prefix)
    "s3": "aws-s3",
    # Database
    "rds:db": "aws-rds",
    "rds:cluster": "aws-aurora",
    "dynamodb:table": "aws-dynamodb",
    "elasticache:cluster": "aws-elasticache",
    "elasticache:replicationgroup": "aws-elasticache",
    "redshift:cluster": "aws-redshift",
    # IAM & Security
    "iam:role": "aws-iam",
    "iam:user": "aws-iam",
    "iam:policy": "aws-iam",
    "kms:key": "aws-kms",
    "cloudtrail:trail": "aws-cloudtrail",
    # Load Balancing / CDN / API GW
    "elasticloadbalancing:loadbalancer": "aws-elb",
    "elasticloadbalancing:targetgroup": "aws-elb",
    "cloudfront:distribution": "aws-cdn",
    "apigateway:restapis": "aws-apigw",
    # Messaging
    "sqs": "aws-sqs",
    "sns": "aws-sns",
    "kinesis:stream": "aws-kinesis",
    # Observability
    "logs:log-group": "aws-cloudwatch",
}

# Deterministic stub returned when boto3 is unavailable (covers 3 resource types)
_STUB_RESOURCES: list[dict[str, Any]] = [
    {
        "ResourceARN": "arn:aws:ec2:us-gov-west-1:000000000000:instance/i-stub0ec2000000",
        "Tags": [
            {"Key": "Name", "Value": "stub-app-server"},
            {"Key": "Environment", "Value": "stub"},
        ],
    },
    {
        "ResourceARN": "arn:aws:s3:::stub-govcloud-data-bucket",
        "Tags": [
            {"Key": "Name", "Value": "stub-govcloud-data-bucket"},
            {"Key": "classification", "Value": "CUI"},
        ],
    },
    {
        "ResourceARN": "arn:aws:lambda:us-gov-west-1:000000000000:function:stub-data-processor",
        "Tags": [
            {"Key": "Team", "Value": "platform"},
            {"Key": "classification", "Value": "CUI"},
        ],
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_arn(arn: str) -> tuple[str, str, str]:
    """Return (service, resource_type, region) from an AWS ARN.

    Handles all three ARN resource formats:
      * type/id  — ``ec2:instance/i-xxx``
      * type:id  — ``lambda:function:my-fn``
      * bare-id  — ``s3:::bucket-name`` (S3, SQS queue names)
    """
    # arn:partition:service:region:account:resource
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return "unknown", "", ""
    service = parts[2].lower()
    region = parts[3]
    resource = parts[5]

    if "/" in resource:
        rtype = resource.split("/")[0].lower()
    elif ":" in resource:
        rtype = resource.split(":")[0].lower()
    else:
        rtype = ""  # S3 bucket name / SQS queue name — service alone identifies type

    return service, rtype, region


def _map_type(service: str, rtype: str) -> str:
    """Map (service, resource_type) to IDC canonical resource type."""
    key = f"{service}:{rtype}" if rtype else service
    if key in _ARN_TYPE_MAP:
        return _ARN_TYPE_MAP[key]
    for k, v in _ARN_TYPE_MAP.items():
        if key.startswith(k):
            return v
    return f"aws-{service}"


def _extract_resource_id(arn: str) -> str:
    """Extract the leaf resource ID (name or physical ID) from an ARN."""
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return arn
    resource = parts[5]
    if "/" in resource:
        return resource.split("/")[-1]
    if ":" in resource:
        return resource.split(":")[-1]
    return resource  # S3 bucket name, SQS queue name


def _tags_to_dict(tags: list[dict[str, str]]) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in tags if "Key" in t and "Value" in t}


# ── Public API ────────────────────────────────────────────────────────────────

def parse(
    resource_tag_mappings: list[dict[str, Any]],
    *,
    snapshot_id: str | None = None,
    project_id: str = "",
    classification: str = "CUI",
    taken_at: str | None = None,
) -> list[SnapshotRow]:
    """Parse AWS RGT ResourceTagMappingList into a list of SnapshotRow.

    Args:
        resource_tag_mappings: ``ResourceTagMappingList`` from get_resources.
        snapshot_id: UUIDv4 hex for the batch; generated if omitted.
        project_id: ICDEV project identifier.
        classification: Default classification marking for all rows.
        taken_at: ISO 8601 UTC timestamp; defaults to now.

    Returns:
        list[SnapshotRow], one per resource entry.
    """
    sid = snapshot_id or uuid.uuid4().hex
    ts = taken_at or datetime.now(timezone.utc).isoformat()

    rows: list[SnapshotRow] = []
    for entry in resource_tag_mappings:
        arn = entry.get("ResourceARN", "")
        if not arn:
            continue

        service, rtype, region = _parse_arn(arn)
        tags = _tags_to_dict(entry.get("Tags", []))

        rows.append(SnapshotRow(
            snapshot_id=sid,
            project_id=project_id,
            csp="aws",
            region=region or "global",
            resource_type=_map_type(service, rtype),
            resource_id=_extract_resource_id(arn),
            config_json=json.dumps({"arn": arn}),
            classification=classification,
            tags_json=json.dumps(tags),
            taken_at=ts,
        ))
    return rows


def fetch_and_parse(
    region: str = "us-east-1",
    *,
    snapshot_id: str | None = None,
    project_id: str = "",
    classification: str = "CUI",
) -> list[SnapshotRow]:
    """Fetch resources from AWS RGT API (or stub) and parse into SnapshotRows.

    Uses boto3 when available; falls back to _STUB_RESOURCES otherwise.
    Respects the AWS_PROFILE environment variable for credential selection.

    Args:
        region: AWS region to query.
        snapshot_id: UUIDv4 hex batch ID; generated if omitted.
        project_id: ICDEV project identifier.
        classification: Default classification marking.

    Returns:
        list[SnapshotRow].
    """
    profile = os.environ.get("AWS_PROFILE")
    mappings: list[dict[str, Any]]

    try:
        import boto3  # type: ignore[import]
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        client = session.client("resourcegroupstaggingapi", region_name=region)
        paginator = client.get_paginator("get_resources")
        mappings = []
        for page in paginator.paginate():
            mappings.extend(page.get("ResourceTagMappingList", []))
    except ImportError:
        mappings = list(_STUB_RESOURCES)

    return parse(
        mappings,
        snapshot_id=snapshot_id,
        project_id=project_id,
        classification=classification,
    )
