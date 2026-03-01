# CPMP Workflow — Contract Performance Management Portal

> Post-award contract lifecycle management for GovProposal. EVM, CPARS, CDRL, subcontractor tracking, COR portal.

## When to Use

- After winning a proposal (`proposal_opportunities.status = 'won'`)
- To manage contract performance, deliverables, and compliance
- To track EVM metrics and forecast cost/schedule
- To predict CPARS ratings and manage negative events
- To auto-generate CDRLs using ICDEV tools
- To provide COR read-only visibility into contract status

## Prerequisites

- GovProposal module active (proposals workflow operational)
- Database initialized with CPMP tables (`python tools/db/init_icdev_db.py`)
- `args/govcon_config.yaml` has `cpmp:` section configured
- Dashboard running (`python tools/dashboard/app.py`)

## Workflow

### Phase A: Contract Setup

1. **Transition from Proposal**
   - When opportunity status changes to `won`, click "Create Contract" on proposal detail
   - `POST /api/proposals/opportunities/<opp_id>/create-contract`
   - Auto-populates: title, agency, NAICS, contract type from opportunity data
   - Links `proposal_opportunities.contract_id` and `customer_deliveries.contract_id`
   - Seeds initial deliverables from compliance matrix CDRL items
   - Records in `cpmp_status_history`

2. **Configure Contract Details**
   - Set contract number, COR info, POP dates, funded/ceiling values
   - `PUT /api/cpmp/contracts/<id>`
   - Configure contract type: FFP, T&M, CPFF, CPIF, IDIQ
   - For IDIQ vehicles, set `idiq_contract_id` on task orders

3. **Define CLINs**
   - Add Contract Line Items with funding breakdown
   - `POST /api/cpmp/contracts/<id>/clins`
   - Types: labor, materials, travel, odc, subcontract, fixed_price
   - Track total/funded/billed values per CLIN

4. **Build WBS**
   - Create Work Breakdown Structure (hierarchical)
   - `POST /api/cpmp/contracts/<id>/wbs`
   - Set BAC, planned dates, percent complete per WBS element
   - Use `?mode=tree` on GET to retrieve hierarchical structure

5. **Define Deliverables**
   - Add CDRLs and other deliverables
   - `POST /api/cpmp/contracts/<id>/deliverables`
   - Set CDRL number, DID number, type, frequency, due dates
   - Types: cdrl, report, software, documentation, test_result, plan

6. **Monitor Portfolio**
   - `GET /api/cpmp/portfolio` — summary stats and contract list
   - Dashboard: `/cpmp` — stat grid, health distribution, contract table, upcoming deliverables

### Phase B: Performance Tracking

7. **Record EVM Data**
   - Monthly EVM snapshots per WBS element
   - `POST /api/cpmp/contracts/<id>/evm` with `{ wbs_id, period_date, pv, ev, ac }`
   - Auto-calculates: CPI, SPI, CV, SV, EAC, ETC, VAC, TCPI per ANSI/EIA-748
   - Aggregate contract-level EVM: `GET /api/cpmp/contracts/<id>/evm`

8. **Forecast with Monte Carlo**
   - `GET /api/cpmp/contracts/<id>/evm/forecast`
   - PERT distribution using recent CPI/SPI performance
   - Returns P10, P50, P90 completion estimates
   - S-curve data: `GET /api/cpmp/contracts/<id>/evm/scurve`
   - IPMDAR data: `GET /api/cpmp/contracts/<id>/evm/ipmdar`

9. **Track Subcontractors**
   - `POST /api/cpmp/contracts/<id>/subcontractors`
   - FAR 52.219-9 compliance: flow-down, cybersecurity, CMMC level
   - Small business plan: `POST /api/cpmp/contracts/<id>/small-business`
   - Compliance check: `GET /api/cpmp/contracts/<id>/subcontractors/sb-compliance`
   - Noncompliance detection: `GET /api/cpmp/contracts/<id>/subcontractors/noncompliance`

