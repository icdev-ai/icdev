# Phase 60 — Contract Performance Management Portal

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 60 |
| Title | Contract Performance Management Portal (CPMP) |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 59 (GovCon Intelligence), Phase 21 (SaaS Multi-Tenancy) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-28 |

---

## 1. Problem Statement

After a proposal is won, the post-award lifecycle introduces an entirely different set of management challenges: tracking earned value, forecasting cost and schedule, managing subcontractors, meeting CDRL delivery deadlines, and satisfying government oversight through CPARS evaluations. GovProposal manages the pre-award lifecycle through opportunity tracking, section drafting, compliance matrices, and review workflows — but the moment a contract is awarded, visibility ends.

Without post-award management, program managers must:
- Manually calculate EVM indicators (CPI, SPI, EAC, ETC) across dozens of WBS elements
- Track CDRL due dates in spreadsheets with no automated generation capability
- Estimate CPARS ratings subjectively, unaware of how NDAA negative events compound penalties
- Manage FAR 52.219-9 small business subcontracting goals without compliance visibility
- Provide government CORs with contract status through ad-hoc emails rather than structured portals
- Maintain portfolio-level health awareness across multiple active contracts

Phase 60 closes this gap with a complete Contract Performance Management Portal that bridges the won-proposal to active-contract lifecycle, providing deterministic scoring, automated artifact generation, and government-facing read-only oversight.

---

## 2. Goals

1. Provide a seamless transition bridge from won proposals to active contracts (D-CPMP-9)
2. Implement ANSI/EIA-748 Earned Value Management with CPI, SPI, EAC, ETC, VAC, TCPI indicators
3. Forecast cost and schedule completion using Monte Carlo simulation with PERT distributions (D-CPMP-2)
4. Predict CPARS ratings via deterministic 5-dimension weighted scoring with NDAA penalty table (D-CPMP-3)
5. Track subcontractors with FAR 52.219-9 small business compliance, flow-down verification, and cybersecurity status
6. Auto-generate CDRLs by dispatching to existing ICDEV tools: SSP, SBOM, STIG, POAM, EVM reports (D-CPMP-5)
7. Auto-detect NDAA negative events from contract performance data (delinquent deliveries, cost overruns, quality rejections)
8. Provide a COR portal with read-only government access filtered by COR email (D-CPMP-4)
9. Sync contract awards from SAM.gov Contract Awards API with content-hash deduplication (D-CPMP-6)
10. Compute portfolio-level health scores using configurable weighted averages across 5 dimensions (D-CPMP-8)
11. Support IDIQ/Task Order hierarchy via self-referencing contract table (D-CPMP-10)
12. Maintain append-only audit trail for all status changes, negative events, CDRL generations, and COR access (D-CPMP-7)

---

## 3. Architecture

```
                      Contract Performance Management Portal
    ┌─────────────────────────────────────────────────────────────────┐
    │                    govcon_config.yaml                           │
    │  (health weights, EVM thresholds, CPARS weights, NDAA rules,   │
    │   CDRL tool mapping, SAM.gov config, COR portal settings)      │
    └───────────────────────────┬─────────────────────────────────────┘
                                │
    ┌───────────┬───────────┬───┴───┬───────────┬───────────┬─────────┐
    │           │           │       │           │           │         │
    ▼           ▼           ▼       ▼           ▼           ▼         ▼
 Contract    EVM        CPARS   Negative   Subcontract  CDRL      SAM.gov
 Manager    Engine    Predictor  Event      Tracker    Generator   Sync
    │           │           │    Tracker       │           │         │
    │           │           │       │           │           │         │
    ▼           ▼           ▼       ▼           ▼           ▼         ▼
 contracts   evm_      cpars_  negative_  subcontract  cdrl_gen   sam_awards
 clins       periods   assess  events     small_biz    (audit)    (cache)
 wbs         (append)  ments   (append)                (append)
 deliverables
 status_history (append)               cor_access_log (append)
    │                                           │
    └─────────────────┬─────────────────────────┘
                      ▼
            Portfolio Manager          COR Portal
            (health scoring,           (read-only,
             transition bridge)         email-filtered)
                      │                      │
                      ▼                      ▼
              Dashboard API            /cpmp/cor
              /api/cpmp/*              /cpmp/cor/<id>
              /cpmp, /cpmp/<id>
              /cpmp/<id>/deliverables/<did>
```

### Key Design Principles

