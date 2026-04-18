# CUI // SP-CTI
"""Tests for tools/infra_canvas/emitters/pulumi.py.

10 cases (5 AWS GovCloud + 5 Azure Government):
  1.  AWS: vpc
  2.  AWS: subnet
  3.  AWS: ec2 instance
  4.  AWS: security group
  5.  AWS: iam-role + CUI tag injection
  6.  Azure: virtual network (az-vnet)
  7.  Azure: subnet (az-subnet)
  8.  Azure: virtual machine (az-vm)
  9.  Azure: network security group (az-nsg) + CUI tag injection
  10. Azure: role assignment (az-role)
"""

import pytest

from tools.infra_canvas.emitters.pulumi import UnsupportedResourceError, emit_resource


# ── Helper ────────────────────────────────────────────────────────────────────

def _node(node_id: str, ntype: str, label: str, **meta) -> dict:
    return {"id": node_id, "type": ntype, "label": label, "metadata": meta}


# ── Case 1: AWS VPC ───────────────────────────────────────────────────────────

def test_emit_aws_vpc():
    node = _node("n-vpc-01", "aws-vpc", "main-vpc", cidr_block="172.16.0.0/16")
    ts = emit_resource(node, "aws-govcloud")

    assert 'const nVpc01 = new aws.ec2.Vpc("n-vpc-01"' in ts
    assert 'cidrBlock: "172.16.0.0/16"' in ts
    assert "enableDnsHostnames: true" in ts
    assert "enableDnsSupport: true" in ts
    assert 'Name: "main-vpc"' in ts
    assert 'ManagedBy: "icdev-pulumi-emitter"' in ts


# ── Case 2: AWS Subnet ────────────────────────────────────────────────────────

def test_emit_aws_subnet():
    node = _node(
        "n-sub-01",
        "aws-subnet",
        "private-subnet",
        cidr_block="10.0.2.0/24",
        availability_zone="us-gov-west-1b",
        vpc_id="vpc-abcdef01",
    )
    ts = emit_resource(node, "aws-govcloud")

    assert 'const nSub01 = new aws.ec2.Subnet("n-sub-01"' in ts
    assert 'cidrBlock: "10.0.2.0/24"' in ts
    assert 'availabilityZone: "us-gov-west-1b"' in ts
    assert 'vpcId: "vpc-abcdef01"' in ts


# ── Case 3: AWS EC2 Instance ──────────────────────────────────────────────────

def test_emit_aws_instance():
    node = _node(
        "n-ec2-01",
        "aws-ec2",
        "web-server",
        ami="ami-abcdef1234567890",
        instance_type="m5.xlarge",
    )
    ts = emit_resource(node, "aws-govcloud")

    assert 'const nEc201 = new aws.ec2.Instance("n-ec2-01"' in ts
    assert 'ami: "ami-abcdef1234567890"' in ts
    assert 'instanceType: "m5.xlarge"' in ts


# ── Case 4: AWS Security Group ────────────────────────────────────────────────

def test_emit_aws_security_group():
    node = _node("n-sg-01", "aws-sg", "web-sg", description="Allow HTTPS inbound")
    ts = emit_resource(node, "aws-govcloud")

    assert 'const nSg01 = new aws.ec2.SecurityGroup("n-sg-01"' in ts
    assert 'name: "web-sg-sg"' in ts
    assert 'description: "Allow HTTPS inbound"' in ts


# ── Case 5: AWS IAM Role + CUI tag injection ──────────────────────────────────

def test_emit_aws_iam_role_with_cui_tags():
    node = _node(
        "n-role-01",
        "aws-iam-role",
        "app-role",
        principal_service="lambda.amazonaws.com",
        classification="CUI",
    )
    ts = emit_resource(node, "aws-govcloud")

    assert 'const nRole01 = new aws.iam.Role("n-role-01"' in ts
    assert 'name: "app-role-role"' in ts
    assert '"sts:AssumeRole"' in ts
    assert '"lambda.amazonaws.com"' in ts
    assert 'Classification: "CUI"' in ts
    assert 'DataHandling: "CUI//SP-CTI"' in ts


# ── Case 6: Azure Virtual Network ─────────────────────────────────────────────

