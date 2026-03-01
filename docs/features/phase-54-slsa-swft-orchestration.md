# Phase 54 — SLSA/SWFT + Cross-Phase Orchestration

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 54 |
| Title | SLSA/SWFT Supply Chain Attestation + Cross-Phase Workflow Orchestration |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 24 (DevSecOps Pipeline Security), Phase 37 (MITRE ATLAS Integration), Phase 47 (Unified MCP Gateway) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-25 |

---

## 1. Problem Statement

ICDEV generates compliance artifacts across 30+ frameworks and executes security scans, ATO acceleration, and build pipelines through individual CLI tools. However, three critical gaps remain:

1. **No SLSA provenance.** DoD and federal software factories increasingly require SLSA (Supply-chain Levels for Software Artifacts) v1.0 provenance attestations to verify build integrity. ICDEV tracks build evidence across `devsecops_pipeline_audit`, `sbom_records`, and `audit_trail` tables, but never assembles it into the standardized in-toto v1 provenance format that Platform One and software factories expect.

2. **No SWFT evidence bundling.** The DoD Software Factory Trust (SWFT) framework mandates a consolidated evidence package containing SLSA provenance, SBOM, VEX, SAST results, dependency audits, secret detection, and compliance assessments. ICDEV generates each artifact independently, but there is no tool to package them into a validated, hash-integrity-verified bundle for software factory authorization.

3. **No cross-phase orchestration.** Running an ATO acceleration requires manually invoking FIPS 199 categorization, then multi-framework assessment, then SSP generation, then POAM, then SBOM, then SLSA, then OSCAL -- each with the correct dependency ordering. Operators must memorize tool chains and execution order. There is no declarative way to compose multi-tool workflows with automatic dependency resolution.

Without these capabilities, ICDEV cannot satisfy DoD software factory authorization requirements or provide operators with one-command compliance workflows.

---

## 2. Goals

1. Generate SLSA v1.0 provenance statements in in-toto v1 format from existing build pipeline evidence
2. Determine SLSA level (0-4) automatically from evidence collected across ICDEV databases
3. Generate VEX (Vulnerability Exploitability eXchange) documents from vulnerability and CVE triage records
4. Bundle all SWFT-required artifacts into a validated evidence package with SHA-256 integrity verification
5. Validate SWFT evidence completeness, freshness, and gap analysis with actionable recommendations
6. Provide a declarative YAML-based workflow engine that resolves tool dependencies via topological sort
7. Ship 4 workflow templates: `ato_acceleration`, `security_hardening`, `full_compliance`, `build_deploy`
8. Support dry-run mode for workflow preview without execution
9. Upgrade CycloneDX spec version support with backward-compatible `--spec-version` flag (D342)

---

## 3. Architecture

```
                Cross-Phase Orchestration + SLSA/SWFT Pipeline
    ┌─────────────────────────────────────────────────────────────┐
    │              workflow_templates/*.yaml (D343)                │
    │   ato_acceleration | security_hardening | full_compliance   │
    │                     build_deploy                            │
    └───────────────────────┬─────────────────────────────────────┘
                            │
                  ┌─────────┴─────────┐
                  │ workflow_composer  │
                  │ (TopologicalSorter│
                  │   D40, D343)      │
                  └────┬─────────┬────┘
                       │         │
            ┌──────────┘         └──────────┐
            ↓                               ↓
    ┌───────────────┐               ┌───────────────┐
    │  SLSA v1.0    │               │  SWFT Bundle  │
    │  Provenance   │               │  Packager     │
    │  (D341)       │               │  (D341)       │
    │               │               │               │
    │ in-toto v1    │←──────────────│ 10 artifact   │
    │ statement     │  provenance   │ categories    │
    │               │  input        │               │
    │ VEX document  │               │ SHA-256       │
    │ (CycloneDX)   │               │ integrity     │
    └───────┬───────┘               └───────┬───────┘
            │                               │
            ↓                               ↓
    ┌───────────────────────────────────────────────┐
    │              Existing ICDEV Evidence           │
    │                                               │
    │  devsecops_pipeline_audit  sbom_records       │
    │  audit_trail               vulnerability_records
    │  cve_triage                production_audits   │
    │  devsecops_profiles        (+ 220 more tables) │
    └───────────────────────────────────────────────┘
```

### Key Design Principles

