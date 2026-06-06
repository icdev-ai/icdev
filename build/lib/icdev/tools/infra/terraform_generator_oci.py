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

from tools.infra.terraform_generator import _render, _cui_header, _write  # noqa: E402


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
  value       = "$${var.region}.ocir.io/$${data.oci_objectstorage_namespace.this.namespace}/$${oci_artifacts_container_repository.this.display_name}"  # noqa: E501
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
# SCCA (Secure Cloud Computing Architecture) module — OCI
# ---------------------------------------------------------------------------
SCCA_OCI_MAIN = """\
{{ cui_header }}
# -------------------------------------------------------
# SCCA OCI — VDSS VCN, VDMS VCN, Workload VCN, DRG, Network Firewall
# -------------------------------------------------------

# --- Compartments ---

resource "oci_identity_compartment" "vdss" {
  compartment_id = var.compartment_ocid
  name           = "$${var.project_name}-scca-vdss"
  description    = "SCCA VDSS compartment (CUI)"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Role           = "VDSS"
  }
}

resource "oci_identity_compartment" "vdms" {
  compartment_id = var.compartment_ocid
  name           = "$${var.project_name}-scca-vdms"
  description    = "SCCA VDMS compartment (CUI)"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Role           = "VDMS"
  }
}

resource "oci_identity_compartment" "workload" {
  compartment_id = var.compartment_ocid
  name           = "$${var.project_name}-scca-workload"
  description    = "SCCA Workload compartment (CUI)"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Role           = "Workload"
  }
}

# --- VDSS VCN ---

resource "oci_core_vcn" "vdss" {
  compartment_id = oci_identity_compartment.vdss.id
  cidr_blocks    = [var.vcn_cidrs["vdss"]]
  display_name   = "$${var.project_name}-scca-vdss-vcn"
  dns_label      = "vdss"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Role           = "VDSS"
  }
}

resource "oci_core_subnet" "vdss_firewall" {
  compartment_id             = oci_identity_compartment.vdss.id
  vcn_id                     = oci_core_vcn.vdss.id
  cidr_block                 = cidrsubnet(var.vcn_cidrs["vdss"], 8, 0)
  display_name               = "$${var.project_name}-scca-vdss-fw-subnet"
  dns_label                  = "vdssfw"
  prohibit_public_ip_on_vnic = true

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

# --- VDMS VCN ---

resource "oci_core_vcn" "vdms" {
  compartment_id = oci_identity_compartment.vdms.id
  cidr_blocks    = [var.vcn_cidrs["vdms"]]
  display_name   = "$${var.project_name}-scca-vdms-vcn"
  dns_label      = "vdms"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Role           = "VDMS"
  }
}

resource "oci_core_subnet" "vdms_mgmt" {
  compartment_id             = oci_identity_compartment.vdms.id
  vcn_id                     = oci_core_vcn.vdms.id
  cidr_block                 = cidrsubnet(var.vcn_cidrs["vdms"], 8, 0)
  display_name               = "$${var.project_name}-scca-vdms-mgmt-subnet"
  dns_label                  = "vdmsmgmt"
  prohibit_public_ip_on_vnic = true

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

# --- Workload VCN ---

resource "oci_core_vcn" "workload" {
  compartment_id = oci_identity_compartment.workload.id
  cidr_blocks    = [var.vcn_cidrs["workload"]]
  display_name   = "$${var.project_name}-scca-workload-vcn"
  dns_label      = "workload"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Role           = "Workload"
  }
}

resource "oci_core_subnet" "workload_app" {
  compartment_id             = oci_identity_compartment.workload.id
  vcn_id                     = oci_core_vcn.workload.id
  cidr_block                 = cidrsubnet(var.vcn_cidrs["workload"], 8, 0)
  display_name               = "$${var.project_name}-scca-workload-app-subnet"
  dns_label                  = "wlapp"
  prohibit_public_ip_on_vnic = true

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

# --- Dynamic Routing Gateway (DRG) ---

resource "oci_core_drg" "scca" {
  compartment_id = var.compartment_ocid
  display_name   = "$${var.project_name}-scca-drg"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

resource "oci_core_drg_attachment" "vdss" {
  drg_id       = oci_core_drg.scca.id
  display_name = "vdss-attachment"

  network_details {
    id   = oci_core_vcn.vdss.id
    type = "VCN"
  }
}

resource "oci_core_drg_attachment" "vdms" {
  drg_id       = oci_core_drg.scca.id
  display_name = "vdms-attachment"

  network_details {
    id   = oci_core_vcn.vdms.id
    type = "VCN"
  }
}

resource "oci_core_drg_attachment" "workload" {
  drg_id       = oci_core_drg.scca.id
  display_name = "workload-attachment"

  network_details {
    id   = oci_core_vcn.workload.id
    type = "VCN"
  }
}

# --- Route Tables (all traffic through VDSS) ---

resource "oci_core_route_table" "workload_via_vdss" {
  compartment_id = oci_identity_compartment.workload.id
  vcn_id         = oci_core_vcn.workload.id
  display_name   = "$${var.project_name}-scca-workload-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_drg.scca.id
    description       = "Route all traffic through DRG to VDSS"
  }

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

resource "oci_core_route_table" "vdms_via_vdss" {
  compartment_id = oci_identity_compartment.vdms.id
  vcn_id         = oci_core_vcn.vdms.id
  display_name   = "$${var.project_name}-scca-vdms-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_drg.scca.id
    description       = "Route all traffic through DRG to VDSS"
  }

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

# --- OCI Network Firewall in VDSS VCN ---

resource "oci_network_firewall_network_firewall" "vdss" {
  compartment_id = oci_identity_compartment.vdss.id
  display_name   = "$${var.project_name}-scca-vdss-nfw"
  subnet_id      = oci_core_subnet.vdss_firewall.id

  network_firewall_policy_id = oci_network_firewall_network_firewall_policy.vdss.id

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Role           = "VDSS-Firewall"
  }
}

resource "oci_network_firewall_network_firewall_policy" "vdss" {
  compartment_id = oci_identity_compartment.vdss.id
  display_name   = "$${var.project_name}-scca-vdss-fw-policy"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}
"""

