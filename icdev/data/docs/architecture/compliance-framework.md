# Compliance Architecture Guide

CUI // SP-CTI

## Overview

ICDEV supports 26 compliance frameworks through a unified architecture built on a dual-hub crosswalk model (D111). Implementing a control in one framework automatically cascades coverage to all mapped frameworks. The system uses the BaseAssessor ABC pattern (D116) to minimize per-framework implementation effort (~60 LOC vs ~400+ LOC) while maintaining full gate enforcement, OSCAL generation, and CLI support.

---

## Supported Compliance Frameworks

| # | Framework | Catalog File | Assessor | Report Generator |
|---|-----------|-------------|----------|------------------|
| 1 | NIST 800-53 Rev 5 | `nist_800_53.json` | `control_mapper.py` | SSP, control matrix |
| 2 | FedRAMP Moderate | `fedramp_moderate_baseline.json` | `fedramp_assessor.py` | `fedramp_report_generator.py` |
| 3 | FedRAMP High | `fedramp_high_baseline.json` | `fedramp_assessor.py` | `fedramp_report_generator.py` |
| 4 | NIST 800-171 | `nist_800_171_controls.json` | via crosswalk | via crosswalk coverage |
| 5 | CMMC Level 2/3 | `cmmc_practices.json` | `cmmc_assessor.py` | `cmmc_report_generator.py` |
| 6 | DoD CSSP (DI 8530.01) | `dod_cssp_8530.json` | `cssp_assessor.py` | `cssp_report_generator.py` |
| 7 | CISA Secure by Design | `cisa_sbd_requirements.json` | `sbd_assessor.py` | `sbd_report_generator.py` |
| 8 | IEEE 1012 IV&V | `ivv_requirements.json` | `ivv_assessor.py` | `ivv_report_generator.py` |
| 9 | DoDI 5000.87 DES | `des_requirements.json` | `des_assessor.py` | `des_report_generator.py` |
| 10 | FIPS 199 | `nist_sp_800_60_types.json` | `fips199_categorizer.py` | Categorization report |
| 11 | FIPS 200 | `fips_200_areas.json` | `fips200_validator.py` | Gap report |
| 12 | CNSSI 1253 | `cnssi_1253_overlay.json` | via fips199_categorizer | Overlay application |
| 13 | CJIS Security Policy | `cjis_security_policy.json` | `cjis_assessor.py` | via base_assessor |
| 14 | HIPAA Security Rule | `hipaa_security_rule.json` | `hipaa_assessor.py` | via base_assessor |
| 15 | HITRUST CSF v11 | `hitrust_csf_v11.json` | `hitrust_assessor.py` | via base_assessor |
| 16 | SOC 2 Type II | `soc2_trust_criteria.json` | `soc2_assessor.py` | via base_assessor |
| 17 | PCI DSS v4.0 | `pci_dss_v4.json` | `pci_dss_assessor.py` | via base_assessor |
| 18 | ISO/IEC 27001:2022 | `iso27001_2022_controls.json` | `iso27001_assessor.py` | via base_assessor |
| 19 | NIST SP 800-207 (ZTA) | `nist_800_207_zta.json` | `nist_800_207_assessor.py` | via base_assessor |
| 20 | DoD MOSA (10 U.S.C. 4401) | `mosa_framework.json` | `mosa_assessor.py` | via base_assessor |
| 21 | MITRE ATLAS v5.4.0 | `atlas_mitigations.json` | `atlas_assessor.py` | `atlas_report_generator.py` |
| 22 | OWASP LLM Top 10 | `owasp_llm_top10.json` | `owasp_llm_assessor.py` | via base_assessor |
| 23 | NIST AI RMF 1.0 | `nist_ai_rmf.json` | `nist_ai_rmf_assessor.py` | via base_assessor |
| 24 | ISO/IEC 42001:2023 | `iso42001_controls.json` | `iso42001_assessor.py` | via base_assessor |
| 25 | OWASP Agentic AI | `owasp_agentic_threats.json` | `owasp_agentic_assessor.py` | via base_assessor |
| 26 | XAI (Observability) | `xai_requirements.json` | `xai_assessor.py` | via base_assessor |