- **Extends, never rewrites** -- SLSA generator extends `attestation_manager.py` patterns (D341)
- **Declarative workflows** -- YAML templates with DAG dependency resolution, no code changes to add workflows (D343, D26)
- **Air-gap safe** -- `graphlib.TopologicalSorter` is stdlib Python 3.9+ (D40), all evidence collection via SQLite
- **Backward-compatible versioning** -- CycloneDX `--spec-version` flag defaults to 1.7, allows 1.4 (D342)
- **Hash-integrity verification** -- SWFT bundles include SHA-256 digest of all artifact evidence

---

## 4. Implementation

### Component 1: SLSA v1.0 Provenance Generator (`tools/compliance/slsa_attestation_generator.py`)

Generates SLSA provenance in the in-toto v1 statement format (`https://in-toto.io/Statement/v1`) with SLSA v1.0 predicate type (`https://slsa.dev/provenance/v1`).

**SLSA Level Determination:**

| Level | Requirements | Description |
|-------|-------------|-------------|
| 0 | None | No guarantees |
| 1 | `build_process_documented` | Documentation of the build process |
| 2 | L1 + `version_controlled_source`, `build_service_authenticated` | Tamper resistance of the build service |
| 3 | L2 + `build_as_code`, `ephemeral_environment`, `isolated_builds` | Extra resistance to specific threats |
| 4 | L3 + `hermetic_builds`, `reproducible_builds` | Highest level of confidence |

**Evidence Collection Sources:**

| Evidence Item | DB Table | Condition |
|---------------|----------|-----------|
| `build_process_documented` | `devsecops_pipeline_audit` | Any records for project |
| `version_controlled_source` | `sbom_records` | SBOM records exist |
| `build_service_authenticated` | `devsecops_profiles` | `image_signing` in active stages |
| `build_as_code` | `devsecops_profiles` | `sbom_attestation` in active stages |
| `ephemeral_environment` | `audit_trail` | Deploy events recorded |
| `isolated_builds` | `audit_trail` | Deploy events recorded |
| `hermetic_builds` | `devsecops_pipeline_audit` | `image_signing` stage completed |
| `reproducible_builds` | (manual) | Requires explicit attestation |

**VEX Document Generation:**

Produces CycloneDX-format VEX documents by collecting vulnerability data from `vulnerability_records` and `cve_triage` tables. Each vulnerability maps to an exploitability state (`not_affected`, `affected`, `fixed`, `under_investigation`).

### Component 2: SWFT Evidence Bundler (`tools/compliance/swft_evidence_bundler.py`)

Packages ICDEV compliance artifacts into a DoD Software Factory Trust evidence bundle.

**10 Artifact Categories:**

| Category | Required | Source Tool |
|----------|----------|-------------|
| `provenance` | Yes | `slsa_attestation_generator` |
| `sbom` | Yes | `sbom_generator` |
| `vex` | Yes | `slsa_attestation_generator` |
| `sast_results` | Yes | `sast_runner` |
| `dependency_audit` | Yes | `dependency_auditor` |
| `secret_detection` | Yes | `secret_detector` |
| `compliance_assessment` | Yes | `multi_regime_assessor` |
| `container_scan` | No | `container_scanner` |
| `image_attestation` | No | `attestation_manager` |
| `production_audit` | No | `production_audit` |

**Bundle Manifest Output:**
- `bundle_id` -- Unique 12-character identifier
- `artifacts` -- Per-category availability, record count, latest date
- `summary` -- Required/optional counts, readiness percentage
- `integrity` -- SHA-256 hash of all artifact evidence JSON

**Validation** checks completeness, freshness (artifact age), and produces gap analysis with actionable recommendations for missing or stale evidence.

### Component 3: Cross-Phase Workflow Composer (`tools/orchestration/workflow_composer.py`)

Declarative workflow engine that composes ICDEV tools into reusable DAG-based workflows.

**Execution Pipeline:**
1. Load YAML template from `args/workflow_templates/`
2. Parse step definitions with `id`, `tool`, `depends_on`, `args`
3. Resolve execution order via `graphlib.TopologicalSorter` (D40)
4. Build subprocess commands with project ID injection and `--json` output
5. Execute steps sequentially respecting dependency order
6. Collect per-step results (status, output, duration, errors)

**4 Workflow Templates:**

