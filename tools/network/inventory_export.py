# [CUI // SP-CTI]
"""ICDEV™ Network Design Canvas — Ansible / Terraform Inventory Export.

Pure functions: take a graph dict and return text strings.
No Flask dependency.

Exported artefacts:
  - Ansible inventory (INI format)  — hosts grouped by security zone/role
  - Terraform HCL skeleton          — VPCs, subnets, security-groups derived
                                      from the diagram's logical structure
"""
from __future__ import annotations

import re
import textwrap
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Node types that represent addressable hosts (ansible targets)
_HOST_TYPES: frozenset[str] = frozenset({
    "server", "router", "firewall", "switch-l3", "switch-l2",
    "wap", "wlc", "siem", "network-tap",
    "endpoint-pc", "endpoint-phone", "endpoint-iot", "endpoint-camera",
    "aws-alb", "aws-nlb", "aws-r53",
    "az-bastion", "az-appgw",
    "gcp-lb", "gcp-router",
    "oci-lb",
    "ibm-lb",
})

# Node types that represent network infrastructure (terraform resources)
_CLOUD_NODE_ROLES: dict[str, str] = {
    # AWS
    "aws-vpc":       "vpc",
    "aws-subnet":    "subnet",
    "aws-tgw":       "transit_gateway",
    "aws-alb":       "lb",
    "aws-nlb":       "lb",
    "aws-nfw":       "firewall",
    "aws-waf":       "waf",
    "aws-cloudfront":"cdn",
    "aws-r53":       "dns",
    "aws-dx":        "direct_connect",
    "aws-vpn":       "vpn_gateway",
    "aws-gw-ep":     "vpc_endpoint",
    # Azure
    "az-vnet":       "virtual_network",
    "az-subnet":     "subnet",
    "az-vwan":       "virtual_wan",
    "az-er":         "express_route_circuit",
    "az-vpn-gw":     "vpn_gateway",
    "az-fw":         "firewall",
    "az-appgw":      "application_gateway",
    "az-front":      "frontdoor",
    "az-dns":        "dns_zone",
    "az-bastion":    "bastion_host",
    "az-nsg":        "network_security_group",
    # GCP
    "gcp-vpc":       "compute_network",
    "gcp-subnet":    "compute_subnetwork",
    "gcp-lb":        "compute_global_forwarding_rule",
    "gcp-nat":       "compute_router_nat",
    "gcp-vpn":       "compute_ha_vpn_gateway",
    "gcp-armor":     "compute_security_policy",
    "gcp-cdn":       "compute_backend_bucket",
    "gcp-dns":       "dns_managed_zone",
    "gcp-ic":        "compute_interconnect_attachment",
    "gcp-router":    "compute_router",
    # OCI
    "oci-vcn":       "core_vcn",
    "oci-subnet":    "core_subnet",
    "oci-drg":       "core_drg",
    "oci-fc":        "fastconnect_virtual_circuit",
    "oci-lb":        "load_balancer",
    "oci-waf":       "waf_policy",
    "oci-nsg":       "core_network_security_group",
    # IBM
    "ibm-vpc":       "is_vpc",
    "ibm-subnet":    "is_subnet",
    "ibm-dl":        "dl_gateway",
    "ibm-vpn":       "is_vpn_gateway",
    "ibm-lb":        "is_lb",
    "ibm-tg":        "tg_gateway",
}

