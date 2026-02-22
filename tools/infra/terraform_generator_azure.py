#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate Terraform configurations for Azure Government deployments.
Produces provider.tf, variables.tf, outputs.tf, main.tf, and modules
for VNet, PostgreSQL Flexible Server, ACR, and Key Vault — all with CUI
header comments.

Reuses shared helpers (_cui_header, _write) from the AWS generator
to maintain a single rendering/writing code path across cloud providers.

Uses a local _render that performs simple placeholder replacement so that
Terraform HCL interpolation syntax (${{var.name}}) passes through
untouched — Jinja2 would incorrectly interpret those as template
expressions."""

import argparse
from pathlib import Path

from tools.infra.terraform_generator import _cui_header, _write


def _render(template_str: str, ctx: dict) -> str:
    """Replace {{ key }} placeholders with ctx values.

    Intentionally uses simple string replacement instead of Jinja2 so that
    Terraform HCL interpolation (${{var.name}}) is preserved in the output.
    Only keys present in *ctx* are substituted.
    """
    result = template_str
    for key, val in ctx.items():
        result = result.replace("{{ " + key + " }}", str(val))
        result = result.replace("{{" + key + "}}", str(val))
    return result

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Base infrastructure
# ---------------------------------------------------------------------------
PROVIDER_TF = """\
{{ cui_header }}
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.80"
    }
  }

  backend "azurerm" {
    resource_group_name  = "{{ project_name }}-tf-state-rg"
    storage_account_name = "{{ storage_account_name }}"
    container_name       = "tfstate"
    key                  = "{{ environment }}/terraform.tfstate"
    environment          = "usgovernment"
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }

  environment = "usgovernment"

  # Azure Government endpoints
  # https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-get-started-connect-with-cli
}
"""

VARIABLES_TF = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier used for resource naming"
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

variable "resource_group_name" {
  description = "Name of the Azure Resource Group"
  type        = string
  default     = "{{ project_name }}-{{ environment }}-rg"
}

variable "location" {
  description = "Azure Government region"
  type        = string
  default     = "usgovvirginia"

  validation {
    condition     = contains(["usgovvirginia", "usgovarizona", "usgovtexas"], var.location)
    error_message = "Location must be an Azure Government region: usgovvirginia, usgovarizona, or usgovtexas."
  }
}

variable "vnet_address_space" {
  description = "Virtual Network address space"
  type        = list(string)
  default     = ["10.0.0.0/16"]
}

variable "common_tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}
"""

OUTPUTS_TF = """\
{{ cui_header }}
output "resource_group_id" {
  description = "Resource Group ID"
  value       = azurerm_resource_group.this.id
}

output "vnet_id" {
  description = "Virtual Network ID"
  value       = module.vnet.vnet_id
}

output "subnet_ids" {
  description = "Subnet IDs"
  value       = module.vnet.subnet_ids
}

output "postgres_fqdn" {
  description = "PostgreSQL Flexible Server FQDN"
  value       = module.postgres.fqdn
  sensitive   = true
}

output "acr_login_server" {
  description = "Azure Container Registry login server"
  value       = module.acr.login_server
}
"""

MAIN_TF = """\
{{ cui_header }}
resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location

  tags = merge(var.common_tags, {
    Project        = var.project_name
    Environment    = var.environment
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

module "vnet" {
  source = "./modules/vnet"

  project_name       = var.project_name
  environment        = var.environment
  resource_group_name = azurerm_resource_group.this.name
  location           = azurerm_resource_group.this.location
  address_space      = var.vnet_address_space
  common_tags        = var.common_tags
}

module "postgres" {
  source = "./modules/postgres"

  project_name        = var.project_name
  environment         = var.environment
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  delegated_subnet_id = module.vnet.subnet_ids["data"]
  private_dns_zone_id = module.vnet.postgres_private_dns_zone_id
  common_tags         = var.common_tags
}

module "acr" {
  source = "./modules/acr"

  project_name        = var.project_name
  environment         = var.environment
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  common_tags         = var.common_tags
}

module "key_vault" {
  source = "./modules/key_vault"

  project_name        = var.project_name
  environment         = var.environment
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  common_tags         = var.common_tags
}
"""


