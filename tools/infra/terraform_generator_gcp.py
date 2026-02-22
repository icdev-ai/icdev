#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate Terraform configurations for GCP Government (Assured Workloads) deployments.
Produces provider.tf, variables.tf, outputs.tf, main.tf, and modules
for VPC, Cloud SQL, Artifact Registry, and Secret Manager — all with CUI header comments."""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Ensure project root is on sys.path for direct script execution
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
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "{{ project_name }}-tf-state"
    prefix = "{{ environment }}/terraform.tfstate"
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.region

  default_labels = {
    project        = "{{ project_name }}"
    environment    = "{{ environment }}"
    classification = "cui"
    managed_by     = "terraform"
  }
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

variable "gcp_project_id" {
  description = "GCP project ID for Assured Workloads"
  type        = string
  default     = "{{ gcp_project_id }}"
}

variable "region" {
  description = "GCP Government region (Assured Workloads)"
  type        = string
  default     = "us-east4"

  validation {
    condition     = contains(["us-east4", "us-central1"], var.region)
    error_message = "Region must be a GCP Government Assured Workloads region: us-east4 or us-central1."
  }
}

variable "network_cidr" {
  description = "Primary network CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "db_tier" {
  description = "Cloud SQL instance tier"
  type        = string
  default     = "db-custom-2-8192"
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "{{ db_name }}"
}

variable "db_password" {
  description = "Database password"
  type        = string
  sensitive   = true
}

variable "common_labels" {
  description = "Common labels for all resources"
  type        = map(string)
  default     = {}
}
"""

OUTPUTS_TF = """\
{{ cui_header }}
output "vpc_id" {
  description = "VPC network self link"
  value       = module.vpc.network_id
}

output "subnet_ids" {
  description = "Private subnet self links"
  value       = module.vpc.subnet_ids
}

output "cloud_sql_connection_name" {
  description = "Cloud SQL instance connection name"
  value       = module.cloud_sql.connection_name
  sensitive   = true
}

output "artifact_registry_url" {
  description = "Artifact Registry repository URL"
  value       = module.artifact_registry.repository_url
}

output "secret_manager_project" {
  description = "Secret Manager project ID"
  value       = module.secret_manager.project_id
}
"""

MAIN_TF = """\
{{ cui_header }}
module "vpc" {
  source = "./modules/vpc"

  project_name         = var.project_name
  environment          = var.environment
  gcp_project_id       = var.gcp_project_id
  region               = var.region
  network_cidr         = var.network_cidr
  private_subnet_cidrs = var.private_subnet_cidrs
  common_labels        = var.common_labels
}

module "cloud_sql" {
  source = "./modules/cloud_sql"

  project_name   = var.project_name
  environment    = var.environment
  gcp_project_id = var.gcp_project_id
  region         = var.region
  network_id     = module.vpc.network_id
  db_tier        = var.db_tier
  db_name        = var.db_name
  db_password    = var.db_password
  common_labels  = var.common_labels
}

module "artifact_registry" {
  source = "./modules/artifact_registry"

  project_name   = var.project_name
  environment    = var.environment
  gcp_project_id = var.gcp_project_id
  region         = var.region
  common_labels  = var.common_labels
}

module "secret_manager" {
  source = "./modules/secret_manager"

  project_name   = var.project_name
  environment    = var.environment
  gcp_project_id = var.gcp_project_id
  common_labels  = var.common_labels
}
"""


