# Phase 53 — FedRAMP 20x KSI Evidence & OWASP ASI Assessment

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 53 |
| Title | FedRAMP 20x KSI Evidence & OWASP ASI Assessment |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 47 (Unified MCP Gateway), Phase 45 (OWASP Agentic Security), Phase 48 (AI Transparency), Phase 49 (AI Accountability) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-25 |

---

## 1. Problem Statement

FedRAMP 20x replaces static documentation-heavy authorization packages with continuous, machine-readable Key Security Indicators (KSIs). Cloud service providers must demonstrate real-time compliance evidence across 43 KSIs organized by NIST 800-53 control families. Existing ICDEV compliance tools generate individual artifacts (SSP, POAM, SBOM, STIG reports) but there is no mechanism to map this evidence to FedRAMP 20x KSI schemas, assess maturity levels, or bundle it into an authorization package.

Separately, agentic AI systems face a distinct threat taxonomy identified by the OWASP Top 10 for Agentic AI (ASI01-ASI10). ICDEV already implements controls for these risks across multiple phases (prompt injection detection, tool chain validation, trust scoring, behavioral drift), but there is no unified assessor that evaluates all 10 ASI risks and generates a compliance posture report.

Without Phase 53:
- No automated mapping from ICDEV evidence to FedRAMP 20x KSI definitions
- No maturity level assessment (none/basic/intermediate/advanced) per KSI
- No authorization package bundling (OSCAL SSP + KSI evidence + supporting artifacts)
- No unified OWASP ASI01-ASI10 risk assessment leveraging existing ICDEV controls
- No dashboard visibility into FedRAMP 20x KSI coverage or agentic AI risk posture

---

## 2. Goals

1. Define 43 KSIs across 11 NIST 800-53 control families (AC, AU, CA, CM, IA, IR, RA, SA, SC, SI, SR) plus an AI-specific family with 6 KSIs
2. Map each KSI to ICDEV evidence sources (DB tables, config files, tool outputs) with automated collection
3. Determine maturity level per KSI: `none` (0% evidence), `basic` (>0%), `intermediate` (>=50%), `advanced` (>=80%)
4. Generate machine-readable KSI evidence artifacts following the `cssp_evidence_collector.py` pattern
5. Bundle OSCAL SSP, KSI evidence, SBOM, POAM, AI-BOM, and OWASP ASI assessment into an authorization package
6. Implement OWASP ASI01-ASI10 assessor via BaseAssessor ABC with 10 automated checks mapping to existing ICDEV controls
7. Provide `/fedramp-20x` dashboard page with KSI status grid, evidence table, and generate/package actions
8. Store OWASP ASI assessment results in `owasp_asi_assessments` table for trend tracking and crosswalk integration

---

## 3. Architecture

```
                FedRAMP 20x + OWASP ASI Pipeline
     ┌──────────────────────────────────────────────────┐
     │          fedramp_20x_ksi_schemas.json             │
     │   (43 KSIs, 11 NIST families + AI-specific)      │
     └───────────────────┬──────────────────────────────┘
                         │
          ┌──────────────┼──────────────────┐
          ↓              ↓                   ↓
   KSI Generator    OWASP ASI Assessor   OSCAL Generator
   (fedramp_ksi_    (owasp_asi_          (oscal_generator)
    generator.py)    assessor.py)
          │              │                   │
          ↓              ↓                   ↓
   Evidence Sources  owasp_asi_          OSCAL SSP
   (70+ collectors)  assessments         artifact
   ┌────────────┐    table
   │ DB tables  │         │                  │
   │ Config YAML│         │                  │
   │ Tool files │    ┌────┘                  │
   └────────────┘    │                       │
          │          │                       │
          └──────────┼───────────────────────┘
                     ↓
           Authorization Packager
           (fedramp_authorization_packager.py)
                     │
                     ↓
           Authorization Package
           (KSI bundle + OSCAL SSP +
            SBOM + POAM + AI-BOM +
            OWASP ASI + Prod Audit)
                     │
                     ↓
           Dashboard API + Page
           /api/fedramp-20x/*
           /fedramp-20x
```

### Key Design Principles

- **Evidence-based, not assessment-based** -- KSI generator maps existing ICDEV evidence to KSI schemas; it does NOT create a new compliance assessment framework (D338)
- **BaseAssessor for OWASP ASI** -- ASI01-ASI10 is a proper risk assessment with satisfied/not_satisfied checks, so it uses BaseAssessor ABC (D339)
- **Bundle extends OSCAL** -- Authorization packager bundles KSI evidence alongside existing OSCAL SSP, not replacing it (D340)
- **Air-gap safe** -- All evidence collection uses stdlib file checks and SQLite queries; no external API calls

---

## 4. Implementation