def generate_base(project_path: str, project_config: dict = None) -> list:
    """Generate provider.tf, variables.tf, outputs.tf, main.tf for Azure Gov."""
    config = project_config or {}
    project_name = config.get("project_name", "icdev-project")
    environment = config.get("environment", "dev")
    # Storage account names: lowercase alphanumeric, max 24 chars
    sanitized = project_name.replace("-", "").replace("_", "")[:14]
    storage_account_name = config.get(
        "storage_account_name", f"{sanitized}tfstate"
    )

    tf_dir = Path(project_path) / "terraform"
    ctx = {
        "cui_header": _cui_header(),
        "project_name": project_name,
        "environment": environment,
        "storage_account_name": storage_account_name,
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
# VNet module
# ---------------------------------------------------------------------------
VNET_MAIN = """\
{{ cui_header }}
resource "azurerm_virtual_network" "this" {
  name                = "${{var.project_name}}-${{var.environment}}-vnet"
  resource_group_name = var.resource_group_name
  location            = var.location
  address_space       = var.address_space

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-vnet"
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

# --- Private subnets -------------------------------------------------------

resource "azurerm_subnet" "app" {
  name                 = "${{var.project_name}}-${{var.environment}}-app-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.subnet_cidrs["app"]]

  service_endpoints = ["Microsoft.KeyVault", "Microsoft.ContainerRegistry"]
}

resource "azurerm_subnet" "data" {
  name                 = "${{var.project_name}}-${{var.environment}}-data-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.subnet_cidrs["data"]]

  delegation {
    name = "postgresql-delegation"

    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }

  service_endpoints = ["Microsoft.Storage"]
}

resource "azurerm_subnet" "mgmt" {
  name                 = "${{var.project_name}}-${{var.environment}}-mgmt-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.subnet_cidrs["mgmt"]]

  service_endpoints = ["Microsoft.KeyVault"]
}

# --- NSG with default deny-all --------------------------------------------

resource "azurerm_network_security_group" "default_deny" {
  name                = "${{var.project_name}}-${{var.environment}}-default-deny-nsg"
  resource_group_name = var.resource_group_name
  location            = var.location

  # Deny all inbound by default (lowest priority = evaluated last, but
  # Azure built-in DenyAllInBound is at 65500; this explicit rule at 4096
  # ensures intent is clear and auditable).
  security_rule {
    name                       = "DenyAllInbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # Deny all outbound by default
  security_rule {
    name                       = "DenyAllOutbound"
    priority                   = 4096
    direction                  = "Outbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # Allow intra-VNet traffic inbound
  security_rule {
    name                       = "AllowVNetInbound"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "VirtualNetwork"
  }

  # Allow intra-VNet traffic outbound
  security_rule {
    name                       = "AllowVNetOutbound"
    priority                   = 100
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "VirtualNetwork"
  }

  # Allow outbound to Azure services (Key Vault, ACR, Storage, etc.)
  security_rule {
    name                       = "AllowAzureServicesOutbound"
    priority                   = 200
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "AzureCloud"
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-default-deny-nsg"
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

# Associate NSG with all subnets
resource "azurerm_subnet_network_security_group_association" "app" {
  subnet_id                 = azurerm_subnet.app.id
  network_security_group_id = azurerm_network_security_group.default_deny.id
}

resource "azurerm_subnet_network_security_group_association" "data" {
  subnet_id                 = azurerm_subnet.data.id
  network_security_group_id = azurerm_network_security_group.default_deny.id
}

resource "azurerm_subnet_network_security_group_association" "mgmt" {
  subnet_id                 = azurerm_subnet.mgmt.id
  network_security_group_id = azurerm_network_security_group.default_deny.id
}

# --- VNet Flow Logs -------------------------------------------------------

resource "azurerm_log_analytics_workspace" "flow_logs" {
  name                = "${{var.project_name}}-${{var.environment}}-flow-logs-law"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "PerGB2018"
  retention_in_days   = 365

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

resource "azurerm_network_watcher" "this" {
  name                = "${{var.project_name}}-${{var.environment}}-nw"
  resource_group_name = var.resource_group_name
  location            = var.location

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

resource "azurerm_storage_account" "flow_logs" {
  name                     = "${{replace(var.project_name, "-", "")}}flowlogs"
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "GRS"
  min_tls_version          = "TLS1_2"

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

resource "azurerm_network_watcher_flow_log" "nsg" {
  network_watcher_name = azurerm_network_watcher.this.name
  resource_group_name  = var.resource_group_name
  name                 = "${{var.project_name}}-${{var.environment}}-nsg-flow-log"

  network_security_group_id = azurerm_network_security_group.default_deny.id
  storage_account_id        = azurerm_storage_account.flow_logs.id
  enabled                   = true
  version                   = 2

  retention_policy {
    enabled = true
    days    = 365
  }

  traffic_analytics {
    enabled               = true
    workspace_id          = azurerm_log_analytics_workspace.flow_logs.workspace_id
    workspace_region      = var.location
    workspace_resource_id = azurerm_log_analytics_workspace.flow_logs.id
    interval_in_minutes   = 10
  }
}

# --- Private DNS zone for PostgreSQL Flexible Server -----------------------

resource "azurerm_private_dns_zone" "postgres" {
  name                = "${{var.project_name}}-${{var.environment}}.private.postgres.database.usgovcloudapi.net"
  resource_group_name = var.resource_group_name

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "${{var.project_name}}-${{var.environment}}-pg-dns-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.this.id

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}
"""

VNET_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "resource_group_name" {
  description = "Resource group name"
  type        = string
}

variable "location" {
  description = "Azure Government region"
  type        = string
}

variable "address_space" {
  description = "Virtual network address space"
  type        = list(string)
  default     = ["10.0.0.0/16"]
}

variable "subnet_cidrs" {
  description = "CIDR blocks for subnets (app, data, mgmt)"
  type        = map(string)
  default = {
    app  = "10.0.1.0/24"
    data = "10.0.2.0/24"
    mgmt = "10.0.3.0/24"
  }
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

VNET_OUTPUTS = """\
{{ cui_header }}
output "vnet_id" {
  description = "Virtual Network ID"
  value       = azurerm_virtual_network.this.id
}

output "subnet_ids" {
  description = "Map of subnet name to subnet ID"
  value = {
    app  = azurerm_subnet.app.id
    data = azurerm_subnet.data.id
    mgmt = azurerm_subnet.mgmt.id
  }
}

output "nsg_id" {
  description = "Default deny NSG ID"
  value       = azurerm_network_security_group.default_deny.id
}

output "postgres_private_dns_zone_id" {
  description = "Private DNS zone ID for PostgreSQL"
  value       = azurerm_private_dns_zone.postgres.id
}

output "vnet_address_space" {
  description = "VNet address space"
  value       = azurerm_virtual_network.this.address_space
}
"""


def generate_vnet(project_path: str) -> list:
    """Generate Azure VNet Terraform module with private subnets, NSG, and flow logs."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "vnet"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", VNET_MAIN),
        ("variables.tf", VNET_VARIABLES),
        ("outputs.tf", VNET_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# PostgreSQL Flexible Server module
# ---------------------------------------------------------------------------
POSTGRES_MAIN = """\
{{ cui_header }}
resource "azurerm_postgresql_flexible_server" "this" {
  name                = "${{var.project_name}}-${{var.environment}}-pgflex"
  resource_group_name = var.resource_group_name
  location            = var.location

  administrator_login    = var.db_username
  administrator_password = var.db_password

  sku_name   = var.sku_name
  version    = "15"
  storage_mb = var.storage_mb

  delegated_subnet_id = var.delegated_subnet_id
  private_dns_zone_id = var.private_dns_zone_id

  zone = var.environment == "prod" ? "1" : null

  backup_retention_days        = var.environment == "prod" ? 35 : 7
  geo_redundant_backup_enabled = var.environment == "prod" ? true : false

  high_availability {
    mode                      = var.environment == "prod" ? "ZoneRedundant" : "Disabled"
    standby_availability_zone = var.environment == "prod" ? "2" : null
  }

  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = true
  }

  tags = merge(var.common_tags, {
    Name            = "${{var.project_name}}-${{var.environment}}-pgflex"
    Classification  = "CUI"
    DataSensitivity = "High"
    ManagedBy       = "Terraform"
  })

  lifecycle {
    prevent_destroy = true
  }
}

# Enforce SSL connections
resource "azurerm_postgresql_flexible_server_configuration" "require_ssl" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "ON"
}

