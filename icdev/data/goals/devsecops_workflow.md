// CUI // SP-CTI
// Distribution: Authorized personnel only
// Handling: In accordance with DoDI 5200.48

# Goal: DevSecOps Lifecycle Workflow

## Purpose

Auto-detect and configure DevSecOps maturity during requirements intake, create per-project DevSecOps profiles that control all downstream pipeline and infrastructure generation, and manage the full DevSecOps lifecycle. Every project gets a profile that drives SAST, DAST, SCA, secret scanning, container scanning, image signing, SBOM attestation, RASP, policy-as-code, and license compliance — calibrated to the organization's actual maturity level rather than a one-size-fits-all default.

**Why this matters:** Most DevSecOps failures come from mismatched tooling — immature teams get overwhelmed by enterprise-grade pipelines, and mature teams get held back by starter configs. Profile-driven generation ensures the security pipeline matches what the customer can actually operate. Maturity grows over time; the profile evolves with it.

---

## Prerequisites

- [ ] Project initialized (`goals/init_project.md` completed)
- [ ] Database initialized (`python tools/db/init_icdev_db.py`)
- [ ] `args/devsecops_config.yaml` present (maturity model, tool registry, gate thresholds, policy templates)
- [ ] `memory/MEMORY.md` loaded (session context)
- [ ] Intake session active or completed (`goals/requirements_intake.md`) — DevSecOps signals detected during intake

---

## Process

### Step 1: DevSecOps Profile Detection

**Tool:** `python tools/requirements/intake_engine.py --session-id <id> --message "<customer message>" --json`

During conversational intake (RICOAS Stage 2), the intake engine detects DevSecOps signals from customer conversation. Signals include:

- Existing CI/CD tooling mentions (Jenkins, GitLab CI, GitHub Actions, Bamboo)
- Security scanning references (Fortify, SonarQube, Checkmarx, Prisma Cloud)
- Container orchestration (K8s, OpenShift, ECS)
- Policy-as-code mentions (OPA, Kyverno, Gatekeeper, Sentinel)
- Attestation/signing references (cosign, Sigstore, Notation, in-toto)
- Compliance framework references that imply DevSecOps maturity (cATO, FedRAMP High, IL5/IL6)

**Expected output (within intake turn response):**
```
DevSecOps signals detected:
  - ci_cd_platform: gitlab_ci (confidence: 0.9)
  - existing_sast: sonarqube (confidence: 0.7)
  - container_runtime: kubernetes (confidence: 0.85)
  - policy_engine: none_detected
  - attestation: none_detected
  - estimated_maturity: level_2
```

**Error handling:**
- No CI/CD signals detected → default to `level_1` (basic), log assumption
- Contradictory signals (e.g., "we have no pipeline" + mentions Jenkins) → flag ambiguity, ask clarifying question
- Customer uses proprietary tool names → attempt mapping to known categories, fall back to `unknown`

**Verify:** Detection results stored in `devsecops_profiles` table. All detected signals have confidence scores.

---

### Step 2: Profile Creation

**Tool:** `python tools/devsecops/profile_manager.py --project-id <id> --create --json`

Create or auto-generate a DevSecOps profile based on detected maturity and project characteristics. The profile drives all downstream pipeline and infrastructure generation.

**Profile structure:**
```json
{
  "project_id": "<id>",
  "maturity_level": "level_2",
  "ci_cd_platform": "gitlab_ci",
  "pipeline_stages": ["sast", "sca", "secrets", "container_scan", "sbom"],
  "policy_engine": "kyverno",
  "attestation": {"enabled": false, "tool": null},
  "gates": {"block_critical": true, "block_high_sast": true, "block_secrets": true},
  "rasp_enabled": false,
  "license_compliance": false,
  "dast_enabled": false,
  "detected_signals": [...],
  "customer_overrides": {},
  "created_at": "<timestamp>"
}
```

**Error handling:**
- Brownfield project with existing `.gitlab-ci.yml` or `Jenkinsfile` → parse existing pipeline, merge detected stages into profile without duplicating
- Air-gapped environment → restrict tool selections to locally available tools (no SaaS scanners), set `air_gapped: true` in profile
- Impact level IL6/SECRET → enforce maximum maturity gates regardless of detected level

**Verify:** Profile stored in DB. Profile contains all required fields. Maturity level is consistent with detected signals.

---

### Step 3: Maturity Assessment

**Tool:** `python tools/devsecops/profile_manager.py --project-id <id> --assess-maturity --json`

Assess current DevSecOps maturity against a 5-level model:

