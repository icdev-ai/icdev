# Goal: Infrastructure Deployment

## Description

Generate all infrastructure-as-code artifacts (Terraform, Ansible, Kubernetes manifests, CI/CD pipeline), verify all pre-deployment gates, commit to GitLab, and monitor the deployment pipeline through all 7 stages to production.

**Why this matters:** Manual deployments are unreproducible, error-prone, and unauditable. Infrastructure-as-code ensures every deployment is identical, testable, and traceable. In government environments, every change must be tracked and reversible.

---

## Prerequisites

- [ ] Project initialized (`goals/init_project.md` completed)
- [ ] All tests pass (`goals/tdd_workflow.md` completed)
- [ ] Security scan gates pass (`goals/security_scan.md` — 0 critical, 0 secrets)
- [ ] Compliance artifacts current (`goals/compliance_workflow.md` — within 30 days)
- [ ] ATO status is READY or existing ATO is valid
- [ ] Target environment defined (dev, staging, production)
- [ ] GitLab repository configured
- [ ] `memory/MEMORY.md` loaded (session context)

**HARD STOP: Do not proceed if any prerequisite fails. Deployment without passing gates is a compliance violation.**

---

## Process

### Step 1: Generate Terraform Configuration

**Tool:** `python tools/infra/terraform_generator.py --project <name>`

**Expected output:**
```
Terraform files generated: projects/<name>/infrastructure/terraform/

Files:
  - main.tf              # Provider, backend, core resources
  - variables.tf         # Input variables
  - outputs.tf           # Output values
  - networking.tf        # VPC, subnets, security groups
  - compute.tf           # EC2/ECS/EKS resources
  - storage.tf           # S3, RDS, EBS
  - iam.tf               # IAM roles, policies (least privilege)
  - security_groups.tf   # Network ACLs
  - terraform.tfvars     # Environment-specific values

Provider: AWS GovCloud (us-gov-west-1)
Backend: S3 + DynamoDB state locking
```

**Security requirements for generated Terraform:**
- All S3 buckets: encryption enabled, public access blocked, versioning on
- All security groups: no 0.0.0.0/0 ingress (except ALB on 443)
- IAM: least-privilege policies, no wildcard (*) actions
- RDS: encryption at rest, no public accessibility
- VPC: private subnets for compute, public only for load balancers

**Error handling:**
- Missing provider credentials → fail clearly, do not generate with placeholder keys
- Unsupported resource type → document limitation, suggest manual creation
- State backend not configured → generate backend config, warn user to initialize

**Verify:** `terraform validate` passes. `terraform plan` shows expected resources. No security group allows unrestricted ingress.

---

### Step 2: Generate Ansible Playbooks

**Tool:** `python tools/infra/ansible_generator.py --project <name>`

**Expected output:**
```
Ansible files generated: projects/<name>/infrastructure/ansible/

Files:
  - site.yml                    # Master playbook
  - inventory/
  │   ├── production.yml        # Production hosts
  │   ├── staging.yml           # Staging hosts
  │   └── group_vars/
  │       └── all.yml           # Shared variables
  - roles/
  │   ├── hardening/            # STIG hardening role
  │   │   ├── tasks/main.yml
  │   │   └── handlers/main.yml
  │   ├── application/          # App deployment role
  │   │   ├── tasks/main.yml
  │   │   └── templates/
  │   └── monitoring/           # Monitoring agent role
  │       └── tasks/main.yml

STIG hardening checks: <count> automated
```

**Security requirements for generated Ansible:**
- STIG hardening role applies all applicable STIG checks
- No plaintext passwords in playbooks (use Ansible Vault)
- SSH key-based authentication only
- Audit logging enabled on all managed hosts
- Firewall rules applied matching Terraform security groups

**Error handling:**
- Ansible not installed → provide installation instructions
- Missing vault password → warn, generate without secrets (user adds later)
- Invalid YAML syntax → validate before writing, fix

**Verify:** `ansible-playbook --syntax-check site.yml` passes. Vault-encrypted files are not plaintext.

---

### Step 3: Generate Kubernetes Manifests

**Tool:** `python tools/infra/k8s_generator.py --project <name>`

