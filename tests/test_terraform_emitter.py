# CUI // SP-CTI
"""Tests for tools/infra_canvas/emitters/terraform.py.

6 cases:
  1. emit_resource produces valid HCL for aws-vpc
  2. emit_resource produces valid HCL for aws-subnet
  3. emit_resource produces valid HCL for aws-ec2
  4. emit_resource produces valid HCL for aws-sg
  5. emit_resource produces valid HCL for aws-iam-role + CUI tag injection
  6. terraform validate smoke (skipped when terraform is not in PATH)
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tools.infra_canvas.emitters.terraform import emit_resource

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
