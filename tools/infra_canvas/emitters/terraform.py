# CUI // SP-CTI
"""Terraform HCL emitter for IDC graph nodes.

emit_resource(node, target_csp) -> str

Supported resource types:
  AWS / AWS GovCloud: aws-vpc, aws-subnet, aws-ec2, aws-sg, aws-iam-role
  GCP:  gcp-vpc, gcp-subnet, gcp-gce, gcp-firewall, gcp-iam
  OCI:  oci-vcn, oci-subnet, oci-compute, oci-security-list, oci-vault
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


# ── GCP helpers & emitters ────────────────────────────────────────────────────

def _gcp_label_block(label: str, node: Node) -> str:
    """Build a GCP labels = { … } block (lowercase values required by GCP)."""
    meta = node.get("metadata") or {}
    classification = str(meta.get("classification", "")).strip()

    safe_label = re.sub(r"[^a-z0-9_-]", "-", label.lower()).strip("-") or "resource"
    lines = [
        f'    name       = "{safe_label}"',
        f'    managed_by = "{_MANAGED_BY}"',
    ]
    if classification:
        cls_val = re.sub(r"[^a-z0-9_-]", "-", classification.lower()).strip("-")
        lines.append(f'    classification = "{cls_val}"')

    return "  labels = {{\n{}\n  }}".format("\n".join(lines))


def _emit_gcp_vpc(node: Node) -> str:
    rid = _safe_id(node.get("id", "vpc"))
    label = node.get("label", "vpc")
    labels = _gcp_label_block(label, node)
    return (
        f'resource "google_compute_network" "{rid}" {{\n'
        f'  name                    = "{label}"\n'
        f'  auto_create_subnetworks = false\n'
        f'\n{labels}\n}}'
    )


def _emit_gcp_subnet(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "subnet"))
    label = node.get("label", "subnet")
    cidr = meta.get("ip_cidr_range", "10.1.0.0/24")
    region = meta.get("region", "us-central1")
    network = meta.get("network", "default")
    labels = _gcp_label_block(label, node)
    return (
        f'resource "google_compute_subnetwork" "{rid}" {{\n'
        f'  name          = "{label}"\n'
        f'  ip_cidr_range = "{cidr}"\n'
        f'  region        = "{region}"\n'
        f'  network       = "{network}"\n'
        f'\n{labels}\n}}'
    )


def _emit_gcp_instance(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "instance"))
    label = node.get("label", "instance")
    machine_type = meta.get("machine_type", "n2-standard-2")
    zone = meta.get("zone", "us-central1-a")
    image = meta.get("image", "debian-cloud/debian-11")
    labels = _gcp_label_block(label, node)
    return (
        f'resource "google_compute_instance" "{rid}" {{\n'
        f'  name         = "{label}"\n'
        f'  machine_type = "{machine_type}"\n'
        f'  zone         = "{zone}"\n'
        f'\n'
        f'  boot_disk {{\n'
        f'    initialize_params {{\n'
        f'      image = "{image}"\n'
        f'    }}\n'
        f'  }}\n'
        f'\n'
        f'  network_interface {{\n'
        f'    network = "default"\n'
        f'  }}\n'
        f'\n{labels}\n}}'
    )


def _emit_gcp_firewall(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "firewall"))
    label = node.get("label", "firewall")
    network = meta.get("network", "default")
    protocol = meta.get("protocol", "tcp")
    ports = meta.get("ports", ["443"])
    ports_hcl = "[" + ", ".join(f'"{p}"' for p in ports) + "]"
    return (
        f'resource "google_compute_firewall" "{rid}" {{\n'
        f'  name    = "{label}"\n'
        f'  network = "{network}"\n'
        f'\n'
        f'  allow {{\n'
        f'    protocol = "{protocol}"\n'
        f'    ports    = {ports_hcl}\n'
        f'  }}\n'
        f'}}'
    )


def _emit_gcp_iam(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "iam"))
    project = meta.get("project", "my-project")
    role = meta.get("role", "roles/viewer")
    member = meta.get("member", "serviceAccount:sa@my-project.iam.gserviceaccount.com")
    return (
        f'resource "google_project_iam_member" "{rid}" {{\n'
        f'  project = "{project}"\n'
        f'  role    = "{role}"\n'
        f'  member  = "{member}"\n'
        f'}}'
    )


# ── OCI helpers & emitters ────────────────────────────────────────────────────

def _oci_freeform_tags(label: str, node: Node) -> str:
    """Build an OCI freeform_tags = { … } block."""
    meta = node.get("metadata") or {}
    classification = str(meta.get("classification", "")).strip()

    lines = [
        f'    ManagedBy = "{_MANAGED_BY}"',
        f'    Name      = "{label}"',
    ]
    if classification:
        lines.append(f'    Classification = "{classification}"')
        lines.append('    DataHandling   = "CUI//SP-CTI"')

    return "  freeform_tags = {{\n{}\n  }}".format("\n".join(lines))


def _emit_oci_vcn(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "vcn"))
    label = node.get("label", "vcn")
    cidr = meta.get("cidr_block", "10.0.0.0/16")
    compartment = meta.get("compartment_id", "ocid1.compartment.oc1..placeholder")
    tags = _oci_freeform_tags(label, node)
    return (
        f'resource "oci_core_vcn" "{rid}" {{\n'
        f'  compartment_id = "{compartment}"\n'
        f'  cidr_block     = "{cidr}"\n'
        f'  display_name   = "{label}"\n'
        f'\n{tags}\n}}'
    )


def _emit_oci_subnet(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "subnet"))
    label = node.get("label", "subnet")
    cidr = meta.get("cidr_block", "10.0.1.0/24")
    compartment = meta.get("compartment_id", "ocid1.compartment.oc1..placeholder")
    vcn_id = meta.get("vcn_id", "placeholder-vcn-id")
    tags = _oci_freeform_tags(label, node)
    return (
        f'resource "oci_core_subnet" "{rid}" {{\n'
        f'  compartment_id = "{compartment}"\n'
        f'  vcn_id         = "{vcn_id}"\n'
        f'  cidr_block     = "{cidr}"\n'
        f'  display_name   = "{label}"\n'
        f'\n{tags}\n}}'
    )


def _emit_oci_instance(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "instance"))
    label = node.get("label", "instance")
    shape = meta.get("shape", "VM.Standard.E4.Flex")
    compartment = meta.get("compartment_id", "ocid1.compartment.oc1..placeholder")
    ad = meta.get("availability_domain", "ad-1")
    image_id = meta.get("image_id", "ocid1.image.oc1..placeholder")
    tags = _oci_freeform_tags(label, node)
    return (
        f'resource "oci_core_instance" "{rid}" {{\n'
        f'  compartment_id      = "{compartment}"\n'
        f'  availability_domain = "{ad}"\n'
        f'  shape               = "{shape}"\n'
        f'  display_name        = "{label}"\n'
        f'\n'
        f'  source_details {{\n'
        f'    source_type = "image"\n'
        f'    source_id   = "{image_id}"\n'
        f'  }}\n'
        f'\n{tags}\n}}'
    )


def _emit_oci_security_list(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "sl"))
    label = node.get("label", "security-list")
    compartment = meta.get("compartment_id", "ocid1.compartment.oc1..placeholder")
    vcn_id = meta.get("vcn_id", "placeholder-vcn-id")
    tags = _oci_freeform_tags(label, node)
    return (
        f'resource "oci_core_security_list" "{rid}" {{\n'
        f'  compartment_id = "{compartment}"\n'
        f'  vcn_id         = "{vcn_id}"\n'
        f'  display_name   = "{label}"\n'
        f'\n{tags}\n}}'
    )


def _emit_oci_vault(node: Node) -> str:
    meta = node.get("metadata") or {}
    rid = _safe_id(node.get("id", "vault"))
    label = node.get("label", "vault")
    compartment = meta.get("compartment_id", "ocid1.compartment.oc1..placeholder")
    vault_type = meta.get("vault_type", "DEFAULT")
    tags = _oci_freeform_tags(label, node)
    return (
        f'resource "oci_kms_vault" "{rid}" {{\n'
        f'  compartment_id = "{compartment}"\n'
        f'  display_name   = "{label}"\n'
        f'  vault_type     = "{vault_type}"\n'
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

_GCP_TYPE_MAP: dict[str, str] = {
    "gcp-vpc": "gcp_vpc",
    "gcp-subnet": "gcp_subnet",
    "gcp-gce": "gcp_instance",
    "gcp-firewall": "gcp_firewall",
    "gcp-iam": "gcp_iam",
}

_OCI_TYPE_MAP: dict[str, str] = {
    "oci-vcn": "oci_vcn",
    "oci-subnet": "oci_subnet",
    "oci-compute": "oci_instance",
    "oci-security-list": "oci_security_list",
    "oci-vault": "oci_vault",
}

_EMITTERS: dict[str, Any] = {
    # AWS
    "vpc": _emit_vpc,
    "subnet": _emit_subnet,
    "instance": _emit_instance,
    "security_group": _emit_security_group,
    "iam_role": _emit_iam_role,
    # GCP
    "gcp_vpc": _emit_gcp_vpc,
    "gcp_subnet": _emit_gcp_subnet,
    "gcp_instance": _emit_gcp_instance,
    "gcp_firewall": _emit_gcp_firewall,
    "gcp_iam": _emit_gcp_iam,
    # OCI
    "oci_vcn": _emit_oci_vcn,
    "oci_subnet": _emit_oci_subnet,
    "oci_instance": _emit_oci_instance,
    "oci_security_list": _emit_oci_security_list,
    "oci_vault": _emit_oci_vault,
}

_CSP_TYPE_MAPS: dict[str, dict[str, str]] = {
    "aws": _AWS_TYPE_MAP,
    "aws-govcloud": _AWS_TYPE_MAP,
    "gcp": _GCP_TYPE_MAP,
    "oci": _OCI_TYPE_MAP,
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
