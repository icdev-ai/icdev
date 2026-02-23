# Phase 24 — DevSecOps Pipeline Security

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 24 |
| Title | DevSecOps Pipeline Security |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 14 (Requirements Intake), Phase 17 (ATO Acceleration) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Most DevSecOps failures stem from mismatched tooling: immature teams get overwhelmed by enterprise-grade security pipelines, while mature teams are held back by starter configurations. A one-size-fits-all pipeline approach forces every project through the same scanning stages regardless of organizational readiness, leading to alert fatigue at the low end and inadequate coverage at the high end. Without profile-driven generation, teams either disable security stages they cannot operate or accept a default configuration that does not match their actual maturity.

Prior to this phase, ICDEV generated the same security pipeline for every project. There was no maturity model, no auto-detection of existing CI/CD tooling during requirements intake, no policy-as-code generation, and no attestation/signing workflow. Projects at DevSecOps Level 1 received the same gate configuration as projects at Level 5, creating friction and false confidence. IL6/SECRET projects lacked enforced minimum maturity floors, and air-gapped environments had no mechanism to restrict pipeline tooling to locally available scanners.

Phase 24 introduces per-project DevSecOps profiles that control all downstream pipeline and infrastructure generation. Profiles are auto-detected during RICOAS intake from CI/CD tooling mentions, security scanner references, container orchestration signals, and compliance framework indicators. A 5-level maturity model (Basic through Optimizing) calibrates scanning stages, gate enforcement, policy-as-code generation (Kyverno/OPA), and image signing/attestation to what the organization can actually operate, with maturity evolving over time as capabilities grow.

---

## 2. Goals

1. **Auto-detect DevSecOps signals** during conversational intake (RICOAS) including CI/CD platforms, existing security scanners, container orchestration, policy engines, and attestation tools
2. Create **per-project DevSecOps profiles** that capture maturity level, pipeline stages, policy engine, attestation configuration, and gate thresholds
3. Implement a **5-level maturity model** (Basic, Managed, Defined, Measured, Optimizing) with dimension scoring across CI/CD automation, security scanning, policy enforcement, supply chain, and monitoring
4. Generate **profile-driven CI/CD pipeline security stages** as platform-specific YAML (GitLab CI, GitHub Actions, Jenkins) calibrated to the project's maturity level
5. Generate **policy-as-code** (Kyverno or OPA/Gatekeeper) admission policies with NIST 800-53 control mappings
6. Configure **image signing and SBOM attestation** (cosign) for Level 3+ projects with KMS or local key management
7. Integrate DevSecOps-specific **security gates** with the existing ICDEV gate framework, additive to existing project gates
8. Require **ISSO review and confirmation** before any profile becomes active

---

## 3. Architecture

### 3.1 Profile-Driven Pipeline Generation

```
Intake Session (RICOAS)
  |
  v
Signal Detection (CI/CD, scanners, orchestration, policy, attestation)
  |
  v
DevSecOps Profile Creation (maturity level, stages, gates)
  |
  +---> Pipeline Security YAML (platform-specific: GitLab/GitHub/Jenkins)
  |
  +---> Policy-as-Code (Kyverno or OPA manifests)
  |
  +---> Attestation Config (cosign + KMS or local keys)
  |
  +---> Gate Configuration (merged with project security gates)
  |
  v
ISSO Review & Confirmation
  |
  v
Active Profile (drives all downstream generation)
```

### 3.2 Maturity Level to Stage Mapping

| Stage | Level 1 | Level 2 | Level 3 | Level 4 | Level 5 |
|-------|---------|---------|---------|---------|---------|
| SAST | bandit | bandit + ruff | + semgrep | + Fortify | + custom rules |
| SCA | pip-audit | pip-audit + SBOM | + license check | + transitive | + auto-remediate |
| Secrets | detect-secrets | detect-secrets | + git history | + rotation alerts | + auto-rotate |
| Container | -- | trivy (warn) | trivy (block) | + distroless | + runtime scan |
| DAST | -- | -- | -- | OWASP ZAP | + auth scanning |
| Image Signing | -- | -- | cosign (warn) | cosign (enforce) | + SLSA Level 3 |
| SBOM Attestation | -- | -- | CycloneDX gen | + in-toto | + VEX generation |
| Policy-as-Code | -- | -- | Kyverno basic | + custom policies | + mutation |
| License Compliance | -- | -- | -- | SPDX check | + legal approval |

---

## 4. Requirements

### 4.1 Signal Detection

#### REQ-24-001: Intake Signal Detection
The system SHALL detect DevSecOps signals during RICOAS conversational intake including CI/CD platform mentions, security scanner references, container orchestration indicators, policy-as-code keywords, and attestation tool references.

#### REQ-24-002: Maturity Estimation
The system SHALL estimate DevSecOps maturity level from detected signals with confidence scores, defaulting to Level 1 (Basic) when no CI/CD signals are detected.

