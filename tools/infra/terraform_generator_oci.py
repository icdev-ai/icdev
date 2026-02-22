#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate Terraform configurations for Oracle OCI Government Cloud deployments.
Produces provider.tf, variables.tf, outputs.tf, main.tf, and modules
for VCN, Autonomous DB (ATP), OCIR, and Vault — all with CUI header comments."""

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
    oci = {
      source  = "oracle/oci"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket                      = "{{ project_name }}-tf-state"
    key                         = "{{ environment }}/terraform.tfstate"
    region                      = "{{ region }}"
    endpoint                    = "https://{{ namespace }}.compat.objectstorage.{{ region }}.oraclegovcloud.com"
    encrypt                     = true
    skip_region_validation      = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    force_path_style            = true
  }
}

provider "oci" {
  region       = var.region
  tenancy_ocid = var.tenancy_ocid
}
"""

VARIABLES_TF = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
  default     = "{{ project_name }}"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "{{ environment }}"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "tenancy_ocid" {
  description = "OCI tenancy OCID"
  type        = string
}

variable "compartment_ocid" {
  description = "OCI compartment OCID for resource deployment"
  type        = string
}

variable "region" {
  description = "OCI Government Cloud region"
  type        = string
  default     = "us-langley-1"

  validation {
    condition     = contains(["us-langley-1", "us-luke-1"], var.region)
    error_message = "Region must be an OCI Government Cloud region: us-langley-1 or us-luke-1."
  }
}

variable "vcn_cidr" {
  description = "VCN CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "common_tags" {
  description = "Common freeform tags for all resources"
  type        = map(string)
  default     = {}
}
"""

OUTPUTS_TF = """\
{{ cui_header }}
output "vcn_id" {
  description = "VCN OCID"
  value       = module.vcn.vcn_id
}

output "subnet_ids" {
  description = "Private subnet OCIDs"
  value       = module.vcn.private_subnet_ids
}

output "autonomous_db_connection_string" {
  description = "Autonomous Database connection string"
  value       = module.autonomous_db.connection_string
  sensitive   = true
}

output "ocir_url" {
  description = "OCI Container Image Registry URL"
  value       = module.ocir.registry_url
}

output "vault_id" {
  description = "OCI Vault OCID"
  value       = module.vault.vault_id
}
"""

MAIN_TF = """\
{{ cui_header }}
module "vcn" {
  source = "./modules/vcn"

  project_name     = var.project_name
  environment      = var.environment
  compartment_ocid = var.compartment_ocid
  vcn_cidr         = var.vcn_cidr
  common_tags      = var.common_tags
}

module "autonomous_db" {
  source = "./modules/autonomous_db"

  project_name     = var.project_name
  environment      = var.environment
  compartment_ocid = var.compartment_ocid
  subnet_id        = module.vcn.private_subnet_ids[0]
  nsg_id           = module.vcn.db_nsg_id
  common_tags      = var.common_tags
}

module "ocir" {
  source = "./modules/ocir"

  project_name     = var.project_name
  environment      = var.environment
  compartment_ocid = var.compartment_ocid
  common_tags      = var.common_tags
}

module "vault" {
  source = "./modules/vault"

  project_name     = var.project_name
  environment      = var.environment
  compartment_ocid = var.compartment_ocid
  common_tags      = var.common_tags
}
"""


def generate_base(project_path: str, project_config: dict = None) -> list:
    """Generate provider.tf, variables.tf, outputs.tf, main.tf for OCI Gov."""
    config = project_config or {}
    project_name = config.get("project_name", "icdev-project")
    environment = config.get("environment", "dev")
    region = config.get("region", "us-langley-1")
    namespace = config.get("namespace", project_name)

    tf_dir = Path(project_path) / "terraform"
    ctx = {
        "cui_header": _cui_header(),
        "project_name": project_name,
        "environment": environment,
        "region": region,
        "namespace": namespace,
    }

    files = []
    for name, template in [
        ("provider.tf", PROVIDER_TF),
        ("variables.tf", VARIABLES_TF),
        ("outputs.tf", OUTPUTS_TF),
        ("main.tf", MAIN_TF),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))

    return files


# ---------------------------------------------------------------------------
# VCN module
# ---------------------------------------------------------------------------
VCN_MAIN = """\
{{ cui_header }}
resource "oci_core_vcn" "this" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "$${var.project_name}-$${var.environment}-vcn"
  dns_label      = replace(var.project_name, "-", "")

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
    Name           = "$${var.project_name}-$${var.environment}-vcn"
  })
}