### Component 1: FedRAMP 20x KSI Generator (`tools/compliance/fedramp_ksi_generator.py`)

Generates machine-readable KSI evidence artifacts for FedRAMP 20x continuous authorization. Maps ICDEV evidence (DB records, configs, scan results) to 43 KSI definitions organized by NIST 800-53 families. Follows the `cssp_evidence_collector.py` pattern (D338).

**70+ Evidence Collectors** organized by source type:

| Source Type | Examples |
|-------------|----------|
| DB table counts | `audit_trail`, `hook_events`, `prompt_injection_log`, `ai_telemetry`, `agent_trust_scores`, `tool_chain_events`, `ai_bom`, `model_cards`, `system_cards`, `fairness_assessments`, etc. |
| Config file checks | `args/security_gates.yaml`, `args/owasp_agentic_config.yaml`, `args/agent_config.yaml`, `args/cloud_config.yaml`, `args/resilience_config.yaml`, etc. |
| Tool file existence | `tools/security/mcp_tool_authorizer.py`, `tools/dashboard/auth.py`, `tools/compliance/cato_monitor.py`, `tools/devsecops/attestation_manager.py`, etc. |
| Keyword search in config | HMAC in `observability_config.yaml`, TLS/mTLS in `agent_config.yaml`, drift in `owasp_agentic_config.yaml`, etc. |

**Maturity Determination Algorithm:**

```python
sources = ksi["evidence_sources"]       # list of evidence source keys
available = count(s for s in sources if evidence_counts[s] > 0)
ratio = available / len(sources)

if ratio >= 0.8:  return "advanced"
if ratio >= 0.5:  return "intermediate"
if ratio > 0:     return "basic"
return "none"
```

**CLI:**
```bash
python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --all --json
python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --ksi-id KSI-AC-01 --json
python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --summary --json
python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --all --human
```

### Component 2: OWASP ASI01-ASI10 Assessor (`tools/compliance/owasp_asi_assessor.py`)

BaseAssessor subclass with 10 automated checks mapping each ASI risk to existing ICDEV controls:

| ASI Risk | Title | ICDEV Control Mapping | Check Method |
|----------|-------|----------------------|--------------|
| ASI-01 | Agentic Goal Hijacking | `prompt_injection_detector` | `prompt_injection_log` records exist |
| ASI-02 | Tool and Function Abuse | `mcp_tool_authorizer`, `tool_chain_validator` | `tool_chain_events` records exist |
| ASI-03 | Identity and Access Abuse | RBAC, `agent_trust_scorer` | `agent_trust_scores` records exist |
| ASI-04 | Supply Chain Risks | AI BOM, marketplace scanning | `ai_bom` records exist |
| ASI-05 | Unsafe Code Execution | `dangerous_pattern_detector` | `args/code_pattern_config.yaml` exists |
| ASI-06 | Memory and Context Manipulation | Behavioral drift, memory consolidation | `ai_telemetry` or `memory_consolidation_log` records |
| ASI-07 | Communication Compromise | A2A mTLS, HMAC signing | TLS/mTLS/HMAC in `args/agent_config.yaml` |
| ASI-08 | Cascading Failures | Circuit breaker, retry config | `circuit_breaker`/`retry` in `args/resilience_config.yaml` |
| ASI-09 | Human Oversight Gaps | Audit trail, HITL gates | `audit_trail` records exist |
| ASI-10 | Rogue Agent Behavior | Agent trust scoring, behavioral red team | `agent_trust_scores` or `atlas_red_team_results` records |

**NIST 800-53 Crosswalk** -- Each ASI risk maps to NIST controls via the catalog (`owasp_agentic_asi.json`), which cascades through the dual-hub crosswalk engine to FedRAMP, CMMC, and other frameworks.

**CLI:**
```bash
python tools/compliance/owasp_asi_assessor.py --project-id proj-123 --json
python tools/compliance/owasp_asi_assessor.py --project-id proj-123 --gate
python tools/compliance/owasp_asi_assessor.py --project-id proj-123 --human
```

### Component 3: Authorization Packager (`tools/compliance/fedramp_authorization_packager.py`)

Bundles all required artifacts into a FedRAMP 20x authorization package. Extends `oscal_generator.py` (D340). Checks readiness across 7 artifact categories:

| Artifact | Source Tool | Required |
|----------|------------|----------|
| KSI Evidence Bundle | `fedramp_ksi_generator.py` | Yes |
| OSCAL SSP | `oscal_generator.py` | Yes |
| SBOM | `sbom_generator.py` | Yes |
| POAM | `poam_generator.py` | Yes |
| AI-BOM | `ai_bom_generator.py` | For AI systems |
| Production Audit | `production_audit.py` | Recommended |
| OWASP ASI Assessment | `owasp_asi_assessor.py` | For agentic systems |