_CLOUD_PREFIXES: tuple[str, ...] = ("aws-", "az-", "gcp-", "oci-", "ibm-")
_PROVIDER_MAP: dict[str, str] = {
    "aws-": "aws",
    "az-":  "azurerm",
    "gcp-": "google",
    "oci-": "oci",
    "ibm-": "ibm",
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_id(label: str) -> str:
    """Turn a node label into a valid Terraform/Ansible identifier."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", label.strip())
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s or "node"


def _classify_zone(node: dict) -> str:
    """Return a simplified zone/role name suitable as an Ansible group."""
    ntype = node.get("type", "")
    label = (node.get("label") or "").lower()

    if ntype in ("server",):
        return "servers"
    if ntype in ("router",):
        return "routers"
    if ntype in ("firewall", "aws-nfw", "az-fw", "gcp-armor", "oci-waf", "aws-waf"):
        return "firewalls"
    if ntype in ("switch-l3", "switch-l2"):
        return "switches"
    if ntype in ("wap", "wlc"):
        return "wireless"
    if ntype in ("siem", "network-tap"):
        return "management"
    if ntype in ("endpoint-pc", "endpoint-phone"):
        return "endpoints"
    if ntype in ("endpoint-iot", "endpoint-camera"):
        return "iot"
    if ntype.startswith("aws-"):
        return "aws"
    if ntype.startswith("az-"):
        return "azure"
    if ntype.startswith("gcp-"):
        return "gcp"
    if ntype.startswith("oci-"):
        return "oci"
    if ntype.startswith("ibm-"):
        return "ibm"
    if "dmz" in label:
        return "dmz"
    if "mgmt" in label or "management" in label:
        return "management"
    return "ungrouped"


def _detect_providers(nodes: list[dict]) -> list[str]:
    """Return deduplicated list of cloud providers present in the topology."""
    providers: list[str] = []
    seen: set[str] = set()
    for n in nodes:
        ntype = n.get("type", "")
        for pfx, prov in _PROVIDER_MAP.items():
            if ntype.startswith(pfx) and prov not in seen:
                providers.append(prov)
                seen.add(prov)
    return providers


# ---------------------------------------------------------------------------
# 1. Ansible Inventory (INI format)
# ---------------------------------------------------------------------------

def to_ansible_inventory(graph: dict, name: str) -> str:
    """Generate an Ansible inventory INI file from a topology graph.

    Hosts are grouped by security zone/role derived from node type and label.
    Only addressable nodes (routers, firewalls, servers, endpoints, etc.) are
    included; pure network-infrastructure nodes (VPCs, subnets, cloud services)
    are emitted as comments for reference.

    Args:
        graph: Dict with "nodes" and "edges" lists.
        name:  Topology name — used in the header comment.

    Returns:
        Ansible inventory INI string.
    """
    nodes = graph.get("nodes", [])

    # Separate host nodes from cloud-infra nodes
    host_nodes: list[dict] = []
    infra_nodes: list[dict] = []
    for n in nodes:
        ntype = n.get("type", "")
        if ntype in _HOST_TYPES:
            host_nodes.append(n)
        else:
            infra_nodes.append(n)

    # Group hosts by zone
    groups: dict[str, list[dict]] = {}
    for n in host_nodes:
        zone = _classify_zone(n)
        groups.setdefault(zone, []).append(n)

    lines: list[str] = []
    lines.append(f"# Ansible Inventory — {name}")
    lines.append(f"# Generated: {_now_utc()} by ICDEV™ Network Canvas")
    lines.append(f"# Topology nodes: {len(nodes)}  |  Addressable hosts: {len(host_nodes)}")
    lines.append("")

    # [all:vars] block
    lines.append("[all:vars]")
    lines.append("ansible_connection=ssh")
    lines.append("ansible_user=ansible")
    lines.append("ansible_become=yes")
    lines.append("")

    # Per-group sections
    for group_name in sorted(groups.keys()):
        group_hosts = groups[group_name]
        lines.append(f"[{group_name}]")
        for n in group_hosts:
            label = n.get("label", n["id"])
            hostname = _safe_id(label)
            ip = n.get("ip") or n.get("ip_address") or ""
            vars_parts: list[str] = []
            if ip:
                vars_parts.append(f"ansible_host={ip}")
            vars_parts.append(f"node_id={n['id']}")
            vars_parts.append(f"node_type={n.get('type', 'unknown')}")
            vars_str = "  " + "  ".join(vars_parts) if vars_parts else ""
            lines.append(f"{hostname}{vars_str}")
        lines.append("")

    # [all:children] meta group
    if groups:
        lines.append("[all:children]")
        for g in sorted(groups.keys()):
            lines.append(g)
        lines.append("")

    # Cloud infrastructure reference (non-host nodes)
    if infra_nodes:
        lines.append(
            "# ── Cloud / Network Infrastructure (not Ansible-managed) ──────────────"
        )
        lines.append("# These nodes exist in the topology but are not SSH-addressable hosts.")
        lines.append("# Configure them via cloud-provider modules or Terraform (see HCL export).")
        lines.append("#")
        for n in infra_nodes:
            ntype = n.get("type", "unknown")
            label = n.get("label", n["id"])
            lines.append(f"#   [{ntype}] {label}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. Terraform HCL skeleton
# ---------------------------------------------------------------------------

_PROVIDER_BLOCKS: dict[str, str] = {
    "aws": textwrap.dedent("""\
        provider "aws" {
          region = var.aws_region
        }

        variable "aws_region" {
          description = "AWS region to deploy into"
          type        = string
          default     = "us-east-1"
        }
    """),
    "azurerm": textwrap.dedent("""\
        provider "azurerm" {
          features {}
        }

        variable "azure_resource_group" {
          description = "Azure Resource Group name"
          type        = string
          default     = "rg-network"
        }

        variable "azure_location" {
          description = "Azure region"
          type        = string
          default     = "eastus"
        }
    """),
    "google": textwrap.dedent("""\
        provider "google" {
          project = var.gcp_project
          region  = var.gcp_region
        }

        variable "gcp_project" {
          description = "GCP project ID"
          type        = string
        }

        variable "gcp_region" {
          description = "GCP region"
          type        = string
          default     = "us-east1"
        }
    """),
    "oci": textwrap.dedent("""\
        provider "oci" {
          region = var.oci_region
        }

        variable "oci_region" {
          type    = string
          default = "us-ashburn-1"
        }

        variable "oci_compartment_id" {
          description = "OCI Compartment OCID"
          type        = string
        }
    """),
    "ibm": textwrap.dedent("""\
        provider "ibm" {
          region = var.ibm_region
        }

        variable "ibm_region" {
          type    = string
          default = "us-east"
        }
    """),
}

# Terraform resource templates per node type
# {label} = safe TF identifier, {node_label} = original label
_RESOURCE_TEMPLATES: dict[str, str] = {
    # ── AWS ────────────────────────────────────────────────────────────────
    "aws-vpc": textwrap.dedent("""\
        resource "aws_vpc" "{label}" {{
          cidr_block           = "10.0.0.0/16"  # TODO: adjust CIDR
          enable_dns_support   = true
          enable_dns_hostnames = true
          tags = {{
            Name = "{node_label}"
          }}
        }}
    """),
    "aws-subnet": textwrap.dedent("""\
        resource "aws_subnet" "{label}" {{
          vpc_id            = aws_vpc.<vpc_id>.id  # TODO: reference correct VPC
          cidr_block        = "10.0.1.0/24"        # TODO: adjust CIDR
          availability_zone = "${{var.aws_region}}a"
          tags = {{
            Name = "{node_label}"
          }}
        }}
    """),
    "aws-tgw": textwrap.dedent("""\
        resource "aws_ec2_transit_gateway" "{label}" {{
          description = "{node_label}"
          tags = {{
            Name = "{node_label}"
          }}
        }}
    """),
    "aws-nfw": textwrap.dedent("""\
        resource "aws_networkfirewall_firewall" "{label}" {{
          name                = "{node_label}"
          firewall_policy_arn = aws_networkfirewall_firewall_policy.{label}_policy.arn
          vpc_id              = aws_vpc.<vpc_id>.id  # TODO: reference correct VPC
          subnet_mapping {{
            subnet_id = aws_subnet.<subnet_id>.id    # TODO: reference firewall subnet
          }}
          tags = {{
            Name = "{node_label}"
          }}
        }}
    """),
    "aws-alb": textwrap.dedent("""\
        resource "aws_lb" "{label}" {{
          name               = "{node_label}"
          load_balancer_type = "application"
          internal           = false
          subnets            = []  # TODO: list subnet IDs
          tags = {{
            Name = "{node_label}"
          }}
        }}
    """),
    "aws-nlb": textwrap.dedent("""\
        resource "aws_lb" "{label}" {{
          name               = "{node_label}"
          load_balancer_type = "network"
          internal           = true
          subnets            = []  # TODO: list subnet IDs
          tags = {{
            Name = "{node_label}"
          }}
        }}
    """),
    "aws-waf": textwrap.dedent("""\
        resource "aws_wafv2_web_acl" "{label}" {{
          name  = "{node_label}"
          scope = "REGIONAL"
          default_action {{ allow {{}} }}
          visibility_config {{
            cloudwatch_metrics_enabled = true
            metric_name                = "{label}"
            sampled_requests_enabled   = true
          }}
          tags = {{
            Name = "{node_label}"
          }}
        }}
    """),
    "aws-vpn": textwrap.dedent("""\
        resource "aws_vpn_gateway" "{label}" {{
          tags = {{ Name = "{node_label}" }}
        }}
    """),
    "aws-dx": textwrap.dedent("""\
        resource "aws_dx_connection" "{label}" {{
          name      = "{node_label}"
          bandwidth = "1Gbps"      # TODO: adjust bandwidth
          location  = "EqDC2"     # TODO: set colocation facility
          tags      = {{ Name = "{node_label}" }}
        }}
    """),
    # ── Azure ──────────────────────────────────────────────────────────────
    "az-vnet": textwrap.dedent("""\
        resource "azurerm_virtual_network" "{label}" {{
          name                = "{node_label}"
          resource_group_name = var.azure_resource_group
          location            = var.azure_location
          address_space       = ["10.0.0.0/16"]  # TODO: adjust CIDR
          tags = {{
            Name = "{node_label}"
          }}
        }}
    """),
    "az-subnet": textwrap.dedent("""\
        resource "azurerm_subnet" "{label}" {{
          name                 = "{node_label}"
          resource_group_name  = var.azure_resource_group
          virtual_network_name = azurerm_virtual_network.<vnet_name>.name  # TODO
          address_prefixes     = ["10.0.1.0/24"]  # TODO: adjust CIDR
        }}
    """),
    "az-nsg": textwrap.dedent("""\
        resource "azurerm_network_security_group" "{label}" {{
          name                = "{node_label}"
          resource_group_name = var.azure_resource_group
          location            = var.azure_location
          # TODO: add security_rule blocks
          tags = {{ Name = "{node_label}" }}
        }}
    """),
    "az-fw": textwrap.dedent("""\
        resource "azurerm_firewall" "{label}" {{
          name                = "{node_label}"
          resource_group_name = var.azure_resource_group
          location            = var.azure_location
          sku_name            = "AZFW_VNet"
          sku_tier            = "Standard"
          ip_configuration {{
            name                 = "configuration"
            subnet_id            = azurerm_subnet.<fw_subnet>.id   # TODO
            public_ip_address_id = azurerm_public_ip.{label}_pip.id
          }}
          tags = {{ Name = "{node_label}" }}
        }}
    """),
    "az-appgw": textwrap.dedent("""\
        resource "azurerm_application_gateway" "{label}" {{
          name                = "{node_label}"
          resource_group_name = var.azure_resource_group
          location            = var.azure_location
          sku {{
            name     = "Standard_v2"
            tier     = "Standard_v2"
            capacity = 2
          }}
          # TODO: add gateway_ip_configuration, frontend_*, backend_*, http_listener, request_routing_rule
          tags = {{ Name = "{node_label}" }}
        }}
    """),
    "az-vwan": textwrap.dedent("""\
        resource "azurerm_virtual_wan" "{label}" {{
          name                = "{node_label}"
          resource_group_name = var.azure_resource_group
          location            = var.azure_location
          tags = {{ Name = "{node_label}" }}
        }}
    """),
    # ── GCP ────────────────────────────────────────────────────────────────
    "gcp-vpc": textwrap.dedent("""\
        resource "google_compute_network" "{label}" {{
          name                    = "{node_label}"
          auto_create_subnetworks = false
        }}
    """),
    "gcp-subnet": textwrap.dedent("""\
        resource "google_compute_subnetwork" "{label}" {{
          name          = "{node_label}"
          ip_cidr_range = "10.0.1.0/24"  # TODO: adjust CIDR
          region        = var.gcp_region
          network       = google_compute_network.<vpc_name>.id  # TODO
        }}
    """),
    "gcp-armor": textwrap.dedent("""\
        resource "google_compute_security_policy" "{label}" {{
          name = "{node_label}"
          rule {{
            action   = "allow"
            priority = "2147483647"
            match {{
              versioned_expr = "SRC_IPS_V1"
              config {{ src_ip_ranges = ["*"] }}
            }}
            description = "default allow"
          }}
        }}
    """),
    "gcp-router": textwrap.dedent("""\
        resource "google_compute_router" "{label}" {{
          name    = "{node_label}"
          region  = var.gcp_region
          network = google_compute_network.<vpc_name>.id  # TODO
        }}
    """),
    "gcp-nat": textwrap.dedent("""\
        resource "google_compute_router_nat" "{label}" {{
          name                               = "{node_label}"
          router                             = google_compute_router.<router_name>.name  # TODO
          region                             = var.gcp_region
          nat_ip_allocate_option             = "AUTO_ONLY"
          source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
        }}
    """),
    # ── OCI ────────────────────────────────────────────────────────────────
    "oci-vcn": textwrap.dedent("""\
        resource "oci_core_vcn" "{label}" {{
          compartment_id = var.oci_compartment_id
          cidr_block     = "10.0.0.0/16"  # TODO: adjust CIDR
          display_name   = "{node_label}"
        }}
    """),
    "oci-subnet": textwrap.dedent("""\
        resource "oci_core_subnet" "{label}" {{
          compartment_id = var.oci_compartment_id
          vcn_id         = oci_core_vcn.<vcn_name>.id  # TODO
          cidr_block     = "10.0.1.0/24"               # TODO: adjust CIDR
          display_name   = "{node_label}"
        }}
    """),
    "oci-drg": textwrap.dedent("""\
        resource "oci_core_drg" "{label}" {{
          compartment_id = var.oci_compartment_id
          display_name   = "{node_label}"
        }}
    """),
    # ── IBM ────────────────────────────────────────────────────────────────
    "ibm-vpc": textwrap.dedent("""\
        resource "ibm_is_vpc" "{label}" {{
          name = "{node_label}"
        }}
    """),
    "ibm-subnet": textwrap.dedent("""\
        resource "ibm_is_subnet" "{label}" {{
          name            = "{node_label}"
          vpc             = ibm_is_vpc.<vpc_name>.id  # TODO
          zone            = "${{var.ibm_region}}-1"
          ipv4_cidr_block = "10.0.1.0/24"             # TODO: adjust CIDR
        }}
    """),
}


def to_terraform_hcl(graph: dict, name: str) -> str:
    """Generate a Terraform HCL skeleton from a topology graph.

    The skeleton includes:
    - terraform {} block with required providers
    - provider blocks for each detected cloud (AWS, Azure, GCP, OCI, IBM)
    - resource blocks for each cloud node in the topology
    - a locals block mapping edges to conceptual security-group rules
    - variable stubs for CIDRs and region overrides

    Args:
        graph: Dict with "nodes" and "edges" lists.
        name:  Topology name — used in the file header comment.

    Returns:
        Terraform HCL string (main.tf equivalent).
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    providers = _detect_providers(nodes)

    lines: list[str] = []
    lines.append(f"# Terraform HCL Skeleton — {name}")
    lines.append(f"# Generated: {_now_utc()} by ICDEV™ Network Canvas")
    lines.append(f"# Topology: {len(nodes)} nodes, {len(edges)} edges")
    lines.append("#")
    lines.append("# IMPORTANT: This is a skeleton — review all TODO comments before applying.")
    lines.append("# Run: terraform init && terraform plan")
    lines.append("")

    # terraform {} block
    if providers:
        lines.append('terraform {')
        lines.append('  required_providers {')
        provider_sources = {
            "aws":      "hashicorp/aws",
            "azurerm":  "hashicorp/azurerm",
            "google":   "hashicorp/google",
            "oci":      "oracle/oci",
            "ibm":      "IBM-Cloud/ibm",
        }
        for prov in providers:
            src = provider_sources.get(prov, f"hashicorp/{prov}")
            lines.append(f'    {prov} = {{')
            lines.append(f'      source  = "{src}"')
            lines.append('      version = ">= 5.0"')
            lines.append('    }')
        lines.append('  }')
        lines.append('}')
        lines.append('')

    # Provider configuration blocks
    for prov in providers:
        if prov in _PROVIDER_BLOCKS:
            lines.append(_PROVIDER_BLOCKS[prov])

    # Resource blocks
    node_map: dict[str, dict] = {n["id"]: n for n in nodes}
    rendered_ids: set[str] = set()
    unknown_nodes: list[dict] = []

    for n in nodes:
        ntype = n.get("type", "")
        label_raw = n.get("label", n["id"])
        tf_id = _safe_id(label_raw)

        # Deduplicate Terraform IDs
        base_id = tf_id
        suffix = 2
        while tf_id in rendered_ids:
            tf_id = f"{base_id}_{suffix}"
            suffix += 1
        rendered_ids.add(tf_id)

        if ntype in _RESOURCE_TEMPLATES:
            block = _RESOURCE_TEMPLATES[ntype].format(
                label=tf_id,
                node_label=label_raw,
            )
            lines.append(block)
        elif ntype in _HOST_TYPES:
            # Physical/virtual server — emit a placeholder comment
            lines.append(f"# [{ntype}] {label_raw} — not a cloud resource; manage via Ansible")
            lines.append("")
        else:
            unknown_nodes.append(n)

    # Edge-derived security group rules (locals block)
    sg_rules: list[str] = []
    for e in edges:
        src = node_map.get(e.get("source", ""), {})
        dst = node_map.get(e.get("target", ""), {})
        src_label = src.get("label", e.get("source", "?"))
        dst_label = dst.get("label", e.get("target", "?"))
        proto = (e.get("protocol") or e.get("label") or "any").lower()
        sg_rules.append(
            f'    {{ src = "{_safe_id(src_label)}", dst = "{_safe_id(dst_label)}", '
            f'proto = "{proto}" }}'
        )

    if sg_rules:
        lines.append("locals {")
        lines.append(
            "  # Edge-derived connectivity rules — use these to populate security group / NACL / firewall policies"
        )
        lines.append("  topology_edges = [")
        for r in sg_rules:
            lines.append(f"    {r},")
        lines.append("  ]")
        lines.append("}")
        lines.append("")

    # Unknown node types — reference comment
    if unknown_nodes:
        lines.append(
            "# ── Unrecognised node types (no Terraform resource template) ──────────"
        )
        for n in unknown_nodes:
            lines.append(
                f"# [{n.get('type', 'unknown')}] {n.get('label', n['id'])}"
            )
        lines.append("")

    return "\n".join(lines)