# --- Private Subnets (3 subnets for HA) ---

resource "oci_core_subnet" "private" {
  count = 3

  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = cidrsubnet(var.vcn_cidr, 8, count.index + 1)
  display_name               = "$${var.project_name}-$${var.environment}-private-$${count.index + 1}"
  dns_label                  = "priv$${count.index + 1}"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.private.id]

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
    Tier           = "Private"
  })
}

# --- Route Table (private — no internet gateway) ---

resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "$${var.project_name}-$${var.environment}-private-rt"

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

# --- Service Gateway (OCI services access without internet) ---

data "oci_core_services" "all" {}

resource "oci_core_service_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "$${var.project_name}-$${var.environment}-sgw"

  services {
    service_id = data.oci_core_services.all.services[0].id
  }

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

# --- Security List (default deny — ingress blocked, egress to OCI services only) ---

resource "oci_core_security_list" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "$${var.project_name}-$${var.environment}-private-sl"

  # Default deny: no ingress rules
  # Egress: allow within VCN only
  egress_security_rules {
    destination      = var.vcn_cidr
    protocol         = "all"
    destination_type = "CIDR_BLOCK"
    description      = "Allow all traffic within VCN"
  }

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

# --- Network Security Group for DB access ---

resource "oci_core_network_security_group" "db" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "$${var.project_name}-$${var.environment}-db-nsg"

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
    Purpose        = "Autonomous Database access control"
  })
}

resource "oci_core_network_security_group_security_rule" "db_ingress" {
  network_security_group_id = oci_core_network_security_group.db.id
  direction                 = "INGRESS"
  protocol                  = "6"
  description               = "Allow TLS connections to Autonomous DB from VCN"
  source                    = var.vcn_cidr
  source_type               = "CIDR_BLOCK"
  stateless                 = false

  tcp_options {
    destination_port_range {
      min = 1522
      max = 1522
    }
  }
}

# --- VCN Flow Logs ---