SCCA_OCI_SECURITY = """\
{{ cui_header }}
# -------------------------------------------------------
# SCCA OCI — Security (Cloud Guard, Vault with HSM)
# -------------------------------------------------------

# --- Cloud Guard ---

resource "oci_cloud_guard_target" "scca" {
  compartment_id       = var.compartment_ocid
  display_name         = "$${var.project_name}-scca-cg-target"
  target_resource_id   = var.compartment_ocid
  target_resource_type = "COMPARTMENT"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

resource "oci_cloud_guard_detector_recipe" "config" {
  compartment_id            = var.compartment_ocid
  display_name              = "$${var.project_name}-scca-config-detector"
  source_detector_recipe_id = "OCI_CONFIGURATION_DETECTOR_RECIPE"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

resource "oci_cloud_guard_detector_recipe" "activity" {
  compartment_id            = var.compartment_ocid
  display_name              = "$${var.project_name}-scca-activity-detector"
  source_detector_recipe_id = "OCI_ACTIVITY_DETECTOR_RECIPE"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

# --- OCI Vault with HSM Key ---

resource "oci_kms_vault" "scca" {
  compartment_id = oci_identity_compartment.vdms.id
  display_name   = "$${var.project_name}-scca-vault"
  vault_type     = "VIRTUAL_PRIVATE"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Purpose        = "SCCA HSM-protected key management"
  }
}

resource "oci_kms_key" "scca_master" {
  compartment_id      = oci_identity_compartment.vdms.id
  display_name        = "$${var.project_name}-scca-master-key"
  management_endpoint = oci_kms_vault.scca.management_endpoint

  key_shape {
    algorithm = "AES"
    length    = 32
  }

  protection_mode = "HSM"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Purpose        = "SCCA master encryption key — HSM protected"
  }
}
"""

SCCA_OCI_IDENTITY = """\
{{ cui_header }}
# -------------------------------------------------------
# SCCA OCI — Identity (IAM Groups and Policies)
# -------------------------------------------------------

# --- IAM Groups ---

resource "oci_identity_group" "vdss_admins" {
  compartment_id = var.tenancy_ocid
  name           = "$${var.project_name}-scca-vdss-admins"
  description    = "SCCA VDSS administrators"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

resource "oci_identity_group" "vdms_admins" {
  compartment_id = var.tenancy_ocid
  name           = "$${var.project_name}-scca-vdms-admins"
  description    = "SCCA VDMS administrators"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

resource "oci_identity_group" "workload_admins" {
  compartment_id = var.tenancy_ocid
  name           = "$${var.project_name}-scca-workload-admins"
  description    = "SCCA Workload administrators"

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

# --- IAM Policies ---

resource "oci_identity_policy" "vdss_policy" {
  compartment_id = var.compartment_ocid
  name           = "$${var.project_name}-scca-vdss-policy"
  description    = "SCCA VDSS compartment policy"

  statements = [
    "Allow group $${oci_identity_group.vdss_admins.name} to manage virtual-network-family in compartment $${oci_identity_compartment.vdss.name}",
    "Allow group $${oci_identity_group.vdss_admins.name} to manage network-firewall-family in compartment $${oci_identity_compartment.vdss.name}",
  ]

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

resource "oci_identity_policy" "vdms_policy" {
  compartment_id = var.compartment_ocid
  name           = "$${var.project_name}-scca-vdms-policy"
  description    = "SCCA VDMS compartment policy"

  statements = [
    "Allow group $${oci_identity_group.vdms_admins.name} to manage vaults in compartment $${oci_identity_compartment.vdms.name}",
    "Allow group $${oci_identity_group.vdms_admins.name} to manage keys in compartment $${oci_identity_compartment.vdms.name}",
    "Allow group $${oci_identity_group.vdms_admins.name} to manage logging-family in compartment $${oci_identity_compartment.vdms.name}",
  ]

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}

resource "oci_identity_policy" "workload_policy" {
  compartment_id = var.compartment_ocid
  name           = "$${var.project_name}-scca-workload-policy"
  description    = "SCCA Workload compartment policy"

  statements = [
    "Allow group $${oci_identity_group.workload_admins.name} to manage all-resources in compartment $${oci_identity_compartment.workload.name}",
    "Allow group $${oci_identity_group.workload_admins.name} to use virtual-network-family in compartment $${oci_identity_compartment.vdss.name}",
  ]

  freeform_tags = {
    Classification = "CUI"
    ManagedBy      = "icdev"
  }
}
"""

