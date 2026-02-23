# [TEMPLATE: CUI // SP-CTI]
# Goal: Zero Trust Architecture (NIST SP 800-207)

## Purpose

Implement NIST SP 800-207 Zero Trust Architecture assessment, ZTA maturity scoring across the 7 DoD Zero Trust pillars, service mesh configuration, network micro-segmentation, PDP/PEP integration, ZTA-specific Terraform security modules, and continuous ZTA posture monitoring for cATO.

**Why this matters:** Executive Order 14028 and DoD Zero Trust Strategy mandate ZTA adoption for all federal systems. Traditional perimeter-based security is insufficient — lateral movement after breach is the primary threat vector. ZTA assumes compromise, verifies explicitly, and enforces least-privilege access on every request. Without demonstrated ZTA maturity, IL4+ systems face ATO delays and risk rejection.

---

## Prerequisites

- [ ] Project initialized (`goals/init_project.md` completed)
- [ ] ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- [ ] `args/zta_config.yaml` present with pillar weights, maturity thresholds, and PDP provider settings
- [ ] NIST 800-207 requirements catalog loaded (`context/compliance/nist_800_207_requirements.json`)
- [ ] FIPS 199 categorization completed (`goals/security_categorization.md`) — baseline drives ZTA rigor
- [ ] `memory/MEMORY.md` loaded (session context)

---

## Process

### Step 1: ZTA Requirement Detection

**Tool:** `python tools/compliance/nist_800_207_assessor.py --project-id <id> --detect --json`

Auto-detect whether the project requires ZTA assessment based on:
- **Project type:** Microservice or API projects (service-to-service trust boundaries)
- **Impact level:** IL4+ projects (DoD mandate for ZTA adoption)
- **Data category:** Explicit ZTA data category assigned via universal classification manager
- **Architecture:** Projects with >3 services or cross-boundary data flows

**Expected output:**
```
ZTA detection complete.
  Project type: microservice — ZTA recommended
  Impact level: IL5 — ZTA required (DoD mandate)
  Services detected: 7
  Cross-boundary flows: 3
  ZTA requirement: REQUIRED
  Trigger: impact_level >= IL4
```

**Error handling:**
- No project metadata → prompt user for project type and impact level
- IL2 project → ZTA recommended but not enforced; log advisory and continue
- Single-service monolith → ZTA still applies to network/identity pillars; skip application/workload mesh

---

### Step 2: NIST 800-207 Assessment

**Tool:** `python tools/compliance/nist_800_207_assessor.py --project-id <id> --assess --json`

Run full assessment against 28 ZTA requirements organized by the 7 DoD pillars and core architecture principles (SDP, micro-segmentation, enhanced identity governance).

**Expected output:**
```
NIST 800-207 assessment complete.

Pillar Results (28 requirements):
  User Identity:              4/4 — 3 satisfied, 1 partial
  Device:                     4/4 — 2 satisfied, 1 partial, 1 not_satisfied
  Network:                    4/4 — 3 satisfied, 1 partial
  Application/Workload:       4/4 — 4 satisfied
  Data:                       4/4 — 3 satisfied, 1 not_assessed
  Visibility/Analytics:       4/4 — 2 satisfied, 2 partial
  Automation/Orchestration:   4/4 — 3 satisfied, 1 partial

Architecture Principles:
  Software Defined Perimeter:       satisfied
  Micro-Segmentation:               partial
  Enhanced Identity Governance:     satisfied

Overall: 20/28 satisfied, 6 partial, 1 not_satisfied, 1 not_assessed
ZTA score: 82%
Gate: PASS
```

**Error handling:**
- Requirements catalog missing → fail with path to expected `context/compliance/nist_800_207_requirements.json`
- Project not found → fail with project ID error
- Auto-check failure on a requirement → mark as "not_assessed" and continue

**Verify:** All 7 pillars assessed. No critical requirements left "not_satisfied" without a documented risk acceptance or POAM entry.

---

### Step 3: ZTA Maturity Scoring

**Tool:** `python tools/devsecops/zta_maturity_scorer.py --project-id <id> --json`

Score all 7 DoD Zero Trust pillars on a 4-level maturity model (Traditional → Initial → Advanced → Optimal). Compute weighted aggregate maturity score.

**Expected output:**
```
ZTA Maturity Scoring complete.

Pillar Maturity:
  User Identity:              Advanced   (3/4) — weight 0.20
  Device:                     Initial    (2/4) — weight 0.15
  Network:                    Advanced   (3/4) — weight 0.15
  Application/Workload:       Advanced   (3/4) — weight 0.15
  Data:                       Initial    (2/4) — weight 0.15
  Visibility/Analytics:       Initial    (2/4) — weight 0.10
  Automation/Orchestration:   Traditional(1/4) — weight 0.10

Weighted aggregate: 2.45 / 4.00 (Initial+)
Minimum pillar: Automation/Orchestration (Traditional — remediation required)
Target maturity: Advanced (3.0) by PI-25.4
Remediation roadmap: 14 actions across 4 pillars
```

