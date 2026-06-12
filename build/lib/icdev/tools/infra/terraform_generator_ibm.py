#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate Terraform configurations for IBM Cloud deployments.
Produces provider.tf, variables.tf, outputs.tf, main.tf for
VPC, IKS/OpenShift, Databases for PostgreSQL, COS, and Key Protect —
all with CUI header comments."""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.infra.terraform_generator import _render, _cui_header, _write  # noqa: E402


# ---------------------------------------------------------------------------
# Base infrastructure
# ---------------------------------------------------------------------------
PROVIDER_TF = """\
{{ cui_header }}
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    ibm = {
      source  = "IBM-Cloud/ibm"
      version = "~> 1.60"
    }
  }

  backend "cos" {
    endpoints   = "https://s3.{{ region }}.cloud-object-storage.appdomain.cloud"
    bucket      = "{{ project_name }}-{{ environment }}-tfstate"
    key         = "terraform.tfstate"
    region      = "{{ region }}"
  }
}

provider "ibm" {
  ibmcloud_api_key = var.ibmcloud_api_key
  region           = var.region
}
"""

VARIABLES_TF = """\
{{ cui_header }}
# -------------------------------------------------------
# IBM Cloud Variables — {{ project_name }}
# -------------------------------------------------------
variable "ibmcloud_api_key" {
  description = "IBM Cloud API key"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "IBM Cloud region"
  type        = string
  default     = "{{ region }}"
}

variable "resource_group" {
  description = "IBM Cloud resource group name"
  type        = string
  default     = "{{ resource_group }}"
}

variable "project_name" {
  description = "Project identifier used in resource names"
  type        = string
  default     = "{{ project_name }}"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "{{ environment }}"
}

variable "classification" {
  description = "Data classification (CUI, SECRET)"
  type        = string
  default     = "CUI"
}

variable "cluster_worker_count" {
  description = "Number of worker nodes for IKS cluster"
  type        = number
  default     = 3
}

variable "cluster_flavor" {
  description = "Worker node machine type"
  type        = string
  default     = "bx2.4x16"
}

variable "db_plan" {
  description = "Databases for PostgreSQL plan"
  type        = string
  default     = "standard"
}

variable "db_memory_mb" {
  description = "PostgreSQL memory allocation (MB)"
  type        = number
  default     = 4096
}

variable "db_disk_mb" {
  description = "PostgreSQL disk allocation (MB)"
  type        = number
  default     = 10240
}
"""

MAIN_TF = """\
{{ cui_header }}
# -------------------------------------------------------
# IBM Cloud Resources — {{ project_name }}
# -------------------------------------------------------

# Resource Group data source
data "ibm_resource_group" "rg" {
  name = var.resource_group
}

# -------------------------------------------------------
# VPC
# -------------------------------------------------------
resource "ibm_is_vpc" "main" {
  name           = "$${var.project_name}-$${var.environment}-vpc"
  resource_group = data.ibm_resource_group.rg.id
}

resource "ibm_is_subnet" "app" {
  name            = "$${var.project_name}-$${var.environment}-app-subnet"
  vpc             = ibm_is_vpc.main.id
  zone            = "$${var.region}-1"
  total_ipv4_address_count = 256
  resource_group  = data.ibm_resource_group.rg.id
}

resource "ibm_is_subnet" "data" {
  name            = "$${var.project_name}-$${var.environment}-data-subnet"
  vpc             = ibm_is_vpc.main.id
  zone            = "$${var.region}-2"
  total_ipv4_address_count = 256
  resource_group  = data.ibm_resource_group.rg.id
}