**Expected output:**
```
Kubernetes manifests generated: projects/<name>/infrastructure/k8s/

Files:
  - namespace.yaml              # Isolated namespace
  - deployment.yaml             # App deployment
  - service.yaml                # ClusterIP/LoadBalancer service
  - ingress.yaml                # Ingress with TLS
  - configmap.yaml              # Non-sensitive config
  - secret.yaml                 # Sensitive config (sealed)
  - hpa.yaml                    # Horizontal Pod Autoscaler
  - networkpolicy.yaml          # Network isolation
  - poddisruptionbudget.yaml   # Availability guarantee
  - serviceaccount.yaml         # RBAC service account
  - rbac.yaml                   # Role and RoleBinding

Security settings applied:
  - runAsNonRoot: true
  - readOnlyRootFilesystem: true
  - allowPrivilegeEscalation: false
  - resource limits set
  - network policies enforced
```

**Security requirements for generated K8s manifests:**
- Pods run as non-root user
- Read-only root filesystem
- No privilege escalation
- Resource limits defined (CPU and memory)
- Network policies restrict pod-to-pod traffic
- Secrets use SealedSecrets or external secret management
- No `latest` tag — all images pinned to specific versions

**Error handling:**
- No Dockerfile → generate one first (`tools/infra/dockerfile_generator.py`)
- Invalid manifest syntax → `kubectl apply --dry-run=client -f <file>` to validate
- Missing namespace → create namespace manifest first

**Verify:** `kubectl apply --dry-run=client -f .` passes for all manifests. Security context is set on all pods.

---

### Step 4: Generate CI/CD Pipeline

**Tool:** `python tools/infra/pipeline_generator.py --project <name>`

**Expected output:**
```
Pipeline generated: projects/<name>/.gitlab-ci.yml

Stages (7):
  1. build        — Compile, package, create container image
  2. test         — Unit tests, integration tests, coverage check
  3. sast         — Static analysis (bandit/eslint-security)
  4. dependency   — pip-audit/npm audit
  5. container    — trivy container scan
  6. compliance   — STIG check, CUI marking verification
  7. deploy       — Terraform apply, Ansible run, K8s deploy

Gates between stages:
  - test → sast: all tests pass, coverage >= 80%
  - sast → dependency: 0 critical/high SAST findings
  - dependency → container: 0 critical/high dependency vulns
  - container → compliance: 0 critical container vulns
  - compliance → deploy: 0 CAT1 STIGs, CUI markings present
  - deploy: manual trigger for production (automatic for dev/staging)

Artifacts:
  - Test reports (JUnit XML)
  - Coverage reports (Cobertura)
  - Scan results (JSON)
  - SBOM (CycloneDX JSON)
```

**Pipeline requirements:**
- Each stage fails fast (no continuing past failures)
- Production deployment requires manual approval
- Rollback procedure documented in pipeline comments
- All artifacts preserved for 90 days
- Pipeline variables use CI/CD variables (never hardcoded)

**Error handling:**
- GitLab CI not available → generate pipeline file anyway, warn about manual execution
- Missing CI/CD variables → document required variables in pipeline comments
- Pipeline too slow → add caching for dependencies

**Verify:** `.gitlab-ci.yml` is valid YAML. Pipeline lint passes (`gitlab-ci-lint` or GitLab API).

---

### Step 5: Verify All Pre-Deployment Gates

**Action:** Final gate check before committing to deployment.

```
=== PRE-DEPLOYMENT GATE CHECK ===

Gate 1: All tests pass                    [PASS/FAIL]
Gate 2: Coverage >= 80%                   [PASS/FAIL]
Gate 3: SAST — 0 critical/high           [PASS/FAIL]
Gate 4: Dependencies — 0 critical/high    [PASS/FAIL]
Gate 5: Secrets — 0 detected             [PASS/FAIL]
Gate 6: Container — 0 critical/high      [PASS/FAIL] (or N/A)
Gate 7: STIG — 0 CAT1                    [PASS/FAIL]
Gate 8: CUI markings present             [PASS/FAIL]
Gate 9: SBOM current (< 30 days)         [PASS/FAIL]
Gate 10: ATO status valid                [PASS/FAIL]

Overall: <ALL GATES PASS | BLOCKED — gates X, Y, Z failed>
```

**If ANY gate fails:** STOP. Do not deploy. Document which gates failed and what remediation is needed. Return to the appropriate workflow to fix.

---

### Step 6: Commit to GitLab

**Action:** Stage all infrastructure files and commit.

```bash
git add projects/<name>/infrastructure/
git add projects/<name>/.gitlab-ci.yml
git commit -m "feat(<name>): infrastructure-as-code for <environment> deployment

- Terraform: AWS GovCloud resources
- Ansible: STIG-hardened configuration
- K8s: Security-hardened manifests
- Pipeline: 7-stage CI/CD with security gates

All pre-deployment gates passed.
Scan date: <YYYY-MM-DD>"

git push origin <branch>
```