- **Deterministic scoring** -- Health, CPARS prediction, and EVM all use weighted averages with fixed, configurable weights (D21, D-CPMP-3, D-CPMP-8)
- **Air-gap safe** -- Monte Carlo uses Python stdlib `random.betavariate` for PERT distributions, no numpy/scipy (D22, D-CPMP-2)
- **Append-only audit** -- Status history, negative events, EVM periods, CDRL generations, and COR access logs are immutable (D6, D-CPMP-7)
- **Tool reuse** -- CDRL generation dispatches to existing ICDEV compliance tools rather than reimplementing (D-CPMP-5)
- **Explicit transitions** -- Proposal-to-contract bridge requires human confirmation, not automatic (D-CPMP-9)
- **Namespace isolation** -- All 13 tables prefixed `cpmp_` to avoid collision with existing schema (D-CPMP-1)

---

## 4. Implementation

### Component 1: Contract Manager (`tools/govcon/contract_manager.py`)

Core CRUD for contracts, CLINs, WBS elements, and deliverables with state machine enforcement.

**Contract Status Machine:**
```
draft → active → option_pending → active (loop)
                → complete → closed
                → terminated → closed
```

**Deliverable Status Pipeline (10 states):**
```
not_started → in_progress → draft_complete → internal_review → submitted
     → government_review → accepted
                         → rejected → resubmitted → government_review
     overdue → in_progress | submitted
```

Every status transition is validated against the configured state machine in `govcon_config.yaml`. Invalid transitions are rejected with an error. All transitions are recorded in `cpmp_status_history` (append-only).

**Contract Types:** FFP, T&M, CPFF, CPIF, IDIQ, BPA, BOA

**CLIN Types:** labor, materials, travel, odc, subcontract, fixed_price

**WBS:** Hierarchical via `parent_id` self-reference. Supports tree retrieval (`?mode=tree`).

### Component 2: EVM Engine (`tools/govcon/evm_engine.py`)

ANSI/EIA-748 Earned Value Management calculations.

**Indicators Computed:**
| Indicator | Formula | Meaning |
|-----------|---------|---------|
| CPI | EV / AC | Cost Performance Index |
| SPI | EV / PV | Schedule Performance Index |
| CV | EV - AC | Cost Variance |
| SV | EV - PV | Schedule Variance |
| EAC | BAC / CPI | Estimate at Completion |
| ETC | EAC - AC | Estimate to Complete |
| VAC | BAC - EAC | Variance at Completion |
| TCPI | (BAC - EV) / (BAC - AC) | To-Complete Performance Index |

**Monte Carlo Forecasting:** Uses PERT distribution (`random.betavariate`) with recent CPI/SPI performance to generate P10, P50, P90 completion estimates. Default 10,000 iterations (configurable).

**Additional Outputs:**
- S-curve time-series data for PV/EV/AC charting
- IPMDAR-compatible export (Format 1, 3, 5)
- Contract-level aggregation across all WBS elements

**Threshold Indicators:**
| Metric | Yellow | Red |
|--------|--------|-----|
| CPI | < 0.95 | < 0.85 |
| SPI | < 0.95 | < 0.85 |

### Component 3: CPARS Predictor (`tools/govcon/cpars_predictor.py`)

Deterministic 5-dimension weighted scoring (D-CPMP-3, D21 pattern).

**Dimension Weights:**
| Dimension | Weight | Data Source |
|-----------|--------|-------------|
| Quality | 0.25 | Deliverable acceptance/rejection rates |
| Schedule | 0.25 | Deliverable on-time delivery rate |
| Cost | 0.20 | EVM CPI performance |
| Management | 0.15 | Negative event count and severity |
| Small Business | 0.15 | SB goal attainment from ISR/SSR |

**NDAA Penalty Table (per event type):**
| Event | Penalty |
|-------|---------|
| delinquent_delivery | -0.05 |
| cost_overrun | -0.08 |
| quality_rejection | -0.06 |
| cybersecurity_breach | -0.10 |
| flowdown_failure | -0.04 |
| safety_violation | -0.12 |
| cure_notice | -0.15 |
| show_cause | -0.20 |
| stop_work | -0.25 |
| termination_default / fraud_waste_abuse | -0.50 |

Completed corrective actions reduce penalties by configurable discount (default 50%).

**Rating Thresholds:**
| Rating | Score |
|--------|-------|
| Exceptional | >= 0.90 |
| Very Good | >= 0.75 |
| Satisfactory | >= 0.60 |
| Marginal | >= 0.40 |
| Unsatisfactory | < 0.40 |