# -------------------------------------------------------
# IKS Cluster (Kubernetes)
# -------------------------------------------------------
resource "ibm_container_vpc_cluster" "main" {
  name              = "$${var.project_name}-$${var.environment}-iks"
  vpc_id            = ibm_is_vpc.main.id
  flavor            = var.cluster_flavor
  worker_count      = var.cluster_worker_count
  resource_group_id = data.ibm_resource_group.rg.id
  kube_version      = "1.29"

  zones {
    name      = "$${var.region}-1"
    subnet_id = ibm_is_subnet.app.id
  }

  zones {
    name      = "$${var.region}-2"
    subnet_id = ibm_is_subnet.data.id
  }
}

# -------------------------------------------------------
# Databases for PostgreSQL
# -------------------------------------------------------
resource "ibm_database" "postgresql" {
  name              = "$${var.project_name}-$${var.environment}-pg"
  plan              = var.db_plan
  service           = "databases-for-postgresql"
  location          = var.region
  resource_group_id = data.ibm_resource_group.rg.id

  group {
    group_id = "member"
    memory { allocation_mb = var.db_memory_mb }
    disk   { allocation_mb = var.db_disk_mb }
  }

  # Encryption at rest with platform-managed keys (BYOK via Key Protect optional)
  key_protect_key = ibm_kms_key.db_key.crn
}

# -------------------------------------------------------
# Cloud Object Storage (COS)
# -------------------------------------------------------
resource "ibm_resource_instance" "cos" {
  name              = "$${var.project_name}-$${var.environment}-cos"
  service           = "cloud-object-storage"
  plan              = "standard"
  location          = "global"
  resource_group_id = data.ibm_resource_group.rg.id
}

resource "ibm_cos_bucket" "artifacts" {
  bucket_name          = "$${var.project_name}-$${var.environment}-artifacts"
  resource_instance_id = ibm_resource_instance.cos.id
  region_location      = var.region
  storage_class        = "smart"

  activity_tracking {
    read_data_events  = true
    write_data_events = true
  }

  metrics_monitoring {
    usage_metrics_enabled   = true
    request_metrics_enabled = true
  }
}

# -------------------------------------------------------
# Key Protect (KMS)
# -------------------------------------------------------
resource "ibm_resource_instance" "key_protect" {
  name              = "$${var.project_name}-$${var.environment}-kp"
  service           = "kms"
  plan              = "tiered-pricing"
  location          = var.region
  resource_group_id = data.ibm_resource_group.rg.id
}

resource "ibm_kms_key" "master" {
  instance_id  = ibm_resource_instance.key_protect.guid
  key_name     = "$${var.project_name}-$${var.environment}-master-key"
  standard_key = false
}

resource "ibm_kms_key" "db_key" {
  instance_id  = ibm_resource_instance.key_protect.guid
  key_name     = "$${var.project_name}-$${var.environment}-db-key"
  standard_key = false
}
"""

OUTPUTS_TF = """\
{{ cui_header }}
# -------------------------------------------------------
# IBM Cloud Outputs — {{ project_name }}
# -------------------------------------------------------
output "vpc_id" {
  description = "VPC ID"
  value       = ibm_is_vpc.main.id
}

output "cluster_id" {
  description = "IKS cluster ID"
  value       = ibm_container_vpc_cluster.main.id
}

output "cluster_endpoint" {
  description = "IKS cluster API endpoint"
  value       = ibm_container_vpc_cluster.main.public_service_endpoint_url
}

output "database_crn" {
  description = "PostgreSQL instance CRN"
  value       = ibm_database.postgresql.id
}

output "cos_crn" {
  description = "COS instance CRN"
  value       = ibm_resource_instance.cos.id
}

output "cos_bucket" {
  description = "Artifacts bucket name"
  value       = ibm_cos_bucket.artifacts.bucket_name
}

output "key_protect_crn" {
  description = "Key Protect instance CRN"
  value       = ibm_resource_instance.key_protect.id
}