| Template | Category | Steps | Pipeline |
|----------|----------|-------|----------|
| `ato_acceleration` | compliance | 7 | Categorize -> Assess -> SSP + POAM (parallel) -> SBOM -> SLSA -> OSCAL |
| `security_hardening` | security | 7 | SAST + Deps + Secrets (parallel) -> OWASP + OWASP ASI (parallel) -> ATLAS -> Patterns |
| `full_compliance` | compliance | 6 | Detect -> Assess -> Crosswalk + AI Transparency + AI Accountability (parallel) -> Prod Audit |
| `build_deploy` | build | 6 | Scaffold -> Lint -> Test -> E2E -> Terraform + Pipeline (parallel) |

**Key Features:**
- `--dry-run` mode previews all commands without execution
- Per-step `required` flag allows optional steps to fail without blocking
- Per-step `timeout` (default 300 seconds)
- Automatic `--project-id` injection for tools that expect it
- Step argument overrides via API

---

## 5. Database

Phase 54 does not introduce new database tables. It reads from existing tables to collect evidence:

| Table | Used By | Purpose |
|-------|---------|---------|
| `devsecops_pipeline_audit` | SLSA generator | Build process, image signing evidence |
| `devsecops_profiles` | SLSA generator | Active pipeline stages |
| `sbom_records` | SLSA generator, SWFT bundler | Source control evidence, SBOM artifacts |
| `audit_trail` | SLSA generator, SWFT bundler | Deploy events, scan records |
| `vulnerability_records` | VEX generator | Vulnerability exploitability status |
| `cve_triage` | VEX generator | CVE triage decisions and CVSS scores |
| `production_audits` | SWFT bundler | Production readiness evidence |

---

## 6. Configuration

### Workflow Templates (`args/workflow_templates/*.yaml`)

Each template follows a declarative schema:

```yaml
# CUI // SP-CTI
description: "Human-readable workflow description"
category: compliance | security | build
steps:
  - id: step_name
    name: "Display Name"
    tool: "tools/path/to/tool.py"
    args:
      flag-name: value
    depends_on: [prior_step_id]
    required: true          # false = optional, can fail
    inject_project_id: true # auto-inject --project-id
    json_output: true       # auto-append --json
    timeout: 300            # seconds
```

### Security Gates (`args/security_gates.yaml`)

```yaml
swft:
  blocking:
    - slsa_provenance_missing
    - sbom_not_generated
    - sast_scan_not_completed
    - secret_detection_not_run
    - dependency_audit_not_completed
  warning:
    - slsa_level_below_target
    - vex_document_missing
    - container_scan_not_completed
    - image_attestation_missing
    - swft_evidence_stale
  thresholds:
    min_slsa_level: 2
    sbom_max_age_days: 30
    sast_required: true
    dependency_audit_required: true
```

---

## 7. Dashboard

Phase 54 tools are invoked via CLI and MCP. Dashboard integration is through existing pages:

- `/prod-audit` -- Production readiness audit includes SWFT evidence checks
- `/evidence` -- Compliance evidence inventory displays SLSA/SWFT artifact status
- `/batch` -- Batch operations panel can execute workflow templates (ato_acceleration, security_hardening, full_compliance, build_deploy)

---

## 8. Security Gates

### SWFT Gate (Blocking)

| Condition | Description |
|-----------|-------------|
| `slsa_provenance_missing` | No SLSA provenance generated for project |
| `sbom_not_generated` | No SBOM records in database |
| `sast_scan_not_completed` | No SAST scan results in audit trail |
| `secret_detection_not_run` | No secret detection records |
| `dependency_audit_not_completed` | No dependency audit records |

### SWFT Gate (Warning)

| Condition | Description |
|-----------|-------------|
| `slsa_level_below_target` | SLSA level < 2 (configurable threshold) |
| `vex_document_missing` | No VEX document generated |
| `container_scan_not_completed` | No container image scan results |
| `image_attestation_missing` | No signed container attestation |
| `swft_evidence_stale` | Evidence older than `sbom_max_age_days` |

---

## 9. Verification

