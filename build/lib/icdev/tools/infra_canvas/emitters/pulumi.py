# CUI // SP-CTI
"""Pulumi TypeScript emitter for IDC graph nodes.

emit_resource(node, target_csp) -> str

Supported resource types:
  AWS / AWS GovCloud:        aws-vpc, aws-subnet, aws-ec2, aws-sg, aws-iam-role
  Azure / Azure Government:  az-vnet, az-subnet, az-vm, az-nsg, az-role
"""

import re
from typing import Any

# ── Type alias ────────────────────────────────────────────────────────────────
Node = dict[str, Any]

# ── Constants ─────────────────────────────────────────────────────────────────
_GOVCLOUD_REGION = "us-gov-west-1"
_AZURE_GOV_LOCATION = "usgovvirginia"
_MANAGED_BY = "icdev-pulumi-emitter"

_CUI_VALUES = {"CUI", "CUI//SP-CTI", "SECRET", "CUI//SP-CTI/IL4", "CUI//SP-CTI/IL5"}


class UnsupportedResourceError(ValueError):
    """Raised when a node type has no emitter for the given CSP."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_id(raw: str) -> str:
    """Convert an IDC node ID to a camelCase TypeScript variable name."""
    parts = re.split(r"[^a-zA-Z0-9]+", raw)
    parts = [p for p in parts if p]
    if not parts:
        return "resource"
    result = parts[0].lower()
    for p in parts[1:]:
        if p:
            result += p[0].upper() + p[1:].lower()
    if result and result[0].isdigit():
        result = "res" + result[0].upper() + result[1:]
    return result or "resource"


def _ts_tags(label: str, node: Node) -> str:
    """Build a TypeScript tags object block; injects CUI fields when classified."""
    meta = node.get("metadata") or {}
    classification = str(meta.get("classification", "")).strip()

    lines = [
        f'        Name: "{label}",',
        f'        ManagedBy: "{_MANAGED_BY}",',
    ]
    if classification:
        lines.append(f'        Classification: "{classification}",')
        lines.append('        DataHandling: "CUI//SP-CTI",')

    inner = "\n".join(lines)
    return f"    tags: {{\n{inner}\n    }},"


# ── AWS emitters ──────────────────────────────────────────────────────────────

def _emit_vpc(node: Node) -> str:
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "vpc"))
    nid = node.get("id", "vpc")
    cidr = meta.get("cidr_block", "10.0.0.0/16")
    tags = _ts_tags(node.get("label", "vpc"), node)
    return (
        f'const {var} = new aws.ec2.Vpc("{nid}", {{\n'
        f'    cidrBlock: "{cidr}",\n'
        f'    enableDnsHostnames: true,\n'
        f'    enableDnsSupport: true,\n'
        f'{tags}\n'
        f'}});'
    )


def _emit_subnet(node: Node) -> str:
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "subnet"))
    nid = node.get("id", "subnet")
    cidr = meta.get("cidr_block", "10.0.1.0/24")
    az = meta.get("availability_zone", f"{_GOVCLOUD_REGION}a")
    vpc_id = meta.get("vpc_id", "vpc-00000000")
    tags = _ts_tags(node.get("label", "subnet"), node)
    return (
        f'const {var} = new aws.ec2.Subnet("{nid}", {{\n'
        f'    vpcId: "{vpc_id}",\n'
        f'    cidrBlock: "{cidr}",\n'
        f'    availabilityZone: "{az}",\n'
        f'{tags}\n'
        f'}});'
    )


def _emit_instance(node: Node) -> str:
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "instance"))
    nid = node.get("id", "instance")
    ami = meta.get("ami", "ami-0123456789abcdef0")
    itype = meta.get("instance_type", "t3.medium")
    tags = _ts_tags(node.get("label", "instance"), node)
    return (
        f'const {var} = new aws.ec2.Instance("{nid}", {{\n'
        f'    ami: "{ami}",\n'
        f'    instanceType: "{itype}",\n'
        f'{tags}\n'
        f'}});'
    )


def _emit_security_group(node: Node) -> str:
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "sg"))
    nid = node.get("id", "sg")
    label = node.get("label", "sg")
    desc = meta.get("description", f"Security group for {label}")
    tags = _ts_tags(label, node)
    return (
        f'const {var} = new aws.ec2.SecurityGroup("{nid}", {{\n'
        f'    name: "{label}-sg",\n'
        f'    description: "{desc}",\n'
        f'{tags}\n'
        f'}});'
    )


def _emit_iam_role(node: Node) -> str:
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "role"))
    nid = node.get("id", "role")
    label = node.get("label", "role")
    service = meta.get("principal_service", "ec2.amazonaws.com")
    tags = _ts_tags(label, node)
    return (
        f'const {var} = new aws.iam.Role("{nid}", {{\n'
        f'    name: "{label}-role",\n'
        f'    assumeRolePolicy: JSON.stringify({{\n'
        f'        Version: "2012-10-17",\n'
        f'        Statement: [{{\n'
        f'            Action: "sts:AssumeRole",\n'
        f'            Effect: "Allow",\n'
        f'            Principal: {{ Service: "{service}" }},\n'
        f'        }}],\n'
        f'    }}),\n'
        f'{tags}\n'
        f'}});'
    )


# ── Azure emitters ────────────────────────────────────────────────────────────

def _emit_az_vnet(node: Node) -> str:
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "vnet"))
    nid = node.get("id", "vnet")
    label = node.get("label", "vnet")
    cidr = meta.get("address_prefix", "10.0.0.0/16")
    rg = meta.get("resource_group_name", "rg-placeholder")
    location = meta.get("location", _AZURE_GOV_LOCATION)
    tags = _ts_tags(label, node)
    return (
        f'const {var} = new azure_native.network.VirtualNetwork("{nid}", {{\n'
        f'    resourceGroupName: "{rg}",\n'
        f'    location: "{location}",\n'
        f'    addressSpace: {{\n'
        f'        addressPrefixes: ["{cidr}"],\n'
        f'    }},\n'
        f'{tags}\n'
        f'}});'
    )


def _emit_az_subnet(node: Node) -> str:
    # azure_native.network.Subnet has no tags property
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "subnet"))
    nid = node.get("id", "subnet")
    cidr = meta.get("address_prefix", "10.0.1.0/24")
    rg = meta.get("resource_group_name", "rg-placeholder")
    vnet = meta.get("virtual_network_name", "vnet-placeholder")
    return (
        f'const {var} = new azure_native.network.Subnet("{nid}", {{\n'
        f'    resourceGroupName: "{rg}",\n'
        f'    virtualNetworkName: "{vnet}",\n'
        f'    addressPrefix: "{cidr}",\n'
        f'}});'
    )


def _emit_az_vm(node: Node) -> str:
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "vm"))
    nid = node.get("id", "vm")
    label = node.get("label", "vm")
    rg = meta.get("resource_group_name", "rg-placeholder")
    location = meta.get("location", _AZURE_GOV_LOCATION)
    size = meta.get("vm_size", "Standard_D2s_v3")
    nic_id = meta.get("network_interface_id", "nic-placeholder-id")
    publisher = meta.get("publisher", "Canonical")
    offer = meta.get("offer", "UbuntuServer")
    sku = meta.get("sku", "18.04-LTS")
    tags = _ts_tags(label, node)
    return (
        f'const {var} = new azure_native.compute.VirtualMachine("{nid}", {{\n'
        f'    resourceGroupName: "{rg}",\n'
        f'    location: "{location}",\n'
        f'    hardwareProfile: {{\n'
        f'        vmSize: "{size}",\n'
        f'    }},\n'
        f'    storageProfile: {{\n'
        f'        imageReference: {{\n'
        f'            publisher: "{publisher}",\n'
        f'            offer: "{offer}",\n'
        f'            sku: "{sku}",\n'
        f'            version: "latest",\n'
        f'        }},\n'
        f'    }},\n'
        f'    networkProfile: {{\n'
        f'        networkInterfaces: [{{\n'
        f'            id: "{nic_id}",\n'
        f'        }}],\n'
        f'    }},\n'
        f'{tags}\n'
        f'}});'
    )


def _emit_az_nsg(node: Node) -> str:
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "nsg"))
    nid = node.get("id", "nsg")
    label = node.get("label", "nsg")
    rg = meta.get("resource_group_name", "rg-placeholder")
    location = meta.get("location", _AZURE_GOV_LOCATION)
    tags = _ts_tags(label, node)
    return (
        f'const {var} = new azure_native.network.NetworkSecurityGroup("{nid}", {{\n'
        f'    resourceGroupName: "{rg}",\n'
        f'    location: "{location}",\n'
        f'{tags}\n'
        f'}});'
    )


def _emit_az_role(node: Node) -> str:
    # azure_native.authorization.RoleAssignment has no tags property
    meta = node.get("metadata") or {}
    var = _safe_id(node.get("id", "role"))
    nid = node.get("id", "role")
    scope = meta.get("scope", "/subscriptions/placeholder-subscription-id")
    role_def_id = meta.get(
        "role_definition_id",
        "/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7",
    )
    principal_id = meta.get("principal_id", "placeholder-principal-id")
    return (
        f'const {var} = new azure_native.authorization.RoleAssignment("{nid}", {{\n'
        f'    scope: "{scope}",\n'
        f'    roleDefinitionId: "{role_def_id}",\n'
        f'    principalId: "{principal_id}",\n'
        f'}});'
    )


# ── Dispatch tables ───────────────────────────────────────────────────────────

_AWS_TYPE_MAP: dict[str, str] = {
    "aws-vpc": "vpc",
    "aws-subnet": "subnet",
    "aws-ec2": "instance",
    "aws-sg": "security_group",
    "aws-iam-role": "iam_role",
}

_AZURE_TYPE_MAP: dict[str, str] = {
    "az-vnet": "az_vnet",
    "az-subnet": "az_subnet",
    "az-vm": "az_vm",
    "az-nsg": "az_nsg",
    "az-role": "az_role",
}

_EMITTERS: dict[str, Any] = {
    # AWS / AWS GovCloud
    "vpc": _emit_vpc,
    "subnet": _emit_subnet,
    "instance": _emit_instance,
    "security_group": _emit_security_group,
    "iam_role": _emit_iam_role,
    # Azure / Azure Government
    "az_vnet": _emit_az_vnet,
    "az_subnet": _emit_az_subnet,
    "az_vm": _emit_az_vm,
    "az_nsg": _emit_az_nsg,
    "az_role": _emit_az_role,
}

_CSP_TYPE_MAPS: dict[str, dict[str, str]] = {
    "aws": _AWS_TYPE_MAP,
    "aws-govcloud": _AWS_TYPE_MAP,
    "azure": _AZURE_TYPE_MAP,
    "azure-government": _AZURE_TYPE_MAP,
}


# ── Public API ────────────────────────────────────────────────────────────────

def emit_resource(node: Node, target_csp: str = "aws-govcloud") -> str:
    """Emit a TypeScript Pulumi resource declaration for a single IDC graph node.

    Returns a ``const <var> = new <ResourceClass>(...)`` block with no import
    statements (callers assemble the full program with appropriate imports).

    Args:
        node: IDC graph node dict with keys ``id``, ``type``, ``label``,
              ``metadata``.  ``metadata`` may include ``classification``
              (e.g. ``"CUI"``) to inject compliance tags into the output.
        target_csp: Target cloud provider.  Supported: ``"aws"``,
                    ``"aws-govcloud"`` (AWS GovCloud), ``"azure"``,
                    ``"azure-government"`` (Azure Government).

    Returns:
        TypeScript string for the Pulumi resource declaration.

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
