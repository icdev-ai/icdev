---
mode: agent
description: "Generate IaC (Terraform, Ansible, K8s) and GitLab CI/CD pipeline for AWS GovCloud deployment"
tools:
  - terminal
  - file_search
---

# icdev-deploy

1. Generates Terraform configurations for AWS GovCloud
2. Generates Ansible playbooks for server configuration
3. Generates Kubernetes manifests with security hardening
4. Generates GitLab CI/CD pipeline (.gitlab-ci.yml)
5. Optionally triggers deployment via CI/CD (requires approval for production)

## Steps

1. **Load Infrastructure Configuration**
```bash
!cat args/project_defaults.yaml
!cat args/security_gates.yaml
```

2. **Pre-Deployment Checks**
Verify before generating IaC:
- All tests passing (check via run_tests)
- 0 CAT1 STIG findings (check via stig_check)

3. **Generate Terraform**
Run the equivalent CLI command for terraform_plan:
- project_id: from arguments
- modules: from `--modules` flag or default [vpc, ecr, rds]

4. **Generate Ansible Playbooks**
Run the equivalent CLI command for ansible_run:
- project_id: from arguments
- playbook_type: "deploy"

5. **Generate Kubernetes Manifests**
Run the equivalent CLI command for k8s_deploy:
- project_id: from arguments
- environment: from `--target` flag or default "staging"

6. **Generate CI/CD Pipeline**
Run the equivalent CLI command for pipeline_generate:
- project_id: from arguments
- stages: [lint, test, security-scan, build, compliance-check, deploy-staging, deploy-prod]

7. **Compliance Mapping**
Run the equivalent CLI command for control_map:
- Map `deploy.staging` or `deploy.production` to NIST controls (CM-3, CM-5, SA-10)

8. **Output Summary**
Display:
- Generated files (Terraform, Ansible, K8s, CI/CD)
- Target environment

9. **Output Summary**
Display:
- Generated files (Terraform, Ansible, K8s, CI/CD)
- Target environment

## Example
```
#prompt:icdev-deploy abc123-uuid --target staging --modules vpc,ecr,rds
```