### Component 4: Negative Event Tracker (`tools/govcon/negative_event_tracker.py`)

Tracks 12 NDAA event types with auto-detection from contract performance data.

**Auto-Detection Rules:**
| Trigger | Condition |
|---------|-----------|
| Delinquent Delivery | `deliverable.days_overdue > 0` |
| Cost Overrun | `evm.cpi < 0.85` for 3 consecutive periods |
| Quality Rejection | `deliverable.rejection_count >= 2` |
| Flow-Down Failure | `subcontract_value > $100K AND flow_down_complete = 0` |
| Cybersecurity Breach | `cybersecurity_compliant = 0 AND days_since_flag > 30` |

All negative events are append-only (D6, D-CPMP-7). Corrective action status is tracked on each record (`open`, `in_progress`, `completed`, `verified`).

### Component 5: Subcontractor Tracker (`tools/govcon/subcontractor_tracker.py`)

FAR 52.219-9 small business subcontracting compliance.

**Tracked Fields:** CAGE code, UEI, business size (large, small, SDB, WOSB, HUBZone, SDVOSB, 8(a)), subcontract type/value, flow-down status, cybersecurity compliance, CMMC level, performance rating.

**Small Business Plan (ISR/SSR):** Tracks goal vs. actual percentages for 5 categories: SB, SDB, WOSB, HUBZone, SDVOSB. Compliance flag computed from goal attainment.

**Noncompliance Detection:** Flags subcontractors with incomplete flow-down, missing cybersecurity compliance, or SB goal shortfalls.

### Component 6: CDRL Generator (`tools/govcon/cdrl_generator.py`)

Dispatches CDRL generation to existing ICDEV tools (D-CPMP-5).

**Tool Mapping:**
| CDRL Type | ICDEV Tool |
|-----------|------------|
| SSP | `tools/compliance/ssp_generator.py` |
| SBOM | `tools/compliance/sbom_generator.py` |
| POAM | `tools/compliance/poam_generator.py` |
| STIG Checklist | `tools/compliance/stig_checker.py` |
| EVM Report | `tools/govcon/evm_engine.py` |
| ICD | `tools/mosa/icd_generator.py` |
| TSP | `tools/mosa/tsp_generator.py` |
| Test Report | `tools/testing/test_orchestrator.py` |
| Security Scan | `tools/security/sast_runner.py` |

**Batch Generation:** Auto-generates all CDRLs due within configurable window (default 14 days before due date).

All generations are recorded in `cpmp_cdrl_generations` (append-only audit trail).

### Component 7: SAM Contract Sync (`tools/govcon/sam_contract_sync.py`)

Fetches contract awards from SAM.gov Contract Awards API (D-CPMP-6).

- Content-hash deduplication (reuses `sam_scanner.py` pattern)
- Rate-limited: 10 requests/second, 0.15s delay between requests
- Lookback window: 90 days
- Awards can be linked to CPMP contracts via `linked_contract_id`

### Component 8: Portfolio Manager (`tools/govcon/portfolio_manager.py`)

**Health Scoring (D-CPMP-8):**
```
health = evm(0.30) + deliverables(0.25) + cpars(0.20)
       + negative_events(0.15) + funding(0.10)
```

**Health Colors:**
| Color | Score |
|-------|-------|
| Green | >= 0.70 |
| Yellow | >= 0.40 |
| Red | < 0.40 |

**Transition Bridge (D-CPMP-9):** When a proposal opportunity transitions to `won`, the portfolio manager creates a new contract auto-populated from opportunity data (title, agency, NAICS, contract type). Links `proposal_opportunities.contract_id` and seeds initial deliverables from compliance matrix CDRL items. Requires explicit human confirmation.

### Component 9: CPMP API (`tools/dashboard/api/cpmp.py`)

Flask Blueprint with approximately 40 endpoints covering all CPMP operations.