| Level | Name | Characteristics |
|-------|------|-----------------|
| 1 | Basic | Manual builds, ad-hoc scanning, no policy enforcement |
| 2 | Managed | CI/CD pipeline, SAST + SCA in pipeline, manual gates |
| 3 | Defined | Automated gates, container scanning, SBOM generation, policy-as-code |
| 4 | Measured | DAST, image signing, attestation, RASP, license compliance, metrics-driven |
| 5 | Optimizing | Full cATO pipeline, automated evidence collection, continuous compliance, self-healing security |

**Expected output:**
```
DevSecOps Maturity Assessment
  Current level: 2 (Managed)
  Target level: 3 (Defined)

  Dimension scores (0-100):
    CI/CD Automation:     75
    Security Scanning:    60
    Policy Enforcement:   20
    Supply Chain:         40
    Monitoring & Response: 30

  Gap to next level:
    - Add policy-as-code engine (Kyverno or OPA)
    - Enable container scanning in pipeline
    - Configure automated SBOM generation
    - Implement gate enforcement (currently manual)

  Estimated effort to Level 3: 2-3 sprints
```

**Error handling:**
- Insufficient data for assessment → prompt for additional information, score available dimensions only
- Customer disputes assessment → allow manual override with justification recorded in audit trail

**Verify:** Assessment stored in DB with dimension scores. Gap analysis identifies specific actions for next level.

---

### Step 4: Pipeline Security Generation

**Tool:** `python tools/devsecops/pipeline_security_generator.py --project-id <id> --json`

Generate profile-driven CI/CD pipeline security stages. Output is platform-specific YAML (GitLab CI, GitHub Actions, or Jenkins) based on the profile's `ci_cd_platform`.

**Stages generated (based on maturity level):**

| Stage | Level 1 | Level 2 | Level 3 | Level 4 | Level 5 |
|-------|---------|---------|---------|---------|---------|
| SAST | bandit | bandit + ruff | bandit + ruff + semgrep | + Fortify/Checkmarx | + custom rules |
| SCA | pip-audit | pip-audit + SBOM | + license check | + transitive analysis | + auto-remediate |
| Secrets | detect-secrets | detect-secrets | + git history scan | + rotation alerts | + auto-rotate |
| Container | -- | trivy (warn) | trivy (block) | + distroless enforce | + runtime scan |
| DAST | -- | -- | -- | OWASP ZAP | + auth scanning |
| Image Signing | -- | -- | cosign (warn) | cosign (enforce) | + SLSA Level 3 |
| SBOM Attestation | -- | -- | CycloneDX gen | + in-toto attestation | + VEX generation |
| RASP | -- | -- | -- | runtime protection | + auto-block |
| Policy-as-Code | -- | -- | Kyverno basic | + custom policies | + mutation policies |
| License Compliance | -- | -- | -- | SPDX check | + legal approval flow |

**Expected output:**
```
Pipeline security stages generated:
  Platform: gitlab_ci
  Output: projects/<name>/ci/security-stages.yml

  Stages configured: 6
    - sast (bandit + ruff)
    - sca (pip-audit + SBOM)
    - secrets (detect-secrets)
    - container_scan (trivy, mode=block)
    - image_signing (cosign, mode=warn)
    - sbom_attestation (CycloneDX)

  Gate enforcement: 3 blocking gates configured
  Estimated pipeline addition: +4-6 minutes
```

**Error handling:**
- Unknown CI/CD platform → generate generic shell scripts with instructions for manual integration
- Tool not available in air-gapped environment → substitute with local equivalent or mark as manual step
- Pipeline YAML syntax error → validate YAML before writing, fail with line-level error

**Verify:** Generated YAML is valid. All stages match profile maturity level. Gate thresholds match `args/devsecops_config.yaml`.

---

### Step 5: Policy-as-Code Generation

**Tool:** `python tools/devsecops/policy_generator.py --project-id <id> --engine kyverno --json`

Generate Kyverno or OPA/Gatekeeper admission policies based on the DevSecOps profile and project's compliance requirements.

**Policies generated (Level 3+):**
- Require signed images (`verify-image-signature`)
- Deny privileged containers (`deny-privileged`)
- Enforce resource limits (`require-resource-limits`)
- Require labels (`require-labels`)
- Deny latest tag (`deny-latest-tag`)
- Enforce read-only root filesystem (`require-readonly-rootfs`)
- CUI namespace isolation (`cui-namespace-isolation`) — IL4+ only
- Deny public load balancers (`deny-public-lb`) — IL5+ only