Additionally, the **SAFE-AI** catalog (`safeai_controls.json`) maps 100 AI-affected NIST 800-53 controls with `ai_concern` narrative per control, functioning as an overlay rather than a standalone framework.

---

## Control Crosswalk Engine

### Dual-Hub Model (D111)

The crosswalk engine uses two hub frameworks connected by a bidirectional bridge. Every framework maps to one of the two hubs.

```
                     DOMESTIC FRAMEWORKS
                     (map to US Hub)
                     +------------------+
                     | FedRAMP Mod/High |
                     | NIST 800-171     |
                     | CMMC Level 2/3   |
                     | CJIS             |
                     | HIPAA            |
                     | HITRUST          |
                     | SOC 2            |
                     | PCI DSS          |
                     | NIST 800-207 ZTA |
                     | MOSA             |
                     | ATLAS            |
                     | OWASP LLM        |
                     | NIST AI RMF      |
                     | OWASP Agentic    |
                     | XAI              |
                     +--------+---------+
                              |
                     +--------v---------+
                     |   US HUB         |
                     |   NIST 800-53    |    iso27001_nist_bridge.json
                     |   Rev 5          | <=============================>
                     +------------------+    (bidirectional mapping)
                                                        |
                                               +--------v---------+
                                               |  INTL HUB        |
                                               |  ISO/IEC 27001   |
                                               |  :2022           |
                                               +--------+---------+
                                                        |
                                               +--------v---------+
                                               | INTL FRAMEWORKS   |
                                               | (map to INTL Hub) |
                                               | ISO/IEC 42001     |
                                               | (future: GDPR,    |
                                               |  SOX, etc.)       |
                                               +-------------------+
```

### How Crosswalk Works

When a control is implemented in any framework, the crosswalk engine propagates coverage:

```
Example: Implement NIST 800-53 AC-2 (Account Management)

  AC-2 (NIST 800-53)
    |
    +---> FedRAMP AC-2         (direct mapping)
    +---> NIST 800-171 3.1.1   (direct mapping)
    +---> CMMC AC.L2-3.1.1     (direct mapping)
    +---> CJIS 5.5.2           (via crosswalk)
    +---> HIPAA 164.312(d)     (via crosswalk)
    +---> SOC 2 CC6.1          (via crosswalk)
    +---> PCI DSS 7.1          (via crosswalk)
    +---> ISO 27001 A.5.15     (via bridge)
    +---> NIST 800-207 ZTA     (via crosswalk)
```

One implementation satisfies requirements across all applicable frameworks. This eliminates redundant work when multiple frameworks apply.

### Crosswalk Commands

```bash
# Query crosswalk for a specific control
python tools/compliance/crosswalk_engine.py --control AC-2

# Coverage analysis across all frameworks for a project
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --coverage

# Gap analysis against a specific framework
python tools/compliance/crosswalk_engine.py --project-id "proj-123" \
    --target fedramp-moderate --gap-analysis
```

---

## BaseAssessor ABC Pattern (D116)

All compliance assessors inherit from a common abstract base class that provides crosswalk integration, gate evaluation, CLI output formatting, and database storage. This reduces per-framework implementation to approximately 60 lines of code.

### Architecture

```
+------------------------------------------+
|          BaseAssessor (ABC)              |
|                                          |
|  - load_catalog()                        |
|  - assess(project_id) -> results         |
|  - evaluate_gate(project_id) -> pass/fail|
|  - generate_report(project_id)           |
|  - integrate_crosswalk()                 |
|  - store_results(project_id, results)    |
|  - cli_main()                            |
+----------------+-------------------------+
                 |
    +------------+--+--+--+--+--+------+
    |            |     |     |         |
+---v---+  +----v--+ +v---+ +v------+ +v---------+
| CJIS  |  | HIPAA | |SOC2| |PCI DSS| | ISO 27001|
| ~60   |  | ~60   | |~60 | | ~60   | | ~60 LOC  |
| LOC   |  | LOC   | |LOC | | LOC   | |          |
+-------+  +-------+ +----+ +-------+ +----------+
```

### Per-Framework Implementation

Each assessor only needs to define:

