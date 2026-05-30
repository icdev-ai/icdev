# CUI // SP-CTI — ICDEV Data Design Canvas Lake Zone IaC Exporter
"""Cloud-agnostic data lake zone IaC exporter for DDC graphs.

Given a DDC design with ent-datalake nodes, generates Terraform HCL for the
medallion (Landing → Raw → Curated → Consumption) zone pattern across all
six supported CSPs.

Returns
-------
dict[str, bytes]
    filename → bytes for ZIP download.
    Per-lake keys: "lake_<slug>_<csp>.tf" (aws/azure/gcp/oci/ibm/local)
    Shared keys  : "variables.tf", "zones.yaml", "README.md"

Stdlib + pyyaml only (no network/DB calls).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ── Zone definitions (medallion architecture) ─────────────────────────────────

ZONES = [
    {
        "id": "landing",
        "label": "Landing Zone",
        "description": "Raw ingestion — ephemeral, no transformation.",
        "retention_days": 7,
        "classification_floor": "FOUO",
        "versioning": False,
    },
    {
        "id": "raw",
        "label": "Raw Zone (Bronze)",
        "description": "Immutable copy, schema-on-read, Parquet/ORC.",
        "retention_days": 365,
        "classification_floor": "FOUO",
        "versioning": True,
    },
    {
        "id": "curated",
        "label": "Curated Zone (Silver)",
        "description": "Cleaned, validated, schema-on-write, Iceberg format.",
        "retention_days": 730,
        "classification_floor": "CUI",
        "versioning": True,
    },
    {
        "id": "consumption",
        "label": "Consumption Zone (Gold)",
        "description": "Aggregated, analytics-ready, Iceberg with column-level security.",
        "retention_days": 2555,
        "classification_floor": "CUI",
        "versioning": True,
    },
]

# ── Classification → IAM posture ──────────────────────────────────────────────

_CLASSIFICATION_IAM: dict[str, dict] = {
    "PUBLIC": {"mfa_required": False, "least_privilege": False, "audit_required": False},
    "FOUO":   {"mfa_required": False, "least_privilege": True,  "audit_required": True},
    "CUI":    {"mfa_required": True,  "least_privilege": True,  "audit_required": True},
    "SECRET": {"mfa_required": True,  "least_privilege": True,  "audit_required": True},
    "TS/SCI": {"mfa_required": True,  "least_privilege": True,  "audit_required": True},
}


def _parse_graph(graph_json) -> tuple[list, list, list]:
    if isinstance(graph_json, str):
        try:
            g = json.loads(graph_json)
        except json.JSONDecodeError:
            return [], [], []
    else:
        g = graph_json or {}
    return g.get("nodes", []), g.get("edges", []), g.get("boundaries", [])


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", text.lower().strip()).strip("-") or "lake"


def _tf_name(text: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", text.lower().strip()).strip("_") or "lake"


def _node_label(node: dict) -> str:
    return node.get("label") or node.get("id", "unknown")


def _get_iam(classification: str) -> dict:
    for key in ("TS/SCI", "SECRET", "CUI", "FOUO", "PUBLIC"):
        if key in classification.upper():
            return _CLASSIFICATION_IAM[key]
    return _CLASSIFICATION_IAM["CUI"]


def _storage_class_gcp(zone_id: str) -> str:
    return "NEARLINE" if zone_id in ("landing", "raw") else "STANDARD"


# ── AWS Terraform blocks ───────────────────────────────────────────────────────

def _aws_header(design_id: str, ts: str, classification: str) -> str:
    return f"""\
# CUI // SP-CTI — Lake Zone Infrastructure — AWS S3 + KMS + IAM + CloudTrail
# Design: {design_id}
# Generated: {ts}
# DO NOT EDIT — regenerate via DDC → Export → Lake Zones

terraform {{
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
  required_version = ">= 1.6.0"
}}

provider "aws" {{
  region = var.aws_region
}}

