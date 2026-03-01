# Phase 57 — EU AI Act Risk Classification & Iron Bank Hardening

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 57 |
| Title | EU AI Act Risk Classification & Iron Bank Hardening |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 48 (AI Transparency), Phase 49 (AI Accountability), Phase 17 (Multi-Framework Compliance) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-26 |

---

## 1. Problem Statement

ICDEV supports 30+ compliance frameworks but lacks two capabilities critical for organizations operating in the European Union market or deploying to DoD Platform One infrastructure:

**EU AI Act (Regulation 2024/1689):** The European Union's comprehensive AI regulation entered force in 2024, imposing mandatory risk classification and compliance requirements on all AI systems placed on the EU market. Organizations building AI-enabled systems for dual-use (US Government + EU allies/NATO partners) need automated risk classification, Annex III high-risk requirement assessment, and crosswalk integration with existing NIST 800-53 controls. Without this, teams manually map EU requirements to their existing compliance posture -- an error-prone and time-consuming process.

**Platform One / Iron Bank:** DoD's centralized repository of hardened container images (Iron Bank) requires specific metadata artifacts for container approval submissions. Teams building ICDEV-managed applications for Platform One deployment must produce `hardening_manifest.yaml` files conforming to Iron Bank's schema, with correct base image references from `registry1.dso.mil`, OCI labels, vulnerability scan references, and classification markings. Without automated generation, teams hand-craft these manifests and frequently fail validation.

Phase 57 closes both gaps with two independent but complementary components that integrate into ICDEV's existing compliance and infrastructure pipelines.

---

## 2. Goals

