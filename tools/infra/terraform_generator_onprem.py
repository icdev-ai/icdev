#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate Terraform configurations for on-premises/bare-metal deployments.
Supports targets: k8s (self-managed Kubernetes), docker (Docker Compose),
vsphere (VMware vSphere). All with CUI header comments."""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.infra.terraform_generator import _render, _cui_header, _write


# ---------------------------------------------------------------------------
# Kubernetes target (self-managed K8s)
# ---------------------------------------------------------------------------
K8S_PROVIDER_TF = """\
{{ cui_header }}
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
    postgresql = {
      source  = "cyrilgdn/postgresql"
      version = "~> 1.21"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
  }

  backend "local" {
    path = "terraform.tfstate"
  }
}

provider "kubernetes" {
  config_path = var.kubeconfig_path
}

provider "helm" {
  kubernetes {
    config_path = var.kubeconfig_path
  }
}

provider "postgresql" {
  host     = var.db_host
  port     = var.db_port
  username = var.db_admin_user
  password = var.db_admin_password
  sslmode  = "require"
}
"""

K8S_VARIABLES_TF = """\
{{ cui_header }}
# -------------------------------------------------------
# On-Premises Kubernetes Variables — {{ project_name }}
# -------------------------------------------------------
variable "kubeconfig_path" {
  description = "Path to kubeconfig file"
  type        = string
  default     = "~/.kube/config"
}

variable "project_name" {
  description = "Project identifier"
  type        = string
  default     = "{{ project_name }}"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "{{ environment }}"
}

variable "namespace" {
  description = "Kubernetes namespace"
  type        = string
  default     = "{{ project_name }}-{{ environment }}"
}

variable "db_host" {
  description = "PostgreSQL host"
  type        = string
  default     = "localhost"
}

variable "db_port" {
  description = "PostgreSQL port"
  type        = number
  default     = 5432
}

variable "db_admin_user" {
  description = "PostgreSQL admin username"
  type        = string
  default     = "postgres"
  sensitive   = true
}

variable "db_admin_password" {
  description = "PostgreSQL admin password"
  type        = string
  sensitive   = true
}

variable "classification" {
  description = "Data classification (CUI, SECRET)"
  type        = string
  default     = "CUI"
}

variable "storage_class" {
  description = "Kubernetes StorageClass for PVCs"
  type        = string
  default     = "local-path"
}
"""

K8S_MAIN_TF = """\
{{ cui_header }}
# -------------------------------------------------------
# On-Premises Kubernetes Resources — {{ project_name }}
# -------------------------------------------------------

# Namespace
resource "kubernetes_namespace" "main" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/managed-by" = "icdev-terraform"
      "icdev/classification"          = var.classification
      "icdev/environment"             = var.environment
    }
  }
}

# Network Policy — default deny ingress
resource "kubernetes_network_policy" "default_deny" {
  metadata {
    name      = "default-deny-ingress"
    namespace = kubernetes_namespace.main.metadata[0].name
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress"]
  }
}

# PostgreSQL database for ICDEV
resource "postgresql_database" "icdev" {
  name  = "$${var.project_name}_$${var.environment}"
  owner = var.db_admin_user
}

# PVC for persistent storage
resource "kubernetes_persistent_volume_claim" "data" {
  metadata {
    name      = "$${var.project_name}-data"
    namespace = kubernetes_namespace.main.metadata[0].name
  }
  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = var.storage_class
    resources {
      requests = {
        storage = "10Gi"
      }
    }
  }
}
"""

K8S_OUTPUTS_TF = """\
{{ cui_header }}
# -------------------------------------------------------
# On-Premises Outputs — {{ project_name }}
# -------------------------------------------------------
output "namespace" {
  description = "Kubernetes namespace"
  value       = kubernetes_namespace.main.metadata[0].name
}

output "database_name" {
  description = "PostgreSQL database name"
  value       = postgresql_database.icdev.name
}
"""


# ---------------------------------------------------------------------------
# Docker Compose target (development)
# ---------------------------------------------------------------------------
DOCKER_PROVIDER_TF = """\
{{ cui_header }}
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }

  backend "local" {
    path = "terraform.tfstate"
  }
}

provider "docker" {}
"""

DOCKER_MAIN_TF = """\
{{ cui_header }}
# -------------------------------------------------------
# Docker Development Resources — {{ project_name }}
# -------------------------------------------------------

resource "docker_network" "icdev" {
  name = "$${var.project_name}-network"
}

resource "docker_volume" "db_data" {
  name = "$${var.project_name}-db-data"
}

resource "docker_volume" "app_data" {
  name = "$${var.project_name}-app-data"
}

variable "project_name" {
  type    = string
  default = "{{ project_name }}"
}
"""


def generate(project_name: str = "icdev", environment: str = "production",
             target: str = "k8s", output_dir: str = ""):
    """Generate on-premises Terraform configuration files."""
    out = Path(output_dir) if output_dir else Path.cwd() / "terraform" / "onprem"
    out.mkdir(parents=True, exist_ok=True)

    ctx = {
        "cui_header": _cui_header(),
        "project_name": project_name,
        "environment": environment,
    }

    files = []
    if target == "k8s":
        _write(out / "provider.tf", _render(K8S_PROVIDER_TF, ctx))
        _write(out / "variables.tf", _render(K8S_VARIABLES_TF, ctx))
        _write(out / "main.tf", _render(K8S_MAIN_TF, ctx))
        _write(out / "outputs.tf", _render(K8S_OUTPUTS_TF, ctx))
        files = ["provider.tf", "variables.tf", "main.tf", "outputs.tf"]
    elif target == "docker":
        _write(out / "provider.tf", _render(DOCKER_PROVIDER_TF, ctx))
        _write(out / "main.tf", _render(DOCKER_MAIN_TF, ctx))
        files = ["provider.tf", "main.tf"]
    else:
        return {"status": "error", "error": f"Unknown target: {target}"}

    return {
        "status": "success",
        "output_dir": str(out),
        "files": files,
        "target": target,
    }


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate on-premises Terraform configurations"
    )
    parser.add_argument("--project-id", default="icdev", help="Project name")
    parser.add_argument("--environment", default="production",
                        help="Environment (production, staging, dev)")
    parser.add_argument("--target", default="k8s",
                        choices=["k8s", "docker"],
                        help="Deployment target")
    parser.add_argument("--output-dir", default="", help="Output directory")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="JSON output")
    args = parser.parse_args()

    import json
    result = generate(
        project_name=args.project_id,
        environment=args.environment,
        target=args.target,
        output_dir=args.output_dir,
    )

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Generated {args.target} Terraform in {result.get('output_dir', 'N/A')}")
        for f in result.get("files", []):
            print(f"  - {f}")


if __name__ == "__main__":
    run_cli()