**Key Endpoint Groups:**
| Group | Prefix | Operations |
|-------|--------|------------|
| Contracts | `/api/cpmp/contracts` | CRUD, status transitions |
| CLINs | `/api/cpmp/contracts/<id>/clins` | Create, update |
| WBS | `/api/cpmp/contracts/<id>/wbs` | Create, update, tree mode |
| Deliverables | `/api/cpmp/contracts/<id>/deliverables` | CRUD, status pipeline |
| EVM | `/api/cpmp/contracts/<id>/evm` | Record, aggregate, forecast, S-curve, IPMDAR |
| Subcontractors | `/api/cpmp/contracts/<id>/subcontractors` | CRUD, SB compliance, noncompliance |
| Small Business | `/api/cpmp/contracts/<id>/small-business` | ISR/SSR plans |
| CPARS | `/api/cpmp/contracts/<id>/cpars` | Assessments, predict, trend |
| Negative Events | `/api/cpmp/contracts/<id>/negative-events` | Create, auto-detect, NDAA thresholds |
| Health | `/api/cpmp/contracts/<id>/health` | Contract health scoring |
| CDRL | `/api/cpmp/contracts/<id>/generate-cdrl/<did>` | Single/batch generation |
| SAM.gov | `/api/cpmp/sam` | Sync, list, link awards |
| Portfolio | `/api/cpmp/portfolio` | Summary stats, contract list |
| COR | `/api/cpmp/cor` | Read-only government endpoints |

---

## 5. Database

### Tables (13, all prefixed `cpmp_`)

| Table | Type | Purpose |
|-------|------|---------|
| `cpmp_contracts` | CRUD | Contract master records with status, health, COR info, IDIQ self-reference |
| `cpmp_clins` | CRUD | Contract Line Items with funding tracking (total/funded/billed) |
| `cpmp_wbs` | CRUD | Work Breakdown Structure (hierarchical via `parent_id`) with BAC and EVM cumulatives |
| `cpmp_deliverables` | CRUD | CDRLs and deliverables with 10-state status pipeline |
| `cpmp_status_history` | Append-only | All entity status transitions (contract, CLIN, WBS, deliverable, etc.) |
| `cpmp_evm_periods` | Append-only | Monthly EVM snapshots per WBS element (PV, EV, AC, CPI, SPI, EAC, etc.) |
| `cpmp_subcontractors` | CRUD | Subcontractor registry with business size, flow-down, cybersecurity, CMMC |
| `cpmp_cpars_assessments` | CRUD | CPARS evaluation period records with 5-dimension ratings |
| `cpmp_negative_events` | Append-only | NDAA negative events with corrective action tracking |
| `cpmp_small_business_plan` | CRUD | FAR 52.219-9 ISR/SSR reports with goal vs. actual by business category |
| `cpmp_cdrl_generations` | Append-only | CDRL auto-generation audit trail (tool, output, hash, status) |
| `cpmp_sam_contract_awards` | Cache | SAM.gov award records with content-hash dedup |
| `cpmp_cor_access_log` | Append-only | COR portal access audit trail (NIST AU-2) |

**Append-only tables** (`cpmp_status_history`, `cpmp_evm_periods`, `cpmp_negative_events`, `cpmp_cdrl_generations`, `cpmp_cor_access_log`) are protected via `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`.

---

## 6. Configuration

`args/govcon_config.yaml` -> `cpmp:` section:

```yaml
cpmp:
  # Contract health scoring weights (D-CPMP-8)
  health_weights:
    evm: 0.30
    deliverables: 0.25
    cpars: 0.20
    negative_events: 0.15
    funding: 0.10

  # Contract and deliverable state machines
  contract_transitions:
    draft: [active]
    active: [option_pending, complete, terminated]
    # ...
  deliverable_transitions:
    not_started: [in_progress]
    in_progress: [draft_complete, overdue]
    # ... (10 states)

  # EVM thresholds and Monte Carlo (D-CPMP-2)
  evm:
    cpi_yellow_threshold: 0.95
    cpi_red_threshold: 0.85
    spi_yellow_threshold: 0.95
    spi_red_threshold: 0.85
    monte_carlo_iterations: 10000
    forecast_confidence_levels: [0.50, 0.80, 0.95]

  # CPARS prediction weights and thresholds (D-CPMP-3)
  cpars:
    prediction_weights:
      quality: 0.25
      schedule: 0.25
      cost: 0.20
      management: 0.15
      small_business: 0.15
    rating_thresholds:
      exceptional: 0.90
      very_good: 0.75
      satisfactory: 0.60
      marginal: 0.40
    corrective_action_discount: 0.50

  # NDAA negative-event auto-detection and penalty table
  negative_events:
    auto_detect: { ... }       # 5 detection rules
    penalty_table: { ... }     # 12 event-type penalties (0.04 to 0.50)

  # CDRL auto-generation tool mapping (D-CPMP-5)
  cdrl:
    output_dir: data/cdrl_output
    auto_generate_days_before_due: 14
    tool_mapping:
      ssp: tools/compliance/ssp_generator.py
      sbom: tools/compliance/sbom_generator.py
      # ... (9 tool mappings)

  # SAM.gov Contract Awards API (D-CPMP-6)
  sam_awards:
    api_url: https://api.sam.gov/opportunities/v1/search
    api_key_env: SAM_GOV_API_KEY
    poll_interval_hours: 24
    rate_limit: { requests_per_second: 10, delay_between_requests: 0.15 }
    lookback_days: 90

  # COR Portal (D-CPMP-4)
  cor_portal:
    enabled: true
    session_timeout_minutes: 30
    hidden_fields: [subcontractor_pricing, internal_notes, corrective_actions, ...]
```

