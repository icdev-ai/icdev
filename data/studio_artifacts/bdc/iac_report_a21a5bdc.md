# BDC IaC Generation Report
**Generated:** 2026-05-19 23:58 UTC
**Canvas:** bdc
**Approval Role:** authorizing_official

## Generated Artifacts

| Artifact | Path | Type |
|----------|------|------|
| Terraform Main | `data/studio_artifacts/bdc/terraform/main_a21a5bdc.tf` | tf |
| Terraform Variables | `data/studio_artifacts/bdc/terraform/variables.tf` | tf |
| Terraform tfvars example | `data/studio_artifacts/bdc/terraform/terraform.tfvars.example` | tf |
| Ansible Playbook | `data/studio_artifacts/bdc/ansible_playbook_a21a5bdc.yml` | yml |
| Validation Script | `data/studio_artifacts/bdc/validate_a21a5bdc.py` | py |
| IaC Report | `data/studio_artifacts/bdc/iac_report_a21a5bdc.md` | md |

## Apply Instructions

```bash
cp terraform.tfvars.example terraform.tfvars
# Fill in: vpc_id, private_subnet_ids, artifacts_bucket, boundary_name, classification
terraform init
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

## Post-Apply Steps

```bash
# Run boundary hardening playbook
export BOUNDARY_NAME=icdev-boundary
ansible-playbook -i inventory.ini ansible_playbook_*.yml

# Run validation
python validate_*.py --region us-gov-west-1 --boundary-name icdev-boundary
```

## Resources Provisioned

- `aws_vpc_endpoint` (S3 Gateway) — Private S3 access without internet
- `aws_vpc_endpoint` (SSM Interface) — Private SSM access
- `aws_vpc_endpoint` (KMS Interface) — Private KMS access
- `aws_route_table` — Boundary private route table (S3 gateway association)
- `aws_network_acl` — Boundary NACL (deny 0.0.0.0/0, allow 10.0.0.0/8)
- `aws_network_acl_rule` (x2) — Deny RDP (3389) and SSH (22) from internet
- `aws_security_group` — Boundary enforcement SG (HTTPS inbound from RFC1918 only)
- `aws_kms_key` — Boundary data encryption CMK (30-day deletion, rotation on)
- `aws_kms_alias` — KMS alias (alias/icdev-<boundary_name>-boundary)
- `aws_ssm_parameter` — Boundary configuration (SecureString, KMS-encrypted)

## AO Sign-off

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Authorizing Official | | | |
| ISSO | | | |
| ISSM | | | |