output "master_key_id" {
  description = "Master encryption key ID"
  value       = ibm_kms_key.master.key_id
}
"""


def generate(
    project_name: str = "icdev",
    environment: str = "production",
    region: str = "us-south",
    resource_group: str = "default",
    output_dir: str = "",
):
    """Generate IBM Cloud Terraform configuration files."""
    out = Path(output_dir) if output_dir else Path.cwd() / "terraform" / "ibm"
    out.mkdir(parents=True, exist_ok=True)

    ctx = {
        "cui_header": _cui_header(),
        "project_name": project_name,
        "environment": environment,
        "region": region,
        "resource_group": resource_group,
    }

    _write(out / "provider.tf", _render(PROVIDER_TF, ctx))
    _write(out / "variables.tf", _render(VARIABLES_TF, ctx))
    _write(out / "main.tf", _render(MAIN_TF, ctx))
    _write(out / "outputs.tf", _render(OUTPUTS_TF, ctx))

    return {
        "status": "success",
        "output_dir": str(out),
        "files": ["provider.tf", "variables.tf", "main.tf", "outputs.tf"],
        "csp": "ibm",
        "region": region,
    }


# ---------------------------------------------------------------------------
# SCCA (Secure Cloud Computing Architecture) module — IBM Cloud
# ---------------------------------------------------------------------------
SCCA_IBM_MAIN = """\
{{ cui_header }}
# -------------------------------------------------------
# SCCA IBM Cloud — Management VPC, Workload VPC, Transit Gateway, Direct Link
# -------------------------------------------------------

# Resource Group data source
data "ibm_resource_group" "scca" {
  name = var.resource_group
}

# --- Management VPC ---

resource "ibm_is_vpc" "mgmt" {
  name           = "$${var.project_name}-scca-mgmt-vpc"
  resource_group = data.ibm_resource_group.scca.id

  tags = ["Classification:CUI", "ManagedBy:icdev", "Role:Management"]
}

resource "ibm_is_subnet" "mgmt_subnet" {
  name            = "$${var.project_name}-scca-mgmt-subnet"
  vpc             = ibm_is_vpc.mgmt.id
  zone            = "$${var.region}-1"
  total_ipv4_address_count = 256
  resource_group  = data.ibm_resource_group.scca.id
}

# --- Workload VPC ---

resource "ibm_is_vpc" "workload" {
  name           = "$${var.project_name}-scca-workload-vpc"
  resource_group = data.ibm_resource_group.scca.id

  tags = ["Classification:CUI", "ManagedBy:icdev", "Role:Workload"]
}

resource "ibm_is_subnet" "workload_subnet" {
  name            = "$${var.project_name}-scca-workload-subnet"
  vpc             = ibm_is_vpc.workload.id
  zone            = "$${var.region}-1"
  total_ipv4_address_count = 256
  resource_group  = data.ibm_resource_group.scca.id
}

# --- Transit Gateway ---

resource "ibm_tg_gateway" "scca" {
  name           = "$${var.project_name}-scca-tgw"
  location       = var.region
  global         = false
  resource_group = data.ibm_resource_group.scca.id
}

resource "ibm_tg_connection" "mgmt" {
  gateway      = ibm_tg_gateway.scca.id
  network_type = "vpc"
  name         = "mgmt-vpc-connection"
  network_id   = ibm_is_vpc.mgmt.resource_crn
}

resource "ibm_tg_connection" "workload" {
  gateway      = ibm_tg_gateway.scca.id
  network_type = "vpc"
  name         = "workload-vpc-connection"
  network_id   = ibm_is_vpc.workload.resource_crn
}

# --- Security Groups (deny-all default, allow-internal) ---

resource "ibm_is_security_group" "mgmt_deny_all" {
  name           = "$${var.project_name}-scca-mgmt-deny-all-sg"
  vpc            = ibm_is_vpc.mgmt.id
  resource_group = data.ibm_resource_group.scca.id
}

resource "ibm_is_security_group_rule" "mgmt_allow_internal_inbound" {
  group     = ibm_is_security_group.mgmt_deny_all.id
  direction = "inbound"
  remote    = ibm_is_vpc.mgmt.default_security_group_crn

  tcp {
    port_min = 1
    port_max = 65535
  }
}

