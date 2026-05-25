# CUI // SP-CTI
"""Tests for tools/infra_canvas/emitters/terraform.py.

21 cases (6 AWS + 5 GCP + 5 OCI + 3 cross-cutting + 2 smoke):
  1–5.  AWS: vpc, subnet, ec2, sg, iam-role + CUI tags
  6.    AWS terraform validate smoke
  7–11. GCP: vpc, subnet, gce, firewall, iam
  12.   GCP CUI label injection
  13.   GCP terraform validate smoke
  14–18. OCI: vcn, subnet, compute, security-list, vault
  19.   OCI CUI freeform_tags injection
  20.   OCI terraform validate smoke
  21.   Unsupported CSP raises UnsupportedResourceError
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tools.infra_canvas.emitters.terraform import UnsupportedResourceError, emit_resource

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _node(node_id: str, ntype: str, label: str, **meta) -> dict:
    return {"id": node_id, "type": ntype, "label": label, "metadata": meta}


TERRAFORM_BIN = shutil.which("terraform")


# ── Case 1: VPC ───────────────────────────────────────────────────────────────

def test_emit_vpc():
    node = _node("n-vpc-01", "aws-vpc", "main-vpc", cidr_block="172.16.0.0/16")
    hcl = emit_resource(node, "aws-govcloud")

    assert 'resource "aws_vpc" "n_vpc_01"' in hcl
    assert 'cidr_block           = "172.16.0.0/16"' in hcl
    assert "enable_dns_hostnames = true" in hcl
    assert 'Name      = "main-vpc"' in hcl


# ── Case 2: Subnet ────────────────────────────────────────────────────────────

def test_emit_subnet():
    node = _node(
        "n-sub-01",
        "aws-subnet",
        "private-subnet",
        cidr_block="10.0.2.0/24",
        availability_zone="us-gov-west-1b",
    )
    hcl = emit_resource(node, "aws-govcloud")

    assert 'resource "aws_subnet" "n_sub_01"' in hcl
    assert 'cidr_block        = "10.0.2.0/24"' in hcl
    assert 'availability_zone = "us-gov-west-1b"' in hcl
    assert "vpc_id" in hcl


# ── Case 3: EC2 Instance ──────────────────────────────────────────────────────

def test_emit_instance():
    node = _node(
        "n-ec2-01",
        "aws-ec2",
        "web-server",
        ami="ami-abcdef1234567890",
        instance_type="m5.xlarge",
    )
    hcl = emit_resource(node, "aws-govcloud")

    assert 'resource "aws_instance" "n_ec2_01"' in hcl
    assert 'ami           = "ami-abcdef1234567890"' in hcl
    assert 'instance_type = "m5.xlarge"' in hcl


# ── Case 4: Security Group ────────────────────────────────────────────────────

def test_emit_security_group():
    node = _node("n-sg-01", "aws-sg", "web-sg", description="Allow HTTPS inbound")
    hcl = emit_resource(node, "aws-govcloud")

    assert 'resource "aws_security_group" "n_sg_01"' in hcl
    assert 'name        = "web-sg-sg"' in hcl
    assert 'description = "Allow HTTPS inbound"' in hcl


# ── Case 5: IAM Role + CUI tag injection ─────────────────────────────────────

def test_emit_iam_role_with_cui_tags():
    node = _node(
        "n-role-01",
        "aws-iam-role",
        "app-role",
        principal_service="lambda.amazonaws.com",
        classification="CUI",
    )
    hcl = emit_resource(node, "aws-govcloud")

    assert 'resource "aws_iam_role" "n_role_01"' in hcl
    assert 'name = "app-role-role"' in hcl
    assert '"sts:AssumeRole"' in hcl
    assert '"lambda.amazonaws.com"' in hcl
    # CUI classification tag block must be present
    assert 'Classification = "CUI"' in hcl
    assert 'DataHandling   = "CUI//SP-CTI"' in hcl


# ── Case 6: terraform validate smoke ─────────────────────────────────────────

@pytest.mark.skipif(TERRAFORM_BIN is None, reason="terraform binary not in PATH")
def test_terraform_validate_smoke(tmp_path: Path):
    """All 5 resource types combined into a smoke project pass terraform validate."""
    nodes = [
        _node("n-vpc-s", "aws-vpc", "smoke-vpc"),
        _node("n-sub-s", "aws-subnet", "smoke-subnet"),
        _node("n-ec2-s", "aws-ec2", "smoke-server"),
        _node("n-sg-s", "aws-sg", "smoke-sg"),
        _node("n-role-s", "aws-iam-role", "smoke-role"),
    ]

    provider_tf = textwrap.dedent("""\
        terraform {
          required_providers {
            aws = {
              source  = "hashicorp/aws"
              version = "~> 5.0"
            }
          }
        }

        provider "aws" {
          region = "us-gov-west-1"
        }
    """)

    (tmp_path / "provider.tf").write_text(provider_tf, encoding="utf-8")

    hcl_blocks = [emit_resource(n, "aws-govcloud") for n in nodes]
    (tmp_path / "main.tf").write_text("\n\n".join(hcl_blocks) + "\n", encoding="utf-8")

    result = subprocess.run(
        [TERRAFORM_BIN, "validate"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"terraform validate failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# ── Case 7: GCP VPC ───────────────────────────────────────────────────────────

def test_emit_gcp_vpc():
    node = _node("n-gvpc-01", "gcp-vpc", "main-network")
    hcl = emit_resource(node, "gcp")

    assert 'resource "google_compute_network" "n_gvpc_01"' in hcl
    assert 'name                    = "main-network"' in hcl
    assert "auto_create_subnetworks = false" in hcl
    assert 'managed_by = "icdev-terraform-emitter"' in hcl


# ── Case 8: GCP Subnet ────────────────────────────────────────────────────────

def test_emit_gcp_subnet():
    node = _node(
        "n-gsub-01",
        "gcp-subnet",
        "app-subnet",
        ip_cidr_range="192.168.1.0/24",
        region="us-east1",
        network="projects/my-project/global/networks/main",
    )
    hcl = emit_resource(node, "gcp")

    assert 'resource "google_compute_subnetwork" "n_gsub_01"' in hcl
    assert 'ip_cidr_range = "192.168.1.0/24"' in hcl
    assert 'region        = "us-east1"' in hcl
    assert 'network       = "projects/my-project/global/networks/main"' in hcl


# ── Case 9: GCP GCE Instance ──────────────────────────────────────────────────

def test_emit_gcp_instance():
    node = _node(
        "n-gce-01",
        "gcp-gce",
        "web-vm",
        machine_type="n2-standard-4",
        zone="us-central1-b",
        image="debian-cloud/debian-12",
    )
    hcl = emit_resource(node, "gcp")

    assert 'resource "google_compute_instance" "n_gce_01"' in hcl
    assert 'machine_type = "n2-standard-4"' in hcl
    assert 'zone         = "us-central1-b"' in hcl
    assert 'image = "debian-cloud/debian-12"' in hcl
    assert "boot_disk" in hcl
    assert "network_interface" in hcl


# ── Case 10: GCP Firewall ─────────────────────────────────────────────────────

def test_emit_gcp_firewall():
    node = _node(
        "n-gfw-01",
        "gcp-firewall",
        "allow-https",
        network="projects/my-project/global/networks/main",
        protocol="tcp",
        ports=["443", "8443"],
    )
    hcl = emit_resource(node, "gcp")

    assert 'resource "google_compute_firewall" "n_gfw_01"' in hcl
    assert 'name    = "allow-https"' in hcl
    assert 'protocol = "tcp"' in hcl
    assert '"443"' in hcl
    assert '"8443"' in hcl


# ── Case 11: GCP IAM ──────────────────────────────────────────────────────────

def test_emit_gcp_iam():
    node = _node(
        "n-giam-01",
        "gcp-iam",
        "viewer-binding",
        project="my-gov-project",
        role="roles/viewer",
        member="serviceAccount:app@my-gov-project.iam.gserviceaccount.com",
    )
    hcl = emit_resource(node, "gcp")

    assert 'resource "google_project_iam_member" "n_giam_01"' in hcl
    assert 'project = "my-gov-project"' in hcl
    assert 'role    = "roles/viewer"' in hcl
    assert 'member  = "serviceAccount:app@my-gov-project.iam.gserviceaccount.com"' in hcl


# ── Case 12: GCP CUI label injection ─────────────────────────────────────────

def test_emit_gcp_instance_with_cui_labels():
    node = _node(
        "n-gce-cui",
        "gcp-gce",
        "secure-vm",
        machine_type="c2-standard-8",
        zone="us-central1-a",
        classification="CUI//SP-CTI",
    )
    hcl = emit_resource(node, "gcp")

    assert 'resource "google_compute_instance" "n_gce_cui"' in hcl
    assert "classification" in hcl
    assert "cui" in hcl  # GCP labels are lowercase


# ── Case 13: GCP terraform validate smoke ────────────────────────────────────

@pytest.mark.skipif(TERRAFORM_BIN is None, reason="terraform binary not in PATH")
def test_gcp_terraform_validate_smoke(tmp_path: Path):
    """All 5 GCP resource types pass terraform validate."""
    nodes = [
        _node("n-gvpc-s", "gcp-vpc", "smoke-network"),
        _node("n-gsub-s", "gcp-subnet", "smoke-subnet", network="smoke-network"),
        _node("n-gce-s", "gcp-gce", "smoke-vm"),
        _node("n-gfw-s", "gcp-firewall", "smoke-fw"),
        _node("n-giam-s", "gcp-iam", "smoke-iam"),
    ]

    provider_tf = textwrap.dedent("""\
        terraform {
          required_providers {
            google = {
              source  = "hashicorp/google"
              version = "~> 5.0"
            }
          }
        }

        provider "google" {
          project = "my-project"
          region  = "us-central1"
        }
    """)

    (tmp_path / "provider.tf").write_text(provider_tf, encoding="utf-8")
    hcl_blocks = [emit_resource(n, "gcp") for n in nodes]
    (tmp_path / "main.tf").write_text("\n\n".join(hcl_blocks) + "\n", encoding="utf-8")

    result = subprocess.run(
        [TERRAFORM_BIN, "validate"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"GCP terraform validate failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# ── Case 14: OCI VCN ──────────────────────────────────────────────────────────

def test_emit_oci_vcn():
    node = _node(
        "n-ovcn-01",
        "oci-vcn",
        "main-vcn",
        cidr_block="10.2.0.0/16",
        compartment_id="ocid1.compartment.oc1..test",
    )
    hcl = emit_resource(node, "oci")

    assert 'resource "oci_core_vcn" "n_ovcn_01"' in hcl
    assert 'cidr_block     = "10.2.0.0/16"' in hcl
    assert 'compartment_id = "ocid1.compartment.oc1..test"' in hcl
    assert 'display_name   = "main-vcn"' in hcl
    assert 'Name      = "main-vcn"' in hcl


# ── Case 15: OCI Subnet ───────────────────────────────────────────────────────

def test_emit_oci_subnet():
    node = _node(
        "n-osub-01",
        "oci-subnet",
        "app-subnet",
        cidr_block="10.2.1.0/24",
        vcn_id="ocid1.vcn.oc1..test",
        compartment_id="ocid1.compartment.oc1..test",
    )
    hcl = emit_resource(node, "oci")

    assert 'resource "oci_core_subnet" "n_osub_01"' in hcl
    assert 'cidr_block     = "10.2.1.0/24"' in hcl
    assert 'vcn_id         = "ocid1.vcn.oc1..test"' in hcl
    assert 'display_name   = "app-subnet"' in hcl


# ── Case 16: OCI Compute Instance ────────────────────────────────────────────

def test_emit_oci_instance():
    node = _node(
        "n-oci-01",
        "oci-compute",
        "app-server",
        shape="VM.Standard.E4.Flex",
        availability_domain="iad-ad-1",
        image_id="ocid1.image.oc1..test",
        compartment_id="ocid1.compartment.oc1..test",
    )
    hcl = emit_resource(node, "oci")

    assert 'resource "oci_core_instance" "n_oci_01"' in hcl
    assert 'shape               = "VM.Standard.E4.Flex"' in hcl
    assert 'availability_domain = "iad-ad-1"' in hcl
    assert 'source_type = "image"' in hcl
    assert 'source_id   = "ocid1.image.oc1..test"' in hcl


# ── Case 17: OCI Security List ────────────────────────────────────────────────

def test_emit_oci_security_list():
    node = _node(
        "n-osl-01",
        "oci-security-list",
        "app-sl",
        vcn_id="ocid1.vcn.oc1..test",
        compartment_id="ocid1.compartment.oc1..test",
    )
    hcl = emit_resource(node, "oci")

    assert 'resource "oci_core_security_list" "n_osl_01"' in hcl
    assert 'vcn_id         = "ocid1.vcn.oc1..test"' in hcl
    assert 'display_name   = "app-sl"' in hcl


# ── Case 18: OCI Vault ────────────────────────────────────────────────────────

def test_emit_oci_vault():
    node = _node(
        "n-ovlt-01",
        "oci-vault",
        "secrets-vault",
        vault_type="DEFAULT",
        compartment_id="ocid1.compartment.oc1..test",
    )
    hcl = emit_resource(node, "oci")

    assert 'resource "oci_kms_vault" "n_ovlt_01"' in hcl
    assert 'display_name   = "secrets-vault"' in hcl
    assert 'vault_type     = "DEFAULT"' in hcl


# ── Case 19: OCI CUI freeform_tags injection ─────────────────────────────────

def test_emit_oci_vcn_with_cui_tags():
    node = _node(
        "n-ovcn-cui",
        "oci-vcn",
        "classified-vcn",
        cidr_block="10.3.0.0/16",
        compartment_id="ocid1.compartment.oc1..test",
        classification="CUI//SP-CTI",
    )
    hcl = emit_resource(node, "oci")

    assert 'resource "oci_core_vcn" "n_ovcn_cui"' in hcl
    assert 'freeform_tags' in hcl
    assert 'Classification = "CUI//SP-CTI"' in hcl
    assert 'DataHandling   = "CUI//SP-CTI"' in hcl


# ── Case 20: OCI terraform validate smoke ────────────────────────────────────

@pytest.mark.skipif(TERRAFORM_BIN is None, reason="terraform binary not in PATH")
def test_oci_terraform_validate_smoke(tmp_path: Path):
    """All 5 OCI resource types pass terraform validate."""
    nodes = [
        _node("n-ovcn-s", "oci-vcn", "smoke-vcn"),
        _node("n-osub-s", "oci-subnet", "smoke-subnet"),
        _node("n-oci-s", "oci-compute", "smoke-instance"),
        _node("n-osl-s", "oci-security-list", "smoke-sl"),
        _node("n-ovlt-s", "oci-vault", "smoke-vault"),
    ]

    provider_tf = textwrap.dedent("""\
        terraform {
          required_providers {
            oci = {
              source  = "oracle/oci"
              version = "~> 5.0"
            }
          }
        }

        provider "oci" {}
    """)

    (tmp_path / "provider.tf").write_text(provider_tf, encoding="utf-8")
    hcl_blocks = [emit_resource(n, "oci") for n in nodes]
    (tmp_path / "main.tf").write_text("\n\n".join(hcl_blocks) + "\n", encoding="utf-8")

    result = subprocess.run(
        [TERRAFORM_BIN, "validate"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"OCI terraform validate failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


# ── Case 21: Unsupported CSP raises UnsupportedResourceError ─────────────────

def test_unsupported_csp_raises():
    node = _node("n-x", "azure-vm", "test-vm")
    with pytest.raises(UnsupportedResourceError, match="CSP not supported"):
        emit_resource(node, "azure")