resource "oci_logging_log_group" "flow_logs" {
  compartment_id = var.compartment_ocid
  display_name   = "$${var.project_name}-$${var.environment}-flow-logs"
  description    = "VCN flow logs for network audit (NIST AU)"

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

resource "oci_logging_log" "vcn_flow_log" {
  display_name = "$${var.project_name}-$${var.environment}-vcn-flow"
  log_group_id = oci_logging_log_group.flow_logs.id
  log_type     = "SERVICE"
  is_enabled   = true

  configuration {
    source {
      category    = "all"
      resource    = oci_core_vcn.this.id
      service     = "flowlogs"
      source_type = "OCISERVICE"
    }
  }

  retention_duration = 365

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}
"""

VCN_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "compartment_ocid" { type = string }
variable "vcn_cidr" { type = string; default = "10.0.0.0/16" }
variable "common_tags" { type = map(string); default = {} }
"""

VCN_OUTPUTS = """\
{{ cui_header }}
output "vcn_id" {
  description = "VCN OCID"
  value       = oci_core_vcn.this.id
}

output "private_subnet_ids" {
  description = "Private subnet OCIDs"
  value       = oci_core_subnet.private[*].id
}

output "vcn_cidr" {
  description = "VCN CIDR block"
  value       = oci_core_vcn.this.cidr_blocks[0]
}

output "db_nsg_id" {
  description = "Network Security Group OCID for database access"
  value       = oci_core_network_security_group.db.id
}

output "service_gateway_id" {
  description = "Service Gateway OCID"
  value       = oci_core_service_gateway.this.id
}
"""


def generate_vcn(project_path: str) -> list:
    """Generate OCI VCN Terraform module with 3 private subnets, default deny, flow logs."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "vcn"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", VCN_MAIN),
        ("variables.tf", VCN_VARIABLES),
        ("outputs.tf", VCN_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Autonomous DB module
# ---------------------------------------------------------------------------
AUTONOMOUS_DB_MAIN = """\
{{ cui_header }}
resource "oci_database_autonomous_database" "this" {
  compartment_id = var.compartment_ocid
  display_name   = "$${var.project_name}-$${var.environment}-adb"
  db_name        = replace("$${var.project_name}$${var.environment}", "-", "")

  # Autonomous Transaction Processing (ATP)
  db_workload = "OLTP"

  # Compute auto-scaling
  cpu_core_count                    = var.cpu_core_count
  is_auto_scaling_enabled           = true
  is_auto_scaling_for_storage_enabled = true
  data_storage_size_in_tbs          = var.data_storage_size_tbs

  # Private endpoint — no public access
  subnet_id          = var.subnet_id
  nsg_ids            = [var.nsg_id]
  is_access_control_enabled = true

  # Mutual TLS required (FIPS 140-2 compliant)
  is_mtls_connection_required = true

  # Admin password from OCI Vault (set via variable — never hardcode)
  admin_password = var.admin_password

  # License model
  license_model = "BRING_YOUR_OWN_LICENSE"

  # Autonomous Data Guard for production
  is_local_data_guard_enabled = var.environment == "prod" ? true : false

  # Deletion protection for production
  is_auto_scaling_enabled = true

  freeform_tags = merge(var.common_tags, {
    Classification  = "CUI"
    ManagedBy       = "Terraform"
    Name            = "$${var.project_name}-$${var.environment}-adb"
    DataSensitivity = "High"
  })
}
"""

AUTONOMOUS_DB_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "compartment_ocid" { type = string }
variable "subnet_id" {
  type        = string
  description = "Private subnet OCID for Autonomous DB private endpoint"
}
variable "nsg_id" {
  type        = string
  description = "Network Security Group OCID for Autonomous DB"
}
variable "cpu_core_count" {
  type        = number
  default     = 2
  description = "Base OCPU count (auto-scales up to 3x)"
}
variable "data_storage_size_tbs" {
  type        = number
  default     = 1
  description = "Data storage size in terabytes (auto-scales)"
}
variable "admin_password" {
  type        = string
  sensitive   = true
  description = "Admin password for Autonomous Database (use OCI Vault)"
}
variable "common_tags" { type = map(string); default = {} }
"""

AUTONOMOUS_DB_OUTPUTS = """\
{{ cui_header }}
output "autonomous_db_id" {
  description = "Autonomous Database OCID"
  value       = oci_database_autonomous_database.this.id
}

output "connection_string" {
  description = "Autonomous Database mTLS connection string"
  value       = oci_database_autonomous_database.this.connection_strings[0].all_connection_strings["MEDIUM"]
  sensitive   = true
}

output "db_name" {
  description = "Autonomous Database name"
  value       = oci_database_autonomous_database.this.db_name
}

output "private_endpoint_ip" {
  description = "Private endpoint IP address"
  value       = oci_database_autonomous_database.this.private_endpoint_ip
}
"""


def generate_autonomous_db(project_path: str, config: dict = None) -> list:
    """Generate OCI Autonomous Database (ATP) Terraform module."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "autonomous_db"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", AUTONOMOUS_DB_MAIN),
        ("variables.tf", AUTONOMOUS_DB_VARIABLES),
        ("outputs.tf", AUTONOMOUS_DB_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# OCIR module
# ---------------------------------------------------------------------------
OCIR_MAIN = """\
{{ cui_header }}
resource "oci_artifacts_container_repository" "this" {
  compartment_id = var.compartment_ocid
  display_name   = "$${var.project_name}-$${var.environment}"
  is_immutable   = true
  is_public      = false
}

# --- Vulnerability Scanning ---

resource "oci_vulnerability_scanning_host_scan_recipe" "container_scan" {
  compartment_id = var.compartment_ocid
  display_name   = "$${var.project_name}-$${var.environment}-container-scan"

  port_settings {
    scan_level = "STANDARD"
  }

  agent_settings {
    scan_level = "STANDARD"

    agent_configuration {
      vendor = "OCI"
    }
  }

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
    Purpose        = "Container image vulnerability scanning"
  })
}

