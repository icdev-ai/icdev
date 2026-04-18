# CUI // SP-CTI
"""Terraform HCL emitter for IDC graph nodes.

emit_resource(node, target_csp) -> str

Supported resource types:
  AWS GovCloud: aws-vpc, aws-subnet, aws-ec2, aws-sg, aws-iam-role
  Azure Gov:    azure-vnet, azure-subnet, azure-vm, azure-nsg, azure-key-vault
"""

import re
from typing import Any

# ── Type alias ────────────────────────────────────────────────────────────────
Node = dict[str, Any]

# ── Constants ─────────────────────────────────────────────────────────────────
_GOVCLOUD_REGION = "us-gov-west-1"
_AZURE_GOV_LOCATION = "usgovvirginia"
_MANAGED_BY = "icdev-terraform-emitter"

# classification values that trigger CUI tag injection
_CUI_VALUES = {"CUI", "CUI//SP-CTI", "SECRET", "CUI//SP-CTI/IL4", "CUI//SP-CTI/IL5"}


class UnsupportedResourceError(ValueError):
    """Raised when a node type has no emitter for the given CSP."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_id(raw: str) -> str:
    """Convert an IDC node ID to a valid Terraform resource name (a-z0-9_)."""
    name = re.sub(r"[^a-zA-Z0-9_]", "_", raw).strip("_")
    if not name or name[0].isdigit():
        name = "res_" + name
    return name or "res"


def _tag_block(label: str, node: Node) -> str:
    """Build an HCL tags = { … } block; injects CUI tags when classified."""
    meta = node.get("metadata") or {}
    classification = str(meta.get("classification", "")).strip()

    lines = [
        f'    Name      = "{label}"',
        f'    ManagedBy = "{_MANAGED_BY}"',
    ]
    if classification:
        lines.append(f'    Classification = "{classification}"')
        lines.append('    DataHandling   = "CUI//SP-CTI"')

    return "  tags = {{\n{}\n  }}".format("\n".join(lines))


# ── Per-resource emitters ─────────────────────────────────────────────────────

def _emit_vpc(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "vpc"))
    label = node.get("label", "vpc")
    cidr = meta.get("cidr_block", "10.0.0.0/16")
    tags = _tag_block(label, node)
    return (
        f'resource "aws_vpc" "{rid}" {{\n'
        f'  cidr_block           = "{cidr}"\n'
        f'  enable_dns_hostnames = true\n'
        f'  enable_dns_support   = true\n'
        f'\n{tags}\n}}'
    )


def _emit_subnet(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "subnet"))
    label = node.get("label", "subnet")
    cidr = meta.get("cidr_block", "10.0.1.0/24")
    az = meta.get("availability_zone", f"{_GOVCLOUD_REGION}a")
    vpc_id = meta.get("vpc_id", "vpc-00000000")
    tags = _tag_block(label, node)
    return (
        f'resource "aws_subnet" "{rid}" {{\n'
        f'  vpc_id            = "{vpc_id}"\n'
        f'  cidr_block        = "{cidr}"\n'
        f'  availability_zone = "{az}"\n'
        f'\n{tags}\n}}'
    )


def _emit_instance(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "instance"))
    label = node.get("label", "instance")
    ami = meta.get("ami", "ami-0123456789abcdef0")
    itype = meta.get("instance_type", "t3.medium")
    tags = _tag_block(label, node)
    return (
        f'resource "aws_instance" "{rid}" {{\n'
        f'  ami           = "{ami}"\n'
        f'  instance_type = "{itype}"\n'
        f'\n{tags}\n}}'
    )


def _emit_security_group(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "sg"))
    label = node.get("label", "sg")
    desc = meta.get("description", f"Security group for {label}")
    tags = _tag_block(label, node)
    return (
        f'resource "aws_security_group" "{rid}" {{\n'
        f'  name        = "{label}-sg"\n'
        f'  description = "{desc}"\n'
        f'\n{tags}\n}}'
    )


def _emit_iam_role(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "role"))
    label = node.get("label", "role")
    service = meta.get("principal_service", "ec2.amazonaws.com")
    tags = _tag_block(label, node)
    assume_policy = (
        '  assume_role_policy = jsonencode({\n'
        '    Version = "2012-10-17"\n'
        '    Statement = [{\n'
        '      Action    = "sts:AssumeRole"\n'
        '      Effect    = "Allow"\n'
        f'      Principal = {{ Service = "{service}" }}\n'
        '    }]\n'
        '  })'
    )
    return (
        f'resource "aws_iam_role" "{rid}" {{\n'
        f'  name = "{label}-role"\n'
        f'\n{assume_policy}\n'
        f'\n{tags}\n}}'
    )


# ── Azure Government emitters ─────────────────────────────────────────────────

def _emit_azure_vnet(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "vnet"))
    label = node.get("label", "vnet")
    rg = meta.get("resource_group", "rg-default")
    address_space = meta.get("address_space", "10.0.0.0/8")
    tags = _tag_block(label, node)
    return (
        f'resource "azurerm_virtual_network" "{rid}" {{\n'
        f'  name                = "{label}"\n'
        f'  resource_group_name = "{rg}"\n'
        f'  location            = "{_AZURE_GOV_LOCATION}"\n'
        f'  address_space       = ["{address_space}"]\n'
        f'\n{tags}\n}}'
    )


def _emit_azure_subnet(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "subnet"))
    label = node.get("label", "subnet")
    rg = meta.get("resource_group", "rg-default")
    vnet_name = meta.get("virtual_network_name", "main-vnet")
    address_prefix = meta.get("address_prefix", "10.0.1.0/24")
    return (
        f'resource "azurerm_subnet" "{rid}" {{\n'
        f'  name                 = "{label}"\n'
        f'  resource_group_name  = "{rg}"\n'
        f'  virtual_network_name = "{vnet_name}"\n'
        f'  address_prefixes     = ["{address_prefix}"]\n'
        f'}}'
    )


def _emit_azure_vm(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "vm"))
    label = node.get("label", "vm")
    rg = meta.get("resource_group", "rg-default")
    size = meta.get("size", "Standard_D2s_v3")
    admin_user = meta.get("admin_username", "azureuser")
    nic_id = meta.get("network_interface_id", "nic-placeholder")
    tags = _tag_block(label, node)
    return (
        f'resource "azurerm_linux_virtual_machine" "{rid}" {{\n'
        f'  name                  = "{label}"\n'
        f'  resource_group_name   = "{rg}"\n'
        f'  location              = "{_AZURE_GOV_LOCATION}"\n'
        f'  size                  = "{size}"\n'
        f'  admin_username        = "{admin_user}"\n'
        f'  network_interface_ids = ["{nic_id}"]\n'
        f'\n'
        f'  os_disk {{\n'
        f'    caching              = "ReadWrite"\n'
        f'    storage_account_type = "Premium_LRS"\n'
        f'  }}\n'
        f'\n'
        f'  source_image_reference {{\n'
        f'    publisher = "Canonical"\n'
        f'    offer     = "UbuntuServer"\n'
        f'    sku       = "18.04-LTS"\n'
        f'    version   = "latest"\n'
        f'  }}\n'
        f'\n{tags}\n}}'
    )


def _emit_azure_nsg(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "nsg"))
    label = node.get("label", "nsg")
    rg = meta.get("resource_group", "rg-default")
    tags = _tag_block(label, node)
    return (
        f'resource "azurerm_network_security_group" "{rid}" {{\n'
        f'  name                = "{label}"\n'
        f'  resource_group_name = "{rg}"\n'
        f'  location            = "{_AZURE_GOV_LOCATION}"\n'
        f'\n{tags}\n}}'
    )


def _emit_azure_key_vault(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "kv"))
    label = node.get("label", "kv")
    rg = meta.get("resource_group", "rg-default")
    tenant_id = meta.get("tenant_id", "00000000-0000-0000-0000-000000000000")
    sku = meta.get("sku_name", "premium")
    tags = _tag_block(label, node)
    return (
        f'resource "azurerm_key_vault" "{rid}" {{\n'
        f'  name                      = "{label}"\n'
        f'  resource_group_name       = "{rg}"\n'
        f'  location                  = "{_AZURE_GOV_LOCATION}"\n'
        f'  tenant_id                 = "{tenant_id}"\n'
        f'  sku_name                  = "{sku}"\n'
        f'  enable_rbac_authorization = true\n'
        f'\n{tags}\n}}'
    )


# ── Dispatch tables ───────────────────────────────────────────────────────────

_AWS_TYPE_MAP: dict[str, str] = {
    "aws-vpc": "vpc",
    "aws-subnet": "subnet",
    "aws-ec2": "instance",
    "aws-sg": "security_group",
    "aws-iam-role": "iam_role",
}

_AZURE_GOV_TYPE_MAP: dict[str, str] = {
    "azure-vnet": "azure_vnet",
    "azure-subnet": "azure_subnet",
    "azure-vm": "azure_vm",
    "azure-nsg": "azure_nsg",
    "azure-key-vault": "azure_key_vault",
}

_EMITTERS: dict[str, Any] = {
    "vpc": _emit_vpc,
    "subnet": _emit_subnet,
    "instance": _emit_instance,
    "security_group": _emit_security_group,
    "iam_role": _emit_iam_role,
    "azure_vnet": _emit_azure_vnet,
    "azure_subnet": _emit_azure_subnet,
    "azure_vm": _emit_azure_vm,
    "azure_nsg": _emit_azure_nsg,
    "azure_key_vault": _emit_azure_key_vault,
}

_CSP_TYPE_MAPS: dict[str, dict[str, str]] = {
    "aws": _AWS_TYPE_MAP,
    "aws-govcloud": _AWS_TYPE_MAP,
    "azure": _AZURE_GOV_TYPE_MAP,
    "azure-govcloud": _AZURE_GOV_TYPE_MAP,
}


# ── Public API ────────────────────────────────────────────────────────────────

def emit_resource(node: Node, target_csp: str = "aws-govcloud") -> str:
    """Emit an HCL resource block for a single IDC graph node.

    Args:
        node: IDC graph node dict with keys ``id``, ``type``, ``label``,
              ``metadata``.  ``metadata`` may include ``classification``
              (e.g. ``"CUI"``) to inject compliance tag blocks.
        target_csp: Target cloud provider.  Supported: ``"aws"``,
                    ``"aws-govcloud"`` (AWS GovCloud HCL); ``"azure"``,
                    ``"azure-govcloud"`` (Azure Government HCL).

    Returns:
        HCL string for the resource block.

    Raises:
        UnsupportedResourceError: Node type is not supported for this CSP.
    """
    csp_key = target_csp.lower()
    type_map = _CSP_TYPE_MAPS.get(csp_key)
    if type_map is None:
        raise UnsupportedResourceError(
            f"CSP not supported: {target_csp!r}. Supported: {sorted(_CSP_TYPE_MAPS)}"
        )

    node_type = node.get("type", "")
    resource_kind = type_map.get(node_type)
    if resource_kind is None:
        raise UnsupportedResourceError(
            f"Node type {node_type!r} not supported for CSP {target_csp!r}. "
            f"Supported types: {sorted(type_map)}"
        )

    return _EMITTERS[resource_kind](node)