locals {{
  lake_tags = {{
    ManagedBy      = "icdev-ddc"
    DesignId       = "{design_id}"
    Classification = "{classification}"
    Architecture   = "medallion-lake"
  }}
}}

"""


def _aws_kms(tf_name: str, lake_label: str, classification: str) -> str:
    return f"""\
resource "aws_kms_key" "lake_{tf_name}_key" {{
  description             = "Lake {lake_label} CMK — {classification}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = local.lake_tags
}}

resource "aws_kms_alias" "lake_{tf_name}_key_alias" {{
  name          = "alias/lake-{tf_name}"
  target_key_id = aws_kms_key.lake_{tf_name}_key.key_id
}}

"""


def _aws_cloudtrail(tf_name: str) -> str:
    return f"""\
resource "aws_cloudtrail" "lake_{tf_name}_audit" {{
  name                          = "lake-{tf_name}-audit"
  s3_bucket_name                = var.audit_log_bucket
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  event_selector {{
    read_write_type           = "All"
    include_management_events = true
    data_resource {{
      type   = "AWS::S3::Object"
      values = ["arn:aws:s3:::"]
    }}
  }}
  tags = local.lake_tags
}}

"""


def _aws_zone_bucket(tf_name: str, zone: dict, bucket_name: str) -> str:
    zid = zone["id"]
    versioning = "Enabled" if zone["versioning"] else "Disabled"
    retention = zone["retention_days"]
    return f"""\
resource "aws_s3_bucket" "lake_{tf_name}_{zid}" {{
  bucket        = "{bucket_name}"
  force_destroy = false
  tags          = merge(local.lake_tags, {{ Zone = "{zone['label']}" }})
}}

resource "aws_s3_bucket_versioning" "lake_{tf_name}_{zid}_ver" {{
  bucket = aws_s3_bucket.lake_{tf_name}_{zid}.id
  versioning_configuration {{ status = "{versioning}" }}
}}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake_{tf_name}_{zid}_sse" {{
  bucket = aws_s3_bucket.lake_{tf_name}_{zid}.id
  rule {{
    apply_server_side_encryption_by_default {{
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.lake_{tf_name}_key.arn
    }}
  }}
}}

resource "aws_s3_bucket_lifecycle_configuration" "lake_{tf_name}_{zid}_lc" {{
  bucket = aws_s3_bucket.lake_{tf_name}_{zid}.id
  rule {{
    id     = "expire-{zid}"
    status = "Enabled"
    expiration {{ days = {retention} }}
    noncurrent_version_expiration {{ noncurrent_days = {retention} }}
  }}
}}

resource "aws_s3_bucket_public_access_block" "lake_{tf_name}_{zid}_pab" {{
  bucket                  = aws_s3_bucket.lake_{tf_name}_{zid}.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}}

"""


def _aws_zone_iam(tf_name: str, zone: dict, bucket_name: str, mfa_required: bool) -> str:
    zid = zone["id"]
    mfa_cond = (
        '\n          Condition = { Bool = { "aws:MultiFactorAuthPresent" = "true" } }'
        if mfa_required else ""
    )
    return f"""\
resource "aws_iam_policy" "lake_{tf_name}_{zid}_read" {{
  name   = "lake-{bucket_name}-read"
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:ListBucket"]
      Resource = [
        aws_s3_bucket.lake_{tf_name}_{zid}.arn,
        "${{aws_s3_bucket.lake_{tf_name}_{zid}.arn}}/*",
      ]{mfa_cond}
    }}]
  }})
  tags = local.lake_tags
}}

resource "aws_iam_policy" "lake_{tf_name}_{zid}_write" {{
  name   = "lake-{bucket_name}-write"
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
      Resource = "${{aws_s3_bucket.lake_{tf_name}_{zid}.arn}}/*"{mfa_cond}
    }}]
  }})
  tags = local.lake_tags
}}

"""


# ── Azure Terraform blocks ─────────────────────────────────────────────────────

def _azure_header(design_id: str, ts: str, classification: str) -> str:
    return f"""\