# Set minimum TLS version to 1.2
resource "azurerm_postgresql_flexible_server_configuration" "tls_version" {
  name      = "ssl_min_protocol_version"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "TLSv1.2"
}

# Enable connection throttling for security
resource "azurerm_postgresql_flexible_server_configuration" "connection_throttling" {
  name      = "connection_throttle.enable"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "ON"
}

# Enable logging for audit compliance (NIST AU)
resource "azurerm_postgresql_flexible_server_configuration" "log_checkpoints" {
  name      = "log_checkpoints"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "ON"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_connections" {
  name      = "log_connections"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "ON"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_disconnections" {
  name      = "log_disconnections"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "ON"
}

# Application database
resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = var.db_name
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Diagnostic settings for monitoring
resource "azurerm_monitor_diagnostic_setting" "postgres" {
  name                       = "${{var.project_name}}-${{var.environment}}-pg-diag"
  target_resource_id         = azurerm_postgresql_flexible_server.this.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "PostgreSQLLogs"
  }

  enabled_log {
    category = "PostgreSQLFlexSessions"
  }

  metric {
    category = "AllMetrics"
    enabled  = true
  }
}
"""

POSTGRES_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "resource_group_name" {
  description = "Resource group name"
  type        = string
}

variable "location" {
  description = "Azure Government region"
  type        = string
}

variable "sku_name" {
  description = "PostgreSQL Flexible Server SKU"
  type        = string
  default     = "GP_Standard_D2s_v3"
}

variable "storage_mb" {
  description = "Storage size in MB"
  type        = number
  default     = 32768
}

variable "db_name" {
  description = "Application database name"
  type        = string
  default     = "appdb"
}

variable "db_username" {
  description = "Database administrator login"
  type        = string
  default     = "dbadmin"
  sensitive   = true
}

variable "db_password" {
  description = "Database administrator password"
  type        = string
  sensitive   = true
}

variable "delegated_subnet_id" {
  description = "Subnet ID delegated to PostgreSQL Flexible Server"
  type        = string
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for PostgreSQL"
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Log Analytics Workspace ID for diagnostics"
  type        = string
  default     = ""
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

POSTGRES_OUTPUTS = """\
{{ cui_header }}
output "fqdn" {
  description = "PostgreSQL Flexible Server FQDN"
  value       = azurerm_postgresql_flexible_server.this.fqdn
  sensitive   = true
}