**Error handling:**
- No 800-207 assessment run yet → run Step 2 first
- Missing pillar data → score available pillars, flag missing as "not_assessed"
- All pillars Traditional → generate full remediation roadmap; do not fail gate (advisory mode)

**Verify:** All 7 pillars scored. Weighted aggregate computed. Remediation roadmap generated for any pillar below Advanced.

---

### Step 4: Service Mesh Generation

**Tool:** `python tools/devsecops/service_mesh_generator.py --project-id <id> --mesh-type istio --json`

Generate service mesh configuration for mTLS enforcement and workload-level zero trust. Supports Istio (default) and Linkerd.

**Expected output:**
```
Service mesh configs generated: projects/<id>/infrastructure/mesh/

Files:
  - peer-authentication.yaml    # Namespace-wide STRICT mTLS
  - authorization-policies/      # Per-service AuthorizationPolicy
  - virtual-services/            # VirtualService routing rules
  - destination-rules/           # DestinationRule TLS settings
  - sidecar-configs/             # Sidecar egress restrictions

Services configured: 7
mTLS mode: STRICT (namespace-wide)
Authorization policies: 12 (per-service allow-list)
Egress restrictions: all sidecars limited to declared dependencies
```

**Error handling:**
- No service mesh needed (monolith, single service) → skip this step, log reason
- Unknown mesh type → default to Istio, warn user
- Service inventory missing → discover from K8s manifests or project config

**Verify:** All services have AuthorizationPolicy. PeerAuthentication is STRICT. No service allows unrestricted ingress from outside the mesh.

---

### Step 5: Network Micro-Segmentation

**Tool:** `python tools/devsecops/network_segmentation_generator.py --project-id <id> --json`

Generate Kubernetes NetworkPolicy manifests implementing ZTA micro-segmentation: default-deny per namespace, per-service allow-list policies, and DNS exception policies.

**Expected output:**
```
Network segmentation configs generated: projects/<id>/infrastructure/k8s/network-policies/

Files:
  - default-deny-all.yaml            # Default deny ingress + egress per namespace
  - allow-dns.yaml                    # CoreDNS exception (UDP 53)
  - svc-api-gateway.yaml             # API gateway: allow from ingress controller only
  - svc-auth-service.yaml            # Auth: allow from API gateway only
  - svc-data-service.yaml            # Data: allow from auth + API gateway
  - svc-monitoring.yaml              # Monitoring: allow from Prometheus scrape

Namespaces: 3 (app, monitoring, istio-system)
Total policies: 9
Default posture: deny-all (ZTA compliant)
```

**Error handling:**
- No K8s deployment → skip this step (bare-metal or VM deployment)
- Service dependencies unknown → generate default-deny only, flag for manual policy creation
- Existing NetworkPolicies → merge, do not overwrite; warn on conflicts

**Verify:** Every namespace has a default-deny policy. Every service has an explicit allow-list. No policy uses `{}` (allow-all) selectors.

---

### Step 6: PDP/PEP Configuration

**Tool:** `python tools/devsecops/pdp_config_generator.py --project-id <id> --pdp-provider <provider> --json`

Generate Policy Enforcement Point (PEP) configurations pointing to an external Policy Decision Point (PDP). Supported PDP providers: `disa_icam`, `zscaler`, `palo_alto`, `crowdstrike`, `microsoft_entra`.

**Expected output:**
```
PDP/PEP configuration generated: projects/<id>/infrastructure/zta/pdp/

Files:
  - pep-envoy-filter.yaml        # Envoy ext_authz filter config
  - pdp-endpoint-config.yaml     # PDP endpoint, timeout, fallback
  - policy-templates/             # Sample XACML/OPA policies
  - token-validation.yaml         # JWT/SAML token validation rules

PDP provider: disa_icam
PEP integration: Envoy ext_authz (Istio compatible)
Auth protocol: OAuth 2.0 + SAML 2.0
Fallback: deny-by-default on PDP timeout (30s)
```

**Error handling:**
- PDP provider not selected yet → generate reference documentation for all 5 providers; skip deployment configs
- PDP unreachable at deploy time → PEP defaults to deny-all (fail-closed)
- Multiple PDP providers → generate configs for each, document selection criteria

**Verify:** PEP fail-closed on PDP timeout. Token validation configured. No allow-by-default fallback.