**Expected output:**
```
Policy-as-code generated:
  Engine: kyverno
  Output directory: projects/<name>/policies/

  Policies: 6
    - deny-privileged.yaml (ClusterPolicy, enforce)
    - require-resource-limits.yaml (ClusterPolicy, enforce)
    - require-labels.yaml (ClusterPolicy, audit)
    - deny-latest-tag.yaml (ClusterPolicy, enforce)
    - require-readonly-rootfs.yaml (ClusterPolicy, enforce)
    - cui-namespace-isolation.yaml (ClusterPolicy, enforce)

  Mode: enforce (3), audit (3)
  Compliance mappings: AC-6, CM-7, SC-7, SI-7
```

**Error handling:**
- Neither Kyverno nor OPA available → generate policies as documentation with manual enforcement instructions
- Policy conflicts with existing cluster policies → detect via dry-run, warn before applying
- Customer requests policy exceptions → record exception with justification and expiration date

**Verify:** Policies are valid YAML. Each policy maps to at least one NIST 800-53 control. Enforce/audit mode matches maturity level.

---

### Step 6: Attestation Setup

**Tool:** `python tools/devsecops/attestation_manager.py --project-id <id> --setup --json`

Configure image signing and SBOM attestation using cosign or notation, based on profile settings.

**Expected output:**
```
Attestation configuration generated:
  Signing tool: cosign
  Key management: AWS KMS (key alias: icdev/<project-id>/signing)

  Configured attestations:
    - Image signature (cosign sign)
    - SBOM attestation (cosign attest --type cyclonedx)
    - SLSA provenance (cosign attest --type slsaprovenance)

  Verification policy:
    - Require signature before deploy: true
    - Require SBOM attestation: true
    - Keyless mode (Fulcio/Rekor): false (air-gap incompatible)

  Output: projects/<name>/attestation/cosign-config.yaml
```

**Error handling:**
- Air-gapped environment → use local key pairs instead of Sigstore keyless, disable Rekor transparency log
- KMS unavailable → fall back to file-based key management with rotation reminders
- Maturity level < 3 → skip attestation setup, log as future enhancement

**Verify:** Config references valid KMS key alias or local key path. Verification policy matches profile gates. No Sigstore keyless in air-gapped profiles.

---

### Step 7: Gate Configuration

**Tool:** `python tools/devsecops/profile_manager.py --project-id <id> --configure-gates --json`

Configure DevSecOps security gates that integrate with existing ICDEV security gates (`args/security_gates.yaml`). These are additive — they do not replace existing gates.

**DevSecOps-specific gates:**

| Gate | Level 1-2 | Level 3 | Level 4-5 |
|------|-----------|---------|-----------|
| Critical policy violations | warn | block | block |
| Missing image signature | -- | warn | block |
| Missing SBOM attestation | -- | warn | block |
| Detected secrets in image | block | block | block |
| Unapproved base image | -- | -- | block |
| License violation (GPL in proprietary) | -- | -- | block |
| DAST critical findings | -- | -- | block |

**Expected output:**
```
DevSecOps gates configured:
  Total gates: 4 (profile level 2)
    - block_critical_vulns: enabled (threshold: 0)
    - block_secrets: enabled (threshold: 0)
    - block_high_sast: enabled (threshold: 0)
    - warn_missing_sbom: enabled (mode: warn)

  Integration: merged into args/security_gates.yaml (project-scoped)
  Gate evaluation: automatic during pipeline execution
```

**Error handling:**
- Gate conflicts with existing project gates → use stricter of the two thresholds
- Customer requests relaxing a critical gate → require ISSO written approval, record in audit trail

**Verify:** Gates stored in DB and merged into project security gates. No critical gate set to warn-only at Level 3+.

---

### Step 8: Profile Review

**Tool:** `python tools/devsecops/profile_manager.py --project-id <id> --review --json`

Present the complete DevSecOps profile to the customer ISSO for review and confirmation. The profile does not become active until confirmed.

**Expected output:**
```
=== DEVSECOPS PROFILE REVIEW ===
Project: <name>
Date: <YYYY-MM-DD>
Classification: CUI // SP-CTI

MATURITY: Level 2 (Managed) → Target: Level 3 (Defined)

PIPELINE STAGES: 6 configured
  [x] SAST (bandit + ruff) — blocking
  [x] SCA (pip-audit) — blocking
  [x] Secrets (detect-secrets) — blocking
  [x] Container scan (trivy) — blocking
  [ ] Image signing (cosign) — not yet (Level 3)
  [x] SBOM generation (CycloneDX) — warn

POLICIES: 6 admission policies
  3 enforce, 3 audit

ATTESTATION: not configured (Level 3 requirement)

GATES: 4 active gates

STATUS: <PENDING_REVIEW | CONFIRMED | REJECTED>
ISSO: <pending confirmation>
```