# CUI // SP-CTI — Lake Zone Infrastructure — Azure ADLS Gen2
# Design: {design_id}
# Generated: {ts}

terraform {{
  required_providers {{
    azurerm = {{
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }}
  }}
}}

provider "azurerm" {{
  features {{}}
}}

locals {{
  lake_tags = {{
    ManagedBy      = "icdev-ddc"
    DesignId       = "{design_id}"
    Classification = "{classification}"
    Architecture   = "medallion-lake"
  }}
}}

"""


def _azure_storage_account(tf_name: str, adls_name: str) -> str:
    return f"""\
resource "azurerm_storage_account" "lake_{tf_name}" {{
  name                     = "{adls_name}"
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "ZRS"
  account_kind             = "StorageV2"
  is_hns_enabled           = true
  min_tls_version          = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags = local.lake_tags
}}

"""


def _azure_zone(tf_name: str, zone: dict, adls_name: str, classification: str) -> str:
    zid = zone["id"]
    retention = zone["retention_days"]
    return f"""\
resource "azurerm_storage_data_lake_gen2_filesystem" "lake_{tf_name}_{zid}" {{
  name               = "{zid}"
  storage_account_id = azurerm_storage_account.lake_{tf_name}.id
  properties         = {{
    zone           = "{zone['label']}"
    classification = "{classification}"
  }}
}}

resource "azurerm_storage_management_policy" "lake_{tf_name}_{zid}_lc" {{
  storage_account_id = azurerm_storage_account.lake_{tf_name}.id
  rule {{
    name    = "expire-{zid}"
    enabled = true
    filters {{
      prefix_match = ["{zid}/"]
      blob_types   = ["blockBlob"]
    }}
    actions {{
      base_blob {{
        delete_after_days_since_modification_greater_than = {retention}
      }}
    }}
  }}
}}

"""


# ── GCP Terraform blocks ───────────────────────────────────────────────────────

def _gcp_header(design_id: str, ts: str, classification: str, tf_name: str) -> str:
    classification_label = classification.lower().replace("/", "_").replace(" ", "_")
    return f"""\
# CUI // SP-CTI — Lake Zone Infrastructure — GCP Cloud Storage + KMS
# Design: {design_id}
# Generated: {ts}

terraform {{
  required_providers {{
    google = {{
      source  = "hashicorp/google"
      version = "~> 5.0"
    }}
  }}
}}

provider "google" {{
  project = var.gcp_project_id
  region  = var.gcp_region
}}

locals {{
  lake_labels = {{
    managed_by     = "icdev-ddc"
    design_id      = "{design_id}"
    classification = "{classification_label}"
    architecture   = "medallion-lake"
  }}
}}

resource "google_kms_key_ring" "lake_{tf_name}" {{
  name     = "lake-{tf_name}-keyring"
  location = var.gcp_region
}}

resource "google_kms_crypto_key" "lake_{tf_name}" {{
  name            = "lake-{tf_name}-key"
  key_ring        = google_kms_key_ring.lake_{tf_name}.id
  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s"
}}

"""


def _gcp_zone(tf_name: str, zone: dict, bucket_name: str) -> str:
    zid = zone["id"]
    versioning = "true" if zone["versioning"] else "false"
    retention = zone["retention_days"]
    storage_class = _storage_class_gcp(zid)
    return f"""\
resource "google_storage_bucket" "lake_{tf_name}_{zid}" {{
  name          = "{bucket_name}"
  project       = var.gcp_project_id
  location      = var.gcp_region
  storage_class = "{storage_class}"
  uniform_bucket_level_access = true
  versioning {{ enabled = {versioning} }}
  lifecycle_rule {{
    condition {{ age = {retention} }}
    action    {{ type = "Delete" }}
  }}
  encryption {{
    default_kms_key_name = google_kms_crypto_key.lake_{tf_name}.id
  }}
  labels = local.lake_labels
}}