**Error handling:**
- Git not initialized → `git init`, configure remote
- Push rejected → pull first, resolve conflicts, re-push
- Large files → check for binaries, use `.gitignore`

**Verify:** Commit exists in remote repository. Pipeline triggered.

---

### Step 7: Monitor Pipeline Execution

**Action:** Watch the 7-stage pipeline for completion.

```
Pipeline status:
  Stage 1 (build):      [PASS] — 2m 15s
  Stage 2 (test):       [PASS] — 4m 30s
  Stage 3 (sast):       [PASS] — 1m 45s
  Stage 4 (dependency): [PASS] — 0m 55s
  Stage 5 (container):  [PASS] — 3m 20s
  Stage 6 (compliance): [PASS] — 2m 10s
  Stage 7 (deploy):     [PASS] — 5m 00s  (manual approval for prod)

Total time: 20m 35s
Status: SUCCESS
```

**If pipeline fails:**
1. Identify which stage failed
2. Read the stage logs
3. Determine if the failure is in code, infra, or pipeline config
4. Fix the issue
5. Re-push and re-trigger pipeline
6. If production deploy fails → execute rollback (`tools/infra/rollback.py --project <name> --environment <env>`)

**Post-deployment health check:**
```bash
python tools/monitor/health_checker.py --url <deployed-url> --retries 5
```

---

### Step 8: Log to Audit Trail

**Tool:** `python tools/audit/audit_logger.py --event "deployment_complete" --actor "orchestrator" --action "deploy" --project <name>`

**Tool:** `python tools/memory/memory_write.py --content "Deployed <name> to <environment>. Pipeline: all 7 stages passed. Health check: <status>" --type event --importance 8`

---

## Success Criteria

- [ ] Terraform configuration generated and validated
- [ ] Ansible playbooks generated with STIG hardening
- [ ] Kubernetes manifests generated with security contexts
- [ ] CI/CD pipeline generated with 7 stages and gates
- [ ] All 10 pre-deployment gates pass
- [ ] Code committed and pushed to GitLab
- [ ] Pipeline completes all 7 stages successfully
- [ ] Health check passes post-deployment
- [ ] Audit trail entry logged

---

## Edge Cases & Notes

1. **Rollback procedure:** If deployment breaks production, execute `python tools/infra/rollback.py --project <name> --environment production`. This reverts to the last known-good deployment. Test rollback in staging first.
2. **Blue-green deployments:** For zero-downtime, use blue-green strategy. Both versions run simultaneously; traffic switches after health check passes.
3. **Canary deployments:** Route 5% of traffic to new version first. Monitor error rates. If stable for 15 minutes, increase to 100%.
4. **Terraform state:** State files contain sensitive information. Store in encrypted S3 bucket with state locking via DynamoDB. Never commit state files to git.
5. **Secret injection:** Pipeline secrets come from GitLab CI/CD variables or AWS Secrets Manager. Never bake secrets into images or manifests.
6. **Multi-environment:** Dev deploys automatically on merge. Staging deploys automatically on tag. Production requires manual approval.
7. **Disaster recovery:** Terraform enables full infrastructure recreation. Document RTO (Recovery Time Objective) and RPO (Recovery Point Objective).
8. **Cost management:** Terraform `plan` shows estimated costs. Review before applying to production.

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Generate Terraform | Tools | terraform_generator.py |
| Generate Ansible | Tools | ansible_generator.py |
| Generate K8s | Tools | k8s_generator.py |
| Generate pipeline | Tools | pipeline_generator.py |
| Gate evaluation | Orchestration | AI (you) |
| Environment config | Args | terraform.tfvars, inventory |
| Infrastructure patterns | Context | GovCloud reference |
| Deployment decisions | Orchestration | AI (you) |

---

## Related Files

- **Tools:** `tools/infra/terraform_generator.py`, `tools/infra/ansible_generator.py`, `tools/infra/k8s_generator.py`, `tools/infra/pipeline_generator.py`, `tools/infra/rollback.py`
- **Depends on:** `goals/tdd_workflow.md`, `goals/security_scan.md`, `goals/compliance_workflow.md`
- **Feeds into:** `goals/monitoring.md` (post-deploy observability)
- **Database:** `data/icdev.db` (deployments table)

---

## Changelog

- 2026-02-14: Initial creation
