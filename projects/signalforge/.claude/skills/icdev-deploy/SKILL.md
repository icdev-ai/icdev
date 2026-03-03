---
name: icdev-deploy
description: Generate IaC (Terraform, Ansible, K8s) and GitLab CI/CD pipeline for AWS GovCloud deployment
context: fork
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /icdev-deploy — Infrastructure & Deployment

## Usage
```
/icdev-deploy <project-id> [--target staging|production] [--generate-only] [--modules vpc,ecr,rds,s3,iam]
```

## What This Does
1. Generates Terraform configurations for AWS GovCloud
2. Generates Ansible playbooks for server configuration
3. Generates Kubernetes manifests with security hardening
4. Generates GitLab CI/CD pipeline (.gitlab-ci.yml)
5. Optionally triggers deployment via CI/CD (requires approval for production)

## Steps

### 1. Load Infrastructure Configuration
```bash
!cat args/project_defaults.yaml
!cat args/security_gates.yaml
```

### 2. Pre-Deployment Checks
Verify before generating IaC:
- All tests passing (check via run_tests)
- 0 CAT1 STIG findings (check via stig_check)
- No critical/high vulnerabilities
- SBOM is current
- CUI markings applied

### 3. Generate Terraform
Use the `terraform_plan` MCP tool from icdev-infra:
- project_id: from arguments
- modules: from `--modules` flag or default [vpc, ecr, rds]
- Generates: provider.tf, variables.tf, main.tf for AWS GovCloud (us-gov-west-1)

### 4. Generate Ansible Playbooks
Use the `ansible_run` MCP tool from icdev-infra:
- project_id: from arguments
- playbook_type: "deploy"
- Generates deployment playbook with security hardening

### 5. Generate Kubernetes Manifests
Use the `k8s_deploy` MCP tool from icdev-infra:
- project_id: from arguments
- environment: from `--target` flag or default "staging"
- Generates: Deployment, Service, ConfigMap, NetworkPolicy
- Security: non-root, read-only rootfs, resource limits

### 6. Generate CI/CD Pipeline
Use the `pipeline_generate` MCP tool from icdev-infra:
- project_id: from arguments
- stages: [lint, test, security-scan, build, compliance-check, deploy-staging, deploy-prod]
- Production deploy requires manual approval gate

### 7. Compliance Mapping
Use the `control_map` MCP tool from icdev-compliance:
- Map `deploy.staging` or `deploy.production` to NIST controls (CM-3, CM-5, SA-10)

### 8. Output Summary
Display:
- Generated files (Terraform, Ansible, K8s, CI/CD)
- Target environment
- Security gates status
- Next steps:
  - If `--generate-only`: "Review and commit IaC files, push to trigger pipeline"
  - If deploying: "Pipeline triggered, monitor at <GitLab URL>"

## Example
```
/icdev-deploy abc123-uuid --target staging --modules vpc,ecr,rds
```

## Error Handling
- If security gates fail: block deployment, show failing gates
- If Terraform module not available: generate basic configs manually
- Production deploy always requires explicit approval (terraform_apply approved=true)