1. **Catalog path** -- which JSON file contains the framework controls
2. **Assessment logic** -- framework-specific check implementations
3. **Gate conditions** -- blocking thresholds for the framework

Everything else (crosswalk, DB storage, CLI, JSON/human output, report generation) is inherited.

### Adding a New Framework

1. Create the control catalog JSON in `context/compliance/`
2. Create an assessor that extends `BaseAssessor`
3. Add the framework to `args/framework_registry.yaml`
4. Add crosswalk mappings to the appropriate hub (US or International)
5. Run: `python tools/testing/claude_dir_validator.py --json`

---

## Classification System

### Impact Levels and Markings

| Impact Level | Classification | Marking | Network |
|-------------|---------------|---------|---------|
| IL2 | Public | None | Commercial |
| IL4 | CUI | `CUI // SP-CTI` | GovCloud |
| IL5 | CUI (Dedicated) | `CUI // SP-CTI` | GovCloud Dedicated |
| IL6 | SECRET | `SECRET // NOFORN` | SIPR (air-gapped) |

### Universal Classification Manager

The system manages 10 data categories with composable markings (D109):

| Category | Handling Standard | Example |
|----------|------------------|---------|
| CUI | NIST 800-171, 32 CFR Part 2002 | Controlled Unclassified Information |
| PHI | HIPAA Security Rule | Protected Health Information |
| PCI | PCI DSS v4.0 | Payment Card Industry data |
| CJIS | CJIS Security Policy | Criminal Justice Information |
| FTI | IRS Publication 1075 | Federal Tax Information |
| SBU | Agency-specific | Sensitive But Unclassified |
| FOUO | DoD marking (legacy) | For Official Use Only |
| PII | NIST 800-122 | Personally Identifiable Information |
| ITAR | 22 CFR Parts 120-130 | Controlled technical data |
| EAR | 15 CFR Parts 730-774 | Export controlled data |

A single artifact can carry multiple data categories simultaneously. The highest-sensitivity category determines handling requirements.

### Classification Commands

```bash
# Apply CUI marking to a file
python tools/compliance/cui_marker.py --file "/path/to/file" --marking "CUI // SP-CTI"

# Generate composite banner (CUI + PHI)
python tools/compliance/universal_classification_manager.py --banner CUI PHI --json

# Generate composite code header
python tools/compliance/universal_classification_manager.py --code-header CUI PCI --language python

# Auto-detect data categories for a project
python tools/compliance/universal_classification_manager.py --detect --project-id "proj-123" --json

# Validate markings
python tools/compliance/universal_classification_manager.py --validate --project-id "proj-123" --json

# Classification settings by impact level
python tools/compliance/classification_manager.py --impact-level IL5
```

---

## FIPS 199/200 Security Categorization

### FIPS 199 (D54-D57)

FIPS 199 categorization determines the security baseline for a project using SP 800-60 information types and the high watermark method.

```
Information Types (SP 800-60)
    |
    v
Provisional C/I/A per type
    |
    v
Organization adjustments (with justification)
    |
    v
High watermark across all types
    |
    v
Final categorization: Low / Moderate / High
    |
    v
CNSSI 1253 overlay (auto-applied for IL6/SECRET)
```

### FIPS 200

Validates all 17 minimum security requirement areas against the FIPS 199 baseline:

| # | Area | Description |
|---|------|-------------|
| 1 | AC | Access Control |
| 2 | AT | Awareness and Training |
| 3 | AU | Audit and Accountability |
| 4 | CA | Security Assessment |
| 5 | CM | Configuration Management |
| 6 | CP | Contingency Planning |
| 7 | IA | Identification and Authentication |
| 8 | IR | Incident Response |
| 9 | MA | Maintenance |
| 10 | MP | Media Protection |
| 11 | PE | Physical and Environmental |
| 12 | PL | Planning |
| 13 | PS | Personnel Security |
| 14 | RA | Risk Assessment |
| 15 | SA | System and Services Acquisition |
| 16 | SC | System and Communications Protection |
| 17 | SI | System and Information Integrity |

### Commands