### 4.2 Profile Management

#### REQ-24-003: Profile Schema
The system SHALL maintain per-project DevSecOps profiles containing maturity level, CI/CD platform, pipeline stages, policy engine, attestation configuration, gate thresholds, and detected signals.

#### REQ-24-004: 5-Level Maturity Assessment
The system SHALL assess maturity across 5 dimensions (CI/CD automation, security scanning, policy enforcement, supply chain, monitoring/response) scored 0-100 with gap analysis and remediation roadmap.

#### REQ-24-005: ISSO Confirmation
The system SHALL require ISSO review and confirmation before a DevSecOps profile becomes active. Profile changes also require re-confirmation.

### 4.3 Pipeline Generation

#### REQ-24-006: Platform-Specific Output
The system SHALL generate CI/CD pipeline security stages as valid platform-specific YAML for GitLab CI, GitHub Actions, or Jenkins based on the profile's CI/CD platform.

#### REQ-24-007: Maturity-Calibrated Stages
Pipeline stages SHALL be calibrated to the project's maturity level: Level 1-2 gets basic scanning, Level 3 adds policy and attestation, Level 4-5 adds DAST, runtime protection, and license compliance.

### 4.4 Policy and Attestation

#### REQ-24-008: Policy-as-Code Generation
The system SHALL generate Kyverno or OPA/Gatekeeper admission policies for Level 3+ projects including deny-privileged, require-resource-limits, deny-latest-tag, require-readonly-rootfs, and CUI namespace isolation (IL4+).

#### REQ-24-009: NIST Control Mapping
Each generated policy SHALL map to at least one NIST 800-53 control (AC-6, CM-7, SC-7, SI-7).

#### REQ-24-010: Attestation Configuration
The system SHALL configure cosign image signing and CycloneDX SBOM attestation for Level 3+ projects with KMS key management (or local key pairs for air-gapped environments).

### 4.5 Enforcement

#### REQ-24-011: IL6 Minimum Floor
IL6/SECRET projects SHALL enforce minimum Level 3 maturity regardless of detected level, with all critical gates enabled and attestation required.

#### REQ-24-012: Air-Gapped Restrictions
Air-gapped environments SHALL restrict tool selections to locally available scanners, disable Sigstore keyless mode, and set `air_gapped: true` in the profile.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `devsecops_profiles` | Per-project DevSecOps profile (maturity, stages, gates, signals) |
| `devsecops_pipeline_audit` | Append-only pipeline execution and gate evaluation log |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/devsecops/profile_manager.py` | Profile CRUD, maturity assessment, gate configuration, ISSO review |
| `tools/devsecops/pipeline_security_generator.py` | Generate maturity-calibrated pipeline security YAML |
| `tools/devsecops/policy_generator.py` | Generate Kyverno or OPA admission policies with control mappings |
| `tools/devsecops/attestation_manager.py` | Configure cosign signing, SBOM attestation, key management |
| `tools/mcp/devsecops_server.py` | MCP server for DevSecOps tools |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D117 | New DevSecOps/ZTA Agent (port 8457) with hard veto on pipeline, ZTA, deployment gate | Distributes security responsibility; hard veto prevents bypassing security pipeline |
| D119 | DevSecOps profile is per-project YAML config in DB, detected during intake | Profile drives all downstream generation; detectable from conversation signals |
| D120 | 5-level maturity model based on DoD DevSecOps reference design | Maturity grows over time; pipeline evolves with the organization |
| D121 | Service mesh and policy engine are profile-selectable (Istio/Linkerd, Kyverno/OPA) | Both engines generated; customer picks based on existing infrastructure |
| D122 | DevSecOps profile inherited by child apps from Phase 19 agentic generation | Children inherit parent's security posture, not a blank slate |

---

## 8. Security Gate

**DevSecOps Gate:**
- 0 critical policy-as-code violations
- 0 missing image attestations (when attestation is active in profile)
- 0 unresolved critical SAST findings
- 0 detected secrets in pipeline artifacts

---

## 9. Commands

```bash
# Profile management
python tools/devsecops/profile_manager.py --project-id "proj-123" --create \
  --maturity level_3_defined --json
python tools/devsecops/profile_manager.py --project-id "proj-123" --detect --json
python tools/devsecops/profile_manager.py --project-id "proj-123" --assess --json
python tools/devsecops/profile_manager.py --project-id "proj-123" --json

# Pipeline generation
python tools/devsecops/pipeline_security_generator.py --project-id "proj-123" --json

# Policy-as-code
python tools/devsecops/policy_generator.py --project-id "proj-123" --engine kyverno --json
python tools/devsecops/policy_generator.py --project-id "proj-123" --engine opa --json

# Attestation
python tools/devsecops/attestation_manager.py --project-id "proj-123" --generate --json
```
