# Phase 6 — Infrastructure & Deployment

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 6 |
| Title | Infrastructure as Code & Deployment Pipeline |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 3 (TDD/BDD Testing Framework), Phase 4 (NIST 800-53 Compliance), Phase 5 (Security Scanning) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Manual deployments are unreproducible, error-prone, and unauditable. In government environments, every infrastructure change must be tracked, reviewed, approved, and reversible. A deployment that cannot be reproduced from version-controlled artifacts is a compliance violation. A deployment that cannot be rolled back is an operational risk. A deployment without security gates is a security incident waiting to happen.

The gap between "code works on my machine" and "code runs securely in GovCloud" is enormous. Applications require hardened infrastructure (STIG-compliant OS, encrypted storage, least-privilege IAM, private networking), orchestration (Kubernetes with security contexts, network policies, pod disruption budgets), configuration management (Ansible with vault-encrypted secrets), and a multi-stage CI/CD pipeline that enforces security gates between every stage.

ICDEV generates all infrastructure-as-code artifacts deterministically from project configuration: Terraform for cloud resources (AWS GovCloud by default, multi-cloud via Phase 38), Ansible for STIG-hardened configuration management, Kubernetes manifests with security-hardened pod specifications, and a 7-stage GitLab CI/CD pipeline with gates between every stage. A 10-gate pre-deployment verification ensures no artifact ships without passing all security, compliance, and quality checks. Rollback capability provides immediate recovery when deployments fail.

---

## 2. Goals

1. Generate Terraform configurations for AWS GovCloud infrastructure with encrypted storage, least-privilege IAM, private networking, and S3+DynamoDB state locking
2. Generate Ansible playbooks with STIG hardening roles, vault-encrypted secrets, SSH key authentication, and audit logging
3. Generate Kubernetes manifests with security-hardened pod specifications (non-root, read-only rootfs, no privilege escalation, resource limits, network policies)
4. Generate a 7-stage GitLab CI/CD pipeline (build, test, SAST, dependency, container, compliance, deploy) with gates between stages
5. Enforce 10 pre-deployment gates before any commit to deployment branch
6. Provide rollback capability for immediate recovery from failed deployments
7. Support multi-environment deployment: dev (auto on merge), staging (auto on tag), production (manual approval required)

---

## 3. Architecture

### 3.1 Deployment Pipeline

```
+----------+    +----------+    +----------+    +----------+
| Terraform|--->| Ansible  |--->|    K8s   |--->| Pipeline |
| Generate |    | Generate |    | Generate |    | Generate |
| IaC      |    | Playbooks|    | Manifests|    | CI/CD    |
+----------+    +----------+    +----------+    +----+-----+
                                                     |
                                                     v
                                              +-------------+
                                              | 10 Pre-     |
                                              | Deploy Gates|
                                              +------+------+
                                                     |
                                         +-----------+-----------+
                                         |                       |
                                    ALL PASS               ANY FAIL
                                         |                       |
                                         v                       v
                                   +-----------+          +----------+
                                   | Commit &  |          | BLOCKED  |
                                   | Push      |          | Remediate|
                                   +-----+-----+          +----------+
                                         |
                                         v
                                   +-----------+
                                   | 7-Stage   |
                                   | Pipeline  |
                                   | Execution |
                                   +-----+-----+
                                         |
                                    +----+----+
                                    |         |
                                  PASS      FAIL
                                    |         |
                                    v         v
                              +---------+ +----------+
                              | Health  | | Rollback |
                              | Check + | |          |
                              | Audit   | +----------+
                              +---------+
```

### 3.2 7-Stage CI/CD Pipeline

```
Stage 1: BUILD -----> Stage 2: TEST -----> Stage 3: SAST
  Compile, package      Unit tests           Static analysis
  Container image       Coverage >= 80%      0 critical/high
         |                    |                    |
         v                    v                    v
Stage 4: DEPS -----> Stage 5: CONTAINER -> Stage 6: COMPLIANCE
  pip-audit             trivy scan           STIG check
  0 critical/high       0 critical           CUI markings
         |                    |                    |
         v                    v                    v
                     Stage 7: DEPLOY
                       Terraform apply
                       Ansible run
                       K8s deploy
                       (manual for prod)
```

### 3.3 Generated Terraform Structure

```
infrastructure/terraform/
  +-- main.tf              # Provider, backend, core resources
  +-- variables.tf         # Input variables
  +-- outputs.tf           # Output values
  +-- networking.tf        # VPC, subnets, security groups
  +-- compute.tf           # EC2/ECS/EKS resources
  +-- storage.tf           # S3, RDS, EBS
  +-- iam.tf               # IAM roles, policies (least privilege)
  +-- security_groups.tf   # Network ACLs
  +-- terraform.tfvars     # Environment-specific values
```

---

## 4. Requirements

### 4.1 Terraform

#### REQ-06-001: GovCloud Provider
Generated Terraform SHALL target AWS GovCloud (us-gov-west-1) by default with S3+DynamoDB state locking.

#### REQ-06-002: Encryption at Rest
All S3 buckets and RDS instances SHALL have encryption enabled. No unencrypted storage resources.

#### REQ-06-003: Least Privilege IAM
Generated IAM policies SHALL follow least-privilege: no wildcard (*) actions, scoped to specific resources.

#### REQ-06-004: Network Isolation
Compute resources SHALL reside in private subnets. Only load balancers may have public-facing ingress on port 443.

#### REQ-06-005: No Hardcoded Secrets
Terraform configurations SHALL reference CI/CD variables or AWS Secrets Manager, never contain hardcoded credentials.

### 4.2 Ansible