```bash
# Browse SP 800-60 information types
python tools/compliance/fips199_categorizer.py --list-catalog

# Add information type to project
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --add-type "D.1.1.1"

# Add with C/I/A adjustment
python tools/compliance/fips199_categorizer.py --project-id "proj-123" \
    --add-type "D.2.3.4" --adjust-c High

# Run categorization
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --categorize --json

# Force CNSSI 1253 method
python tools/compliance/fips199_categorizer.py --project-id "proj-123" \
    --categorize --method cnssi_1253

# Validate FIPS 200 (17 areas)
python tools/compliance/fips200_validator.py --project-id "proj-123" --json

# Gate evaluations
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --gate
python tools/compliance/fips200_validator.py --project-id "proj-123" --gate --json
```

---

## Multi-Regime Assessment

When multiple compliance frameworks apply to a project (common in GovCloud environments), the multi-regime assessor provides a unified view.

### Assessment Flow

```
1. DETECT applicable frameworks
   (auto-detection from data types, impact level, sector)
   |
   v
2. CONFIRM (advisory only, D110 -- ISSO must approve)
   |
   v
3. ASSESS all confirmed frameworks
   |
   v
4. DEDUPLICATE via crosswalk
   (N frameworks --> 1 unified NIST control set, D113)
   |
   v
5. REPORT unified compliance posture
   |
   v
6. GATE evaluation (all frameworks must pass individually)
```

### Auto-Detection Triggers

| Data Type | Triggered Frameworks |
|-----------|---------------------|
| CUI | NIST 800-171, CMMC, FedRAMP |
| PHI | HIPAA, HITRUST |
| PCI | PCI DSS |
| CJIS | CJIS Security Policy |
| IL4+ | FedRAMP (minimum) |
| DoD/IC customer | CMMC, MOSA (D125) |
| AI/ML components | ATLAS, OWASP LLM, NIST AI RMF |

### Commands

```bash
# Detect applicable frameworks
python tools/compliance/compliance_detector.py --project-id "proj-123" --json

# Detect + store in DB
python tools/compliance/compliance_detector.py --project-id "proj-123" --apply --json

# Confirm all detected (ISSO action)
python tools/compliance/compliance_detector.py --project-id "proj-123" --confirm --json

# Assess all frameworks
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --json

# Multi-regime gate check
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --gate

# Prioritized minimal control list
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" \
    --minimal-controls --json
```

---

## ATO Acceleration

### Pipeline

```
FIPS 199/200 Categorization
    |
    v
FedRAMP Assessment (Moderate or High baseline)
    |
    v
CMMC Assessment (Level 2 or 3)
    |
    v
OSCAL Generation (machine-readable)
    |
    v
eMASS Sync (push/pull, hybrid mode)
    |
    v
cATO Monitoring (continuous evidence, freshness checks)
```

### Key Commands

```bash
# FedRAMP assessment
python tools/compliance/fedramp_assessor.py --project-id "proj-123" --baseline moderate
python tools/compliance/fedramp_report_generator.py --project-id "proj-123"

# CMMC assessment
python tools/compliance/cmmc_assessor.py --project-id "proj-123" --level 2
python tools/compliance/cmmc_report_generator.py --project-id "proj-123"

# OSCAL generation
python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact ssp

# eMASS sync
python tools/compliance/emass/emass_sync.py --project-id "proj-123" --mode hybrid
python tools/compliance/emass/emass_export.py --project-id "proj-123" --type controls

# cATO monitoring
python tools/compliance/cato_monitor.py --project-id "proj-123" --check-freshness
python tools/compliance/cato_scheduler.py --project-id "proj-123" --run-due

# PI compliance velocity
python tools/compliance/pi_compliance_tracker.py --project-id "proj-123" --velocity
```

### eMASS Integration

Two modes of operation:

| Mode | Description | Use Case |
|------|-------------|----------|
| **Push** | ICDEV writes controls/POAMs to eMASS | Automated ATO updates |
| **Pull** | ICDEV reads eMASS status into local DB | Status synchronization |
| **Hybrid** | Both push and pull | Full bidirectional sync |

### cATO (Continuous ATO)

cATO monitoring tracks evidence freshness and triggers re-assessment when evidence expires:

- Evidence records stored in `cato_evidence` table
- Freshness checks via configurable schedules
- ZTA posture score feeds as additional evidence dimension (D123)
- MOSA evidence optionally integrated (D130)

---

## Security Gates

All compliance-related gates are defined in `args/security_gates.yaml`. A gate failure blocks the corresponding workflow stage.

### Compliance Gates

| Gate | Blocking Conditions |
|------|-------------------|
| **FedRAMP** | 0 `other_than_satisfied` on high-priority controls; encryption must be FIPS 140-2 |
| **CMMC** | 0 `not_met` Level 2 practices; evidence current within 90 days |
| **cATO** | 0 expired evidence on critical controls; readiness >= 50% |
| **DES** | 0 `non_compliant` on critical DoDI 5000.87 Digital Engineering requirements |
| **FIPS 199** | Categorization required for ATO projects; IL6 must have CNSSI 1253; categorization approved |
| **FIPS 200** | 0 `not_satisfied` requirement areas; all 17 areas assessed; coverage >= 80% |
| **HIPAA** | 0 `not_satisfied` on Administrative/Technical Safeguards; encryption FIPS 140-2 for PHI |
| **PCI DSS** | 0 `not_satisfied` on Requirements 3-4 (data protection), 6 (secure dev), 10 (logging) |
| **CJIS** | 0 `not_satisfied` on Policy Areas 4 (audit), 5 (access control), 6 (identification), 10 (encryption) |
| **Multi-Regime** | All applicable frameworks must pass individual gates; 0 framework failures across all detected regimes |

### Security and Operations Gates

| Gate | Blocking Conditions |
|------|-------------------|
| **Code Review** | >= 1 approval, all comments resolved, SAST clean, no secrets, CUI markings present |
| **Merge** | All tests pass, >= 80% coverage, 0 CAT1 STIG, 0 critical vulns, SBOM current |
| **Deploy** | Staging tests pass, compliance artifacts current, change request approved, rollback plan exists |
| **DevSecOps** | 0 critical policy-as-code violations, 0 missing image attestations, 0 unresolved critical SAST, 0 detected secrets |
| **ZTA** | ZTA maturity >= Advanced (0.34) for IL4+, mTLS enforced when service mesh active, default-deny NetworkPolicy, no pillar at 0.0 |
| **MOSA** | 0 external interfaces without ICD, 0 circular dependencies, modularity score >= 0.6, 0 direct coupling violations |

### AI Security Gates

| Gate | Blocking Conditions |
|------|-------------------|
| **AI Security (ATLAS)** | Prompt injection defense active, AI telemetry enabled, AI BOM present, >= 80% ATLAS coverage, agent permissions configured |
| **OWASP Agentic** | Agent trust above untrusted threshold, 0 critical tool chain violations, 0 output classification leaks, no critical behavioral drift, MCP authorization configured |
| **Observability/XAI** | Tracing active, provenance graph non-empty, XAI assessment completed, no plaintext content tracing in CUI without approval |
| **Translation** | Syntax errors in output, API surface < 90%, compliance coverage < 95%, secrets detected, CUI markings missing |

### Special Gates

| Gate | Blocking Conditions |
|------|-------------------|
| **RICOAS** | Readiness score >= 0.7, 0 unresolved critical gaps, RED requirements must have alternative COAs |
| **Supply Chain** | 0 critical SCRM risks unmitigated, 0 expired ISAs with active data flows, 0 overdue critical CVE SLAs, 0 Section 889 prohibited vendors |
| **Migration** | ATO coverage >= 95% maintained during modernization, compliance bridge validated per PI |
| **Marketplace Publish** | 0 critical/high SAST, 0 secrets, 0 critical/high dep vulns, CUI markings, SBOM generated, digitally signed |
| **Marketplace Cross-Tenant** | All publish gate requirements + human ISSO/security officer review + code review confirmed |
| **Genome Propagation** | 72-hour stability window, capability evaluation score >= 0.65, HITL approval, compliance preservation verified |
| **Remote Command** | User binding required, signature verification, 300s replay window, rate limits, deploy commands blocked on all remote channels |
| **Claude Config Alignment** | Append-only table protected in pre_tool_use.py, hook syntax valid, hook reference exists |

---

## OSCAL Generation