**Readiness Calculation:**
```
readiness_pct = (ready_artifacts / total_artifacts) * 100
```

**CLI:**
```bash
python tools/compliance/fedramp_authorization_packager.py --project-id proj-123 --json
python tools/compliance/fedramp_authorization_packager.py --project-id proj-123 --output-dir /path --json
```

### Component 4: KSI Schema Catalog (`context/compliance/fedramp_20x_ksi_schemas.json`)

Machine-readable JSON catalog defining 43 KSIs across 11 NIST 800-53 families plus an AI-specific family:

| Family | KSI Count | Example KSIs |
|--------|-----------|--------------|
| AC (Access Control) | 5 | Least Privilege, Account Mgmt, Session Mgmt, Remote Access, Info Flow |
| AU (Audit) | 4 | Audit Generation, Review, Storage Protection, Non-Repudiation |
| CA (Assessment) | 3 | Security Assessment, Continuous Monitoring, POAM |
| CM (Configuration) | 4 | Baseline Config, Change Control, Least Functionality, Software Inventory |
| IA (Identification) | 3 | User ID, Device/Service ID, Credential Mgmt |
| IR (Incident Response) | 2 | IR Plan, Incident Detection |
| RA (Risk Assessment) | 2 | Risk Assessment, Vulnerability Scanning |
| SA (System Acquisition) | 4 | Security Engineering, Developer Testing, Supply Chain, Dev Process |
| SC (System/Comms) | 4 | Boundary Protection, Transmission, Crypto, DoS Protection |
| SI (System Integrity) | 4 | Flaw Remediation, Malicious Code, Input Validation, Software Integrity |
| SR (Supply Chain) | 2 | Supply Chain Controls, Component Authenticity |
| AI (AI-Specific) | 6 | AI Inventory, Transparency, Risk Mgmt, Accountability, Fairness, Agentic Security |

Each KSI includes:
- `ksi_id` -- unique identifier (e.g., `KSI-AC-01`)
- `title` -- human-readable name
- `nist_controls` -- mapped NIST 800-53 control IDs
- `evidence_sources` -- list of ICDEV evidence collector keys
- `maturity_levels` -- descriptions for basic/intermediate/advanced

### Component 5: OWASP ASI Risk Catalog (`context/compliance/owasp_agentic_asi.json`)

JSON catalog defining 10 ASI risks with risk levels, evidence requirements, NIST 800-53 crosswalk mappings, ICDEV control references, and automated check descriptions. Risk levels range from `critical` (ASI-01 through ASI-05) to `high` (ASI-06 through ASI-10).

---

## 5. Database

### `owasp_asi_assessments`

Stores OWASP ASI assessment results. Standard BaseAssessor table schema.

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID |
| `project_id` | TEXT | Project identifier |
| `assessment_date` | TEXT | ISO timestamp |
| `results_json` | TEXT | Full assessment results as JSON |
| `total_controls` | INTEGER | Total ASI risks assessed (10) |
| `satisfied_count` | INTEGER | Risks with controls verified |
| `not_satisfied_count` | INTEGER | Risks without evidence |
| `coverage_pct` | REAL | Percentage satisfied |
| `assessor_version` | TEXT | Assessor version string |
| `created_at` | TEXT | Record creation timestamp |

Indexed on `project_id`. Append-only (D6 pattern).

---

## 6. Configuration

### KSI Schema: `context/compliance/fedramp_20x_ksi_schemas.json`

Declarative JSON catalog of all 43 KSIs. Add new KSIs without code changes -- the generator dynamically reads this file. Each KSI family is a JSON object with `family_id`, `family_name`, and `ksis` array.

### ASI Risk Catalog: `context/compliance/owasp_agentic_asi.json`

Declarative JSON catalog of 10 ASI risks following BaseAssessor `requirements` schema. Each risk entry includes `id`, `title`, `family`, `description`, `risk_level`, `evidence_required`, `priority`, `nist_800_53_crosswalk`, `icdev_controls`, and `automated_check`.

### Security Gates: `args/security_gates.yaml`

No new standalone gate was added for FedRAMP 20x (KSIs are evidence, not gate checks). The OWASP ASI assessor integrates with the existing BaseAssessor `--gate` flag, which blocks on coverage thresholds defined in the assessor class.

---

## 7. Dashboard

### Page: `/fedramp-20x`

FedRAMP 20x KSI Dashboard providing real-time visibility into KSI evidence coverage and authorization package readiness.

**UI Components:**
- **6-metric stat grid** -- Total KSIs, Coverage %, Advanced count, Intermediate count, Basic count, No Evidence count
- **Project ID selector** with Generate Evidence and Package buttons
- **KSI evidence table** -- Sortable table showing KSI ID, title, NIST control family, maturity level, evidence source status
- **Maturity level color coding** -- Green (advanced), blue (intermediate), orange (basic), red (none)