def generate_base(project_path: str, project_config: dict = None) -> list:
    """Generate provider.tf, variables.tf, outputs.tf, main.tf for GCP Government."""
    config = project_config or {}
    project_name = config.get("project_name", "icdev-project")
    environment = config.get("environment", "dev")
    gcp_project_id = config.get("gcp_project_id", "my-assured-project")
    db_name = config.get("db_name", "appdb")

    tf_dir = Path(project_path) / "terraform-gcp"
    ctx = {
        "cui_header": _cui_header(),
        "project_name": project_name,
        "environment": environment,
        "gcp_project_id": gcp_project_id,
        "db_name": db_name,
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
# VPC module
# ---------------------------------------------------------------------------
VPC_MAIN = """\
{{ cui_header }}
resource "google_compute_network" "this" {
  name                    = "$${var.project_name}-$${var.environment}-vpc"
  project                 = var.gcp_project_id
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  description             = "CUI VPC for $${var.project_name} ($${var.environment})"
}

resource "google_compute_subnetwork" "private" {
  count = length(var.private_subnet_cidrs)

  name          = "$${var.project_name}-$${var.environment}-private-$${count.index + 1}"
  project       = var.gcp_project_id
  region        = var.region
  network       = google_compute_network.this.id
  ip_cidr_range = var.private_subnet_cidrs[count.index]

  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = 1.0
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# Default deny all ingress
resource "google_compute_firewall" "deny_all_ingress" {
  name    = "$${var.project_name}-$${var.environment}-deny-all-ingress"
  project = var.gcp_project_id
  network = google_compute_network.this.id

  priority  = 65534
  direction = "INGRESS"

  deny {
    protocol = "all"
  }

  source_ranges = ["0.0.0.0/0"]

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

# Allow internal traffic within VPC
resource "google_compute_firewall" "allow_internal" {
  name    = "$${var.project_name}-$${var.environment}-allow-internal"
  project = var.gcp_project_id
  network = google_compute_network.this.id

  priority  = 1000
  direction = "INGRESS"

  allow {
    protocol = "tcp"
  }

  allow {
    protocol = "udp"
  }

  allow {
    protocol = "icmp"
  }

  source_ranges = [var.network_cidr]

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

# Allow IAP for secure administrative access
resource "google_compute_firewall" "allow_iap" {
  name    = "$${var.project_name}-$${var.environment}-allow-iap"
  project = var.gcp_project_id
  network = google_compute_network.this.id

  priority  = 900
  direction = "INGRESS"

  allow {
    protocol = "tcp"
    ports    = ["22", "3389"]
  }

  # IAP source range
  source_ranges = ["35.235.240.0/20"]

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

# Cloud Router for NAT gateway
resource "google_compute_router" "this" {
  name    = "$${var.project_name}-$${var.environment}-router"
  project = var.gcp_project_id
  region  = var.region
  network = google_compute_network.this.id
}

# Cloud NAT for private subnet outbound access
resource "google_compute_router_nat" "this" {
  name    = "$${var.project_name}-$${var.environment}-nat"
  project = var.gcp_project_id
  region  = var.region
  router  = google_compute_router.this.name

  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ALL"
  }
}
"""

VPC_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "gcp_project_id" { type = string }
variable "region" { type = string; default = "us-east4" }
variable "network_cidr" { type = string; default = "10.0.0.0/16" }
variable "private_subnet_cidrs" { type = list(string); default = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"] }
variable "common_labels" { type = map(string); default = {} }
"""

VPC_OUTPUTS = """\
{{ cui_header }}
output "network_id" {
  description = "VPC network self link"
  value       = google_compute_network.this.self_link
}

output "network_name" {
  description = "VPC network name"
  value       = google_compute_network.this.name
}

output "subnet_ids" {
  description = "Private subnet self links"
  value       = google_compute_subnetwork.private[*].self_link
}

output "subnet_names" {
  description = "Private subnet names"
  value       = google_compute_subnetwork.private[*].name
}
"""


def generate_vpc(project_path: str) -> list:
    """Generate GCP VPC Terraform module with private subnets and VPC Flow Logs."""
    tf_dir = Path(project_path) / "terraform-gcp" / "modules" / "vpc"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", VPC_MAIN),
        ("variables.tf", VPC_VARIABLES),
        ("outputs.tf", VPC_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Cloud SQL module
# ---------------------------------------------------------------------------
CLOUD_SQL_MAIN = """\
{{ cui_header }}
resource "google_compute_global_address" "private_ip" {
  name          = "$${var.project_name}-$${var.environment}-sql-ip"
  project       = var.gcp_project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = var.network_id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = var.network_id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip.name]
}

resource "google_sql_database_instance" "this" {
  name             = "$${var.project_name}-$${var.environment}-pg"
  project          = var.gcp_project_id
  region           = var.region
  database_version = "POSTGRES_15"

  depends_on = [google_service_networking_connection.private_vpc]

  settings {
    tier              = var.db_tier
    availability_type = var.environment == "prod" ? "REGIONAL" : "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 20
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
      require_ssl     = true
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "03:00"
      transaction_log_retention_days = var.environment == "prod" ? 7 : 3

      backup_retention_settings {
        retained_backups = var.environment == "prod" ? 35 : 7
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = 7
      hour         = 4
      update_track = "stable"
    }

    database_flags {
      name  = "log_checkpoints"
      value = "on"
    }

    database_flags {
      name  = "log_connections"
      value = "on"
    }

    database_flags {
      name  = "log_disconnections"
      value = "on"
    }

    database_flags {
      name  = "log_lock_waits"
      value = "on"
    }

    insights_config {
      query_insights_enabled  = true
      query_plans_per_minute  = 5
      query_string_length     = 1024
      record_application_tags = true
      record_client_address   = true
    }

    user_labels = merge(var.common_labels, {
      classification  = "cui"
      data_sensitivity = "high"
      managed_by       = "terraform"
    })
  }

  deletion_protection = var.environment == "prod" ? true : false
}

resource "google_sql_database" "this" {
  name     = var.db_name
  project  = var.gcp_project_id
  instance = google_sql_database_instance.this.name
}

resource "google_sql_user" "this" {
  name     = "dbadmin"
  project  = var.gcp_project_id
  instance = google_sql_database_instance.this.name
  password = var.db_password
}
"""

CLOUD_SQL_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "gcp_project_id" { type = string }
variable "region" { type = string; default = "us-east4" }
variable "network_id" { type = string }
variable "db_tier" { type = string; default = "db-custom-2-8192" }
variable "db_name" { type = string; default = "appdb" }
variable "db_password" { type = string; sensitive = true }
variable "common_labels" { type = map(string); default = {} }
"""

CLOUD_SQL_OUTPUTS = """\
{{ cui_header }}
output "connection_name" {
  description = "Cloud SQL instance connection name"
  value       = google_sql_database_instance.this.connection_name
  sensitive   = true
}

output "instance_name" {
  description = "Cloud SQL instance name"
  value       = google_sql_database_instance.this.name
}

output "private_ip_address" {
  description = "Cloud SQL private IP address"
  value       = google_sql_database_instance.this.private_ip_address
  sensitive   = true
}

output "db_name" {
  description = "Database name"
  value       = google_sql_database.this.name
}
"""


def generate_cloud_sql(project_path: str, db_config: dict = None) -> list:
    """Generate Cloud SQL for PostgreSQL Terraform module."""
    tf_dir = Path(project_path) / "terraform-gcp" / "modules" / "cloud_sql"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", CLOUD_SQL_MAIN),
        ("variables.tf", CLOUD_SQL_VARIABLES),
        ("outputs.tf", CLOUD_SQL_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Artifact Registry module
# ---------------------------------------------------------------------------
ARTIFACT_REGISTRY_MAIN = """\
{{ cui_header }}
resource "google_artifact_registry_repository" "this" {
  location      = var.region
  project       = var.gcp_project_id
  repository_id = "$${var.project_name}-$${var.environment}-docker"
  description   = "CUI Docker registry for $${var.project_name} ($${var.environment})"
  format        = "DOCKER"

  docker_config {
    immutable_tags = true
  }

  cleanup_policies {
    id     = "keep-tagged-30"
    action = "KEEP"

    condition {
      tag_state  = "TAGGED"
      tag_prefixes = ["v"]
      newer_than   = "$${30 * 24 * 3600}s"
    }
  }

  cleanup_policies {
    id     = "delete-untagged-7d"
    action = "DELETE"

    condition {
      tag_state  = "UNTAGGED"
      older_than = "$${7 * 24 * 3600}s"
    }
  }

  labels = merge(var.common_labels, {
    classification = "cui"
    managed_by     = "terraform"
  })
}

# Enable vulnerability scanning on the project
resource "google_project_service" "container_scanning" {
  project = var.gcp_project_id
  service = "containerscanning.googleapis.com"

  disable_on_destroy = false
}

# Enable Artifact Registry API
resource "google_project_service" "artifact_registry" {
  project = var.gcp_project_id
  service = "artifactregistry.googleapis.com"

  disable_on_destroy = false
}
"""

ARTIFACT_REGISTRY_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "gcp_project_id" { type = string }
variable "region" { type = string; default = "us-east4" }
variable "common_labels" { type = map(string); default = {} }
"""

ARTIFACT_REGISTRY_OUTPUTS = """\
{{ cui_header }}
output "repository_url" {
  description = "Artifact Registry repository URL"
  value       = "$${var.region}-docker.pkg.dev/$${var.gcp_project_id}/$${google_artifact_registry_repository.this.repository_id}"
}

output "repository_id" {
  description = "Artifact Registry repository ID"
  value       = google_artifact_registry_repository.this.repository_id
}

output "repository_name" {
  description = "Artifact Registry repository name"
  value       = google_artifact_registry_repository.this.name
}
"""


def generate_artifact_registry(project_path: str) -> list:
    """Generate Google Artifact Registry Terraform module with vulnerability scanning."""
    tf_dir = Path(project_path) / "terraform-gcp" / "modules" / "artifact_registry"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", ARTIFACT_REGISTRY_MAIN),
        ("variables.tf", ARTIFACT_REGISTRY_VARIABLES),
        ("outputs.tf", ARTIFACT_REGISTRY_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Secret Manager module
# ---------------------------------------------------------------------------
SECRET_MANAGER_MAIN = """\
{{ cui_header }}
# Enable Secret Manager API
resource "google_project_service" "secret_manager" {
  project = var.gcp_project_id
  service = "secretmanager.googleapis.com"

  disable_on_destroy = false
}

# Database password secret
resource "google_secret_manager_secret" "db_password" {
  secret_id = "$${var.project_name}-$${var.environment}-db-password"
  project   = var.gcp_project_id

  replication {
    auto {}
  }

  labels = merge(var.common_labels, {
    classification  = "cui"
    data_sensitivity = "high"
    managed_by       = "terraform"
    purpose          = "database"
  })

  depends_on = [google_project_service.secret_manager]
}

# Application secrets
resource "google_secret_manager_secret" "app_secret_key" {
  secret_id = "$${var.project_name}-$${var.environment}-app-secret-key"
  project   = var.gcp_project_id

  replication {
    auto {}
  }

  labels = merge(var.common_labels, {
    classification = "cui"
    managed_by     = "terraform"
    purpose        = "application"
  })

  depends_on = [google_project_service.secret_manager]
}

# TLS certificate secret
resource "google_secret_manager_secret" "tls_cert" {
  secret_id = "$${var.project_name}-$${var.environment}-tls-cert"
  project   = var.gcp_project_id

  replication {
    auto {}
  }

  labels = merge(var.common_labels, {
    classification = "cui"
    managed_by     = "terraform"
    purpose        = "tls"
  })

  depends_on = [google_project_service.secret_manager]
}

# TLS private key secret
resource "google_secret_manager_secret" "tls_key" {
  secret_id = "$${var.project_name}-$${var.environment}-tls-key"
  project   = var.gcp_project_id

  replication {
    auto {}
  }

  labels = merge(var.common_labels, {
    classification  = "cui"
    data_sensitivity = "high"
    managed_by       = "terraform"
    purpose          = "tls"
  })

  depends_on = [google_project_service.secret_manager]
}
"""

SECRET_MANAGER_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "gcp_project_id" { type = string }
variable "common_labels" { type = map(string); default = {} }
"""

SECRET_MANAGER_OUTPUTS = """\
{{ cui_header }}
output "project_id" {
  description = "GCP project ID where secrets are stored"
  value       = var.gcp_project_id
}

output "db_password_secret_id" {
  description = "Secret Manager secret ID for database password"
  value       = google_secret_manager_secret.db_password.secret_id
}

output "app_secret_key_id" {
  description = "Secret Manager secret ID for application secret key"
  value       = google_secret_manager_secret.app_secret_key.secret_id
}

output "tls_cert_secret_id" {
  description = "Secret Manager secret ID for TLS certificate"
  value       = google_secret_manager_secret.tls_cert.secret_id
}

output "tls_key_secret_id" {
  description = "Secret Manager secret ID for TLS private key"
  value       = google_secret_manager_secret.tls_key.secret_id
}
"""


def generate_secret_manager(project_path: str) -> list:
    """Generate Google Secret Manager Terraform module with automatic replication."""
    tf_dir = Path(project_path) / "terraform-gcp" / "modules" / "secret_manager"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", SECRET_MANAGER_MAIN),
        ("variables.tf", SECRET_MANAGER_VARIABLES),
        ("outputs.tf", SECRET_MANAGER_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Top-level generate() entry point
# ---------------------------------------------------------------------------
def generate(project_path: str, project_config: dict = None) -> list:
    """Generate all GCP Government Terraform configurations.

    Produces provider.tf, variables.tf, outputs.tf, main.tf at the root level
    plus modules for VPC, Cloud SQL, Artifact Registry, and Secret Manager.

    Args:
        project_path: Target project directory.
        project_config: Optional configuration dict with keys:
            - project_name (str): Project identifier (default: "icdev-project")
            - environment (str): dev|staging|prod (default: "dev")
            - gcp_project_id (str): GCP project ID (default: "my-assured-project")
            - db_name (str): Database name (default: "appdb")
            - components (str): Comma-separated list of components to generate
              (default: "base,vpc,cloud_sql,artifact_registry,secret_manager")

    Returns:
        List of absolute file paths generated.
    """
    config = project_config or {}
    components_str = config.get(
        "components", "base,vpc,cloud_sql,artifact_registry,secret_manager"
    )
    components = [c.strip() for c in components_str.split(",")]

    generators = {
        "base": lambda: generate_base(project_path, config),
        "vpc": lambda: generate_vpc(project_path),
        "cloud_sql": lambda: generate_cloud_sql(project_path, config),
        "artifact_registry": lambda: generate_artifact_registry(project_path),
        "secret_manager": lambda: generate_secret_manager(project_path),
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
        description="Generate Terraform for GCP Government (Assured Workloads)"
    )
    parser.add_argument(
        "--project-path", required=True, help="Target project directory"
    )
    parser.add_argument(
        "--components",
        default="base,vpc,cloud_sql,artifact_registry,secret_manager",
        help="Comma-separated components: base,vpc,cloud_sql,artifact_registry,secret_manager",
    )
    parser.add_argument(
        "--project-name",
        default="icdev-project",
        help="Project name for resource naming",
    )
    parser.add_argument(
        "--environment",
        default="dev",
        choices=["dev", "staging", "prod"],
        help="Target environment",
    )
    parser.add_argument(
        "--gcp-project-id",
        default="my-assured-project",
        help="GCP project ID (Assured Workloads)",
    )
    parser.add_argument(
        "--db-name", default="appdb", help="Database name for Cloud SQL module"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    args = parser.parse_args()

    config = {
        "project_name": args.project_name,
        "environment": args.environment,
        "gcp_project_id": args.gcp_project_id,
        "db_name": args.db_name,
    }

    components = [c.strip() for c in args.components.split(",")]
    all_files = []

    generators = {
        "base": lambda: generate_base(args.project_path, config),
        "vpc": lambda: generate_vpc(args.project_path),
        "cloud_sql": lambda: generate_cloud_sql(args.project_path, config),
        "artifact_registry": lambda: generate_artifact_registry(args.project_path),
        "secret_manager": lambda: generate_secret_manager(args.project_path),
    }

    for comp in components:
        if comp in generators:
            files = generators[comp]()
            all_files.extend(files)
            if not args.json:
                print(f"[terraform-gcp] Generated {comp}: {len(files)} files")
        else:
            if not args.json:
                print(f"[terraform-gcp] Unknown component: {comp}")

    if args.json:
        print(json.dumps({
            "status": "success",
            "provider": "gcp",
            "region": "us-east4",
            "components": components,
            "files_generated": len(all_files),
            "files": all_files,
        }, indent=2))
    else:
        print(f"\n[terraform-gcp] Total files generated: {len(all_files)}")
        for f in all_files:
            print(f"  -> {f}")


if __name__ == "__main__":
    main()
