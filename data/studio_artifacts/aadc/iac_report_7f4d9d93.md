# AADC IaC Generation Report
**Generated:** 2026-05-19T23:59:00Z
**Canvas:** Agentic AI Design Canvas (AADC)
**Approval Role:** ai_governance_board
**Classification:** CUI // SP-CTI

## Artifacts Generated

| Artifact | Type | Purpose |
|----------|------|---------|
| `main_7f4d9d93.tf` | Terraform | AWS agentic AI infrastructure |
| `variables.tf` | Terraform | Variable declarations |
| `ansible_playbook_7f4d9d93.yml` | Ansible | Lambda layer + API key rotation |
| `validate_7f4d9d93.py` | Python | Post-deploy boto3 validation |

## Resources Provisioned

- **aws_lambda_function** (orchestrator) — Agent orchestration runtime
- **aws_lambda_function** (guardrail) — Input validation and content filtering
- **aws_api_gateway_rest_api** — Agent API endpoint
- **aws_api_gateway_stage** — Dev stage with access logging
- **aws_s3_bucket** — Model artifacts and logs
- **aws_s3_bucket_versioning** — Artifact versioning
- **aws_dynamodb_table** — Agent state store (PAY_PER_REQUEST)
- **aws_cloudwatch_log_group** — Agent execution logs
- **aws_ssm_parameter** — Model endpoint configuration

## Apply Instructions

```bash
cd data/studio_artifacts/aadc/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
python ../validate_7f4d9d93.py --region us-gov-west-1
```

## Approval Gate

**Required approver:** `ai_governance_board`
AI Governance Board review required before production agent traffic.
