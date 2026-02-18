#!/usr/bin/env python3
"""ICDEV SaaS -- AWS Account Provisioner.

CUI // SP-CTI

Creates dedicated AWS sub-accounts for IL5/IL6 tenants via AWS Organizations.
IL5 tenants get a GovCloud sub-account with VPC peering back to the platform.
IL6 tenants get a SIPR-isolated sub-account with NO VPC peering (air-gapped).

When boto3 is not available (local dev), the provisioner generates a YAML
execution plan describing every AWS resource that would be created.

Usage:
    # Provision a new AWS sub-account for a tenant
    python tools/saas/infra/account_provisioner.py --provision \\
        --slug acme-defense --il IL5 --email acme-defense@icdev.gov

    # Check account creation status
    python tools/saas/infra/account_provisioner.py --status \\
        --account-id 123456789012

    # Decommission an account
    python tools/saas/infra/account_provisioner.py --decommission \\
        --account-id 123456789012

    # Generate plan only (no AWS calls)
    python tools/saas/infra/account_provisioner.py --plan \\
        --slug acme-defense --il IL5 --email acme-defense@icdev.gov
"""

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = BASE_DIR / "data"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("account_provisioner")

# ---------------------------------------------------------------------------
# boto3 import (graceful fallback)
# ---------------------------------------------------------------------------
try:
    import boto3
    from botocore.exceptions import ClientError, WaiterError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    logger.warning(
        "boto3 not installed. Account provisioner will generate YAML plans "
        "instead of executing AWS API calls. Install with: pip install boto3")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_IMPACT_LEVELS = {"IL5", "IL6"}
GOVCLOUD_REGION = "us-gov-west-1"
PLATFORM_VPC_ID_ENV = "ICDEV_PLATFORM_VPC_ID"

# RDS configuration per impact level
RDS_CONFIG = {
    "IL5": {
        "engine": "postgres",
        "engine_version": "15.4",
        "instance_class": "db.r6g.large",
        "storage_encrypted": True,
        "multi_az": True,
        "backup_retention_period": 35,
        "deletion_protection": True,
        "storage_type": "gp3",
        "allocated_storage": 100,
    },
    "IL6": {
        "engine": "postgres",
        "engine_version": "15.4",
        "instance_class": "db.r6g.xlarge",
        "storage_encrypted": True,
        "multi_az": True,
        "backup_retention_period": 90,
        "deletion_protection": True,
        "storage_type": "io1",
        "allocated_storage": 200,
        "iops": 3000,
    },
}

# VPC CIDR allocation (simplified; production would use IPAM)
VPC_CIDR = "10.{octet2}.0.0/16"


# ============================================================================
# Plan Generation (always available, no AWS dependency)
# ============================================================================