resource "google_storage_bucket_iam_binding" "lake_{tf_name}_{zid}_read" {{
  bucket  = google_storage_bucket.lake_{tf_name}_{zid}.name
  role    = "roles/storage.objectViewer"
  members = var.lake_{zid}_readers
}}

resource "google_storage_bucket_iam_binding" "lake_{tf_name}_{zid}_write" {{
  bucket  = google_storage_bucket.lake_{tf_name}_{zid}.name
  role    = "roles/storage.objectCreator"
  members = var.lake_{zid}_writers
}}

"""


# ── OCI Terraform blocks ───────────────────────────────────────────────────────

def _oci_header(design_id: str, ts: str) -> str:
    return f"""\
# CUI // SP-CTI — Lake Zone Infrastructure — OCI Object Storage
# Design: {design_id}
# Generated: {ts}

terraform {{
  required_providers {{
    oci = {{
      source  = "oracle/oci"
      version = "~> 5.0"
    }}
  }}
}}

"""


def _oci_zone(tf_name: str, zone: dict, bucket_name: str, classification: str) -> str:
    zid = zone["id"]
    retention = zone["retention_days"]
    versioning = "Enabled" if zone["versioning"] else "Disabled"
    return f"""\
resource "oci_objectstorage_bucket" "lake_{tf_name}_{zid}" {{
  compartment_id = var.oci_compartment_id
  namespace      = var.oci_namespace
  name           = "{bucket_name}"
  access_type    = "NoPublicAccess"
  versioning     = "{versioning}"
  defined_tags   = {{
    "icdev.zone"           = "{zone['label']}"
    "icdev.classification" = "{classification}"
  }}
}}

resource "oci_objectstorage_object_lifecycle_policy" "lake_{tf_name}_{zid}_lc" {{
  namespace = var.oci_namespace
  bucket    = oci_objectstorage_bucket.lake_{tf_name}_{zid}.name
  rules {{
    action     = "DELETE"
    is_enabled = true
    name       = "expire-{zid}"
    object_name_filter {{ inclusion_prefixes = ["{zid}/"] }}
    time_amount = {retention}
    time_unit   = "DAYS"
  }}
}}

"""


# ── IBM Terraform blocks ───────────────────────────────────────────────────────

def _ibm_header(design_id: str, ts: str) -> str:
    return f"""\
# CUI // SP-CTI — Lake Zone Infrastructure — IBM Cloud Object Storage
# Design: {design_id}
# Generated: {ts}

terraform {{
  required_providers {{
    ibm = {{
      source  = "IBM-Cloud/ibm"
      version = "~> 1.60"
    }}
  }}
}}

provider "ibm" {{
  region = var.ibm_region
}}

"""


def _ibm_cos_instance(tf_name: str, classification: str) -> str:
    return f"""\
resource "ibm_resource_instance" "lake_{tf_name}_cos" {{
  name              = "lake-{tf_name}-cos"
  service           = "cloud-object-storage"
  plan              = "standard"
  location          = "global"
  resource_group_id = var.ibm_resource_group_id
  tags              = ["icdev", "datalake", "{classification}"]
}}

"""


def _ibm_zone(tf_name: str, zone: dict, bucket_name: str) -> str:
    zid = zone["id"]
    retention = zone["retention_days"]
    return f"""\
resource "ibm_cos_bucket" "lake_{tf_name}_{zid}" {{
  bucket_name          = "{bucket_name}"
  resource_instance_id = ibm_resource_instance.lake_{tf_name}_cos.id
  region_location      = var.ibm_region
  storage_class        = "smart"
  expire_rule {{
    rule_id = "expire-{zid}"
    enable  = true
    days    = {retention}
    prefix  = "{zid}/"
  }}
}}

"""


# ── Variables ─────────────────────────────────────────────────────────────────

_TF_VARIABLES = """\
# variables.tf — Lake Zone Variables
# Generated by ICDEV™ DDC Lake Zone IaC Exporter

# --- AWS ---
variable "aws_region"       { default = "us-gov-west-1" }
variable "aws_account_id"   { description = "AWS Account ID for KMS policy" }
variable "audit_log_bucket" { default = "icdev-audit-logs" }