```bash
# SLSA provenance generation
python tools/compliance/slsa_attestation_generator.py --project-id proj-test --generate --json

# VEX document generation
python tools/compliance/slsa_attestation_generator.py --project-id proj-test --vex --json

# SLSA level verification with gap analysis
python tools/compliance/slsa_attestation_generator.py --project-id proj-test --verify --json

# SWFT evidence bundling
python tools/compliance/swft_evidence_bundler.py --project-id proj-test --bundle --json

# SWFT evidence bundling to directory
python tools/compliance/swft_evidence_bundler.py --project-id proj-test --bundle --output-dir /tmp/swft --json

# SWFT validation (completeness + freshness)
python tools/compliance/swft_evidence_bundler.py --project-id proj-test --validate --json

# List available workflow templates
python tools/orchestration/workflow_composer.py --list --json

# Dry-run ATO acceleration workflow
python tools/orchestration/workflow_composer.py --template ato_acceleration --project-id proj-test --dry-run --json

# Execute security hardening workflow
python tools/orchestration/workflow_composer.py --template security_hardening --project-id proj-test --json

# Execute full compliance workflow
python tools/orchestration/workflow_composer.py --template full_compliance --project-id proj-test --json

# Execute build and deploy workflow
python tools/orchestration/workflow_composer.py --template build_deploy --project-id proj-test --json

# Run tests
pytest tests/test_slsa_attestation.py -v        # 16 tests
pytest tests/test_workflow_composer.py -v       # 17 tests
```

**Total: 33 tests covering all Phase 54 functionality.**

### MCP Tools (4 tools registered in unified gateway)

| Tool | Description |
|------|-------------|
| `slsa_generate` | Generate SLSA v1.0 provenance statement from build pipeline evidence |
| `slsa_verify` | Verify project meets target SLSA level with gap analysis |
| `swft_bundle` | Bundle DoD SWFT evidence package with all required artifacts |
| `vex_generate` | Generate VEX document from vulnerability data |

---

## 10. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D341 | SLSA attestation generator extends existing `attestation_manager.py` | Produces SLSA v1.0 provenance from build pipeline evidence already collected in ICDEV databases. Reuses existing infrastructure rather than introducing new attestation pipeline. |
| D342 | CycloneDX version upgrade is backward-compatible with `--spec-version` flag | Default spec version 1.7 for new features, but allows `--spec-version 1.4` for environments requiring older format. No breaking changes to existing SBOM consumers. |
| D343 | Workflow composer uses declarative YAML templates + `graphlib.TopologicalSorter` DAG | Add new workflows without code changes (D26 pattern). `TopologicalSorter` is stdlib Python 3.9+ (D40) -- air-gap safe, zero deps, cycle detection built-in. Templates define tool sequences with dependencies; engine resolves execution order automatically. |

---

## Files

### New Files (8)

| File | LOC | Purpose |
|------|-----|---------|
| `tools/compliance/slsa_attestation_generator.py` | ~489 | SLSA v1.0 provenance + VEX generation |
| `tools/compliance/swft_evidence_bundler.py` | ~336 | DoD SWFT evidence bundler + validator |
| `tools/orchestration/__init__.py` | 2 | Package |
| `tools/orchestration/workflow_composer.py` | ~360 | Cross-phase DAG workflow engine |
| `args/workflow_templates/ato_acceleration.yaml` | ~55 | ATO acceleration workflow (7 steps) |
| `args/workflow_templates/security_hardening.yaml` | ~56 | Security hardening workflow (7 steps) |
| `args/workflow_templates/full_compliance.yaml` | ~44 | Full compliance workflow (6 steps) |
| `args/workflow_templates/build_deploy.yaml` | ~64 | Build and deploy workflow (6 steps) |

### Test Files (2)

| File | Tests | Purpose |
|------|-------|---------|
| `tests/test_slsa_attestation.py` | 16 | SLSA provenance, VEX, level determination, evidence collection |
| `tests/test_workflow_composer.py` | 17 | Template loading, DAG resolution, command building, dry-run, execution |

### Modified Files

| File | Change |
|------|--------|
| `tools/mcp/tool_registry.py` | +4 tool entries (`slsa_generate`, `slsa_verify`, `swft_bundle`, `vex_generate`) |
| `tools/mcp/gap_handlers.py` | +4 handler functions |
| `args/security_gates.yaml` | +`swft` gate with blocking/warning conditions and thresholds |
| `CLAUDE.md` | +D341-D343, +commands, +config, +workflow templates, +security gate |
| `tools/manifest.md` | +SLSA/SWFT + Orchestration section |
| `goals/manifest.md` | +Cross-Phase Orchestration entry |