resource "ibm_is_security_group" "workload_deny_all" {
  name           = "$${var.project_name}-scca-workload-deny-all-sg"
  vpc            = ibm_is_vpc.workload.id
  resource_group = data.ibm_resource_group.scca.id
}

resource "ibm_is_security_group_rule" "workload_allow_internal_inbound" {
  group     = ibm_is_security_group.workload_deny_all.id
  direction = "inbound"
  remote    = ibm_is_vpc.workload.default_security_group_crn

  tcp {
    port_min = 1
    port_max = 65535
  }
}

# --- Direct Link Gateway ---

resource "ibm_dl_gateway" "scca" {
  name                  = "$${var.project_name}-scca-dl"
  type                  = "dedicated"
  speed_mbps            = 1000
  bgp_asn               = 64999
  cross_connect_router  = "xcr01.dal09"
  location_name         = "dal09"
  global                = false
  resource_group        = data.ibm_resource_group.scca.id
}
"""

SCCA_IBM_SECURITY = """\
{{ cui_header }}
# -------------------------------------------------------
# SCCA IBM Cloud — Security (SCC, Key Protect, Activity Tracker)
# -------------------------------------------------------

# --- Security & Compliance Center ---

resource "ibm_scc_instance_settings" "scca" {
  event_notifications {
    instance_crn = ""
  }
  object_storage {
    bucket            = "$${var.project_name}-scca-scc-results"
    bucket_location   = var.region
    instance_crn      = ibm_resource_instance.cos_scca.crn
  }
}

resource "ibm_scc_profile_attachment" "dod_scc" {
  profile_id = "nist-800-53-rev5"
  name       = "$${var.project_name}-scca-scc-attachment"

  scope {
    environment = "ibm-cloud"

    properties {
      name  = "scope_id"
      value = data.ibm_resource_group.scca.id
    }
    properties {
      name  = "scope_type"
      value = "account.resource_group"
    }
  }

  schedule = "daily"
  status   = "enabled"
}

# --- COS for SCC results ---

resource "ibm_resource_instance" "cos_scca" {
  name              = "$${var.project_name}-scca-cos"
  service           = "cloud-object-storage"
  plan              = "standard"
  location          = "global"
  resource_group_id = data.ibm_resource_group.scca.id
}

# --- Key Protect ---

resource "ibm_resource_instance" "key_protect_scca" {
  name              = "$${var.project_name}-scca-kp"
  service           = "kms"
  plan              = "tiered-pricing"
  location          = var.region
  resource_group_id = data.ibm_resource_group.scca.id
}

resource "ibm_kms_key" "scca_root" {
  instance_id  = ibm_resource_instance.key_protect_scca.guid
  key_name     = "$${var.project_name}-scca-root-key"
  standard_key = false
}

# --- Activity Tracker ---

resource "ibm_resource_instance" "activity_tracker" {
  name              = "$${var.project_name}-scca-at"
  service           = "logdnaat"
  plan              = "30-day"
  location          = var.region
  resource_group_id = data.ibm_resource_group.scca.id

  parameters = {
    service_supertenant    = "activity-tracker"
    associated_logging_crn = ""
  }
}
"""

SCCA_IBM_VARIABLES = """\
{{ cui_header }}
# -------------------------------------------------------
# SCCA IBM Cloud — Variables
# -------------------------------------------------------
variable "ibmcloud_api_key" {
  description = "IBM Cloud API key"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "IBM Cloud region"
  type        = string
  default     = "us-south"
}

variable "resource_group" {
  description = "IBM Cloud resource group name"
  type        = string
  default     = "Default"
}

variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "il_level" {
  description = "DoD Impact Level (IL4, IL5, IL6)"
  type        = string
  default     = "IL4"

  validation {
    condition     = contains(["IL4", "IL5", "IL6"], var.il_level)
    error_message = "IL level must be IL4, IL5, or IL6."
  }
}
"""

SCCA_IBM_OUTPUTS = """\
{{ cui_header }}
# -------------------------------------------------------
# SCCA IBM Cloud — Outputs
# -------------------------------------------------------
output "mgmt_vpc_id" {
  description = "Management VPC ID"
  value       = ibm_is_vpc.mgmt.id
}

output "workload_vpc_id" {
  description = "Workload VPC ID"
  value       = ibm_is_vpc.workload.id
}

output "transit_gw_id" {
  description = "Transit Gateway ID"
  value       = ibm_tg_gateway.scca.id
}

output "key_protect_id" {
  description = "Key Protect instance ID"
  value       = ibm_resource_instance.key_protect_scca.id
}
"""


def generate_scca_ibm(project_path: str, project_config: dict = None) -> list:
    """Generate SCCA (Secure Cloud Computing Architecture) Terraform module for IBM Cloud.

    Produces terraform/ibm/modules/scca-ibm/ with main.tf (Management VPC,
    Workload VPC, Transit Gateway, Direct Link), security.tf, variables.tf,
    and outputs.tf.

    Args:
        project_path: Target project directory.
        project_config: Optional configuration dict.

    Returns:
        List of absolute file paths generated.
    """
    config = project_config or {}
    project_name = config.get("project_name", "icdev")
    tf_dir = Path(project_path) / "terraform" / "ibm" / "modules" / "scca-ibm"
    ctx = {"cui_header": _cui_header(), "project_name": project_name}

    files = []
    for name, template in [
        ("main.tf", SCCA_IBM_MAIN),
        ("security.tf", SCCA_IBM_SECURITY),
        ("variables.tf", SCCA_IBM_VARIABLES),
        ("outputs.tf", SCCA_IBM_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Security Baseline module — IBM Cloud
# ---------------------------------------------------------------------------
SECURITY_BASELINE_IBM_MAIN = """\
{{ cui_header }}
# -------------------------------------------------------
# IBM Cloud Security Baseline — SCC, Key Protect, Activity Tracker, Flow Logs
# -------------------------------------------------------

data "ibm_resource_group" "security_baseline" {
  name = var.resource_group
}

# --- Security & Compliance Center (SCC) ---

resource "ibm_scc_instance_settings" "baseline" {
  event_notifications {
    instance_crn = ""
  }
  object_storage {
    bucket            = "$${var.project_name}-scc-baseline-results"
    bucket_location   = var.region
    instance_crn      = ibm_resource_instance.cos_baseline.crn
  }
}

resource "ibm_scc_profile_attachment" "nist_800_53" {
  profile_id = "nist-800-53-rev5"
  name       = "$${var.project_name}-nist-800-53-attachment"

  scope {
    environment = "ibm-cloud"

    properties {
      name  = "scope_id"
      value = data.ibm_resource_group.security_baseline.id
    }
    properties {
      name  = "scope_type"
      value = "account.resource_group"
    }
  }

  schedule = "daily"
  status   = "enabled"
}

# --- COS for SCC results ---

resource "ibm_resource_instance" "cos_baseline" {
  name              = "$${var.project_name}-scc-baseline-cos"
  service           = "cloud-object-storage"
  plan              = "standard"
  location          = "global"
  resource_group_id = data.ibm_resource_group.security_baseline.id
}

# --- Key Protect + Root Key ---

resource "ibm_resource_instance" "key_protect_baseline" {
  name              = "$${var.project_name}-baseline-kp"
  service           = "kms"
  plan              = "tiered-pricing"
  location          = var.region
  resource_group_id = data.ibm_resource_group.security_baseline.id
}

resource "ibm_kms_key" "baseline_root" {
  instance_id  = ibm_resource_instance.key_protect_baseline.guid
  key_name     = "$${var.project_name}-baseline-root-key"
  standard_key = false
}

# --- Activity Tracker ---