OSCAL (Open Security Controls Assessment Language) produces machine-readable compliance artifacts for automated ATO processing.

```bash
# Generate OSCAL SSP
python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact ssp

# Generate OSCAL Assessment Results
python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact assessment

# Generate OSCAL POAM
python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact poam
```

---

## Per-Framework CLI Commands

### Core Compliance

```bash
# NIST 800-53 control lookup
python tools/compliance/nist_lookup.py --control "AC-2"

# Control mapping for an activity
python tools/compliance/control_mapper.py --activity "code.commit" --project-id "proj-123"

# SSP generation
python tools/compliance/ssp_generator.py --project-id "proj-123"

# POAM generation
python tools/compliance/poam_generator.py --project-id "proj-123"

# STIG checking
python tools/compliance/stig_checker.py --project-id "proj-123"

# SBOM generation
python tools/compliance/sbom_generator.py --project-dir "/path/to/project"
```

### DoD Compliance

```bash
# CSSP assessment (DI 8530.01)
python tools/compliance/cssp_assessor.py --project-id "proj-123" --functional-area all
python tools/compliance/cssp_report_generator.py --project-id "proj-123"
python tools/compliance/incident_response_plan.py --project-id "proj-123"

# Secure by Design (CISA)
python tools/compliance/sbd_assessor.py --project-id "proj-123" --domain all
python tools/compliance/sbd_report_generator.py --project-id "proj-123"

# IV&V (IEEE 1012)
python tools/compliance/ivv_assessor.py --project-id "proj-123" --process-area all
python tools/compliance/ivv_report_generator.py --project-id "proj-123"

# DES (DoDI 5000.87)
# Assessed via MBSE tools:
python tools/mbse/des_assessor.py --project-id "proj-123" --project-dir /path --json
python tools/mbse/des_report_generator.py --project-id "proj-123" --output-dir /path

# MOSA (10 U.S.C. 4401)
python tools/compliance/mosa_assessor.py --project-id "proj-123" --json
python tools/mosa/modular_design_analyzer.py --project-dir /path --project-id "proj-123" --store --json
python tools/mosa/mosa_code_enforcer.py --project-dir /path --fix-suggestions --json
python tools/mosa/icd_generator.py --project-id "proj-123" --all --json
python tools/mosa/tsp_generator.py --project-id "proj-123" --json
```

### Industry Compliance

```bash
# CJIS
python tools/compliance/cjis_assessor.py --project-id "proj-123" --json
python tools/compliance/cjis_assessor.py --project-id "proj-123" --gate

# HIPAA
python tools/compliance/hipaa_assessor.py --project-id "proj-123" --json
python tools/compliance/hipaa_assessor.py --project-id "proj-123" --gate

# HITRUST
python tools/compliance/hitrust_assessor.py --project-id "proj-123" --json

# SOC 2
python tools/compliance/soc2_assessor.py --project-id "proj-123" --json

# PCI DSS
python tools/compliance/pci_dss_assessor.py --project-id "proj-123" --json

# ISO 27001
python tools/compliance/iso27001_assessor.py --project-id "proj-123" --json
```

### AI Security Compliance

```bash
# MITRE ATLAS
python tools/compliance/atlas_assessor.py --project-id "proj-123" --json
python tools/compliance/atlas_report_generator.py --project-id "proj-123" --json

# OWASP LLM Top 10
python tools/compliance/owasp_llm_assessor.py --project-id "proj-123" --json

# NIST AI RMF
python tools/compliance/nist_ai_rmf_assessor.py --project-id "proj-123" --json

# ISO 42001
python tools/compliance/iso42001_assessor.py --project-id "proj-123" --json

# OWASP Agentic AI
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --json
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --gate

# XAI (Observability)
python tools/compliance/xai_assessor.py --project-id "proj-123" --json
python tools/compliance/xai_assessor.py --project-id "proj-123" --gate
```

### Zero Trust and DevSecOps

```bash
# ZTA maturity (7 pillars)
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --all --json

# NIST 800-207
python tools/compliance/nist_800_207_assessor.py --project-id "proj-123" --json
python tools/compliance/nist_800_207_assessor.py --project-id "proj-123" --gate

# DevSecOps maturity
python tools/devsecops/profile_manager.py --project-id "proj-123" --assess --json
```