#### REQ-06-006: STIG Hardening Role
Generated Ansible playbooks SHALL include a STIG hardening role that automates applicable STIG checks.

#### REQ-06-007: Vault-Encrypted Secrets
All sensitive values in Ansible playbooks SHALL use Ansible Vault encryption, never plaintext.

#### REQ-06-008: SSH Key Authentication
Ansible configurations SHALL enforce SSH key-based authentication only, no password authentication.

### 4.3 Kubernetes

#### REQ-06-009: Security Context
All generated K8s pod specifications SHALL include: `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, and resource limits.

#### REQ-06-010: Network Policies
Generated K8s manifests SHALL include NetworkPolicy resources restricting pod-to-pod traffic (default deny).

#### REQ-06-011: Image Pinning
All container image references SHALL use specific version tags, never `latest`.

#### REQ-06-012: RBAC
Generated K8s manifests SHALL include ServiceAccount, Role, and RoleBinding resources following least-privilege.

### 4.4 Pipeline

#### REQ-06-013: 7-Stage Pipeline
The generated GitLab CI/CD pipeline SHALL include 7 stages: build, test, sast, dependency, container, compliance, deploy.

#### REQ-06-014: Fail-Fast
Each pipeline stage SHALL fail fast -- no continuing past failures to subsequent stages.

#### REQ-06-015: Production Manual Approval
Production deployment SHALL require manual approval. Dev and staging may deploy automatically.

#### REQ-06-016: Artifact Retention
All pipeline artifacts (test reports, scan results, SBOM) SHALL be retained for 90 days.

### 4.5 Pre-Deployment Gates

#### REQ-06-017: 10-Gate Verification
The system SHALL verify 10 gates before deployment: (1) all tests pass, (2) coverage >= 80%, (3) 0 critical/high SAST, (4) 0 critical/high deps, (5) 0 secrets, (6) 0 critical container vulns, (7) 0 CAT1 STIG, (8) CUI markings present, (9) SBOM current (<30 days), (10) ATO status valid.

### 4.6 Rollback

#### REQ-06-018: Rollback Capability
The system SHALL provide rollback to the last known-good deployment via `tools/infra/rollback.py` when post-deployment health checks fail.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `deployments` | Deployment records with environment, status, commit hash, pipeline ID |
| `projects` | Project metadata including ATO status and deployment history |
| `audit_trail` | Append-only log of deployment events and gate results |
| `agents` | Infrastructure agent (port 8448) registration and health |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/infra/terraform_generator.py` | Generate Terraform configurations for cloud infrastructure |
| `tools/infra/ansible_generator.py` | Generate Ansible playbooks with STIG hardening roles |
| `tools/infra/k8s_generator.py` | Generate security-hardened Kubernetes manifests |
| `tools/infra/pipeline_generator.py` | Generate 7-stage GitLab CI/CD pipeline |
| `tools/infra/rollback.py` | Roll back to last known-good deployment |
| `tools/monitor/health_checker.py` | Post-deployment health check with retry |
| `tools/audit/audit_logger.py` | Log deployment events to append-only audit trail |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D1 | SQLite for ICDEV internals; PostgreSQL for apps ICDEV builds | Generated applications get production-grade databases |
| D3 | Flask over FastAPI | Simpler, fewer dependencies, auditable SSR, smaller STIG attack surface |
| D141 | HPA with CPU/memory metrics as baseline | Cloud-agnostic auto-scaling; works on EKS, GKE, AKS, OpenShift |
| D143 | PDB with minAvailable=1 for core agents | Guarantees availability during rolling updates and node maintenance |
| D226 | Multi-cloud Terraform generators produce CSP-specific IaC | Azure Gov, GCP Assured Workloads, OCI Gov, IBM IC4G, on-prem K8s/Docker |
| D229 | Helm value overlays per CSP | CSP-specific K8s config without modifying base templates |

---

## 8. Security Gate

**Deploy Gate:**
- Staging tests pass
- Compliance artifacts current (within 30 days)
- Change request approved
- Rollback plan exists

**Pre-Deployment Gate (10 checks):**
1. All tests pass
2. Coverage >= 80%
3. SAST -- 0 critical/high
4. Dependencies -- 0 critical/high
5. Secrets -- 0 detected
6. Container -- 0 critical/high (or N/A)
7. STIG -- 0 CAT1
8. CUI markings present
9. SBOM current (< 30 days)
10. ATO status valid

**Container Security:**
- All containers: read-only rootfs, drop ALL capabilities, non-root (UID 1000), resource limits enforced
- STIG-hardened base images for all agents and services

---

## 9. Commands

```bash
# Infrastructure generation
python tools/infra/terraform_generator.py --project-id "proj-123"
python tools/infra/ansible_generator.py --project-id "proj-123"
python tools/infra/k8s_generator.py --project-id "proj-123"
python tools/infra/pipeline_generator.py --project-id "proj-123"

# Multi-cloud Terraform (Phase 38 extension)
python tools/infra/terraform_generator.py --project-id "proj-123" --csp azure
python tools/infra/terraform_generator.py --project-id "proj-123" --csp gcp
python tools/infra/terraform_generator.py --project-id "proj-123" --csp oci

# Rollback
python tools/infra/rollback.py --deployment-id "deploy-123"

# Health check
python tools/monitor/health_checker.py --target "http://service:8080/health"

# K8s auto-scaling (Phase 38 extension)
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/pdb.yaml

# Deployment skill
/icdev-deploy    # Generate IaC and deploy via GitLab CI/CD

# Audit logging
python tools/audit/audit_logger.py --event-type "deployment_complete" \
  --actor "infra-agent" --action "Deploy to staging" --project-id "proj-123"
```