**API Endpoints** (`tools/dashboard/api/fedramp_20x.py`):

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/fedramp-20x/stats` | GET | KSI summary statistics with maturity distribution |
| `/api/fedramp-20x/ksis` | GET | All KSIs with maturity levels and evidence details |
| `/api/fedramp-20x/ksi/<ksi_id>` | GET | Single KSI detail with per-source evidence status |
| `/api/fedramp-20x/package` | GET | Authorization package readiness and artifact inventory |

---

## 8. Security Gates

The OWASP ASI assessor inherits the standard BaseAssessor gate evaluation pattern. When invoked with `--gate`, it returns exit code 0 (pass) or 1 (fail) based on coverage thresholds.

FedRAMP 20x KSIs feed into the existing FedRAMP gate as evidence artifacts -- they do not define a separate blocking gate. KSI coverage is reported as a readiness metric in the authorization package.

The `owasp_asi_assessments` table results also feed into the KSI-RA-01 (Risk Assessment) and KSI-AI-03 (AI Risk Management) KSIs as evidence sources, creating a reinforcing loop between the two Phase 53 components.

---

## 9. Verification

```bash
# KSI Generator tests (15 tests)
pytest tests/test_fedramp_ksi_generator.py -v

# OWASP ASI Assessor tests (18 tests)
pytest tests/test_owasp_asi_assessor.py -v
```

**Total: 33 tests covering all Phase 53 functionality.**

### Manual Verification

```bash
# Generate all KSI evidence
python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --all --json

# Generate KSI summary
python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --summary --json

# Run OWASP ASI assessment
python tools/compliance/owasp_asi_assessor.py --project-id proj-123 --json

# Check OWASP ASI gate
python tools/compliance/owasp_asi_assessor.py --project-id proj-123 --gate

# Generate authorization package
python tools/compliance/fedramp_authorization_packager.py --project-id proj-123 --json

# Start dashboard and navigate to /fedramp-20x
python tools/dashboard/app.py
```

---

## 10. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D338 | KSI generator follows `cssp_evidence_collector.py` pattern, not BaseAssessor | KSIs are evidence artifacts, not assessment checks. BaseAssessor expects satisfied/not_satisfied evaluations; KSIs report maturity levels (none/basic/intermediate/advanced) based on evidence availability. |
| D339 | OWASP ASI assessor uses BaseAssessor ABC | ASI01-ASI10 is a proper risk assessment with binary check outcomes (controls present or absent). BaseAssessor gives automatic CLI, gate evaluation, crosswalk integration, and DB storage with approximately 200 LOC. |
| D340 | Authorization packager extends `oscal_generator.py` | FedRAMP 20x packages require OSCAL SSP as the foundation plus KSI evidence as supplemental continuous authorization artifacts. Bundling both in one package aligns with FedRAMP 20x submission requirements. |

---

## 11. Files

### New Files (7)
| File | LOC | Purpose |
|------|-----|---------|
| `tools/compliance/fedramp_ksi_generator.py` | ~354 | KSI evidence generator with 70+ evidence collectors |
| `tools/compliance/owasp_asi_assessor.py` | ~199 | OWASP ASI01-ASI10 BaseAssessor subclass |
| `tools/compliance/fedramp_authorization_packager.py` | ~136 | Authorization package bundler |
| `tools/dashboard/api/fedramp_20x.py` | ~76 | Dashboard API Blueprint (4 endpoints) |
| `tools/dashboard/templates/fedramp_20x.html` | ~207 | Dashboard page template |
| `context/compliance/fedramp_20x_ksi_schemas.json` | ~133 | KSI schema catalog (43 KSIs, 11+1 families) |
| `context/compliance/owasp_agentic_asi.json` | ~133 | OWASP ASI risk catalog (10 risks) |

### Test Files (2)
| File | Tests | Purpose |
|------|-------|---------|
| `tests/test_fedramp_ksi_generator.py` | 15 | KSI generator unit tests |
| `tests/test_owasp_asi_assessor.py` | 18 | OWASP ASI assessor unit tests |

### Modified Files
| File | Change |
|------|--------|
| `tools/db/init_icdev_db.py` | +`owasp_asi_assessments` CREATE TABLE |
| `tools/dashboard/app.py` | +`/fedramp-20x` route, +Blueprint registration |
| `tools/mcp/tool_registry.py` | +KSI and ASI tool entries |
| `CLAUDE.md` | +D338-D340, +tables, +commands, +config, +dashboard page |
| `tools/manifest.md` | +FedRAMP 20x and OWASP ASI section |
| `goals/manifest.md` | +FedRAMP 20x entry |