---

## 7. Dashboard

### Pages

| Route | Template | Content |
|-------|----------|---------|
| `/cpmp` | `cpmp/portfolio.html` | Stat grid (total/active/value/burn rate/overdue/at-risk), health distribution chart, contract table with search/sort, upcoming deliverables |
| `/cpmp/<id>` | `cpmp/detail.html` | 7 tabs: Overview (funding gauge, countdown), CLINs (funding table), WBS (tree view), Deliverables (status pipeline), EVM (CPI/SPI charts, S-curve, Monte Carlo), Subcontractors (SB compliance), CPARS (prediction donut, trend bar chart) |
| `/cpmp/<id>/deliverables/<did>` | `cpmp/deliverable_detail.html` | 10-state status pipeline, CDRL generation button, submission history, info grid, notes |
| `/cpmp/cor` | `cpmp/cor_portal.html` | COR-filtered contract list (blue accent, "Government Read-Only View" badge) |
| `/cpmp/cor/<id>` | `cpmp/cor_detail.html` | COR read-only view: deliverables, EVM charts, CPARS ratings only (no CLINs, WBS, internal costs) |

### COR Portal Access Control

- COR sees only contracts where `cor_email` matches their authenticated email
- All endpoints are GET-only (no create, update, or delete)
- Internal fields are sanitized from responses: subcontractor pricing, internal notes, corrective action details
- All access is logged to `cpmp_cor_access_log` (NIST AU-2)
- Distinct visual styling: blue accent color, "Government Read-Only View" badge

### RBAC

6 roles interact with CPMP: `admin` (full access), `pm` (full access), `developer` (read-only), `isso` (compliance view), `co` (contracting officer view), `cor` (COR portal only).

---

## 8. Security Gates

**State Machine Enforcement:** Invalid contract and deliverable status transitions are rejected with an error. All valid transitions are recorded in `cpmp_status_history`.

**Append-Only Compliance (NIST AU-2):**
- `cpmp_status_history` -- No UPDATE/DELETE
- `cpmp_evm_periods` -- No UPDATE/DELETE
- `cpmp_negative_events` -- No UPDATE/DELETE
- `cpmp_cdrl_generations` -- No UPDATE/DELETE
- `cpmp_cor_access_log` -- No UPDATE/DELETE

**CUI Markings:** All generated CDRL artifacts include classification markings via `classification_manager.py`. All database records include a `classification` column defaulting to `CUI`.

**COR Access Control:** Email-based filtering ensures CORs only access their assigned contracts. GET-only endpoint enforcement prevents data modification. IP address and user agent are logged.

---

## 9. Verification

```bash
# 1. Verify 13 CPMP tables created
python tools/db/init_icdev_db.py

# 2. Test proposal-to-contract transition
# Create opportunity → set status to 'won' → POST /api/proposals/opportunities/<id>/create-contract
# Verify contract auto-created with correct data

# 3. Test CRUD operations
# Add CLINs, WBS elements, deliverables → verify create/read/update/transitions

# 4. Test EVM calculations
# Record PV/EV/AC per WBS → GET /api/cpmp/contracts/<id>/evm
# Verify CPI, SPI, EAC, ETC, VAC, TCPI calculations

# 5. Test Monte Carlo forecasting
# GET /api/cpmp/contracts/<id>/evm/forecast → verify P10, P50, P90 estimates

# 6. Test CPARS prediction
# GET /api/cpmp/contracts/<id>/cpars/predict → verify weighted scores and NDAA penalties

# 7. Test negative event auto-detection
# POST /api/cpmp/contracts/<id>/negative-events/auto-detect
# Verify: delinquent deliveries, CPI<0.85, quality rejections, flowdown failures, cyber breaches

# 8. Test CDRL generation
# POST /api/cpmp/contracts/<id>/generate-cdrl/<did> → verify tool dispatch and audit record

# 9. Test COR portal
# Login as COR → /cpmp/cor → verify filtered contract list and hidden internal fields

# 10. Test portfolio health
# GET /api/cpmp/portfolio → verify health scores and stat grid

# 11. Test SAM.gov sync
# POST /api/cpmp/sam/sync-awards → verify cached awards with content-hash dedup

# 12. Test IDIQ/TO hierarchy
# Create IDIQ parent → create task order with idiq_contract_id → verify aggregation
```