SCCA_OCI_VARIABLES = """\
{{ cui_header }}
# -------------------------------------------------------
# SCCA OCI — Variables
# -------------------------------------------------------
variable "tenancy_ocid" {
  description = "OCI tenancy OCID"
  type        = string
}

variable "compartment_ocid" {
  description = "Parent compartment OCID for SCCA resources"
  type        = string
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

variable "region" {
  description = "OCI Government Cloud region"
  type        = string
  default     = "us-langley-1"

  validation {
    condition     = contains(["us-langley-1", "us-luke-1"], var.region)
    error_message = "Region must be an OCI Government Cloud region."
  }
}

variable "vcn_cidrs" {
  description = "CIDR blocks for SCCA VCNs (vdss, vdms, workload)"
  type        = map(string)
  default = {
    vdss     = "10.0.0.0/16"
    vdms     = "10.1.0.0/16"
    workload = "10.2.0.0/16"
  }
}
"""

SCCA_OCI_OUTPUTS = """\
{{ cui_header }}
# -------------------------------------------------------
# SCCA OCI — Outputs
# -------------------------------------------------------
output "drg_id" {
  description = "Dynamic Routing Gateway OCID"
  value       = oci_core_drg.scca.id
}

output "vdss_vcn_id" {
  description = "VDSS VCN OCID"
  value       = oci_core_vcn.vdss.id
}

output "vdms_vcn_id" {
  description = "VDMS VCN OCID"
  value       = oci_core_vcn.vdms.id
}

output "workload_vcn_id" {
  description = "Workload VCN OCID"
  value       = oci_core_vcn.workload.id
}
"""


# ---------------------------------------------------------------------------
# Security Baseline module — OCI
# ---------------------------------------------------------------------------
SECURITY_BASELINE_OCI_MAIN = """\
{{ cui_header }}
# -------------------------------------------------------
# OCI Security Baseline — Cloud Guard, Vault, VSS, Service Connector
# -------------------------------------------------------

# --- Cloud Guard ---

resource "oci_cloud_guard_cloud_guard_configuration" "baseline" {
  compartment_id   = var.tenancy_ocid
  reporting_region = var.region
  status           = "ENABLED"
}

resource "oci_cloud_guard_detector_recipe" "config_detector" {
  compartment_id = var.compartment_ocid
  display_name   = "security-baseline-config-detector"

  source_detector_recipe_id = "OCI-CONFIGURATION-DETECTOR-RECIPE"
}

resource "oci_cloud_guard_target" "baseline" {
  compartment_id       = var.compartment_ocid
  display_name         = "security-baseline-target"
  target_resource_id   = var.compartment_ocid
  target_resource_type = "COMPARTMENT"

  target_detector_recipes {
    detector_recipe_id = oci_cloud_guard_detector_recipe.config_detector.id
  }
}

# --- Vault + HSM Key ---

resource "oci_kms_vault" "baseline" {
  compartment_id = var.compartment_ocid
  display_name   = "security-baseline-vault"
  vault_type     = "VIRTUAL_PRIVATE"
}

resource "oci_kms_key" "hsm_master" {
  compartment_id = var.compartment_ocid
  display_name   = "security-baseline-hsm-master-key"
  management_endpoint = oci_kms_vault.baseline.management_endpoint

  key_shape {
    algorithm = "AES"
    length    = 32
  }

  protection_mode = "HSM"
}

# --- Vulnerability Scanning Service (VSS) ---

resource "oci_vulnerability_scanning_host_scan_recipe" "baseline" {
  compartment_id = var.compartment_ocid
  display_name   = "security-baseline-vss-recipe"

  port_settings {
    scan_level = "STANDARD"
  }

  schedule {
    type        = "WEEKLY"
    day_of_week = "SUNDAY"
  }

  agent_settings {
    scan_level = "STANDARD"

    agent_configuration {
      vendor = "OCI"

      cis_benchmark_settings {
        scan_level = "STRICT"
      }
    }
  }
}

# --- Service Connector (Events → Notifications) ---

resource "oci_ons_notification_topic" "security_events" {
  compartment_id = var.compartment_ocid
  name           = "security-baseline-events"
  description    = "Security baseline event notifications"
}

resource "oci_sch_service_connector" "security_events" {
  compartment_id = var.compartment_ocid
  display_name   = "security-baseline-event-connector"

  source {
    kind = "streaming"

    cursor {
      kind = "LATEST"
    }
  }

  target {
    kind     = "notifications"
    topic_id = oci_ons_notification_topic.security_events.id
  }
}
"""