10. **Predict CPARS Ratings**
    - `GET /api/cpmp/contracts/<id>/cpars/predict`
    - Deterministic weighted scoring: quality(0.25), schedule(0.25), cost(0.20), management(0.15), small_business(0.15)
    - NDAA negative event penalties applied per event type
    - Corrective action discount: 50% penalty reduction
    - Rating thresholds: >=0.90 exceptional, >=0.80 very_good, >=0.65 satisfactory, >=0.40 marginal, <0.40 unsatisfactory

11. **Track Negative Events (NDAA)**
    - `POST /api/cpmp/contracts/<id>/negative-events`
    - 12 event types: delinquent_delivery, cost_overrun, quality_rejection, cybersecurity_breach, etc.
    - Auto-detection: `POST /api/cpmp/contracts/<id>/negative-events/auto-detect`
    - Detects: overdue deliverables, CPI<0.85 for 3+ periods, 2+ quality rejections, flowdown failures, unresolved cyber incidents
    - NDAA threshold check: `GET /api/cpmp/contracts/<id>/negative-events/ndaa-thresholds`

12. **Contract Health Scoring**
    - `GET /api/cpmp/contracts/<id>/health`
    - Weighted: EVM(0.30) + deliverables(0.25) + CPARS(0.20) + negative_events(0.15) + funding(0.10)
    - Returns: overall score, per-dimension scores, health color (green/yellow/red), recommendations

### Phase C: Automation & Oversight

13. **Auto-Generate CDRLs**
    - `POST /api/cpmp/contracts/<id>/generate-cdrl/<deliverable_id>`
    - Maps cdrl_type to ICDEV tool: ssp→ssp_generator, sbom→sbom_generator, evm_report→evm_engine, icd→icd_generator
    - Records generation in `cpmp_cdrl_generations` (append-only audit)
    - Batch: `POST /api/cpmp/contracts/<id>/generate-due` — generates all CDRLs due within configured window

14. **Sync SAM.gov Contract Awards**
    - `POST /api/cpmp/sam/sync-awards` — fetch awards from SAM.gov Contract Awards API
    - `GET /api/cpmp/sam/awards` — list cached awards
    - `POST /api/cpmp/sam/link/<sam_award_id>` — link award to contract
    - Content hash dedup, rate-limited (reuses sam_scanner pattern)

15. **COR Portal**
    - Read-only government view: `/cpmp/cor`
    - COR sees ONLY contracts where `cor_email` matches their email
    - Visible: deliverable status/dates, EVM charts (CPI/SPI), CPARS ratings, negative event summaries
    - Hidden: internal cost details, subcontractor pricing, internal notes, corrective actions
    - All access logged to `cpmp_cor_access_log` (NIST AU-2)
    - Distinct visual: blue accent, "Government Read-Only View" badge

## Tools

| Tool | File | Purpose |
|------|------|---------|
| Contract Manager | `tools/govcon/contract_manager.py` | Contract/CLIN/WBS/deliverable CRUD, state transitions |
| Portfolio Manager | `tools/govcon/portfolio_manager.py` | Portfolio summary, health scoring, proposal transition |
| EVM Engine | `tools/govcon/evm_engine.py` | ANSI/EIA-748 calculations, Monte Carlo, S-curve, IPMDAR |
| CPARS Predictor | `tools/govcon/cpars_predictor.py` | Weighted scoring, NDAA penalty, rating thresholds |
| Subcontractor Tracker | `tools/govcon/subcontractor_tracker.py` | FAR 52.219-9, flow-down, cybersecurity, ISR/SSR |
| Negative Event Tracker | `tools/govcon/negative_event_tracker.py` | NDAA events, auto-detection, CPARS impact |
| CDRL Generator | `tools/govcon/cdrl_generator.py` | ICDEV tool dispatch, batch generation |
| SAM Contract Sync | `tools/govcon/sam_contract_sync.py` | SAM.gov Contract Awards API adapter |
| CPMP API | `tools/dashboard/api/cpmp.py` | Flask Blueprint with ~40 endpoints |

## Dashboard Pages