resource "oci_vulnerability_scanning_container_scan_recipe" "this" {
  compartment_id = var.compartment_ocid
  display_name   = "$${var.project_name}-$${var.environment}-ocir-scan"

  scan_settings {
    scan_level = "STANDARD"
  }

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

resource "oci_vulnerability_scanning_container_scan_target" "this" {
  compartment_id            = var.compartment_ocid
  container_scan_recipe_id  = oci_vulnerability_scanning_container_scan_recipe.this.id
  display_name              = "$${var.project_name}-$${var.environment}-ocir-target"

  target_registry {
    compartment_id = var.compartment_ocid
    type           = "OCIR"
    repositories   = [oci_artifacts_container_repository.this.display_name]
  }

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}
"""

OCIR_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "compartment_ocid" { type = string }
variable "common_tags" { type = map(string); default = {} }
"""

OCIR_OUTPUTS = """\
{{ cui_header }}
output "repository_id" {
  description = "Container Repository OCID"
  value       = oci_artifacts_container_repository.this.id
}

output "registry_url" {
  description = "OCIR registry URL"
  value       = "$${var.region}.ocir.io/$${data.oci_objectstorage_namespace.this.namespace}/$${oci_artifacts_container_repository.this.display_name}"
}

output "scan_recipe_id" {
  description = "Vulnerability scanning recipe OCID"
  value       = oci_vulnerability_scanning_container_scan_recipe.this.id
}
"""

OCIR_DATA = """\
{{ cui_header }}
data "oci_objectstorage_namespace" "this" {
  compartment_id = var.compartment_ocid
}
"""


def generate_ocir(project_path: str) -> list:
    """Generate OCI Container Image Registry Terraform module with vulnerability scanning."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "ocir"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", OCIR_MAIN),
        ("variables.tf", OCIR_VARIABLES),
        ("outputs.tf", OCIR_OUTPUTS),
        ("data.tf", OCIR_DATA),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Vault module
# ---------------------------------------------------------------------------
VAULT_MAIN = """\
{{ cui_header }}
resource "oci_kms_vault" "this" {
  compartment_id = var.compartment_ocid
  display_name   = "$${var.project_name}-$${var.environment}-vault"
  vault_type     = "VIRTUAL_PRIVATE"

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
    Name           = "$${var.project_name}-$${var.environment}-vault"
    Purpose        = "HSM-protected key management"
  })
}

# --- Master Encryption Key (HSM-protected, AES-256) ---

resource "oci_kms_key" "master" {
  compartment_id = var.compartment_ocid
  display_name   = "$${var.project_name}-$${var.environment}-master-key"
  management_endpoint = oci_kms_vault.this.management_endpoint

  key_shape {
    algorithm = "AES"
    length    = 32
  }

  protection_mode = "HSM"

  freeform_tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
    Purpose        = "Master encryption key — HSM protected"
    KeyType        = "AES-256"
  })
}

# --- Data Encryption Key for database TDE ---

resource "oci_kms_key" "data_encryption" {
  compartment_id = var.compartment_ocid
  display_name   = "$${var.project_name}-$${var.environment}-data-key"
  management_endpoint = oci_kms_vault.this.management_endpoint

  key_shape {
    algorithm = "AES"
    length    = 32
  }

  protection_mode = "HSM"

  freeform_tags = merge(var.common_tags, {
    Classification  = "CUI"
    ManagedBy       = "Terraform"
    Purpose         = "Data encryption key for database TDE"
    DataSensitivity = "High"
  })
}

# --- Secret for Autonomous DB admin password ---

resource "oci_vault_secret" "db_admin_password" {
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.this.id
  key_id         = oci_kms_key.master.id
  secret_name    = "$${var.project_name}-$${var.environment}-db-admin-password"

  secret_content {
    content_type = "BASE64"
    content      = base64encode(var.initial_db_password)
  }

  freeform_tags = merge(var.common_tags, {
    Classification  = "CUI"
    ManagedBy       = "Terraform"
    SecretType      = "DatabaseCredential"
    RotationEnabled = "true"
  })
}
"""

VAULT_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "compartment_ocid" { type = string }
variable "initial_db_password" {
  type        = string
  sensitive   = true
  default     = "ChangeMe_Immediately_1!"
  description = "Initial DB admin password stored in Vault (rotate immediately after provisioning)"
}
variable "common_tags" { type = map(string); default = {} }
"""