def _generate_plan(tenant_slug, impact_level, email):
    """Generate a declarative plan describing resources to be provisioned.

    Returns:
        dict with plan details
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan_id = "plan-" + uuid.uuid4().hex[:12]

    # Deterministic CIDR based on slug hash
    slug_hash = hash(tenant_slug) % 200 + 10  # range 10-209
    cidr = VPC_CIDR.format(octet2=slug_hash)

    rds_config = RDS_CONFIG.get(impact_level, RDS_CONFIG["IL5"])
    is_air_gapped = impact_level == "IL6"

    plan = {
        "plan_id": plan_id,
        "tenant_slug": tenant_slug,
        "impact_level": impact_level,
        "email": email,
        "region": GOVCLOUD_REGION,
        "classification": "CUI // SP-CTI" if impact_level == "IL5" else "SECRET",
        "timestamp": now,
        "resources": [],
    }

    # 1. AWS Organizations sub-account
    plan["resources"].append({
        "type": "organizations:Account",
        "action": "CreateAccount",
        "properties": {
            "AccountName": "icdev-tenant-{}".format(tenant_slug),
            "Email": email,
            "RoleName": "ICDEVOrganizationRole",
            "IamUserAccessToBilling": "DENY",
        },
    })

    # 2. KMS key for tenant encryption
    plan["resources"].append({
        "type": "kms:Key",
        "action": "CreateKey",
        "properties": {
            "Description": "ICDEV tenant encryption key: {}".format(tenant_slug),
            "KeySpec": "SYMMETRIC_DEFAULT",
            "KeyUsage": "ENCRYPT_DECRYPT",
            "MultiRegion": False,
            "Tags": [
                {"TagKey": "icdev/tenant", "TagValue": tenant_slug},
                {"TagKey": "classification", "TagValue": "CUI"},
            ],
        },
    })

    # 3. VPC with private subnets
    plan["resources"].append({
        "type": "ec2:VPC",
        "action": "CreateVpc",
        "properties": {
            "CidrBlock": cidr,
            "EnableDnsSupport": True,
            "EnableDnsHostnames": True,
            "Tags": [
                {"Key": "Name", "Value": "icdev-tenant-{}-vpc".format(tenant_slug)},
                {"Key": "icdev/tenant", "Value": tenant_slug},
                {"Key": "classification", "Value": "CUI"},
            ],
        },
    })

    # 3a. Private subnets (2 AZs for Multi-AZ RDS)
    for az_suffix, subnet_cidr_offset in [("a", 0), ("b", 1)]:
        subnet_cidr = "10.{}.{}.0/24".format(slug_hash, subnet_cidr_offset)
        plan["resources"].append({
            "type": "ec2:Subnet",
            "action": "CreateSubnet",
            "properties": {
                "CidrBlock": subnet_cidr,
                "AvailabilityZone": "{region}{az}".format(
                    region=GOVCLOUD_REGION, az=az_suffix),
                "MapPublicIpOnLaunch": False,
                "Tags": [
                    {"Key": "Name", "Value": "icdev-tenant-{}-private-{}".format(
                        tenant_slug, az_suffix)},
                    {"Key": "icdev/tenant", "Value": tenant_slug},
                ],
            },
        })

    # 3b. DB subnet group
    plan["resources"].append({
        "type": "rds:DBSubnetGroup",
        "action": "CreateDBSubnetGroup",
        "properties": {
            "DBSubnetGroupName": "icdev-tenant-{}-db-subnet".format(tenant_slug),
            "DBSubnetGroupDescription": (
                "Private subnets for ICDEV tenant {} RDS".format(tenant_slug)),
            "SubnetIds": ["<subnet-a>", "<subnet-b>"],
        },
    })

    # 4. Security group for RDS
    plan["resources"].append({
        "type": "ec2:SecurityGroup",
        "action": "CreateSecurityGroup",
        "properties": {
            "GroupName": "icdev-tenant-{}-rds-sg".format(tenant_slug),
            "Description": "RDS access for tenant {}".format(tenant_slug),
            "VpcId": "<vpc-id>",
            "IngressRules": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "Description": "PostgreSQL from K8s pods",
                    "SourceSecurityGroupId": "<k8s-node-sg>",
                },
            ],
            "EgressRules": [],
        },
    })

    # 5. RDS PostgreSQL instance
    rds_props = {
        "DBInstanceIdentifier": "icdev-tenant-{}".format(tenant_slug),
        "DBName": "icdev_{}".format(tenant_slug.replace("-", "_")),
        "MasterUsername": "icdev_admin",
        "MasterUserPassword": "<from-secrets-manager>",
        "DBSubnetGroupName": "icdev-tenant-{}-db-subnet".format(tenant_slug),
        "VpcSecurityGroupIds": ["<rds-sg-id>"],
        "KmsKeyId": "<kms-key-arn>",
    }
    rds_props.update(rds_config)
    plan["resources"].append({
        "type": "rds:DBInstance",
        "action": "CreateDBInstance",
        "properties": rds_props,
    })

    # 6. Secrets Manager secret for RDS credentials
    plan["resources"].append({
        "type": "secretsmanager:Secret",
        "action": "CreateSecret",
        "properties": {
            "Name": "icdev/tenant/{}/rds-credentials".format(tenant_slug),
            "Description": "RDS credentials for tenant {}".format(tenant_slug),
            "KmsKeyId": "<kms-key-arn>",
            "GenerateSecretString": {
                "SecretStringTemplate": json.dumps({
                    "username": "icdev_admin"}),
                "GenerateStringKey": "password",
                "PasswordLength": 32,
                "ExcludeCharacters": "\"@/\\",
            },
        },
    })

    # 7. VPC peering (IL5 only, IL6 is air-gapped)
    if not is_air_gapped:
        plan["resources"].append({
            "type": "ec2:VPCPeeringConnection",
            "action": "CreateVpcPeeringConnection",
            "properties": {
                "VpcId": "<tenant-vpc-id>",
                "PeerVpcId": "<platform-vpc-id>",
                "PeerRegion": GOVCLOUD_REGION,
                "Tags": [
                    {"Key": "Name", "Value": "icdev-tenant-{}-peering".format(
                        tenant_slug)},
                    {"Key": "icdev/tenant", "Value": tenant_slug},
                ],
            },
        })
        plan["resources"].append({
            "type": "ec2:Route",
            "action": "CreateRoute",
            "properties": {
                "RouteTableId": "<tenant-route-table>",
                "DestinationCidrBlock": "<platform-vpc-cidr>",
                "VpcPeeringConnectionId": "<peering-connection-id>",
            },
        })
    else:
        plan["air_gapped"] = True
        plan["air_gapped_note"] = (
            "IL6/SECRET: No VPC peering. Tenant operates on SIPR in "
            "complete network isolation. Data exchange via approved "
            "cross-domain solution only.")

    return plan


def _plan_to_yaml(plan):
    """Convert a provisioning plan to human-readable YAML-like output.

    Returns:
        str: YAML-formatted plan
    """
    lines = [
        "# CUI // SP-CTI",
        "# ICDEV SaaS — AWS Account Provisioning Plan",
        "# Plan ID: {}".format(plan["plan_id"]),
        "# Tenant: {}".format(plan["tenant_slug"]),
        "# Impact Level: {}".format(plan["impact_level"]),
        "# Region: {}".format(plan["region"]),
        "# Generated: {}".format(plan["timestamp"]),
        "",
    ]

    if plan.get("air_gapped"):
        lines.append("# WARNING: IL6/SECRET — Air-gapped deployment.")
        lines.append("# {}".format(plan.get("air_gapped_note", "")))
        lines.append("")

    lines.append("resources:")
    for i, resource in enumerate(plan.get("resources", []), 1):
        lines.append("  # --- Resource {} ---".format(i))
        lines.append("  - type: {}".format(resource["type"]))
        lines.append("    action: {}".format(resource["action"]))
        lines.append("    properties:")
        for key, value in resource.get("properties", {}).items():
            if isinstance(value, (dict, list)):
                lines.append("      {}: {}".format(key, json.dumps(value)))
            elif isinstance(value, bool):
                lines.append("      {}: {}".format(key, str(value).lower()))
            else:
                lines.append("      {}: {}".format(key, value))
        lines.append("")

    return "\n".join(lines)


# ============================================================================
# AWS API Operations (require boto3)
# ============================================================================

def _get_organizations_client():
    """Get boto3 Organizations client."""
    if not BOTO3_AVAILABLE:
        raise ImportError(
            "boto3 is required for AWS operations. "
            "Install with: pip install boto3")
    return boto3.client("organizations", region_name=GOVCLOUD_REGION)


def _get_ec2_client(region=None):
    """Get boto3 EC2 client."""
    if not BOTO3_AVAILABLE:
        raise ImportError("boto3 is required.")
    return boto3.client("ec2", region_name=region or GOVCLOUD_REGION)


def _get_rds_client(region=None):
    """Get boto3 RDS client."""
    if not BOTO3_AVAILABLE:
        raise ImportError("boto3 is required.")
    return boto3.client("rds", region_name=region or GOVCLOUD_REGION)


def _get_kms_client(region=None):
    """Get boto3 KMS client."""
    if not BOTO3_AVAILABLE:
        raise ImportError("boto3 is required.")
    return boto3.client("kms", region_name=region or GOVCLOUD_REGION)


def _get_secretsmanager_client(region=None):
    """Get boto3 Secrets Manager client."""
    if not BOTO3_AVAILABLE:
        raise ImportError("boto3 is required.")
    return boto3.client("secretsmanager", region_name=region or GOVCLOUD_REGION)


def _create_sub_account(tenant_slug, email):
    """Create an AWS Organizations sub-account.

    Returns:
        dict with account_id, create_request_id, status
    """
    org_client = _get_organizations_client()

    try:
        response = org_client.create_account(
            Email=email,
            AccountName="icdev-tenant-{}".format(tenant_slug),
            RoleName="ICDEVOrganizationRole",
            IamUserAccessToBilling="DENY",
            Tags=[
                {"Key": "icdev/tenant", "Value": tenant_slug},
                {"Key": "classification", "Value": "CUI"},
                {"Key": "managed-by", "Value": "icdev-account-provisioner"},
            ],
        )

        create_status = response.get("CreateAccountStatus", {})
        return {
            "account_id": create_status.get("AccountId"),
            "create_request_id": create_status.get("Id"),
            "status": create_status.get("State", "UNKNOWN"),
        }
    except ClientError as exc:
        logger.error("Failed to create sub-account: %s", exc)
        raise


def _wait_for_account(create_request_id, max_wait=300, poll_interval=10):
    """Poll until account creation completes.

    Returns:
        dict with account_id and final status
    """
    org_client = _get_organizations_client()
    elapsed = 0

    while elapsed < max_wait:
        try:
            response = org_client.describe_create_account_status(
                CreateAccountRequestId=create_request_id)
            status = response.get("CreateAccountStatus", {})
            state = status.get("State", "UNKNOWN")

            if state == "SUCCEEDED":
                return {
                    "account_id": status.get("AccountId"),
                    "status": "SUCCEEDED",
                }
            elif state == "FAILED":
                return {
                    "account_id": None,
                    "status": "FAILED",
                    "failure_reason": status.get("FailureReason", "Unknown"),
                }

            logger.info(
                "Account creation in progress (%ds/%ds)...", elapsed, max_wait)
            time.sleep(poll_interval)
            elapsed += poll_interval

        except ClientError as exc:
            logger.error("Error polling account status: %s", exc)
            raise

    return {"account_id": None, "status": "TIMEOUT"}


def _create_kms_key(tenant_slug):
    """Create a per-tenant KMS key for encryption.

    Returns:
        str: KMS key ARN
    """
    kms_client = _get_kms_client()
    try:
        response = kms_client.create_key(
            Description="ICDEV tenant encryption key: {}".format(tenant_slug),
            KeySpec="SYMMETRIC_DEFAULT",
            KeyUsage="ENCRYPT_DECRYPT",
            Tags=[
                {"TagKey": "icdev/tenant", "TagValue": tenant_slug},
                {"TagKey": "classification", "TagValue": "CUI"},
            ],
        )
        key_arn = response["KeyMetadata"]["Arn"]

        # Create alias for easier reference
        kms_client.create_alias(
            AliasName="alias/icdev-tenant-{}".format(tenant_slug),
            TargetKeyId=key_arn,
        )

        return key_arn
    except ClientError as exc:
        logger.error("Failed to create KMS key: %s", exc)
        raise


# ============================================================================
# Public API
# ============================================================================

def provision_account(tenant_slug, impact_level, email):
    """Provision a dedicated AWS sub-account for an IL5/IL6 tenant.

    Creates:
      1. AWS Organizations sub-account
      2. VPC with private subnets
      3. Per-tenant KMS encryption key
      4. RDS PostgreSQL instance (encrypted with tenant KMS key)
      5. Secrets Manager entry for RDS credentials
      6. VPC peering to platform VPC (IL5 only; IL6 is air-gapped)

    Args:
        tenant_slug: URL-safe tenant identifier
        impact_level: IL5 or IL6
        email: Root email for the AWS sub-account

    Returns:
        dict with account_id, status, plan, and resource details
    """
    if impact_level not in VALID_IMPACT_LEVELS:
        raise ValueError(
            "Account provisioning is only for IL5/IL6. Got: {}".format(
                impact_level))
    if not email or "@" not in email:
        raise ValueError("Valid email required for AWS account creation.")
    if not tenant_slug or not tenant_slug.strip():
        raise ValueError("tenant_slug must be a non-empty string.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Always generate the plan
    plan = _generate_plan(tenant_slug, impact_level, email)

    if not BOTO3_AVAILABLE:
        logger.warning(
            "boto3 not available. Returning provisioning plan only.")
        plan_yaml = _plan_to_yaml(plan)
        return {
            "status": "plan_only",
            "message": (
                "boto3 not installed. Plan generated but not executed. "
                "Install boto3 and re-run to provision AWS resources."),
            "plan": plan,
            "plan_yaml": plan_yaml,
            "timestamp": now,
        }

    # Execute the plan via AWS APIs
    result = {
        "tenant_slug": tenant_slug,
        "impact_level": impact_level,
        "email": email,
        "status": "provisioning",
        "plan_id": plan["plan_id"],
        "timestamp": now,
        "resources_created": [],
    }

    try:
        # Step 1: Create sub-account
        logger.info("Creating AWS sub-account for tenant %s...", tenant_slug)
        account_result = _create_sub_account(tenant_slug, email)
        result["create_request_id"] = account_result["create_request_id"]

        if account_result["status"] == "IN_PROGRESS":
            logger.info("Waiting for account creation to complete...")
            account_result = _wait_for_account(
                account_result["create_request_id"])

        if account_result["status"] != "SUCCEEDED":
            result["status"] = "failed"
            result["error"] = (
                "Account creation {}: {}".format(
                    account_result["status"],
                    account_result.get("failure_reason", "Unknown")))
            return result

        result["account_id"] = account_result["account_id"]
        result["resources_created"].append({
            "type": "organizations:Account",
            "id": account_result["account_id"],
        })

        # Step 2: Create KMS key
        logger.info("Creating per-tenant KMS key...")
        kms_key_arn = _create_kms_key(tenant_slug)
        result["kms_key_arn"] = kms_key_arn
        result["resources_created"].append({
            "type": "kms:Key",
            "arn": kms_key_arn,
        })

        # Note: VPC, subnets, RDS, and VPC peering would be created here
        # using the respective boto3 clients. In production, this would
        # use Terraform via tools/infra/terraform_generator.py for
        # idempotent infrastructure management.
        logger.info(
            "Sub-account %s created. VPC, RDS, and networking resources "
            "should be provisioned via Terraform for production use.",
            account_result["account_id"])

        result["status"] = "partial"
        result["next_steps"] = [
            "Run Terraform to create VPC and subnets in sub-account",
            "Create RDS instance with per-tenant KMS encryption",
            "Configure VPC peering (IL5) or verify air-gap (IL6)",
            "Store RDS credentials in Secrets Manager",
            "Update tenant record with account_id and db_host",
        ]

        return result

    except (ClientError, ImportError) as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        logger.error("Account provisioning failed: %s", exc)
        return result


def get_account_status(account_id):
    """Get the status of an AWS sub-account.

    Args:
        account_id: AWS account ID (12-digit)

    Returns:
        dict with account details and status
    """
    if not account_id or len(str(account_id).strip()) < 12:
        raise ValueError("Valid 12-digit AWS account ID required.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not BOTO3_AVAILABLE:
        return {
            "account_id": account_id,
            "status": "unknown",
            "message": "boto3 not available — cannot query AWS.",
            "timestamp": now,
        }

    try:
        org_client = _get_organizations_client()
        response = org_client.describe_account(AccountId=str(account_id))
        account = response.get("Account", {})

        return {
            "account_id": account.get("Id"),
            "name": account.get("Name"),
            "email": account.get("Email"),
            "status": account.get("Status"),
            "arn": account.get("Arn"),
            "joined_method": account.get("JoinedMethod"),
            "joined_timestamp": str(account.get("JoinedTimestamp", "")),
            "timestamp": now,
        }
    except ClientError as exc:
        return {
            "account_id": account_id,
            "status": "error",
            "error": str(exc),
            "timestamp": now,
        }


def decommission_account(account_id):
    """Decommission an AWS sub-account.

    This does NOT delete the account (AWS does not support programmatic
    account deletion). Instead it:
      1. Removes the account from the organization
      2. Tags it as decommissioned
      3. Returns next steps for manual cleanup

    Args:
        account_id: AWS account ID (12-digit)

    Returns:
        dict with decommission status and cleanup steps
    """
    if not account_id or len(str(account_id).strip()) < 12:
        raise ValueError("Valid 12-digit AWS account ID required.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not BOTO3_AVAILABLE:
        return {
            "account_id": account_id,
            "status": "plan_only",
            "message": "boto3 not available. Manual decommission required.",
            "steps": [
                "Tag account as decommissioned in AWS Console",
                "Delete all RDS instances (after final backup)",
                "Delete VPC peering connections",
                "Delete VPC and subnets",
                "Schedule KMS key deletion (30-day wait)",
                "Remove account from AWS Organization",
                "Close account via AWS Support (requires root email)",
            ],
            "timestamp": now,
        }

    try:
        org_client = _get_organizations_client()

        # Tag as decommissioned before removal
        try:
            org_client.tag_resource(
                ResourceId=str(account_id),
                Tags=[
                    {"Key": "icdev/status", "Value": "decommissioned"},
                    {"Key": "icdev/decommissioned-at", "Value": now},
                ],
            )
        except ClientError as tag_exc:
            logger.warning("Failed to tag account: %s", tag_exc)

        # Remove from organization
        try:
            org_client.remove_account_from_organization(
                AccountId=str(account_id))
            removal_status = "removed"
        except ClientError as remove_exc:
            error_code = remove_exc.response.get("Error", {}).get("Code", "")
            if error_code == "AccountNotFoundException":
                removal_status = "not_found"
            else:
                removal_status = "failed"
                logger.error(
                    "Failed to remove account from organization: %s",
                    remove_exc)

        return {
            "account_id": account_id,
            "status": "decommissioned",
            "removal_status": removal_status,
            "remaining_steps": [
                "Delete all RDS instances (verify final backup first)",
                "Delete VPC and networking resources",
                "Schedule KMS key deletion",
                "Close account via AWS Support",
            ],
            "timestamp": now,
        }

    except ClientError as exc:
        return {
            "account_id": account_id,
            "status": "error",
            "error": str(exc),
            "timestamp": now,
        }


# ============================================================================
# CLI
# ============================================================================

def _print_result(data, as_json=False):
    """Print result to stdout."""
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        for key, value in data.items():
            if key == "plan_yaml":
                print("\n--- Provisioning Plan (YAML) ---\n")
                print(value)
            elif key == "plan":
                # Skip raw plan dict when showing YAML
                continue
            elif isinstance(value, dict):
                print("  {}:".format(key))
                for k, v in value.items():
                    print("    {}: {}".format(k, v))
            elif isinstance(value, list):
                print("  {}:".format(key))
                for item in value:
                    if isinstance(item, dict):
                        print("    - {}".format(
                            ", ".join("{}: {}".format(k, v)
                                      for k, v in item.items())))
                    else:
                        print("    - {}".format(item))
            else:
                print("  {}: {}".format(key, value))


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI -- ICDEV SaaS AWS Account Provisioner",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--provision", action="store_true",
        help="Provision a new AWS sub-account for a tenant")
    action.add_argument(
        "--status", action="store_true",
        help="Check AWS account creation status")
    action.add_argument(
        "--decommission", action="store_true",
        help="Decommission an AWS sub-account")
    action.add_argument(
        "--plan", action="store_true",
        help="Generate provisioning plan only (no AWS calls)")

    parser.add_argument(
        "--slug", type=str,
        help="Tenant slug (e.g., acme-defense)")
    parser.add_argument(
        "--il", type=str, default="IL5",
        help="Impact level: IL5 or IL6 (default: IL5)")
    parser.add_argument(
        "--email", type=str,
        help="Root email for the AWS sub-account")
    parser.add_argument(
        "--account-id", type=str,
        help="AWS account ID (12-digit, for --status/--decommission)")
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Output as JSON")

    args = parser.parse_args()

    try:
        if args.provision:
            if not args.slug or not args.email:
                parser.error("--provision requires --slug and --email")
            result = provision_account(
                args.slug, args.il.upper(), args.email)
            _print_result(result, args.as_json)

        elif args.status:
            if not args.account_id:
                parser.error("--status requires --account-id")
            result = get_account_status(args.account_id)
            _print_result(result, args.as_json)

        elif args.decommission:
            if not args.account_id:
                parser.error("--decommission requires --account-id")
            result = decommission_account(args.account_id)
            _print_result(result, args.as_json)

        elif args.plan:
            if not args.slug or not args.email:
                parser.error("--plan requires --slug and --email")
            il = args.il.upper()
            if il not in VALID_IMPACT_LEVELS:
                parser.error(
                    "Account provisioning is for IL5/IL6 only. Got: {}".format(il))
            plan = _generate_plan(args.slug, il, args.email)
            if args.as_json:
                print(json.dumps(plan, indent=2, default=str))
            else:
                print(_plan_to_yaml(plan))

    except ValueError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print("FATAL: {}".format(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