---

### Step 7: ZTA Terraform Security Modules

**Tool:** `python tools/devsecops/zta_terraform_generator.py --project-id <id> --json`

Generate AWS GovCloud Terraform modules for ZTA-aligned security services.

**Expected output:**
```
ZTA Terraform modules generated: projects/<id>/infrastructure/terraform/zta/

Modules:
  - guardduty.tf          # GuardDuty threat detection (all regions)
  - security_hub.tf       # Security Hub aggregation + CIS benchmarks
  - waf.tf                # WAF v2 with managed rule groups (AWSManagedRulesCommonRuleSet)
  - config_rules.tf       # AWS Config rules (15 ZTA-relevant rules)
  - vpc_flow_logs.tf      # VPC Flow Logs to CloudWatch + S3
  - secrets_rotation.tf   # Secrets Manager automatic rotation (30-day cycle)
  - kms.tf                # KMS CMK with automatic rotation
  - cloudtrail.tf         # CloudTrail multi-region + data events

Provider: AWS GovCloud (us-gov-west-1)
Config rules: 15 (encrypted-volumes, iam-mfa, restricted-ssh, etc.)
Estimated monthly cost: ~$450 (GuardDuty + Security Hub + WAF + Flow Logs)
```

**Error handling:**
- On-prem deployment (no AWS) → skip this step, log reason
- GovCloud not available → generate commercial AWS config with migration notes
- Existing Terraform state → generate as additional modules, do not conflict with existing `main.tf`

**Verify:** All modules use GovCloud provider. KMS encryption enabled for all storage. No public access on any resource. Flow Logs retention meets AU control requirements (>=90 days).

---

### Step 8: cATO Evidence Integration

**Tool:** `python tools/compliance/cato_monitor.py --project-id <id> --add-evidence --evidence-type zta_posture --json`

Feed the ZTA maturity score into the cATO monitoring system as an additional evidence dimension. The ZTA posture score becomes a required evidence artifact for continuous authorization.

**Expected output:**
```
cATO evidence updated.

Evidence added:
  Type: zta_posture
  Score: 2.45 / 4.00
  Maturity level: Initial+
  Pillar scores: [3, 2, 3, 3, 2, 2, 1]
  Timestamp: 2026-02-18T14:30:00Z
  Freshness: current (< 24 hours)

cATO readiness (with ZTA):
  Traditional evidence: 85%
  ZTA posture: 61%
  Combined readiness: 79%
```

**Error handling:**
- cATO not enabled for project → skip, log advisory
- ZTA maturity not yet scored → run Step 3 first
- Evidence already exists for today → update existing record, do not duplicate

**Verify:** Evidence record stored in `cato_evidence` table. ZTA posture included in cATO readiness calculation. Audit trail logged.

---

### Step 9: Continuous ZTA Posture Monitoring

**Tool:** `python tools/compliance/nist_800_207_assessor.py --project-id <id> --posture-check --json`

Continuous monitoring of ZTA posture for cATO readiness. Checks maturity score, pillar minimums, evidence freshness, and drift from last assessment.

**Expected output:**
```
ZTA posture check complete.

Status: HEALTHY
  Aggregate maturity: 2.45 / 4.00 (minimum: 2.0 — PASS)
  Pillar minimums: 6/7 pass (Automation/Orchestration below floor — WARNING)
  Evidence freshness: 2 days (maximum: 30 days — PASS)
  Drift from last assessment: +0.15 (improving)
  Next scheduled assessment: 2026-03-04

Alerts:
  [WARN] Automation/Orchestration pillar at Traditional (1/4) — below Initial floor
  [INFO] Remediation action AO-1: "Implement automated incident response playbooks" — due PI-25.3

Posture gate: PASS (with warning)
```

**Error handling:**
- No previous assessment → run full assessment (Step 2) first
- Evidence expired (>30 days) → gate fails; require re-assessment
- All pillars degraded → alert and escalate; do not auto-remediate ZTA posture changes

**Verify:** Posture check results stored. Alerts generated for pillar minimums. cATO dashboard reflects current ZTA posture.

---

### Step 10: Log to Audit Trail

**Tool:** `python tools/audit/audit_logger.py --event-type "zta.assessment" --actor "orchestrator" --action "ZTA assessment and posture scoring completed" --project-id <id>`

**Tool:** `python tools/memory/memory_write.py --content "ZTA assessment for <id>. Maturity: <score>/4.0. Pillars: <pillar_summary>. Gate: <PASS|FAIL>" --type event --importance 7`

---

## Success Criteria