# --- Azure ---
variable "resource_group_name" { description = "Azure Resource Group name" }
variable "location"            { default = "usgovvirginia" }

# --- GCP ---
variable "gcp_project_id" { description = "GCP Project ID" }
variable "gcp_region"     { default = "us-east1" }

# --- OCI ---
variable "oci_compartment_id" { description = "OCI Compartment OCID" }
variable "oci_namespace"      { description = "OCI Object Storage namespace" }

# --- IBM ---
variable "ibm_region"            { default = "us-south" }
variable "ibm_resource_group_id" { description = "IBM Resource Group ID" }

# --- Lake IAM ---
variable "lake_landing_readers"     { default = [] }
variable "lake_landing_writers"     { default = [] }
variable "lake_raw_readers"         { default = [] }
variable "lake_raw_writers"         { default = [] }
variable "lake_curated_readers"     { default = [] }
variable "lake_curated_writers"     { default = [] }
variable "lake_consumption_readers" { default = [] }
variable "lake_consumption_writers" { default = [] }
"""


# ── Public API ────────────────────────────────────────────────────────────────

def export_lake_zones(
    name: str,
    graph_json,
    design_id: str | None = None,
    classification: str = "CUI",
) -> dict[str, bytes]:
    """Export lake zone IaC for all ent-datalake nodes in a DDC design.

    Parameters
    ----------
    name : str
        Design name (used for bucket/resource naming).
    graph_json : dict | str
        DDC graph JSON with nodes, edges, boundaries.
    design_id : str, optional
        Design UUID embedded in resource tags.
    classification : str
        Classification marking (e.g. "CUI", "SECRET").

    Returns
    -------
    dict[str, bytes]
        filename → bytes for ZIP construction.
    """
    nodes, edges, boundaries = _parse_graph(graph_json)
    ntypes = {n["id"]: n.get("type", "") for n in nodes}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    did = design_id or "unknown"

    lake_nodes = [n for n in nodes if ntypes.get(n["id"]) == "ent-datalake"]
    if not lake_nodes:
        return {
            "README.md": (
                f"# Lake Zone IaC — {name}\n\n"
                "No `ent-datalake` nodes found in this design.\n"
                "Add a **Data Lake** entity to your DDC canvas to generate lake zone IaC.\n"
            ).encode("utf-8")
        }

    files: dict[str, bytes] = {}
    zone_manifests: list[dict] = []
    iam = _get_iam(classification)

    for lake in lake_nodes:
        lake_label = _node_label(lake)
        tf_name = _tf_name(lake_label)
        lake_slug = _slug(lake_label)
        adls_name = re.sub(r"[^a-z0-9]", "", lake_slug)[:24]

        # AWS
        aws_parts = [_aws_header(did, ts, classification), _aws_kms(tf_name, lake_label, classification)]
        if iam["audit_required"]:
            aws_parts.append(_aws_cloudtrail(tf_name))

        # Azure
        azure_parts = [_azure_header(did, ts, classification), _azure_storage_account(tf_name, adls_name)]

        # GCP
        gcp_parts = [_gcp_header(did, ts, classification, tf_name)]

        # OCI
        oci_parts = [_oci_header(did, ts)]

        # IBM
        ibm_parts = [_ibm_header(did, ts), _ibm_cos_instance(tf_name, classification)]

        local_lines = [
            f"# Local dev lake: {lake_label}\n# Classification: {classification}\n# Generated: {ts}\n\n"
        ]
        manifest_zones: list[dict] = []

        for zone in ZONES:
            zid = zone["id"]
            bucket_name = f"{lake_slug}-{zid}"[:63]

            aws_parts.append(_aws_zone_bucket(tf_name, zone, bucket_name))
            aws_parts.append(_aws_zone_iam(tf_name, zone, bucket_name, iam["mfa_required"]))
            azure_parts.append(_azure_zone(tf_name, zone, adls_name, classification))
            gcp_parts.append(_gcp_zone(tf_name, zone, bucket_name))
            oci_parts.append(_oci_zone(tf_name, zone, bucket_name, classification))
            ibm_parts.append(_ibm_zone(tf_name, zone, bucket_name))

            local_path = f"/data/lake/{lake_slug}/{zid}"
            local_lines.append(
                f"# Zone: {zone['label']}\n"
                f"# Path: {local_path}\n"
                f"# Retention: {zone['retention_days']} days\n"
                f"# mkdir -p {local_path}\n\n"
            )

            manifest_zones.append({
                "id": zid,
                "label": zone["label"],
                "description": zone["description"],
                "paths": {
                    "aws":   f"s3://{bucket_name}/",
                    "azure": f"abfss://{zid}@{adls_name}.dfs.core.windows.net/",
                    "gcp":   f"gs://{bucket_name}/",
                    "oci":   f"oci://{bucket_name}/",
                    "ibm":   f"cos://{bucket_name}/",
                    "local": local_path,
                },
                "retention_days": zone["retention_days"],
                "classification_floor": zone["classification_floor"],
                "iam": {
                    "mfa_required":   iam["mfa_required"],
                    "least_privilege": iam["least_privilege"],
                    "audit_required": iam["audit_required"],
                },
            })

        zone_manifests.append({
            "lake": lake_label,
            "slug": lake_slug,
            "design_id": did,
            "classification": classification,
            "zones": manifest_zones,
        })

        prefix = f"lake_{tf_name}"
        files[f"{prefix}_aws.tf"]   = "".join(aws_parts).encode("utf-8")
        files[f"{prefix}_azure.tf"] = "".join(azure_parts).encode("utf-8")
        files[f"{prefix}_gcp.tf"]   = "".join(gcp_parts).encode("utf-8")
        files[f"{prefix}_oci.tf"]   = "".join(oci_parts).encode("utf-8")
        files[f"{prefix}_ibm.tf"]   = "".join(ibm_parts).encode("utf-8")
        files[f"{prefix}_local.txt"] = "".join(local_lines).encode("utf-8")

    # Shared variables
    files["variables.tf"] = _TF_VARIABLES.encode("utf-8")

    # Zone manifest YAML
    manifest: dict[str, Any] = {
        "apiVersion": "icdev/v1",
        "kind": "LakeZoneManifest",
        "metadata": {
            "design": name,
            "design_id": did,
            "classification": classification,
            "generated": ts,
            "generator": "ICDEV DDC Lake Zone IaC Exporter v1.0",
        },
        "spec": {"lakes": zone_manifests},
    }
    if _HAS_YAML:
        manifest_body = _yaml.dump(
            manifest, default_flow_style=False, allow_unicode=True,
            sort_keys=False, width=120,
        )
    else:
        manifest_body = json.dumps(manifest, indent=2, ensure_ascii=False)
    files["zones.yaml"] = f"# CUI // SP-CTI\n{manifest_body}".encode("utf-8")

    # README
    zone_table = "\n".join(
        f"| `{z['id']}` | {z['label']} | {z['retention_days']}d |" for z in ZONES
    )
    readme = (
        f"# Lake Zone IaC — {name}\n\n"
        f"**Design ID:** `{did}`  \n"
        f"**Classification:** {classification}  \n"
        f"**Generated:** {ts}  \n\n"
        f"## Zones\n\n"
        f"| Zone | Label | Retention |\n|------|-------|----------|\n"
        f"{zone_table}\n\n"
        "## Usage\n\n"
        "1. Choose CSP: use `lake_<name>_aws.tf`, `_azure.tf`, `_gcp.tf`, `_oci.tf`, or `_ibm.tf`\n"
        "2. Copy `variables.tf` alongside the chosen file and fill in `terraform.tfvars`\n"
        "3. `terraform init && terraform plan && terraform apply`\n\n"
        "See `zones.yaml` for storage paths and IAM posture per zone.\n"
    )
    files["README.md"] = readme.encode("utf-8")

    return files