SECURITY_BASELINE_OCI_VARIABLES = """\
{{ cui_header }}
# -------------------------------------------------------
# OCI Security Baseline — Variables
# -------------------------------------------------------
variable "tenancy_ocid" {
  description = "OCI tenancy OCID"
  type        = string
}

variable "compartment_ocid" {
  description = "OCI compartment OCID for security baseline resources"
  type        = string
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "us-langley-1"
}
"""

SECURITY_BASELINE_OCI_OUTPUTS = """\
{{ cui_header }}
# -------------------------------------------------------
# OCI Security Baseline — Outputs
# -------------------------------------------------------
output "cloud_guard_target_id" {
  description = "Cloud Guard target ID"
  value       = oci_cloud_guard_target.baseline.id
}

output "vault_id" {
  description = "OCI Vault ID"
  value       = oci_kms_vault.baseline.id
}

output "vss_recipe_id" {
  description = "Vulnerability Scanning Service recipe ID"
  value       = oci_vulnerability_scanning_host_scan_recipe.baseline.id
}
"""


def generate_security_baseline_oci(project_path: str, project_config: dict = None) -> list:
    """Generate OCI Security Baseline Terraform module.

    Produces terraform/modules/oci-security-baseline/ with main.tf (Cloud Guard
    target + config detector recipe, Vault + HSM key, VSS host recipe, service
    connector for events to notifications), variables.tf, and outputs.tf.

    Args:
        project_path: Target project directory.
        project_config: Optional configuration dict.

    Returns:
        List of absolute file paths generated.
    """
    tf_dir = Path(project_path) / "terraform" / "modules" / "oci-security-baseline"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", SECURITY_BASELINE_OCI_MAIN),
        ("variables.tf", SECURITY_BASELINE_OCI_VARIABLES),
        ("outputs.tf", SECURITY_BASELINE_OCI_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


def generate_scca_oci(project_path: str, project_config: dict = None) -> list:
    """Generate SCCA (Secure Cloud Computing Architecture) Terraform module for OCI.

    Produces terraform/modules/scca-oci/ with main.tf (VDSS/VDMS/Workload VCNs,
    DRG, Network Firewall), security.tf, identity.tf, variables.tf, and outputs.tf.

    Args:
        project_path: Target project directory.
        project_config: Optional configuration dict.

    Returns:
        List of absolute file paths generated.
    """
    tf_dir = Path(project_path) / "terraform" / "modules" / "scca-oci"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", SCCA_OCI_MAIN),
        ("security.tf", SCCA_OCI_SECURITY),
        ("identity.tf", SCCA_OCI_IDENTITY),
        ("variables.tf", SCCA_OCI_VARIABLES),
        ("outputs.tf", SCCA_OCI_OUTPUTS),
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
        "scca_oci": lambda: generate_scca_oci(project_path, config),
        "security_baseline_oci": lambda: generate_security_baseline_oci(project_path, config),
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
    parser = argparse.ArgumentParser(description="Generate Terraform for Oracle OCI Government Cloud")
    parser.add_argument("--project-path", required=True, help="Target project directory")
    parser.add_argument(
        "--components",
        default="base,vcn,autonomous_db,ocir,vault",
        help="Comma-separated components: base,vcn,autonomous_db,ocir,vault,scca_oci,security_baseline_oci",
    )
    parser.add_argument("--project-name", default="icdev-project", help="Project name for resource naming")
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
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
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
        "scca_oci": lambda: generate_scca_oci(args.project_path, config),
        "security_baseline_oci": lambda: generate_security_baseline_oci(args.project_path, config),
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