1. Classify AI systems into 4 EU AI Act risk levels: Unacceptable, High-Risk, Limited Risk, Minimal Risk
2. Assess compliance against 12 Annex III high-risk requirements (Articles 9-15) using existing ICDEV evidence
3. Bridge EU AI Act requirements through the ISO 27001 international hub to the NIST 800-53 US hub via the dual-hub crosswalk (D111)
4. Integrate as a BaseAssessor subclass with full gate evaluation, CLI, and crosswalk support (D116)
5. Generate Platform One Iron Bank hardening manifests (`hardening_manifest.yaml`) with OCI-compliant labels
6. Auto-detect project language and select the correct Iron Bank base image from `registry1.dso.mil`
7. Produce container approval records and validate manifests against Iron Bank required fields
8. Support all 6 ICDEV first-class languages (Python, Java, Go, Node/TypeScript, Rust, C#/.NET) plus a UBI9 base fallback

---

## 3. Architecture

```
                    Phase 57 Architecture
    ┌───────────────────────────────────────────────┐
    │              EU AI Act Classifier              │
    │         (BaseAssessor subclass, D349)          │
    │                                                │
    │  12 Annex III Requirements (EUAI-01..EUAI-12)  │
    │  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
    │  │AI Invent.│  │Model/Sys │  │Oversight │    │
    │  │  Cards   │  │  Cards   │  │  Plans   │    │
    │  └────┬─────┘  └────┬─────┘  └────┬─────┘    │
    │       └──────────┬───┘             │          │
    │                  ↓                 ↓          │
    │        ┌─────────────────────────────┐        │
    │        │  Existing ICDEV DB Tables   │        │
    │        │  (Phase 48/49 evidence)     │        │
    │        └────────────┬────────────────┘        │
    │                     ↓                         │
    │        eu_ai_act_assessments table             │
    └────────────┬──────────────────────────────────┘
                 │
        ┌────────┼────────────────────┐
        ↓        ↓                    ↓
   ISO 27001   Crosswalk           Dashboard
   Bridge      Engine              /api endpoints
   (D111)      (dual-hub)          + gate eval
                 │
                 ↓
            NIST 800-53
            US Hub
    ┌───────────────────────────────────────────────┐
    │         Iron Bank Metadata Generator           │
    │         (terraform_generator pattern, D350)    │
    │                                                │
    │  ┌─────────────┐  ┌──────────────────────┐    │
    │  │  Language    │  │  IRONBANK_BASE_IMAGES │   │
    │  │  Detection   │  │  (7 base images)     │   │
    │  │  (6 langs)  │  │  registry1.dso.mil   │    │
    │  └──────┬──────┘  └──────────┬───────────┘    │
    │         └────────┬───────────┘                 │
    │                  ↓                             │
    │   hardening_manifest.yaml                      │
    │   container_approval.json                      │
    │   (OCI labels, CUI markings, digest pins)      │
    └───────────────────────────────────────────────┘
```

### Key Design Principles

- **BaseAssessor pattern** -- EU AI Act classifier inherits gate evaluation, CLI, and crosswalk from BaseAssessor ABC (D116, D349)
- **International hub bridge** -- EU regulation crosswalks through ISO 27001 to NIST 800-53, not directly (D111)
- **Optional activation** -- EU AI Act assessment triggers only when `eu_market: true` in project configuration (D349)
- **Evidence reuse** -- All 12 checks query existing Phase 48/49 DB tables; no new data collection required (D313 pattern)
- **terraform_generator pattern** -- Iron Bank generator follows the same dispatching and output structure as existing IaC generators (D350)
- **Air-gap safe** -- Both components use stdlib only, zero external dependencies

---

## 4. Implementation

### Component 1: EU AI Act Risk Classifier (`tools/compliance/eu_ai_act_classifier.py`)

**Class `EUAIActClassifier(BaseAssessor)`** with 12 automated checks mapping EU requirements to existing ICDEV evidence:

| Requirement | ID | Evidence Source | Check Logic |
|-------------|-----|----------------|-------------|
| Risk Classification | EUAI-01 | `ai_use_case_inventory` | AI inventory registered with risk level |
| Data Governance | EUAI-02 | `model_cards` | Model cards exist with training data documentation |
| Technical Documentation | EUAI-03 | `model_cards` + `system_cards` | Both model and system cards present |
| Record-Keeping | EUAI-04 | `audit_trail` + `ai_telemetry` | Audit trail and AI telemetry active |
| Transparency | EUAI-05 | `omb_m25_21_assessments`, `omb_m26_04_assessments`, `gao_ai_assessments`, `nist_ai_600_1_assessments` | At least one transparency assessment completed |
| Human Oversight | EUAI-06 | `ai_oversight_plans` + `ai_caio_registry` | Oversight plan and CAIO designated |
| Accuracy/Robustness | EUAI-07 | `stig_results` | SAST and security scanning evidence present |
| Risk Management | EUAI-08 | `nist_ai_rmf_assessments` | NIST AI RMF assessment completed |
| Conformity Assessment | EUAI-09 | `production_audits` | Production readiness audit completed |
| Post-Market Monitoring | EUAI-10 | `cato_evidence` | cATO evidence or heartbeat monitoring active |
| Incident Reporting | EUAI-11 | `ai_incident_log` | AI incident logging configured |
| Fundamental Rights Impact | EUAI-12 | `ai_ethics_reviews` | Ethics review / impact assessment completed |

**Risk Level Classification (4 tiers):**

| Level | Description | ICDEV Treatment |
|-------|-------------|-----------------|
| Unacceptable | Banned practices (social scoring, real-time biometric ID) | Blocked -- system cannot proceed |
| High-Risk | Annex III areas (biometrics, critical infra, law enforcement, etc.) | Full 12-requirement compliance required |
| Limited Risk | Transparency obligations (chatbots, deepfakes) | Transparency checks only (EUAI-05) |
| Minimal Risk | No specific requirements (spam filters, games) | Logged, no enforcement |

**8 Annex III High-Risk Categories:**

| ID | Category |
|----|----------|
| AX3-1 | Biometric identification and categorisation of natural persons |
| AX3-2 | Management and operation of critical infrastructure |
| AX3-3 | Education and vocational training |
| AX3-4 | Employment, workers management and access to self-employment |
| AX3-5 | Access to essential private and public services |
| AX3-6 | Law enforcement |
| AX3-7 | Migration, asylum and border control management |
| AX3-8 | Administration of justice and democratic processes |

**ISO 27001 Bridge Crosswalk:**

Each EUAI requirement maps to NIST 800-53 controls, which cascade through the dual-hub crosswalk:

| EUAI | NIST 800-53 Crosswalk |
|------|-----------------------|
| EUAI-01 | RA-2, RA-3 |
| EUAI-02 | SA-3, SI-12 |
| EUAI-03 | SA-5, PL-2 |
| EUAI-04 | AU-2, AU-3, AU-6 |
| EUAI-05 | PL-4, AT-2 |
| EUAI-06 | CA-7, SI-4 |
| EUAI-07 | SA-11, SI-2, SC-7 |
| EUAI-08 | RA-1, RA-2, PM-9 |
| EUAI-09 | CA-2, CA-6 |
| EUAI-10 | CA-7, SI-4, PM-14 |
| EUAI-11 | IR-6, SI-5 |
| EUAI-12 | RA-5, PM-9 |

### Component 2: Iron Bank Metadata Generator (`tools/infra/ironbank_metadata_generator.py`)

**3 output artifacts:**
1. `hardening_manifest.yaml` -- Iron Bank v1 hardening manifest with OCI labels, base image pin, CUI markings
2. `container_approval.json` -- Approval tracking record with Iron Bank URL, P1 registry path, submission status
3. Human-readable output via CLI

**Language Auto-Detection:**

Scans project directory for language indicators and selects the corresponding Iron Bank base image:

| Language | Detection Signal | Iron Bank Base Image | Tag |
|----------|-----------------|---------------------|-----|
| Python | `requirements.txt` or `*.py` | `registry1.dso.mil/ironbank/opensource/python` | 3.11 |
| Java | `pom.xml` or `build.gradle` | `registry1.dso.mil/ironbank/redhat/openjdk` | 17 |
| Go | `go.mod` | `registry1.dso.mil/ironbank/opensource/go` | 1.22 |
| Node/TS | `package.json` | `registry1.dso.mil/ironbank/opensource/nodejs/nodejs` | 18 |
| Rust | `Cargo.toml` | `registry1.dso.mil/ironbank/opensource/rust` | 1.75 |
| C#/.NET | `*.csproj` | `registry1.dso.mil/ironbank/microsoft/dotnet/dotnet-aspnet` | 8.0 |
| (fallback) | None matched | `registry1.dso.mil/ironbank/redhat/ubi/ubi9` | latest |

All base images use Red Hat UBI9 as the underlying OS.

**Manifest Validation:**

`validate_hardening_manifest()` checks:
- All required fields present (`apiVersion`, `name`, `tags`, `labels`, `base_image`, `image_author`)
- DoD-specific labels (`mil.dod.impact.level`, `mil.dod.classification`)
- Base image references `registry1.dso.mil` (Iron Bank registry)
- Digest pin status (warns on placeholder digests)
- CUI marking present in manifest

---

## 5. Database

### `eu_ai_act_assessments` (append-only)

Assessment results for EU AI Act compliance. Standard BaseAssessor schema:

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Assessment UUID |
| project_id | TEXT | Project reference |
| assessment_date | TEXT | ISO 8601 timestamp |
| results_json | TEXT | Per-requirement status JSON |
| total_controls | INTEGER | Total requirements assessed (12) |
| satisfied_count | INTEGER | Requirements fully satisfied |
| not_satisfied_count | INTEGER | Requirements not satisfied |
| coverage_pct | REAL | Satisfaction percentage |
| assessor_version | TEXT | Classifier version |
| created_at | TEXT | Row creation timestamp |

**Note:** Iron Bank metadata generation does not require its own database table -- artifacts are written to the filesystem and tracked via the existing `audit_trail` table.

---

## 6. Configuration

### EU AI Act Catalog (`context/compliance/eu_ai_act_annex_iii.json`)

Declarative JSON catalog containing:
- 12 requirements (EUAI-01 through EUAI-12) with NIST 800-53 crosswalk references
- 8 Annex III high-risk categories (AX3-1 through AX3-8)
- 4 risk levels with descriptions

### Framework Registry (`args/framework_registry.yaml`)

EU AI Act registered as an active framework with:
- Hub: International (ISO 27001 bridge)
- Trigger: `eu_market: true` in project configuration
- Crosswalk: Through `iso27001_nist_bridge.json` to NIST 800-53 US hub

### Iron Bank Base Images

Hardcoded in `IRONBANK_BASE_IMAGES` dict within `ironbank_metadata_generator.py`. Registry references point to `registry1.dso.mil` (DoD Software Factory).

---

## 7. Dashboard

EU AI Act assessment results are visible through existing compliance dashboard pages:

- `/ai-transparency` -- EU AI Act coverage included in cross-framework audit view
- `/proposals/<id>` -- Compliance matrix tab shows EU AI Act status when `eu_market: true`
- Standard BaseAssessor CLI output with `--json` and `--human` flags

Iron Bank manifest generation is a CLI-only operation (not dashboard-exposed) -- artifacts are generated for Platform One submission pipelines.

---

## 8. Security Gates

No new standalone security gate is defined for Phase 57. EU AI Act enforcement operates through existing gates:

- **`ai_transparency` gate** -- Covers `high_impact_ai_not_classified` and `model_cards_missing_for_deployed_models`, which overlap with EUAI-01 and EUAI-03
- **`ai_governance` gate** -- Covers `caio_not_designated_for_rights_impacting_ai` and `oversight_plan_missing_for_high_impact_ai`, which overlap with EUAI-06
- **`ai_accountability` gate** -- Covers `impact_assessment_not_completed`, which overlaps with EUAI-12

Iron Bank manifest validation is a pre-submission check (`--validate` flag) rather than a pipeline gate -- it runs before container submission to Platform One, not during standard CI/CD.

---

## 9. Verification

```bash
# EU AI Act Classifier
python tools/compliance/eu_ai_act_classifier.py --project-id "proj-123" --json     # Full assessment
python tools/compliance/eu_ai_act_classifier.py --project-id "proj-123" --gate      # Gate evaluation

# Iron Bank Metadata Generator
python tools/infra/ironbank_metadata_generator.py --project-id "proj-123" --generate --json
python tools/infra/ironbank_metadata_generator.py --project-id "proj-123" --generate --output-dir .tmp/ironbank
python tools/infra/ironbank_metadata_generator.py --project-id "proj-123" --validate --manifest-path .tmp/ironbank/hardening_manifest.yaml
python tools/infra/ironbank_metadata_generator.py --list-base-images --json

# Tests
pytest tests/test_eu_ai_act_classifier.py -v     # 20 tests
pytest tests/test_ironbank_generator.py -v        # 20 tests
```

**Total: 40 tests covering all Phase 57 functionality.**

---

## 10. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D349 | EU AI Act classifier uses BaseAssessor ABC; bridges through ISO 27001 international hub (D111); optional -- triggered only when `eu_market: true` | EU regulation is international; dual-hub model (D111) routes through ISO 27001 to NIST 800-53. BaseAssessor (D116) provides gate/CLI/crosswalk with ~200 LOC. Optional trigger avoids overhead for purely domestic projects. |
| D350 | Iron Bank metadata generator follows `terraform_generator.py` pattern; produces `hardening_manifest.yaml` for Platform One Big Bang; language auto-detection from project directory | Consistent with existing IaC generation patterns. Language detection reuses the same file-presence heuristics as `language_support.py`. Stdlib-only, air-gap safe. |

---

## 11. Files

### New Files (4)
| File | LOC | Purpose |
|------|-----|---------|
| `tools/compliance/eu_ai_act_classifier.py` | ~193 | EU AI Act BaseAssessor subclass |
| `tools/infra/ironbank_metadata_generator.py` | ~410 | Iron Bank hardening manifest generator |
| `context/compliance/eu_ai_act_annex_iii.json` | ~108 | EU AI Act requirements catalog |
| `tests/test_eu_ai_act_classifier.py` | ~355 | 20 EU AI Act classifier tests |

### New Files (continued)
| File | LOC | Purpose |
|------|-----|---------|
| `tests/test_ironbank_generator.py` | ~182 | 20 Iron Bank generator tests |

### Modified Files
| File | Change |
|------|--------|
| `tools/db/init_icdev_db.py` | +1 CREATE TABLE (`eu_ai_act_assessments`) |
| `CLAUDE.md` | +D349, D350, +commands, +table references |
| `tools/manifest.md` | +Phase 57 tool entries |
| `goals/manifest.md` | +EU AI Act entry |