VAULT_OUTPUTS = """\
{{ cui_header }}
output "vault_id" {
  description = "OCI Vault OCID"
  value       = oci_kms_vault.this.id
}

output "master_key_id" {
  description = "Master encryption key OCID"
  value       = oci_kms_key.master.id
}

output "data_encryption_key_id" {
  description = "Data encryption key OCID"
  value       = oci_kms_key.data_encryption.id
}

output "management_endpoint" {
  description = "Vault management endpoint URL"
  value       = oci_kms_vault.this.management_endpoint
}

output "crypto_endpoint" {
  description = "Vault crypto endpoint URL"
  value       = oci_kms_vault.this.crypto_endpoint
}

output "db_admin_secret_id" {
  description = "DB admin password secret OCID"
  value       = oci_vault_secret.db_admin_password.id
  sensitive   = true
}
"""


def generate_vault(project_path: str) -> list:
    """Generate OCI Vault Terraform module with HSM-protected master encryption key."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "vault"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", VAULT_MAIN),
        ("variables.tf", VAULT_VARIABLES),
        ("outputs.tf", VAULT_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def generate(project_path: str, project_config: dict = None) -> list:
    """Generate complete OCI Government Cloud Terraform configuration.

    Generates provider.tf, variables.tf, outputs.tf, main.tf, and modules
    for VCN, Autonomous DB (ATP), OCIR, and Vault.

    Args:
        project_path: Target project directory.
        project_config: Optional dict with keys: project_name, environment,
            region, namespace, components.

    Returns:
        List of generated file paths.
    """
    config = project_config or {}
    components = config.get("components", ["base", "vcn", "autonomous_db", "ocir", "vault"])
    if isinstance(components, str):
        components = [c.strip() for c in components.split(",")]

    generators = {
        "base": lambda: generate_base(project_path, config),
        "vcn": lambda: generate_vcn(project_path),
        "autonomous_db": lambda: generate_autonomous_db(project_path, config),
        "ocir": lambda: generate_ocir(project_path),
        "vault": lambda: generate_vault(project_path),
    }

    all_files = []
    for comp in components:
        if comp in generators:
            files = generators[comp]()
            all_files.extend(files)

    return all_files


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Generate Terraform for Oracle OCI Government Cloud"
    )
    parser.add_argument("--project-path", required=True, help="Target project directory")
    parser.add_argument(
        "--components",
        default="base,vcn,autonomous_db,ocir,vault",
        help="Comma-separated components: base,vcn,autonomous_db,ocir,vault",
    )
    parser.add_argument(
        "--project-name", default="icdev-project", help="Project name for resource naming"
    )
    parser.add_argument(
        "--environment",
        default="dev",
        choices=["dev", "staging", "prod"],
        help="Target environment",
    )
    parser.add_argument(
        "--region",
        default="us-langley-1",
        choices=["us-langley-1", "us-luke-1"],
        help="OCI Government Cloud region",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="OCI Object Storage namespace (defaults to project-name)",
    )
    args = parser.parse_args()

    config = {
        "project_name": args.project_name,
        "environment": args.environment,
        "region": args.region,
        "namespace": args.namespace or args.project_name,
    }

    components = [c.strip() for c in args.components.split(",")]
    all_files = []

    generators = {
        "base": lambda: generate_base(args.project_path, config),
        "vcn": lambda: generate_vcn(args.project_path),
        "autonomous_db": lambda: generate_autonomous_db(args.project_path, config),
        "ocir": lambda: generate_ocir(args.project_path),
        "vault": lambda: generate_vault(args.project_path),
    }

    for comp in components:
        if comp in generators:
            files = generators[comp]()
            all_files.extend(files)
            print(f"[terraform-oci] Generated {comp}: {len(files)} files")
        else:
            print(f"[terraform-oci] Unknown component: {comp}")

    print(f"\n[terraform-oci] Total files generated: {len(all_files)}")
    for f in all_files:
        print(f"  -> {f}")


if __name__ == "__main__":
    main()