### Xacta 360 Integration

```bash
# Sync with Xacta
python tools/compliance/xacta/xacta_sync.py --project-id "proj-123" --mode hybrid

# Export for Xacta
python tools/compliance/xacta/xacta_export.py --project-id "proj-123" --format oscal
```

---

## Configuration Files

### Framework Registry (args/framework_registry.yaml)

Central registry of all 26 frameworks with metadata:

```yaml
frameworks:
  nist_800_53:
    version: "Rev 5"
    hub: us
    catalog: "nist_800_53.json"
    active: true

  fedramp_moderate:
    version: "Rev 5 Moderate"
    hub: us
    parent: nist_800_53
    catalog: "fedramp_moderate_baseline.json"
    active: true

  iso27001:
    version: "2022"
    hub: international
    catalog: "iso27001_2022_controls.json"
    active: true
```

### Security Gates (args/security_gates.yaml)

All gate thresholds and blocking conditions:

```yaml
gates:
  fedramp:
    blocking:
      - other_than_satisfied_high_priority
      - encryption_not_fips_140_2
    thresholds:
      min_control_coverage_pct: 100

  cmmc:
    blocking:
      - not_met_level_2_practice
      - evidence_older_than_90_days

  atlas_ai:
    blocking:
      - critical_atlas_technique_unmitigated
      - prompt_injection_defense_inactive
      - ai_telemetry_not_active
      - agent_permissions_not_configured
      - ai_bom_missing
    thresholds:
      min_atlas_coverage_pct: 80
      ai_telemetry_required: true
```

### Classification Config (args/classification_config.yaml)

```yaml
data_categories:
  - id: CUI
    label: "Controlled Unclassified Information"
    handling_standard: "NIST 800-171, 32 CFR Part 2002"
    sensitivity_order: 5
  - id: PHI
    label: "Protected Health Information"
    handling_standard: "HIPAA Security Rule"
    sensitivity_order: 4
  # ... 8 more categories

composite_rules:
  banner_separator: " // "
  code_header_format: "# {banner}"
  highest_sensitivity_determines_handling: true
```

---

## Compliance Diagram Validation

Vision-based validation of architecture and network diagrams for compliance:

```bash
# Validate network zone diagram
python tools/compliance/diagram_validator.py --image network.png \
    --type network_zone --project-id "proj-123" --json

# Validate ATO boundary diagram
python tools/compliance/diagram_validator.py --image ato_boundary.png \
    --type ato_boundary --expected-components "Web,App,DB" --json

# Validate data flow markings
python tools/compliance/diagram_validator.py --image dataflow.png \
    --type data_flow --classification CUI --json
```

---

## Key Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| D5 | CUI markings applied at generation time (inline, not post-processing) |
| D109 | Composable data markings -- single artifact can carry CUI + PHI + PCI simultaneously |
| D110 | Compliance auto-detection is advisory only -- ISSO must confirm before gates enforce |
| D111 | Dual-hub crosswalk -- NIST 800-53 as US hub, ISO 27001 as international hub |
| D112 | Framework catalogs versioned independently -- update one without touching others |
| D113 | Multi-regime deduplication via crosswalk -- N frameworks produce 1 unified control set |
| D114 | Compliance framework as marketplace asset type -- community-contributed catalogs |
| D115 | Data type to framework mapping is declarative JSON -- no code changes for new rules |
| D116 | BaseAssessor ABC pattern -- ~60 LOC per new framework vs ~400+ without |
| D118 | NIST 800-207 maps into existing NIST 800-53 US hub (not a third hub) |
| D127 | MOSA implemented as full compliance framework via BaseAssessor pattern |
| D218 | ATLAS assessor maps MITRE ATLAS mitigations to automated checks via BaseAssessor |
| D220 | OWASP LLM Top 10 crosswalks through ATLAS to NIST 800-53 US hub |
| D222 | ISO 42001 bridges through ISO 27001 international hub for crosswalk |
| D289 | XAI assessor via BaseAssessor -- crosswalk to NIST 800-53 cascades to FedRAMP/CMMC |