resource "ibm_resource_instance" "activity_tracker_baseline" {
  name              = "$${var.project_name}-baseline-at"
  service           = "logdnaat"
  plan              = "30-day"
  location          = var.region
  resource_group_id = data.ibm_resource_group.security_baseline.id

  parameters = {
    service_supertenant    = "activity-tracker"
    associated_logging_crn = ""
  }
}

# --- Flow Logs Collector ---

resource "ibm_is_flow_log" "baseline" {
  name           = "$${var.project_name}-baseline-flow-log"
  target         = ""
  active         = true
  storage_bucket = ibm_cos_bucket.flow_logs.s3_endpoint_direct
}

resource "ibm_cos_bucket" "flow_logs" {
  bucket_name          = "$${var.project_name}-baseline-flow-logs"
  resource_instance_id = ibm_resource_instance.cos_baseline.id
  region_location      = var.region
  storage_class        = "smart"

  activity_tracking {
    read_data_events  = true
    write_data_events = true
  }

  metrics_monitoring {
    usage_metrics_enabled   = true
    request_metrics_enabled = true
  }
}
"""

SECURITY_BASELINE_IBM_VARIABLES = """\
{{ cui_header }}
# -------------------------------------------------------
# IBM Cloud Security Baseline — Variables
# -------------------------------------------------------
variable "ibmcloud_api_key" {
  description = "IBM Cloud API key"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "IBM Cloud region"
  type        = string
  default     = "us-south"
}

variable "resource_group" {
  description = "IBM Cloud resource group name"
  type        = string
  default     = "Default"
}

variable "project_name" {
  description = "Project identifier"
  type        = string
}
"""

SECURITY_BASELINE_IBM_OUTPUTS = """\
{{ cui_header }}
# -------------------------------------------------------
# IBM Cloud Security Baseline — Outputs
# -------------------------------------------------------
output "scc_instance_id" {
  description = "Security & Compliance Center instance ID"
  value       = ibm_scc_profile_attachment.nist_800_53.id
}

output "key_protect_id" {
  description = "Key Protect instance ID"
  value       = ibm_resource_instance.key_protect_baseline.id
}
"""


def generate_security_baseline_ibm(project_path: str, project_config: dict = None) -> list:
    """Generate IBM Cloud Security Baseline Terraform module.

    Produces terraform/ibm/modules/ibm-security-baseline/ with main.tf (SCC
    instance + NIST 800-53 profile attachment, Key Protect + root key, Activity
    Tracker, Flow Logs collector), variables.tf, and outputs.tf.

    Args:
        project_path: Target project directory.
        project_config: Optional configuration dict.

    Returns:
        List of absolute file paths generated.
    """
    config = project_config or {}
    project_name = config.get("project_name", "icdev")
    tf_dir = Path(project_path) / "terraform" / "ibm" / "modules" / "ibm-security-baseline"
    ctx = {"cui_header": _cui_header(), "project_name": project_name}

    files = []
    for name, template in [
        ("main.tf", SECURITY_BASELINE_IBM_MAIN),
        ("variables.tf", SECURITY_BASELINE_IBM_VARIABLES),
        ("outputs.tf", SECURITY_BASELINE_IBM_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate IBM Cloud Terraform configurations")
    parser.add_argument("--project-id", default="icdev", help="Project name")
    parser.add_argument("--environment", default="production", help="Environment (production, staging, dev)")
    parser.add_argument("--region", default="us-south", help="IBM Cloud region")
    parser.add_argument("--resource-group", default="default", help="IBM Cloud resource group")
    parser.add_argument("--output-dir", default="", help="Output directory")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    import json

    result = generate(
        project_name=args.project_id,
        environment=args.environment,
        region=args.region,
        resource_group=args.resource_group,
        output_dir=args.output_dir,
    )

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Generated IBM Cloud Terraform in {result['output_dir']}")
        for f in result["files"]:
            print(f"  - {f}")


if __name__ == "__main__":
    run_cli()