def test_emit_az_vnet():
    node = _node(
        "n-vnet-01",
        "az-vnet",
        "main-vnet",
        address_prefix="10.1.0.0/16",
        resource_group_name="rg-govcloud",
        location="usgovvirginia",
    )
    ts = emit_resource(node, "azure-government")

    assert 'const nVnet01 = new azure_native.network.VirtualNetwork("n-vnet-01"' in ts
    assert 'resourceGroupName: "rg-govcloud"' in ts
    assert 'location: "usgovvirginia"' in ts
    assert 'addressPrefixes: ["10.1.0.0/16"]' in ts
    assert 'Name: "main-vnet"' in ts


# ── Case 7: Azure Subnet ──────────────────────────────────────────────────────

def test_emit_az_subnet():
    node = _node(
        "n-azs-01",
        "az-subnet",
        "app-subnet",
        address_prefix="10.1.1.0/24",
        resource_group_name="rg-govcloud",
        virtual_network_name="main-vnet",
    )
    ts = emit_resource(node, "azure-government")

    assert 'const nAzs01 = new azure_native.network.Subnet("n-azs-01"' in ts
    assert 'resourceGroupName: "rg-govcloud"' in ts
    assert 'virtualNetworkName: "main-vnet"' in ts
    assert 'addressPrefix: "10.1.1.0/24"' in ts


# ── Case 8: Azure Virtual Machine ─────────────────────────────────────────────

def test_emit_az_vm():
    node = _node(
        "n-vm-01",
        "az-vm",
        "app-server",
        vm_size="Standard_D4s_v3",
        resource_group_name="rg-govcloud",
        location="usgovvirginia",
        publisher="Canonical",
        offer="UbuntuServer",
        sku="18.04-LTS",
    )
    ts = emit_resource(node, "azure-government")

    assert 'const nVm01 = new azure_native.compute.VirtualMachine("n-vm-01"' in ts
    assert 'resourceGroupName: "rg-govcloud"' in ts
    assert 'vmSize: "Standard_D4s_v3"' in ts
    assert 'publisher: "Canonical"' in ts
    assert 'offer: "UbuntuServer"' in ts
    assert 'sku: "18.04-LTS"' in ts
    assert "hardwareProfile" in ts
    assert "storageProfile" in ts
    assert "networkProfile" in ts


# ── Case 9: Azure NSG + CUI tag injection ────────────────────────────────────

def test_emit_az_nsg_with_cui_tags():
    node = _node(
        "n-nsg-01",
        "az-nsg",
        "app-nsg",
        resource_group_name="rg-govcloud",
        location="usgovtexas",
        classification="CUI//SP-CTI",
    )
    ts = emit_resource(node, "azure-government")

    assert 'const nNsg01 = new azure_native.network.NetworkSecurityGroup("n-nsg-01"' in ts
    assert 'resourceGroupName: "rg-govcloud"' in ts
    assert 'location: "usgovtexas"' in ts
    assert 'Classification: "CUI//SP-CTI"' in ts
    assert 'DataHandling: "CUI//SP-CTI"' in ts


# ── Case 10: Azure Role Assignment ────────────────────────────────────────────

def test_emit_az_role_assignment():
    node = _node(
        "n-azrole-01",
        "az-role",
        "reader-binding",
        scope="/subscriptions/sub-gov-001/resourceGroups/rg-govcloud",
        role_definition_id=(
            "/providers/Microsoft.Authorization/roleDefinitions/"
            "acdd72a7-3385-48ef-bd42-f606fba81ae7"
        ),
        principal_id="aad-object-id-001",
    )
    ts = emit_resource(node, "azure-government")

    assert 'const nAzrole01 = new azure_native.authorization.RoleAssignment("n-azrole-01"' in ts
    assert 'scope: "/subscriptions/sub-gov-001/resourceGroups/rg-govcloud"' in ts
    assert "roleDefinitionId" in ts
    assert 'principalId: "aad-object-id-001"' in ts


# ── Bonus: unsupported CSP raises UnsupportedResourceError ───────────────────

def test_unsupported_csp_raises():
    node = _node("n-x", "aws-vpc", "test-vpc")
    with pytest.raises(UnsupportedResourceError, match="CSP not supported"):
        emit_resource(node, "gcp")