**Error handling:**
- ISSO requests changes → update profile, re-run affected generation steps (4-7), re-present for review
- ISSO rejects profile → record rejection with rationale, escalate to project lead
- Profile confirmed → mark as active, pipeline generation uses this profile going forward

**Verify:** Profile status is `confirmed` before any pipeline uses it. Confirmation recorded in audit trail with ISSO identifier.

---

### Step 9: Log to Audit Trail

**Tool:** `python tools/audit/audit_logger.py --event "devsecops_profile_created" --actor "orchestrator" --action "DevSecOps profile created and confirmed" --project <name>`

**Tool:** `python tools/memory/memory_write.py --content "DevSecOps profile created for <name>. Maturity: Level <n>. Stages: <count>. Gates: <count>. Status: <confirmed|pending>" --type event --importance 7`

---

## Edge Cases & Notes

1. **No CI/CD detected:** Default to `level_1` (basic) with manual build instructions. Generate shell scripts instead of pipeline YAML. Log assumption for ISSO review.
2. **Brownfield project with existing tools:** Parse existing pipeline configs (`.gitlab-ci.yml`, `Jenkinsfile`, `.github/workflows/`). Merge detected stages into profile. Do not duplicate or overwrite existing security stages.
3. **Air-gapped environment:** Restrict to locally installable tools only (bandit, ruff, pip-audit, detect-secrets, trivy offline DB). Disable Sigstore keyless, SaaS scanners, and cloud-based policy engines. Set `air_gapped: true` in profile.
4. **Customer overrides auto-detected maturity:** Allow manual override with justification. Record both detected and overridden values. If override is lower than detected, warn that security posture may be reduced.
5. **Maturity level changes mid-project:** Re-run Steps 3-7 with new level. Pipeline stages are additive (never remove existing stages). New gates may be added but existing gates are never relaxed without ISSO approval.
6. **IL6/SECRET projects:** Force minimum Level 3 maturity regardless of detection. Enforce all critical gates. Require attestation. Disable any cloud-based or SaaS scanner integration.
7. **Multi-language projects:** Generate scanner configurations for all detected languages. Each language gets its own SAST toolchain per `context/languages/language_registry.json`.

---

## Success Criteria

- [ ] DevSecOps signals detected during intake with confidence scores
- [ ] Profile created with maturity level, pipeline stages, and gate configuration
- [ ] Maturity assessment completed with dimension scores and gap analysis
- [ ] Pipeline security stages generated as valid platform-specific YAML
- [ ] Policy-as-code generated (Level 3+) with NIST 800-53 control mappings
- [ ] Attestation configured (Level 3+) with key management setup
- [ ] Gates configured and merged with existing project security gates
- [ ] Profile reviewed and confirmed by customer ISSO
- [ ] Audit trail entry logged for all profile creation and confirmation events

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Signal detection | Orchestration | AI detects DevSecOps signals during intake |
| Profile creation | Tools | profile_manager.py |
| Maturity assessment | Tools + Args | profile_manager.py + devsecops_config.yaml |
| Pipeline generation | Tools | pipeline_security_generator.py |
| Policy generation | Tools | policy_generator.py |
| Attestation setup | Tools | attestation_manager.py |
| Gate configuration | Args | security_gates.yaml (project-scoped merge) |
| Profile review | Orchestration | AI presents to ISSO for confirmation |
| Maturity model | Context | 5-level model reference in devsecops_config.yaml |
| Pipeline templates | Hard Prompts | Platform-specific pipeline templates |

---

## Related Files

- **Tools:** `tools/devsecops/profile_manager.py`, `tools/devsecops/pipeline_security_generator.py`, `tools/devsecops/policy_generator.py`, `tools/devsecops/attestation_manager.py`
- **Args:** `args/devsecops_config.yaml`, `args/security_gates.yaml`
- **Context:** `context/languages/language_registry.json`
- **Feeds from:** `goals/requirements_intake.md` (DevSecOps signals from intake), `goals/init_project.md` (project setup)
- **Feeds into:** `goals/deploy_workflow.md` (pipeline uses profile), `goals/security_scan.md` (scanner selection from profile), `goals/compliance_workflow.md` (policy-as-code maps to NIST controls)

---

## Changelog

- 2026-02-18: Initial creation
