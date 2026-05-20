"""Seed 143 GovCon + CPMP lifecycle E2E test tasks into kanban_tasks table."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from tools.db.storage import get_connection
from datetime import datetime, timezone

def utcnow():
    return datetime.now(timezone.utc).isoformat()

TASKS = [
    # ── DISC ─────────────────────────────────────────────────────────────
    ("gcpl-disc-01", "gcpl-disc-01: GET /api/govcon/sam/opportunities returns HTTP < 400", "API smoke test — GovCon opportunity list endpoint", None),
    ("gcpl-disc-02", "gcpl-disc-02: GET /api/govcon/pipeline/status returns daemon state JSON", "Verify pipeline daemon status endpoint returns valid JSON", "gcpl-disc-01"),
    ("gcpl-disc-03", "gcpl-disc-03: /govcon HTTP < 400, no server error", "Page smoke — GovCon pipeline hub loads without error", "gcpl-disc-02"),
    ("gcpl-disc-04", "gcpl-disc-04: /govcon — CUI banner 'CUI // SP-CTI' present", "CUI classification banner visible on pipeline hub", "gcpl-disc-03"),
    ("gcpl-disc-05", "gcpl-disc-05: /govcon — SAM.gov scan status card visible", "UI: SAM.gov scan status card renders on pipeline hub", "gcpl-disc-04"),
    ("gcpl-disc-06", "gcpl-disc-06: /govcon — opportunity count stat card visible", "UI: opportunity count stat card present", "gcpl-disc-05"),
    ("gcpl-disc-07", "gcpl-disc-07: /govcon — domain distribution section visible", "UI: domain distribution section renders", "gcpl-disc-06"),
    ("gcpl-disc-08", "gcpl-disc-08: /govcon — pipeline controls or action buttons exist", "UI: action buttons (Run Full Pipeline, Scan, etc.) present", "gcpl-disc-07"),
    ("gcpl-disc-09", "gcpl-disc-09: POST /api/govcon/sam/scan triggers scan (200 or 202)", "API: SAM.gov scan endpoint responds 200/202/503", "gcpl-disc-08"),
    ("gcpl-disc-10", "gcpl-disc-10: POST /api/govcon/sam/import/<id> imports opp into proposals", "API: import opportunity endpoint reachable", "gcpl-disc-09"),
    ("gcpl-disc-11", "gcpl-disc-11: /govcon responsive — 1920x1080 screenshot", "Visual: desktop viewport screenshot captured", "gcpl-disc-10"),
    ("gcpl-disc-12", "gcpl-disc-12: /govcon responsive — 768x1024 screenshot", "Visual: tablet viewport screenshot captured", "gcpl-disc-11"),
    ("gcpl-disc-13", "gcpl-disc-13: /govcon responsive — 375x812 screenshot", "Visual: mobile viewport screenshot captured", "gcpl-disc-12"),

    # ── EXT ──────────────────────────────────────────────────────────────
    ("gcpl-ext-01", "gcpl-ext-01: /govcon/requirements HTTP < 400, no server error", "Page smoke — requirements matrix loads", "gcpl-disc-13"),
    ("gcpl-ext-02", "gcpl-ext-02: /govcon/requirements — CUI banner present", "CUI classification banner on requirements page", "gcpl-ext-01"),
    ("gcpl-ext-03", "gcpl-ext-03: /govcon/requirements — pattern frequency content visible", "UI: shall statement pattern frequency table renders", "gcpl-ext-02"),
    ("gcpl-ext-04", "gcpl-ext-04: /govcon/requirements — domain heatmap section exists", "UI: domain heatmap section present", "gcpl-ext-03"),
    ("gcpl-ext-05", "gcpl-ext-05: /govcon/requirements — statement types breakdown visible", "UI: statement types breakdown (shall/will/must) present", "gcpl-ext-04"),
    ("gcpl-ext-06", "gcpl-ext-06: POST /api/govcon/opportunities/<id>/extract-requirements returns 200", "API: requirement extraction endpoint responds", "gcpl-ext-05"),
    ("gcpl-ext-07", "gcpl-ext-07: GET /api/govcon/requirement-patterns returns pattern list", "API: requirement patterns endpoint returns valid JSON array", "gcpl-ext-06"),
    ("gcpl-ext-08", "gcpl-ext-08: GET /api/govcon/opportunities/<id>/requirements returns shall statements", "API: shall statements for seeded opportunity present", "gcpl-ext-07"),
    ("gcpl-ext-09", "gcpl-ext-09: /govcon/requirements responsive — 3-viewport screenshots", "Visual: 3-viewport screenshots at 1920/768/375 captured", "gcpl-ext-08"),

    # ── MAP ──────────────────────────────────────────────────────────────
    ("gcpl-map-01", "gcpl-map-01: /govcon/capabilities HTTP < 400, no server error", "Page smoke — capabilities library loads", "gcpl-ext-09"),
    ("gcpl-map-02", "gcpl-map-02: /govcon/capabilities — CUI banner present", "CUI classification banner on capabilities page", "gcpl-map-01"),
    ("gcpl-map-03", "gcpl-map-03: /govcon/capabilities — L/M/N coverage breakdown visible", "UI: L/M/N (Large/Medium/None) coverage grades render", "gcpl-map-02"),
    ("gcpl-map-04", "gcpl-map-04: /govcon/capabilities — gap list section visible", "UI: capability gap list section present", "gcpl-map-03"),
    ("gcpl-map-05", "gcpl-map-05: /govcon/capabilities — enhancement recommendations visible", "UI: enhancement recommendations section renders", "gcpl-map-04"),
    ("gcpl-map-06", "gcpl-map-06: POST /api/govcon/opportunities/<id>/map-capabilities returns coverage scores", "API: capability mapping returns L/M/N scores", "gcpl-map-05"),
    ("gcpl-map-07", "gcpl-map-07: GET /api/govcon/opportunities/<id>/coverage returns L/M/N grades", "API: coverage grades endpoint returns valid JSON", "gcpl-map-06"),
    ("gcpl-map-08", "gcpl-map-08: GET /api/govcon/gaps returns gap list with domain and severity", "API: gaps endpoint returns domain+severity attributes", "gcpl-map-07"),
    ("gcpl-map-09", "gcpl-map-09: GET /api/govcon/gaps/recommendations returns actionable recommendations", "API: gap recommendations endpoint returns array", "gcpl-map-08"),
    ("gcpl-map-10", "gcpl-map-10: GET /api/govcon/gaps/heatmap returns heatmap data per domain", "API: heatmap data endpoint returns domain-keyed object", "gcpl-map-09"),
    ("gcpl-map-11", "gcpl-map-11: /govcon/capabilities responsive — 3-viewport screenshots", "Visual: 3-viewport screenshots at 1920/768/375 captured", "gcpl-map-10"),

    # ── DFT ──────────────────────────────────────────────────────────────
    ("gcpl-dft-01", "gcpl-dft-01: GET /api/govcon/opportunities/<id>/bid-recommendation returns score + rationale", "API: bid recommendation endpoint returns numeric score 0.0–1.0", "gcpl-map-11"),
    ("gcpl-dft-02", "gcpl-dft-02: Bid score is numeric 0.0–1.0 with bid/no_bid decision", "Validate bid score type, range, and binary decision field", "gcpl-dft-01"),
    ("gcpl-dft-03", "gcpl-dft-03: POST /api/govcon/opportunities/<id>/auto-compliance returns compliance matrix rows", "API: auto-compliance endpoint returns matrix with rows", "gcpl-dft-02"),
    ("gcpl-dft-04", "gcpl-dft-04: POST /api/govcon/opportunities/<id>/auto-draft returns draft section list", "API: auto-draft endpoint returns section list", "gcpl-dft-03"),
    ("gcpl-dft-05", "gcpl-dft-05: GET /api/govcon/opportunities/<id>/drafts returns draft records", "API: draft list endpoint returns array", "gcpl-dft-04"),
    ("gcpl-dft-06", "gcpl-dft-06: Draft records have status=draft and composite quality score", "Validate draft record schema (status, quality_score)", "gcpl-dft-05"),
    ("gcpl-dft-07", "gcpl-dft-07: PUT /api/govcon/drafts/<id>/approve moves draft to proposal_sections", "API: draft approval endpoint transitions status", "gcpl-dft-06"),
    ("gcpl-dft-08", "gcpl-dft-08: PUT /api/govcon/drafts/<id>/reject records reason, sets status=rejected", "API: draft rejection with reason recorded", "gcpl-dft-07"),
    ("gcpl-dft-09", "gcpl-dft-09: POST /api/govcon/opportunities/<id>/generate-questions returns question list", "API: Q&A generation endpoint returns question array", "gcpl-dft-08"),
    ("gcpl-dft-10", "gcpl-dft-10: GET /api/govcon/opportunities/<id>/questions returns Q&A records", "API: questions list endpoint returns records", "gcpl-dft-09"),
    ("gcpl-dft-11", "gcpl-dft-11: PUT /api/govcon/questions/<id>/status transitions question status", "API: question status transition endpoint responds", "gcpl-dft-10"),
    ("gcpl-dft-12", "gcpl-dft-12: GET /api/govcon/knowledge-base returns reusable content blocks", "API: knowledge base list endpoint returns content blocks", "gcpl-dft-11"),
    ("gcpl-dft-13", "gcpl-dft-13: POST /api/govcon/knowledge-base creates new KB entry", "API: knowledge base creation endpoint adds entry", "gcpl-dft-12"),

    # ── PROP ─────────────────────────────────────────────────────────────
    ("gcpl-prop-01", "gcpl-prop-01: /proposals list HTTP < 400, no server error", "Page smoke — proposals list loads", "gcpl-dft-13"),
    ("gcpl-prop-02", "gcpl-prop-02: /proposals — CUI banner present", "CUI classification banner on proposals list page", "gcpl-prop-01"),
    ("gcpl-prop-03", "gcpl-prop-03: /proposals — shows opportunity table with status badges", "UI: opportunity table with status badges renders", "gcpl-prop-02"),
    ("gcpl-prop-04", "gcpl-prop-04: /proposals/<id> detail HTTP < 400, no server error", "Page smoke — proposal detail page loads", "gcpl-prop-03"),
    ("gcpl-prop-05", "gcpl-prop-05: /proposals/<id> — CUI banner present", "CUI classification banner on proposal detail page", "gcpl-prop-04"),
    ("gcpl-prop-06", "gcpl-prop-06: /proposals/<id> — GovCon Intelligence action bar visible", "UI: Extract/Map/Compliance/Draft/Bid Rec action bar present", "gcpl-prop-05"),
    ("gcpl-prop-07", "gcpl-prop-07: /proposals/<id> — AI Drafts tab shows draft content", "UI: AI Drafts tab renders with draft section content", "gcpl-prop-06"),
    ("gcpl-prop-08", "gcpl-prop-08: /proposals/<id> — Extract Requirements button triggers API (200)", "Interactive: clicking Extract triggers API, returns 200", "gcpl-prop-07"),
    ("gcpl-prop-09", "gcpl-prop-09: /proposals/<id> — Map Capabilities button is present", "UI: Map Capabilities button exists in action bar", "gcpl-prop-08"),
    ("gcpl-prop-10", "gcpl-prop-10: POST /api/govcon/opportunities/<id>/auto-compliance via direct API", "API: auto-compliance direct call returns 200", "gcpl-prop-09"),
    ("gcpl-prop-11", "gcpl-prop-11: GET /api/govcon/opportunities/<id>/bid-recommendation returns score", "API: bid recommendation score present for seeded opp", "gcpl-prop-10"),
    ("gcpl-prop-12", "gcpl-prop-12: /proposal-genesis daemon page HTTP < 400, CUI banner present", "Page smoke + CUI check — genesis daemon page", "gcpl-prop-11"),
    ("gcpl-prop-13", "gcpl-prop-13: /proposals responsive — 3-viewport screenshots", "Visual: proposals list 3-viewport screenshots", "gcpl-prop-12"),
    ("gcpl-prop-14", "gcpl-prop-14: /proposals/<id> responsive — 3-viewport screenshots", "Visual: proposal detail 3-viewport screenshots", "gcpl-prop-13"),

    # ── CSET ─────────────────────────────────────────────────────────────
    ("gcpl-cset-01", "gcpl-cset-01: /cpmp portfolio HTTP < 400, no server error", "Page smoke — CPMP portfolio dashboard loads", "gcpl-prop-14"),
    ("gcpl-cset-02", "gcpl-cset-02: /cpmp — CUI banner present", "CUI classification banner on CPMP portfolio page", "gcpl-cset-01"),
    ("gcpl-cset-03", "gcpl-cset-03: /cpmp — stat grid content visible (contracts, health, deliverables)", "UI: stat grid with contract/health/deliverable counts renders", "gcpl-cset-02"),
    ("gcpl-cset-04", "gcpl-cset-04: /cpmp — health distribution content visible (green/yellow/red)", "UI: health distribution breakdown (green/yellow/red) present", "gcpl-cset-03"),
    ("gcpl-cset-05", "gcpl-cset-05: /cpmp — contract table visible with status badges", "UI: contract table with status badges renders", "gcpl-cset-04"),
    ("gcpl-cset-06", "gcpl-cset-06: /cpmp — upcoming deliverables section visible", "UI: upcoming deliverables section present on portfolio", "gcpl-cset-05"),
    ("gcpl-cset-07", "gcpl-cset-07: GET /api/cpmp/portfolio returns summary JSON with health stats", "API: portfolio summary endpoint returns health stats JSON", "gcpl-cset-06"),
    ("gcpl-cset-08", "gcpl-cset-08: POST /api/cpmp/from-opportunity/<id> creates contract (D-CPMP-9)", "API: contract creation from won opportunity (D-CPMP-9 explicit gate)", "gcpl-cset-07"),
    ("gcpl-cset-09", "gcpl-cset-09: GET /api/cpmp/contracts returns contract list", "API: contracts list endpoint returns array", "gcpl-cset-08"),
    ("gcpl-cset-10", "gcpl-cset-10: PUT /api/cpmp/contracts/<id> updates contract COR info and POP dates", "API: contract update (cor_email, cor_name, pop_start, pop_end) responds", "gcpl-cset-09"),
    ("gcpl-cset-11", "gcpl-cset-11: PUT /api/cpmp/contracts/<id>/status transitions draft→active (state machine)", "API: contract state machine draft→active transition", "gcpl-cset-10"),
    ("gcpl-cset-12", "gcpl-cset-12: Invalid contract state transition returns 400 with error message", "API: invalid state 'invalid_state_xyz' returns 400/422", "gcpl-cset-11"),
    ("gcpl-cset-13", "gcpl-cset-13: POST /api/cpmp/contracts/<id>/clins creates labor CLIN $250K", "API: CLIN creation with labor type and $250K total value", "gcpl-cset-12"),
    ("gcpl-cset-14", "gcpl-cset-14: PUT /api/cpmp/clins/<id> updates CLIN billed value", "API: CLIN billed value update to $50K responds", "gcpl-cset-13"),
    ("gcpl-cset-15", "gcpl-cset-15: GET /api/cpmp/contracts/<id>/clins returns CLIN list", "API: CLIN list endpoint returns array", "gcpl-cset-14"),
    ("gcpl-cset-16", "gcpl-cset-16: POST /api/cpmp/contracts/<id>/wbs creates WBS element with BAC and dates", "API: WBS element creation with BAC=$500K and planned dates", "gcpl-cset-15"),
    ("gcpl-cset-17", "gcpl-cset-17: GET /api/cpmp/contracts/<id>/wbs?mode=tree returns hierarchical WBS", "API: WBS tree mode returns hierarchical structure", "gcpl-cset-16"),
    ("gcpl-cset-18", "gcpl-cset-18: PUT /api/cpmp/wbs/<id> updates WBS percent complete to 25%", "API: WBS percent_complete update responds", "gcpl-cset-17"),
    ("gcpl-cset-19", "gcpl-cset-19: POST /api/cpmp/contracts/<id>/deliverables creates CDRL with due date", "API: deliverable (CDRL A001, DI-MGMT-81466) creation with due date", "gcpl-cset-18"),
    ("gcpl-cset-20", "gcpl-cset-20: GET /api/cpmp/contracts/<id>/deliverables returns deliverable list", "API: deliverables list endpoint returns array", "gcpl-cset-19"),
    ("gcpl-cset-21", "gcpl-cset-21: PUT /api/cpmp/deliverables/<id>/status transitions to in_progress", "API: deliverable state machine pending→in_progress", "gcpl-cset-20"),
    ("gcpl-cset-22", "gcpl-cset-22: /cpmp/<id> detail HTTP < 400, CUI banner present", "Page smoke + CUI — contract detail page loads", "gcpl-cset-21"),
    ("gcpl-cset-23", "gcpl-cset-23: /cpmp/<id> — 7 tabs visible (Overview, CLINs, WBS, Deliverables, EVM, Subcontractors, CPARS)", "UI: all 7 contract detail tabs render", "gcpl-cset-22"),
    ("gcpl-cset-24", "gcpl-cset-24: /cpmp responsive — 3-viewport screenshots", "Visual: CPMP portfolio 3-viewport screenshots", "gcpl-cset-23"),

    # ── EVM ──────────────────────────────────────────────────────────────
    ("gcpl-evm-01", "gcpl-evm-01: POST /api/cpmp/contracts/<id>/evm records monthly snapshot (PV/EV/AC)", "API: EVM period record (PV=100K, EV=95K, AC=97K) created", "gcpl-cset-24"),
    ("gcpl-evm-02", "gcpl-evm-02: GET /api/cpmp/contracts/<id>/evm returns aggregate ANSI/EIA-748 metrics", "API: aggregate EVM metrics endpoint returns JSON", "gcpl-evm-01"),
    ("gcpl-evm-03", "gcpl-evm-03: CPI = EV/AC — verify calculation accuracy", "Validate CPI is numeric and positive (ANSI/EIA-748)", "gcpl-evm-02"),
    ("gcpl-evm-04", "gcpl-evm-04: SPI = EV/PV — verify calculation accuracy", "Validate SPI is numeric and positive (ANSI/EIA-748)", "gcpl-evm-03"),
    ("gcpl-evm-05", "gcpl-evm-05: EAC is present and > 0 when CPI < 1.0 (cost overrun forecast)", "Validate EAC is a positive number in aggregate response", "gcpl-evm-04"),
    ("gcpl-evm-06", "gcpl-evm-06: GET /api/cpmp/contracts/<id>/evm/forecast returns P10/P50/P90 (Monte Carlo)", "API: Monte Carlo forecast endpoint returns P10/P50/P90", "gcpl-evm-05"),
    ("gcpl-evm-07", "gcpl-evm-07: Monte Carlo P10 <= P50 <= P90 (valid PERT distribution ordering)", "Validate PERT distribution ordering (D-CPMP-2, stdlib random)", "gcpl-evm-06"),
    ("gcpl-evm-08", "gcpl-evm-08: GET /api/cpmp/contracts/<id>/evm/scurve returns ordered date/PV/EV/AC arrays", "API: S-curve data arrays endpoint returns JSON", "gcpl-evm-07"),
    ("gcpl-evm-09", "gcpl-evm-09: GET /api/cpmp/contracts/<id>/evm/ipmdar returns IPMDAR-format data", "API: IPMDAR-format endpoint returns structured JSON", "gcpl-evm-08"),
    ("gcpl-evm-10", "gcpl-evm-10: GET /api/cpmp/contracts/<id>/evm/periods returns period history list", "API: EVM period history list returns array", "gcpl-evm-09"),
    ("gcpl-evm-11", "gcpl-evm-11: /cpmp/<id> EVM tab — CPI/SPI values rendered in page", "UI: EVM tab shows CPI/SPI/EAC/Earned Value content", "gcpl-evm-10"),
    ("gcpl-evm-12", "gcpl-evm-12: /cpmp/<id> EVM tab — S-curve or chart section visible", "UI: S-curve or chart visualization renders on EVM tab", "gcpl-evm-11"),

    # ── COR (parallel with EVM, both depend on cset-24) ──────────────────
    ("gcpl-cor-01", "gcpl-cor-01: /cpmp/cor HTTP < 400, no server error", "Page smoke — COR government read-only portal loads", "gcpl-cset-24"),
    ("gcpl-cor-02", "gcpl-cor-02: /cpmp/cor — CUI banner present", "CUI classification banner on COR portal page", "gcpl-cor-01"),
    ("gcpl-cor-03", "gcpl-cor-03: /cpmp/cor — 'Government Read-Only View' or COR badge visible", "UI: government/read-only/COR badge visible on portal", "gcpl-cor-02"),
    ("gcpl-cor-04", "gcpl-cor-04: /cpmp/cor — blue accent or distinct COR visual styling present", "UI: COR-specific blue accent styling distinguishes from internal view", "gcpl-cor-03"),
    ("gcpl-cor-05", "gcpl-cor-05: GET /api/cpmp/cor/contracts returns only COR-email-matched contracts", "API: COR contracts filtered by email header, returns array", "gcpl-cor-04"),
    ("gcpl-cor-06", "gcpl-cor-06: GET /api/cpmp/cor/contracts/<id> hides internal_cost_details from COR response", "Security: internal_cost_detail NOT in COR API response body", "gcpl-cor-05"),
    ("gcpl-cor-07", "gcpl-cor-07: GET /api/cpmp/cor/contracts/<id> hides subcontractor_pricing from COR response", "Security: subcontractor_pric/rate/cost NOT in COR API response", "gcpl-cor-06"),
    ("gcpl-cor-08", "gcpl-cor-08: GET /api/cpmp/cor/contracts/<id>/deliverables returns status and dates", "API: COR deliverables endpoint returns status/dates", "gcpl-cor-07"),
    ("gcpl-cor-09", "gcpl-cor-09: GET /api/cpmp/cor/contracts/<id>/evm returns CPI/SPI without AC breakdown", "Security: COR EVM response excludes subcontractor_cost and internal_cost_breakdown", "gcpl-cor-08"),
    ("gcpl-cor-10", "gcpl-cor-10: GET /api/cpmp/cor/contracts/<id>/cpars returns CPARS ratings", "API: COR CPARS ratings endpoint returns JSON", "gcpl-cor-09"),
    ("gcpl-cor-11", "gcpl-cor-11: /cpmp/cor/<id> HTTP < 400, CUI banner present, no edit controls", "Page: COR detail is read-only (≤2 edit/delete buttons)", "gcpl-cor-10"),
    ("gcpl-cor-12", "gcpl-cor-12: COR access logged to cpmp_cor_access_log (NIST AU-2)", "Audit: COR access generates audit log entry (NIST AU-2)", "gcpl-cor-11"),
    ("gcpl-cor-13", "gcpl-cor-13: Non-matched COR email returns empty array, not an error", "API: unmatched email → 200 with empty contracts array or 404", "gcpl-cor-12"),
    ("gcpl-cor-14", "gcpl-cor-14: /cpmp/cor responsive — 3-viewport screenshots", "Visual: COR portal 3-viewport screenshots at 1920/768/375", "gcpl-cor-13"),

    # ── PERF ─────────────────────────────────────────────────────────────
    ("gcpl-perf-01", "gcpl-perf-01: POST /api/cpmp/contracts/<id>/subcontractors adds sub with FAR 52.219-9 fields", "API: subcontractor with cmmc_level, cybersecurity_compliant, flowdown_verified created", "gcpl-evm-12"),
    ("gcpl-perf-02", "gcpl-perf-02: GET /api/cpmp/contracts/<id>/subcontractors returns subcontractor list", "API: subcontractor list endpoint returns array", "gcpl-perf-01"),
    ("gcpl-perf-03", "gcpl-perf-03: GET /api/cpmp/contracts/<id>/subcontractors/noncompliance detects flowdown gaps", "API: flowdown noncompliance detection endpoint returns JSON", "gcpl-perf-02"),
    ("gcpl-perf-04", "gcpl-perf-04: POST /api/cpmp/contracts/<id>/small-business records small business plan", "API: FAR 52.219-9 small business plan (sb/sdb/wosb/hubzone/sdvosb goals) created", "gcpl-perf-03"),
    ("gcpl-perf-05", "gcpl-perf-05: GET /api/cpmp/contracts/<id>/sb-compliance returns FAR 52.219-9 compliance status", "API: small business compliance status endpoint returns JSON", "gcpl-perf-04"),
    ("gcpl-perf-06", "gcpl-perf-06: GET /api/cpmp/contracts/<id>/cpars/predict returns weighted score + rating", "API: CPARS prediction (D-CPMP-3 deterministic weighted avg) returns score+rating", "gcpl-perf-05"),
    ("gcpl-perf-07", "gcpl-perf-07: CPARS weights sum to 1.0 (quality+schedule+cost+mgmt+sb=1.0)", "Validate CPARS dimension weights: 0.25+0.25+0.20+0.15+0.15=1.0", "gcpl-perf-06"),
    ("gcpl-perf-08", "gcpl-perf-08: CPARS rating thresholds correct (exceptional>=0.90, very_good>=0.80, satisfactory>=0.65)", "Validate rating enum values against NDAA thresholds", "gcpl-perf-07"),
    ("gcpl-perf-09", "gcpl-perf-09: POST /api/cpmp/contracts/<id>/cpars records assessment", "API: CPARS assessment record created (quality/schedule/cost/mgmt/sb scores)", "gcpl-perf-08"),
    ("gcpl-perf-10", "gcpl-perf-10: GET /api/cpmp/contracts/<id>/cpars/trend returns trend data array", "API: CPARS trend endpoint returns array", "gcpl-perf-09"),
    ("gcpl-perf-11", "gcpl-perf-11: POST /api/cpmp/contracts/<id>/negative-events records delinquent_delivery", "API: negative event (delinquent_delivery, NDAA) created (append-only)", "gcpl-perf-10"),
    ("gcpl-perf-12", "gcpl-perf-12: GET /api/cpmp/contracts/<id>/negative-events returns event list (append-only)", "API: negative events list returns array (NIST AU, append-only)", "gcpl-perf-11"),
    ("gcpl-perf-13", "gcpl-perf-13: POST /api/cpmp/contracts/<id>/negative-events/auto-detect detects overdue deliverables", "API: auto-detect overdue deliverables and create negative events", "gcpl-perf-12"),
    ("gcpl-perf-14", "gcpl-perf-14: GET /api/cpmp/contracts/<id>/negative-events/ndaa-thresholds returns threshold status", "API: NDAA threshold status endpoint returns JSON", "gcpl-perf-13"),
    ("gcpl-perf-15", "gcpl-perf-15: GET /api/cpmp/contracts/<id>/health returns composite score 0.0–1.0", "API: health score endpoint returns numeric 0.0–1.0", "gcpl-perf-14"),
    ("gcpl-perf-16", "gcpl-perf-16: Health weights sum to 1.0 (EVM 0.30+deliverables 0.25+CPARS 0.20+neg 0.15+funding 0.10)", "Validate health dimension weights sum to 1.0", "gcpl-perf-15"),
    ("gcpl-perf-17", "gcpl-perf-17: Health color is green/yellow/red (score >=0.75 / >=0.50 / <0.50)", "Validate health color thresholds: green>=0.75, yellow>=0.50, red<0.50", "gcpl-perf-16"),
    ("gcpl-perf-18", "gcpl-perf-18: Health response includes recommendations array", "Validate health response has recommendations array field", "gcpl-perf-17"),
    ("gcpl-perf-19", "gcpl-perf-19: /cpmp/<id> CPARS tab — predicted rating badge rendered", "UI: CPARS tab shows exceptional/very_good/satisfactory rating badge", "gcpl-perf-18"),
    ("gcpl-perf-20", "gcpl-perf-20: /cpmp/<id> Subcontractors tab — compliance badges rendered", "UI: Subcontractors tab shows FAR/compliance/small business badges", "gcpl-perf-19"),

    # ── CDRL ─────────────────────────────────────────────────────────────
    ("gcpl-cdrl-01", "gcpl-cdrl-01: POST /api/cpmp/contracts/<id>/generate-cdrl/<did> dispatches to ICDEV tool", "API: CDRL auto-generation endpoint dispatches (200/201/400/404/503 acceptable)", "gcpl-perf-20"),
    ("gcpl-cdrl-02", "gcpl-cdrl-02: GET /api/cpmp/cdrl-generations shows audit trail after generation", "API: CDRL generation audit trail endpoint returns JSON", "gcpl-cdrl-01"),
    ("gcpl-cdrl-03", "gcpl-cdrl-03: CDRL generation records have contract_id, deliverable_id, tool_used, status", "Validate CDRL generation record schema fields present", "gcpl-cdrl-02"),
    ("gcpl-cdrl-04", "gcpl-cdrl-04: POST /api/cpmp/contracts/<id>/generate-due batch-generates all due CDRLs", "API: batch CDRL generation for all due deliverables", "gcpl-cdrl-03"),
    ("gcpl-cdrl-05", "gcpl-cdrl-05: GET /api/cpmp/cdrl-generations returns full audit history (append-only)", "API: full CDRL generation audit history (append-only, NIST AU)", "gcpl-cdrl-04"),
    ("gcpl-cdrl-06", "gcpl-cdrl-06: /cpmp/<id>/deliverables/<did> HTTP < 400, CUI banner present", "Page smoke + CUI — deliverable detail page loads", "gcpl-cdrl-05"),
    ("gcpl-cdrl-07", "gcpl-cdrl-07: Deliverable detail shows status pipeline visualization", "UI: deliverable status pipeline (pending/in_progress/submitted/accepted) renders", "gcpl-cdrl-06"),
    ("gcpl-cdrl-08", "gcpl-cdrl-08: Deliverable detail shows CDRL generation button", "UI: Generate/CDRL/Create button present on deliverable detail", "gcpl-cdrl-07"),
    ("gcpl-cdrl-09", "gcpl-cdrl-09: Deliverable detail shows generation history table or section", "UI: generation history/audit section present on deliverable detail", "gcpl-cdrl-08"),
    ("gcpl-cdrl-10", "gcpl-cdrl-10: POST /api/cpmp/sam/sync-awards returns 200 (graceful without SAM API key)", "API: SAM.gov awards sync graceful degradation (D366 — 503 OK without key)", "gcpl-cdrl-09"),
    ("gcpl-cdrl-11", "gcpl-cdrl-11: GET /api/cpmp/sam/awards returns cached awards array", "API: cached SAM.gov awards endpoint returns JSON", "gcpl-cdrl-10"),
    ("gcpl-cdrl-12", "gcpl-cdrl-12: GET /api/cpmp/sam/awards/search returns search results", "API: SAM.gov awards search endpoint returns JSON", "gcpl-cdrl-11"),
    ("gcpl-cdrl-13", "gcpl-cdrl-13: POST /api/cpmp/sam/link/<award_id> links SAM award to contract", "API: link SAM award to contract (404 acceptable — no matching award in test env)", "gcpl-cdrl-12"),
]

def seed():
    conn = get_connection()
    now = utcnow()
    inserted = 0
    skipped = 0

    for task_id, title, description, dep_id in TASKS:
        existing = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        # Validate dep exists if specified
        if dep_id:
            dep_exists = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = ?", (dep_id,)
            ).fetchone()
            if not dep_exists:
                print(f"  WARNING: dep {dep_id} for {task_id} not yet in DB — inserting without dep link")
                dep_id = None

        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, status, "
            "executor_type, depends_on_task_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                task_id, title, description,
                "test",       # task_type
                "high",       # priority (critical → high)
                "backlog",    # status
                "claude_cli", # executor_type
                dep_id,       # depends_on_task_id
                now, now,
            ),
        )

        # Also write to junction table for multi-dep support
        if dep_id:
            conn.execute(
                "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) "
                "VALUES (?, ?, ?) ON CONFLICT (task_id, depends_on_id) DO NOTHING",
                (task_id, dep_id, now),
            )

        inserted += 1

    conn.commit()
    print(f"Seeded {inserted} tasks ({skipped} already existed) into kanban_tasks.")
    return inserted, skipped

if __name__ == "__main__":
    inserted, skipped = seed()
    if inserted == 0 and skipped > 0:
        print("All tasks already present — no duplicates created.")