---

## 10. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D-CPMP-1 | All tables prefixed `cpmp_` | Namespace isolation from existing govcon/proposal tables |
| D-CPMP-2 | Monte Carlo via stdlib `random` | Air-gap safe, no numpy/scipy required (D22 pattern) |
| D-CPMP-3 | CPARS prediction is deterministic weighted average | Reproducible, not probabilistic (D21 pattern); ML upgrade path later |
| D-CPMP-4 | COR portal is read-only routes on same Flask app | Reuses existing auth; role-based access sufficient |
| D-CPMP-5 | CDRL generator dispatches to existing ICDEV tools | Reuse ssp_generator, sbom_generator, stig_checker, etc. |
| D-CPMP-6 | SAM.gov follows sam_scanner pattern | Consistent rate limiting, content-hash deduplication |
| D-CPMP-7 | Negative events append-only | NIST AU-2 compliance (D6 pattern) |
| D-CPMP-8 | Health is deterministic weighted average | Configurable weights via YAML (D21, D26 patterns) |
| D-CPMP-9 | Transition bridge is explicit API call | Human confirms contract creation from won proposal |
| D-CPMP-10 | IDIQ self-reference for task orders | `idiq_contract_id` on `cpmp_contracts` avoids separate table |

---

## 11. Files

### New Files
| File | Purpose |
|------|---------|
| `tools/govcon/contract_manager.py` | Contract/CLIN/WBS/deliverable CRUD, state transitions |
| `tools/govcon/portfolio_manager.py` | Portfolio summary, health scoring, proposal transition bridge |
| `tools/govcon/evm_engine.py` | ANSI/EIA-748 calculations, Monte Carlo, S-curve, IPMDAR |
| `tools/govcon/cpars_predictor.py` | Weighted CPARS scoring, NDAA penalty, rating thresholds |
| `tools/govcon/subcontractor_tracker.py` | FAR 52.219-9, flow-down, cybersecurity, ISR/SSR |
| `tools/govcon/negative_event_tracker.py` | NDAA events, auto-detection, CPARS impact |
| `tools/govcon/cdrl_generator.py` | ICDEV tool dispatch, batch CDRL generation |
| `tools/govcon/sam_contract_sync.py` | SAM.gov Contract Awards API adapter |
| `tools/dashboard/api/cpmp.py` | Flask Blueprint with ~40 CPMP endpoints |
| `tools/dashboard/templates/cpmp/portfolio.html` | Portfolio dashboard page |
| `tools/dashboard/templates/cpmp/detail.html` | Contract detail page (7 tabs) |
| `tools/dashboard/templates/cpmp/deliverable_detail.html` | Deliverable detail with status pipeline |
| `tools/dashboard/templates/cpmp/cor_portal.html` | COR read-only contract list |
| `tools/dashboard/templates/cpmp/cor_detail.html` | COR read-only contract detail |
| `goals/cpmp_workflow.md` | Goal document |

### Modified Files
| File | Change |
|------|--------|
| `tools/db/init_icdev_db.py` | +13 CREATE TABLE statements for cpmp_* tables |
| `tools/dashboard/app.py` | +CPMP routes, +Blueprint registration, +COR portal routes |
| `args/govcon_config.yaml` | +`cpmp:` section with health weights, EVM, CPARS, NDAA, CDRL, SAM, COR config |
| `.claude/hooks/pre_tool_use.py` | +5 append-only tables to APPEND_ONLY_TABLES |
| `CLAUDE.md` | +D-CPMP-1 through D-CPMP-10, +tables, +commands, +dashboard pages, +config |
| `tools/manifest.md` | +CPMP tool entries |
| `goals/manifest.md` | +CPMP Workflow entry |
