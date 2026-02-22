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

from tools.infra.terraform_generator import _render, _cui_header, _write


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


def generate(project_name: str = "icdev", environment: str = "production",
             region: str = "us-south", resource_group: str = "default",
             output_dir: str = ""):
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


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate IBM Cloud Terraform configurations"
    )
    parser.add_argument("--project-id", default="icdev", help="Project name")
    parser.add_argument("--environment", default="production",
                        help="Environment (production, staging, dev)")
    parser.add_argument("--region", default="us-south", help="IBM Cloud region")
    parser.add_argument("--resource-group", default="default",
                        help="IBM Cloud resource group")
    parser.add_argument("--output-dir", default="", help="Output directory")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="JSON output")
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