output "server_id" {
  description = "PostgreSQL Flexible Server ID"
  value       = azurerm_postgresql_flexible_server.this.id
}

output "db_name" {
  description = "Application database name"
  value       = azurerm_postgresql_flexible_server_database.app.name
}
"""


def generate_postgres(project_path: str, db_config: dict = None) -> list:
    """Generate Azure PostgreSQL Flexible Server Terraform module."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "postgres"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", POSTGRES_MAIN),
        ("variables.tf", POSTGRES_VARIABLES),
        ("outputs.tf", POSTGRES_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# ACR module
# ---------------------------------------------------------------------------
ACR_MAIN = """\
{{ cui_header }}
resource "azurerm_container_registry" "this" {
  name                = "${{replace(var.project_name, "-", "")}}${{var.environment}}acr"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Premium"

  # Premium SKU features for Gov/DoD
  admin_enabled                 = false
  public_network_access_enabled = false
  zone_redundancy_enabled       = var.environment == "prod" ? true : false
  data_endpoint_enabled         = true
  export_policy_enabled         = false
  anonymous_pull_enabled        = false

  # Content trust (Docker Content Trust / Notary v2)
  trust_policy {
    enabled = true
  }

  # Quarantine policy — images quarantined until scanned
  quarantine_policy_enabled = true

  # Retention policy — untagged manifests
  retention_policy {
    days    = 30
    enabled = true
  }

  # Encryption with customer-managed key (optional)
  # encryption {
  #   enabled            = true
  #   key_vault_key_id   = var.cmk_key_id
  #   identity_client_id = var.cmk_identity_client_id
  # }

  network_rule_set {
    default_action = "Deny"

    virtual_network {
      action    = "Allow"
      subnet_id = var.allowed_subnet_id
    }
  }

  georeplications {
    location                = var.geo_replication_location
    zone_redundancy_enabled = true
    tags = {
      Classification = "CUI"
      ManagedBy      = "Terraform"
    }
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-acr"
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

# Diagnostic settings for audit trail
resource "azurerm_monitor_diagnostic_setting" "acr" {
  name                       = "${{var.project_name}}-${{var.environment}}-acr-diag"
  target_resource_id         = azurerm_container_registry.this.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "ContainerRegistryRepositoryEvents"
  }

  enabled_log {
    category = "ContainerRegistryLoginEvents"
  }

  metric {
    category = "AllMetrics"
    enabled  = true
  }
}
"""

ACR_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "resource_group_name" {
  description = "Resource group name"
  type        = string
}

variable "location" {
  description = "Azure Government region"
  type        = string
}

variable "allowed_subnet_id" {
  description = "Subnet ID allowed to access ACR"
  type        = string
  default     = ""
}

variable "geo_replication_location" {
  description = "Azure Government region for geo-replication"
  type        = string
  default     = "usgovarizona"
}

variable "log_analytics_workspace_id" {
  description = "Log Analytics Workspace ID for diagnostics"
  type        = string
  default     = ""
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

ACR_OUTPUTS = """\
{{ cui_header }}
output "login_server" {
  description = "ACR login server URL"
  value       = azurerm_container_registry.this.login_server
}

output "acr_id" {
  description = "ACR resource ID"
  value       = azurerm_container_registry.this.id
}

output "acr_name" {
  description = "ACR name"
  value       = azurerm_container_registry.this.name
}
"""


def generate_acr(project_path: str) -> list:
    """Generate Azure Container Registry (Premium) Terraform module."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "acr"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", ACR_MAIN),
        ("variables.tf", ACR_VARIABLES),
        ("outputs.tf", ACR_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Key Vault module
# ---------------------------------------------------------------------------
KEY_VAULT_MAIN = """\
{{ cui_header }}
data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "this" {
  name                = "${{var.project_name}}-${{var.environment}}-kv"
  resource_group_name = var.resource_group_name
  location            = var.location
  tenant_id           = data.azurerm_client_config.current.tenant_id

  sku_name = "premium"

  # Security hardening
  enabled_for_deployment          = false
  enabled_for_disk_encryption     = false
  enabled_for_template_deployment = false

  # Soft delete and purge protection (required for CUI)
  soft_delete_retention_days = 90
  purge_protection_enabled   = true

  # RBAC authorization (recommended over access policies)
  enable_rbac_authorization = true

  # Network restrictions
  public_network_access_enabled = false

  network_acls {
    default_action             = "Deny"
    bypass                     = "AzureServices"
    virtual_network_subnet_ids = var.allowed_subnet_ids
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-kv"
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

# Private endpoint for Key Vault
resource "azurerm_private_endpoint" "key_vault" {
  name                = "${{var.project_name}}-${{var.environment}}-kv-pe"
  resource_group_name = var.resource_group_name
  location            = var.location
  subnet_id           = var.private_endpoint_subnet_id

  private_service_connection {
    name                           = "${{var.project_name}}-${{var.environment}}-kv-psc"
    private_connection_resource_id = azurerm_key_vault.this.id
    is_manual_connection           = false
    subresource_names              = ["vault"]
  }

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "Terraform"
  })
}

# Diagnostic settings for audit trail (NIST AU)
resource "azurerm_monitor_diagnostic_setting" "key_vault" {
  name                       = "${{var.project_name}}-${{var.environment}}-kv-diag"
  target_resource_id         = azurerm_key_vault.this.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "AuditEvent"
  }

  enabled_log {
    category = "AzurePolicyEvaluationDetails"
  }

  metric {
    category = "AllMetrics"
    enabled  = true
  }
}
"""

KEY_VAULT_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "resource_group_name" {
  description = "Resource group name"
  type        = string
}

variable "location" {
  description = "Azure Government region"
  type        = string
}

variable "allowed_subnet_ids" {
  description = "Subnet IDs allowed to access Key Vault"
  type        = list(string)
  default     = []
}

variable "private_endpoint_subnet_id" {
  description = "Subnet ID for Key Vault private endpoint"
  type        = string
  default     = ""
}

variable "log_analytics_workspace_id" {
  description = "Log Analytics Workspace ID for diagnostics"
  type        = string
  default     = ""
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

KEY_VAULT_OUTPUTS = """\
{{ cui_header }}
output "key_vault_id" {
  description = "Key Vault resource ID"
  value       = azurerm_key_vault.this.id
}

output "key_vault_uri" {
  description = "Key Vault URI"
  value       = azurerm_key_vault.this.vault_uri
}

output "key_vault_name" {
  description = "Key Vault name"
  value       = azurerm_key_vault.this.name
}

output "private_endpoint_id" {
  description = "Key Vault private endpoint ID"
  value       = azurerm_private_endpoint.key_vault.id
}
"""


def generate_key_vault(project_path: str) -> list:
    """Generate Azure Key Vault Terraform module with soft delete and purge protection."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "key_vault"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", KEY_VAULT_MAIN),
        ("variables.tf", KEY_VAULT_VARIABLES),
        ("outputs.tf", KEY_VAULT_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def generate(project_path: str, project_config: dict = None) -> list:
    """Generate all Azure Government Terraform files.

    This is the main entry point. It generates base infrastructure
    (provider, variables, outputs, main) plus all four modules
    (vnet, postgres, acr, key_vault).

    Args:
        project_path: Target project directory.
        project_config: Optional dict with project_name, environment,
            storage_account_name, db_name, components.

    Returns:
        List of absolute file paths that were generated.
    """
    config = project_config or {}
    components = config.get("components", "base,vnet,postgres,acr,key_vault")
    if isinstance(components, str):
        components = [c.strip() for c in components.split(",")]

    generators = {
        "base": lambda: generate_base(project_path, config),
        "vnet": lambda: generate_vnet(project_path),
        "postgres": lambda: generate_postgres(project_path, config),
        "acr": lambda: generate_acr(project_path),
        "key_vault": lambda: generate_key_vault(project_path),
    }

    all_files = []
    for comp in components:
        gen_fn = generators.get(comp)
        if gen_fn:
            all_files.extend(gen_fn())

    return all_files


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    """CLI entry point for Azure Government Terraform generation."""
    parser = argparse.ArgumentParser(
        description="Generate Terraform for Azure Government (AzureUSGovernment)"
    )
    parser.add_argument(
        "--project-path",
        required=True,
        help="Target project directory",
    )
    parser.add_argument(
        "--components",
        default="base,vnet,postgres,acr,key_vault",
        help="Comma-separated components: base,vnet,postgres,acr,key_vault",
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
    args = parser.parse_args()

    config = {
        "project_name": args.project_name,
        "environment": args.environment,
        "components": args.components,
    }

    components = [c.strip() for c in args.components.split(",")]
    all_files = []

    generators = {
        "base": lambda: generate_base(args.project_path, config),
        "vnet": lambda: generate_vnet(args.project_path),
        "postgres": lambda: generate_postgres(args.project_path, config),
        "acr": lambda: generate_acr(args.project_path),
        "key_vault": lambda: generate_key_vault(args.project_path),
    }

    for comp in components:
        if comp in generators:
            files = generators[comp]()
            all_files.extend(files)
            print(f"[terraform-azure] Generated {comp}: {len(files)} files")
        else:
            print(f"[terraform-azure] Unknown component: {comp}")

    print(f"\n[terraform-azure] Total files generated: {len(all_files)}")
    for f in all_files:
        print(f"  -> {f}")


if __name__ == "__main__":
    main()