- [ ] ZTA requirement detection identifies project ZTA needs
- [ ] NIST 800-207 assessment completes across all 28 requirements / 7 pillars
- [ ] ZTA maturity scored with weighted aggregate and per-pillar breakdown
- [ ] Service mesh configs generated with STRICT mTLS and per-service authorization (if applicable)
- [ ] NetworkPolicy manifests enforce default-deny micro-segmentation (if K8s)
- [ ] PDP/PEP configs generated with fail-closed behavior
- [ ] ZTA Terraform modules generated for AWS GovCloud security services (if cloud)
- [ ] ZTA posture score integrated as cATO evidence dimension
- [ ] Continuous posture monitoring operational with freshness and pillar-minimum checks
- [ ] Audit trail entry logged

---

## Edge Cases

1. **No service mesh needed:** Single-service or monolith projects skip Step 4. ZTA still applies to identity, device, network, and data pillars. Document the skip reason.
2. **On-prem deployment:** Skip Step 7 (Terraform). Network segmentation and PDP/PEP configs still apply. Generate equivalent firewall rules instead of AWS security modules.
3. **PDP not selected yet:** Generate reference documentation for all supported providers. Do not generate deployment configs. Flag as POAM item with 90-day deadline.
4. **Traditional maturity level:** If aggregate maturity is Traditional (1.0), do not fail the gate for IL2/IL3 projects. Generate a remediation roadmap with prioritized actions and target PI milestones.
5. **IL2 project:** ZTA is recommended but not required. Run assessment in advisory mode. Gate evaluation is informational only (does not block deployment).
6. **Air-gapped environment:** PDP integration requires network connectivity to external provider. For air-gapped IL6/SECRET, generate local OPA-based PDP configs instead of external provider integration.
7. **Existing service mesh:** If Istio/Linkerd is already deployed, generate incremental policies only. Do not overwrite existing PeerAuthentication or AuthorizationPolicy resources.
8. **Mixed deployment:** Hybrid cloud + on-prem requires separate segmentation policies per environment. Generate both K8s NetworkPolicies and traditional firewall rules.

---

## Gate Criteria

| Gate | Criteria |
|------|----------|
| ZTA Assessment | 0 critical requirements "not_satisfied" without risk acceptance |
| ZTA Maturity | Aggregate maturity >= 2.0 (Initial) for IL4+; >= 3.0 (Advanced) for IL5+ target |
| Pillar Minimum | No pillar below Traditional for IL4+; no pillar below Initial for IL5+ |
| mTLS | All service-to-service communication uses STRICT mTLS (if mesh deployed) |
| Network Segmentation | Default-deny policy exists for every namespace |
| PDP Fail-Closed | PEP denies on PDP timeout (no allow-by-default) |
| Evidence Freshness | ZTA posture evidence < 30 days old for cATO |

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| ZTA detection | Tools | nist_800_207_assessor.py (--detect) |
| 800-207 assessment | Tools | nist_800_207_assessor.py (--assess) |
| Maturity scoring | Tools | zta_maturity_scorer.py |
| Service mesh generation | Tools | service_mesh_generator.py |
| Network segmentation | Tools | network_segmentation_generator.py |
| PDP/PEP configuration | Tools | pdp_config_generator.py |
| ZTA Terraform modules | Tools | zta_terraform_generator.py |
| cATO evidence | Tools | cato_monitor.py |
| Posture monitoring | Tools | nist_800_207_assessor.py (--posture-check) |
| Workflow sequencing | Orchestration | AI (you) |
| Pillar weights / thresholds | Args | zta_config.yaml |
| 800-207 requirements | Context | nist_800_207_requirements.json |
| PDP provider settings | Args | zta_config.yaml |

---

## Related Files

- **Tools:** `tools/compliance/nist_800_207_assessor.py`, `tools/devsecops/zta_maturity_scorer.py`, `tools/devsecops/service_mesh_generator.py`, `tools/devsecops/network_segmentation_generator.py`, `tools/devsecops/pdp_config_generator.py`, `tools/devsecops/zta_terraform_generator.py`, `tools/compliance/cato_monitor.py`
- **Args:** `args/zta_config.yaml`
- **Context:** `context/compliance/nist_800_207_requirements.json`
- **Feeds from:** `goals/security_categorization.md` (FIPS 199 baseline), `goals/init_project.md` (project setup)
- **Feeds into:** `goals/ato_acceleration.md` (cATO evidence), `goals/deploy_workflow.md` (mesh/segmentation configs), `goals/compliance_workflow.md` (ZTA as compliance dimension)
- **Database:** `data/icdev.db` (zta_assessments, zta_pillar_scores, cato_evidence tables)

---

## Changelog

- 2026-02-18: Initial creation