| Route | Template | Content |
|-------|----------|---------|
| `/cpmp` | `cpmp/portfolio.html` | Stat grid, health distribution, contract table, upcoming deliverables |
| `/cpmp/<id>` | `cpmp/detail.html` | 7 tabs: Overview, CLINs, WBS, Deliverables, EVM, Subcontractors, CPARS |
| `/cpmp/<id>/deliverables/<did>` | `cpmp/deliverable_detail.html` | Status pipeline, CDRL generation, submission history |
| `/cpmp/cor` | `cpmp/cor_portal.html` | COR-filtered contract list, read-only |
| `/cpmp/cor/<id>` | `cpmp/cor_detail.html` | COR contract view (no edit, no internal costs) |

## Configuration

`args/govcon_config.yaml` → `cpmp:` section:
- `health_weights`: EVM 0.30, deliverables 0.25, CPARS 0.20, negative_events 0.15, funding 0.10
- `evm`: CPI/SPI yellow (0.95) and red (0.85) thresholds, Monte Carlo iterations (10000)
- `cpars`: rating thresholds, prediction weights, corrective action discount
- `negative_events`: auto-detection rules and thresholds
- `cdrl`: output directory, auto-generate window, tool mapping
- `sam_awards`: API URL, rate limiting, poll interval
- `cor_portal`: enabled flag, hidden fields, session timeout
- `contract_transitions` and `deliverable_transitions`: state machines

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D-CPMP-1 | All tables prefixed `cpmp_` | Namespace isolation |
| D-CPMP-2 | Monte Carlo via stdlib `random` | Air-gap safe (D22) |
| D-CPMP-3 | CPARS prediction is deterministic weighted average | Reproducible (D21) |
| D-CPMP-4 | COR portal is read-only routes on same Flask app | Reuses existing auth |
| D-CPMP-5 | CDRL generator dispatches to existing ICDEV tools | Reuse existing generators |
| D-CPMP-6 | SAM.gov follows sam_scanner pattern | Consistent rate limiting |
| D-CPMP-7 | Negative events append-only | NIST AU-2 (D6) |
| D-CPMP-8 | Health is deterministic weighted average | Configurable via YAML (D26) |
| D-CPMP-9 | Transition bridge is explicit API call | Human confirms contract creation |
| D-CPMP-10 | IDIQ self-reference for task orders | No separate table needed |

## Security & Compliance

- **Append-only tables**: cpmp_status_history, cpmp_negative_events, cpmp_evm_periods, cpmp_cdrl_generations, cpmp_cor_access_log
- **COR access control**: Email-based filtering, GET-only endpoints, internal data sanitization
- **NIST AU-2**: All status changes, COR access, CDRL generations logged
- **State machine enforcement**: Invalid transitions rejected with error
- **CUI markings**: Applied to all generated CDRL artifacts via classification_manager

## Edge Cases

- **IDIQ with Task Orders**: Set `idiq_contract_id` on TO contracts; parent IDIQ aggregates TO metrics
- **FFP vs T&M EVM**: FFP uses milestone-based EV; T&M uses hours-consumed EV
- **Unfunded CLINs**: `remaining_value` = total - funded; yellow health when >20% unfunded
- **COR without email match**: Returns empty contract list (no error)
- **CDRL tool not available**: Records generation as `failed` with error message
- **SAM.gov API down**: Uses cached data, logs warning, retries on next sync

## Verification

1. `python tools/db/init_icdev_db.py` — verify 13 CPMP tables created
2. Create opportunity → win → "Create Contract" → verify contract auto-created
3. Add CLINs, WBS, deliverables → verify CRUD and transitions
4. Record EVM data → verify CPI/SPI/EAC calculations
5. Predict CPARS → verify weighted scoring and NDAA penalties
6. Auto-detect negative events → verify delinquent/cost/quality/flowdown/cyber detection
7. Generate CDRL → verify tool dispatch and audit trail
8. COR portal → verify read-only access and hidden fields
9. Portfolio dashboard → verify health scoring and stat grid
