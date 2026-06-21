#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed WriteGuard Integration + Info Hints kanban tasks.

Decomposes the WriteGuard integration plan (15 atomic tasks) into kanban_tasks
rows with a proper depends_on DAG. The Kanban scheduler will pick these up in
dep order, run each in its own worktree, and merge sequentially to main.

Two epics:
  1. hints — Contextual help panels on /proposals, /govcon, /cpmp, /cpmp/deliverables
  2. wg    — WriteGuard inline badges, score columns, and text-area hooks

Naming: prefix=wgint-, epic=hints|wg|vv, num=01..05 / 06..15
Per args/projects.yaml rule: <prefix><epic_key>-<NN>
  Examples: wgint-hints-01, wgint-wg-06, wgint-vv-14

Cross-file serialization (so worktree merges resolve cleanly):
  proposals/list.html touched by hints-01, wg-06, wg-07
     -> chain  hints-01 -> wg-06 -> wg-07
  govcon/pipeline.html touched by hints-02, wg-08, wg-09
     -> chain  hints-02 -> wg-08 -> wg-09
  cpmp/detail.html touched by hints-03, wg-10, wg-11
     -> chain  hints-03 -> wg-10 -> wg-11
  cpmp/deliverable_center.html touched by hints-04, wg-12
     -> chain  hints-04 -> wg-12
  cpmp/deliverable_detail.html touched by hints-05, wg-13
     -> chain  hints-05 -> wg-13
  icdev/ mirrors touched by vv-14, vv-15
     -> chain  wg-07 -> vv-14 -> vv-15

Run: python tools/kanban/seed_wgint.py [--dry-run]

Idempotent: skips tasks that already exist.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402

_NOW = datetime.now(timezone.utc).isoformat()
PROJECT_PREFIX = "wgint-"
PROJECT_KEY = "wgint"
PROJECT_NAME = "WriteGuard Integration + Info Hints"
PROJECT_DESCRIPTION = (
    "Two-epic initiative to (1) add contextual sliding help panels to every "
    "tab and section of /proposals, /govcon, /cpmp, and /cpmp/deliverables, "
    "and (2) wire WriteGuard inline quality badges into every text-editing "
    "surface on those pages. Reuses existing cpmp/detail.html help-panel "
    "pattern, writeguard-inline.js, and wg_analysis_results DB table. "
    "No new schemas, no new reflexes, no new LLM calls. "
    "Reference plan: C:/Users/schuo/.claude/plans/"
    "i-use-http-localhost-5050-writeguard-to-drifting-finch.md"
)


def _conn():
    return get_connection()


TASKS: list[dict] = [
    # ══════════════════════════════════════════════════════════════════
    # EPIC: hints — Contextual Help Panels
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "wgint-hints-01",
        "title": "Hints: proposals/list.html — full help panel + section ? buttons",
        "description": (
            "File: tools/dashboard/templates/proposals/list.html (MODIFY)\n\n"
            "Goal: add a right-side sliding help panel with rich contextual guidance "
            "for every section on /proposals.\n\n"
            "PATTERN TO COPY EXACTLY from tools/dashboard/templates/cpmp/detail.html "
            "(lines 38-67 for CSS, lines 1121-1148 for JS showTabHelp function):\n"
            "  - Fixed panel: position:fixed; top:0; right:-420px; width:400px; "
            "height:100vh; transition:right 0.28s ease; z-index:1400\n"
            "  - Panel id: #proposals-help-panel\n"
            "  - Close button + Escape key listener\n\n"
            "RENAME function to showSectionHelp(key) (not showTabHelp, no tabs here).\n\n"
            "JS object _SECTION_HELP with 6 keys:\n\n"
            "  pipeline_stats: {\n"
            "    title: 'Pipeline Stats',\n"
            "    html: '<h4>Total Opportunities</h4>All active bids tracked in the system — "
            "any status. <h4>In Writing</h4>Proposals currently being authored (status=writing). "
            "<h4>In Review</h4>Drafts under internal review before submission. "
            "<h4>Submitted</h4>Proposals handed off to the government. "
            "<h4>Days to Deadline</h4>Calendar days until the soonest due date among active proposals. "
            "Red = 7 or fewer days remaining.'\n"
            "  },\n\n"
            "  bubble_chart: {\n"
            "    title: 'Capture Portfolio — pWin x Weighted Value',\n"
            "    html: '<h4>What This Chart Shows</h4>Each bubble = one active opportunity. "
            "<div class=\"formula\">X-axis = Probability of Win (pWin %)</div>"
            "<div class=\"formula\">Y-axis = Weighted Contract Value ($)</div>"
            "<div class=\"formula\">Bubble size = Contract ceiling (total potential value)</div>"
            "<h4>Colors by Stage</h4>Blue=Discover, Orange=Extract, Green=Map, Purple=Draft, Teal=Submitted."
            "<h4>How to Use</h4>Top-right quadrant (high pWin, high value) = highest-priority pursuits. "
            "Bottom-left = low-ROI bids to reconsider. Large bubbles on the left = high-value but low-confidence.'\n"
            "  },\n\n"
            "  pipeline_value: {\n"
            "    title: 'Weighted Pipeline Value',\n"
            "    html: '<h4>Formula</h4><div class=\"formula\">Weighted Value = pWin% x Contract Ceiling</div>"
            "A $10M contract with 60% pWin contributes $6M to weighted pipeline. "
            "<h4>Why It Matters</h4>Raw pipeline (sum of ceilings) overstates likely revenue. "
            "Weighted pipeline is the BD team\\'s expected-value forecast. "
            "<h4>Scored / Total</h4>How many opportunities have a computed pWin model vs. manual estimate. "
            "Low scored ratio = less reliable forecast.'\n"
            "  },\n\n"
            "  opportunity_table: {\n"
            "    title: 'Opportunity Table',\n"
            "    html: '<h4>Status Lifecycle</h4>"
            "<table><tr><th>Status</th><th>Meaning</th></tr>"
            "<tr><td>intake</td><td>Initial RFI review, qualification pending</td></tr>"
            "<tr><td>bid_no_bid</td><td>Decision gate — go or no-go</td></tr>"
            "<tr><td>go</td><td>Approved to pursue — capture in progress</td></tr>"
            "<tr><td>writing</td><td>Proposal being authored</td></tr>"
            "<tr><td>review</td><td>Internal Red Team / Gold Team review</td></tr>"
            "<tr><td>final</td><td>Final production and formatting</td></tr>"
            "<tr><td>submitted</td><td>Submitted to government</td></tr></table>"
            "<h4>Days Column</h4>Red = ≤7 days, Yellow = ≤14 days, colored = ok. "
            "<h4>pWin Bar</h4>Green ≥70%, Yellow ≥40%, Red <40%. "
            "Click the ℹ icon to see factor breakdown.'\n"
            "  },\n\n"
            "  pwin_scoring: {\n"
            "    title: 'pWin Scoring — 10-Factor Model',\n"
            "    html: '<h4>Scoring Guide</h4>Rate each factor 0–10 "
            "(0=very unfavorable, 5=neutral, 10=highly favorable). "
            "<h4>Factors</h4>"
            "<table><tr><th>Factor</th><th>What to assess</th></tr>"
            "<tr><td>Incumbent</td><td>Are we the current contract holder?</td></tr>"
            "<tr><td>Relationships</td><td>Strength of agency relationships</td></tr>"
            "<tr><td>Technical Fit</td><td>How well our capabilities match the requirement</td></tr>"
            "<tr><td>Past Performance</td><td>Relevant CPARS + similar work</td></tr>"
            "<tr><td>Price Competitiveness</td><td>Estimated cost vs. competitors</td></tr>"
            "<tr><td>Teaming</td><td>Quality of team and small-business alignment</td></tr>"
            "<tr><td>NAICS / Set-Aside</td><td>Eligibility for the procurement vehicle</td></tr>"
            "<tr><td>PWS Clarity</td><td>How clearly the SOW is defined</td></tr>"
            "<tr><td>Timeline Realism</td><td>Whether the schedule is achievable</td></tr>"
            "<tr><td>Competitive Intel</td><td>Known competition strength</td></tr></table>'\n"
            "  },\n\n"
            "  stage_lifecycle: {\n"
            "    title: 'Stage Lifecycle',\n"
            "    html: '<h4>DISCOVER (🔍)</h4>SAM.gov opportunity scanned and qualified. "
            "Team is reviewing the solicitation and building the capture plan. "
            "<h4>EXTRACT (⚙️)</h4>Requirements and evaluation criteria extracted. "
            "Compliance matrix being built. "
            "<h4>MAP (🗺️)</h4>Capabilities mapped to requirements. "
            "Win themes identified, teaming partners confirmed. "
            "<h4>DRAFT (✍️)</h4>Proposal sections being written. "
            "Two-tier LLM drafts (speed pass → quality pass) available on section detail pages. "
            "<h4>SUBMITTED (✅)</h4>Proposal delivered to the government. "
            "Awaiting evaluation and source selection.'\n"
            "  }\n\n"
            "Add ? buttons (small blue circle, same style as cpmp/detail.html tab-help-btn):\n"
            "  - Next to the page title h1\n"
            "  - Next to the stat grid section header\n"
            "  - Next to the bubble chart section header (if present)\n"
            "  - In the table <thead>, next to the 'pWin' <th> and 'Status' <th>\n\n"
            "DO NOT touch tools/dashboard/app.py or tools/dashboard/api/__init__.py.\n"
            "DO NOT mirror to icdev/ in this task — vv-14 handles that."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "wgint-hints-02",
        "title": "Hints: govcon/pipeline.html — full help panel + section ? buttons",
        "description": (
            "File: tools/dashboard/templates/govcon/pipeline.html (MODIFY)\n\n"
            "Goal: add a right-side sliding help panel to /govcon (the GovCon Intelligence "
            "Pipeline page) explaining every section to new users.\n\n"
            "PATTERN: copy exactly from cpmp/detail.html help panel (same CSS, same JS structure).\n"
            "Panel id: #govcon-help-panel\n"
            "Function name: showSectionHelp(key)\n\n"
            "JS object _SECTION_HELP with 7 keys:\n\n"
            "  pipeline_overview: explain the 4-stage automation pipeline end-to-end; "
            "when to run full pipeline vs individual stages; expected runtime per stage.\n\n"
            "  stat_grid: explain all 8 stat cards:\n"
            "    Opportunities = SAM.gov cached opportunity count\n"
            "    Requirements = 'shall/must/will' clauses extracted from solicitations\n"
            "    Patterns = recurring requirement clusters detected across multiple solicitations\n"
            "    Mapped = opportunities with capabilities matched to requirements\n"
            "    Drafts = LLM-generated proposal section drafts\n"
            "    Awards = contracts awarded to this organization\n"
            "    KB Blocks = knowledge base chunks used for draft generation\n"
            "    Linked = SAM.gov opportunities tied to active proposals\n\n"
            "  pipeline_stages: rich explanation of each stage:\n"
            "    DISCOVER: NAICS + keyword scan on SAM.gov API; results cached in "
            "govcon_opportunities table; click to run.\n"
            "    EXTRACT: NLP parse of solicitation documents for 'shall/must/will' language; "
            "results stored in govcon_requirements; clusters into govcon_patterns.\n"
            "    MAP: vector-similarity matching of extracted requirements to ICDEV capability "
            "statements in the knowledge base; gap analysis produced.\n"
            "    DRAFT: two-tier LLM generation — speed pass (qwen3) then quality pass (Claude); "
            "drafts stored and linked to proposal sections.\n\n"
            "  pipeline_value: formula Weighted = pWin% x contract ceiling; "
            "why weighted > raw pipeline; 'Scored/Total' = opps with computed pWin model.\n\n"
            "  proposal_pipeline: column-by-column guide (Stage icon, pWin bar thresholds "
            "green≥70%/yellow≥40%/red<40%, Wtd Value = pWin x ceiling, Days = calendar "
            "days to proposal due date, ℹ = factor breakdown popup).\n\n"
            "  domain_distribution: how requirement domains are detected (NLP keyword "
            "clustering on requirement text); common domains (cybersecurity, cloud, "
            "software development, training, logistics); why frequency matters for "
            "capability gap analysis.\n\n"
            "  sam_opportunities: column guide for the Recent SAM.gov table:\n"
            "    'Import' = create a linked opportunity entry in the proposals system\n"
            "    'Extract' = run NLP on this solicitation's requirements immediately\n"
            "    'Linked ✓' = already tied to an active proposal\n"
            "    NAICS code = North American Industry Classification System\n"
            "    Notice Type = Presolicitation/RFP/Sources Sought/IDIQ\n\n"
            "Add ? buttons (same style as cpmp tab-help-btn) next to:\n"
            "  - The hero header h1 'GovCon Intelligence Pipeline'\n"
            "  - Each .gc-section-hdr h2 (Pipeline Flow, Active Proposal Pipeline, "
            "Requirement Domain Distribution, Recent SAM.gov Opportunities)\n\n"
            "DO NOT touch tools/dashboard/app.py or tools/dashboard/api/__init__.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "wgint-hints-03",
        "title": "Hints: cpmp/detail.html — AI Advisor help entry + MOD field tooltips",
        "description": (
            "File: tools/dashboard/templates/cpmp/detail.html (MODIFY, minor)\n\n"
            "The CPMP detail page already has a comprehensive sliding help panel "
            "(_TAB_HELP JS object, 10 tab entries). This task adds minor enhancements:\n\n"
            "1. Add 'ai_advisor' entry to the existing _TAB_HELP object:\n"
            "   ai_advisor: {\n"
            "     title: 'AI PMO Advisor',\n"
            "     html: '<h4>Recommendations</h4>ML-ranked action list based on contract "
            "health, EVM trends, and risk register. Actions are prioritized by urgency "
            "and potential impact on CPARS score. <h4>Predictive Award Fee</h4>Forward-looking "
            "CPARS score prediction using current CPI, SPI, deliverable status, and "
            "NDAA penalty accumulation. Updated each time you visit this page. "
            "<h4>Auto-Detect Issues</h4>Autonomous scan across all 10 data layers "
            "(Overview, CLINs, WBS, Deliverables, EVM, Schedule, Subcontractors, "
            "CPARS, Modifications, Risks). Surfaces anomalies: cost overruns, "
            "schedule slips, overdue CDRLs, CMMC compliance gaps, risk items "
            "without mitigations.'\n"
            "   }\n\n"
            "   Wire it: add a ? button next to the 'AI PMO Advisor' label bar "
            "that calls showTabHelp('ai_advisor').\n\n"
            "2. Add title= tooltip attributes to the Modifications modal form fields:\n"
            "   - Mod Type <select>: title='Admin=no cost change; Funding=obligation of "
            "additional money; Scope=change in work requirements; POP=period of performance extension'\n"
            "   - #mod-desc <textarea>: title='Describe what changes and why. "
            "This text becomes part of the official contract record.'\n"
            "   - Value Delta <input>: title='Change in contract value in dollars. "
            "Positive=increase, Negative=deobligation, Zero=admin change with no cost impact.'\n\n"
            "DO NOT restructure or rewrite the existing help panel. "
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "wgint-hints-04",
        "title": "Hints: cpmp/deliverable_center.html — full help panel + section ? buttons",
        "description": (
            "File: tools/dashboard/templates/cpmp/deliverable_center.html (MODIFY)\n\n"
            "Goal: add a right-side sliding help panel to /cpmp/deliverables explaining "
            "stat cards, filters, table columns, and bulk actions.\n\n"
            "PATTERN: copy from cpmp/detail.html help panel.\n"
            "Panel id: #deliv-help-panel\n"
            "Function name: showSectionHelp(key)\n\n"
            "JS object _SECTION_HELP with 4 keys:\n\n"
            "  stat_cards: {\n"
            "    title: 'Deliverable Summary Stats',\n"
            "    html: '<h4>Total CDRLs</h4>All Contract Data Requirements List items "
            "across every active contract. <h4>Overdue</h4>Past due_date with no "
            "'accepted' status — each day overdue increases CPARS penalty risk. "
            "Address these immediately. <h4>Due in ≤7 Days</h4>High-urgency window: "
            "generate or finalize now to allow review time. <h4>Due in ≤14 Days</h4>"
            "Planning horizon — schedule generation and WriteGuard validation this week. "
            "<h4>Auto-Generated %</h4>Percentage of CDRLs produced by the AI CDRL "
            "drafter. High % = good automation coverage; ensure each is reviewed before submission.'\n"
            "  },\n\n"
            "  filter_bar: {\n"
            "    title: 'Filter Controls',\n"
            "    html: '<h4>Contract Filter</h4>Scope the view to a single contract — "
            "useful when reviewing one program at a time. <h4>Status Filter</h4>"
            "Lifecycle stage: not_started / in_progress / submitted / government_review "
            "/ accepted / rejected. <h4>Type Filter</h4>CDRL category: technical "
            "(software/hardware deliverables), management (plans, reports, schedules), "
            "data (datasets, models, documentation). <h4>DID Filter</h4>Data Item "
            "Description number — the MIL-STD-963 format specification the deliverable "
            "must follow (e.g. DI-MGMT-80004A for a CDRL itself).'\n"
            "  },\n\n"
            "  deliverables_table: {\n"
            "    title: 'Deliverables Table',\n"
            "    html: '<h4>CDRL #</h4>Sequential deliverable identifier within the "
            "contract (e.g., CDRL-001). Assigned at contract award. "
            "<h4>DID #</h4>Data Item Description number — references MIL-STD-963 "
            "and specifies the exact format, content, and level of detail required. "
            "<h4>Urgency Colors</h4>"
            "<table><tr><th>Border Color</th><th>Meaning</th></tr>"
            "<tr><td style=\"color:#dc3545\">Red</td><td>Overdue — past due date</td></tr>"
            "<tr><td style=\"color:#e8590c\">Orange</td><td>Due in ≤7 days</td></tr>"
            "<tr><td style=\"color:#ffc107\">Yellow</td><td>Due in ≤14 days</td></tr>"
            "<tr><td>None</td><td>On track</td></tr></table>"
            "<h4>Generate Button</h4>Triggers the AI CDRL drafter for this specific "
            "deliverable. Always validate with WriteGuard before government submission.'\n"
            "  },\n\n"
            "  bulk_generate: {\n"
            "    title: 'Bulk Generate CDRLs',\n"
            "    html: '<h4>What It Does</h4>Triggers the AI CDRL drafter for all "
            "deliverables due within the next 14 days that have not yet been generated. "
            "<h4>Content Quality</h4>AI-generated CDRLs use SOW section alignment and "
            "DID compliance checks, but must always be reviewed by a human before "
            "government submission. Use WriteGuard to validate language quality, "
            "passive voice usage, and consistency. <h4>After Generation</h4>"
            "Each generated CDRL shows in deliverable_detail.html with a "
            "'Validate with WriteGuard' link.'\n"
            "  }\n\n"
            "Add ? buttons next to:\n"
            "  - Page title h1\n"
            "  - The stat cards row header\n"
            "  - The table <thead> row (one ? next to 'CDRL #' th)\n"
            "  - Next to the 'Bulk Generate' button label\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "wgint-hints-05",
        "title": "Hints: cpmp/deliverable_detail.html — full help panel + section ? buttons",
        "description": (
            "File: tools/dashboard/templates/cpmp/deliverable_detail.html (MODIFY)\n\n"
            "Goal: add a right-side sliding help panel to /cpmp/deliverables/<id> "
            "explaining the CDRL lifecycle, info grid, generation, and status history.\n\n"
            "PATTERN: copy from cpmp/detail.html help panel.\n"
            "Panel id: #deliv-detail-help-panel\n"
            "Function name: showSectionHelp(key)\n\n"
            "JS object _SECTION_HELP with 4 keys:\n\n"
            "  status_pipeline: {\n"
            "    title: 'CDRL Status Pipeline',\n"
            "    html: '<h4>Lifecycle Steps</h4>"
            "<table><tr><th>Status</th><th>Meaning</th><th>Next Step</th></tr>"
            "<tr><td>not_started</td><td>Not yet begun</td><td>Generate or author the CDRL</td></tr>"
            "<tr><td>in_progress</td><td>Being prepared by the team</td><td>Complete draft + WriteGuard review</td></tr>"
            "<tr><td>submitted</td><td>Sent to the government</td><td>Await government review</td></tr>"
            "<tr><td>government_review</td><td>Under government review</td><td>Respond to comments if requested</td></tr>"
            "<tr><td>accepted</td><td>Formally accepted ✓</td><td>No action required</td></tr>"
            "<tr><td>rejected</td><td>Returned for revision</td><td>Address comments → resubmit</td></tr></table>"
            "<h4>Days Overdue</h4>Auto-computed from due_date. Any positive value triggers "
            "a CPARS negative event flag.'\n"
            "  },\n\n"
            "  info_grid: {\n"
            "    title: 'CDRL Information Grid',\n"
            "    html: '<h4>CDRL #</h4>Sequential contract-unique deliverable ID "
            "(e.g., CDRL-003). <h4>DID #</h4>Data Item Description number from "
            "MIL-STD-963 — specifies exact format, content, and level of detail "
            "required (e.g., DI-IPSC-81441B for software design description). "
            "<h4>Type</h4>technical (software/hardware), management (plans/reports), "
            "data (datasets/models). <h4>Frequency</h4>How often: monthly, quarterly, "
            "annual, one-time, event-driven (milestone-triggered). "
            "<h4>Dates</h4>Due = contractual deadline; Submitted = when you delivered; "
            "Accepted = when government formally accepted.'\n"
            "  },\n\n"
            "  cdrl_generation: {\n"
            "    title: 'AI CDRL Generation',\n"
            "    html: '<h4>What Is Generated</h4>The AI CDRL drafter produces a draft "
            "document aligned to the DID format specification. It uses SOW section "
            "mapping to fill content fields with program-relevant details. "
            "<h4>Quality Review Required</h4>AI-generated CDRLs must always be reviewed "
            "before government submission. Use the WriteGuard link to check grammar, "
            "readability, passive voice percentage, and policy-language compliance. "
            "<h4>DID Compliance</h4>The drafter checks that required sections from the "
            "DID are present, but a human reviewer should verify completeness.'\n"
            "  },\n\n"
            "  status_history: {\n"
            "    title: 'Status History',\n"
            "    html: '<h4>What It Records</h4>Every status change for this CDRL: "
            "who changed it, when, and what it changed to. This is an immutable "
            "audit trail (append-only — rows are never updated or deleted). "
            "<h4>Why It Matters</h4>Status history is the primary evidence record "
            "for CPARS disputes. If a deliverable was submitted on time but the "
            "government review took too long, the history proves the submission date. "
            "<h4>Actor Column</h4>Shows the user (or 'system' for automated transitions) "
            "responsible for each status change.'\n"
            "  }\n\n"
            "Add ? buttons next to:\n"
            "  - Page title h1\n"
            "  - The 'Status Pipeline' section header\n"
            "  - The 'CDRL Information' / info grid header\n"
            "  - The 'CDRL Generation' section header\n"
            "  - The 'Status History' section header\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: wg — WriteGuard Integration
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "wgint-wg-06",
        "title": "WG: Add GET /api/govcon/opportunities/wg-scores batch endpoint",
        "description": (
            "File: tools/dashboard/api/govcon.py (MODIFY, +35 LoC)\n\n"
            "Add a new GET endpoint that returns the latest WriteGuard score for each "
            "of the requested opportunity IDs. Used by proposals/list.html and "
            "govcon/pipeline.html to populate WG score badge columns.\n\n"
            "Route: GET /api/govcon/opportunities/wg-scores\n"
            "Auth: requires same roles as existing govcon endpoints (admin, bd, capture_mgr, pm)\n"
            "Query param: ?ids=1,2,3,4  (comma-separated opportunity IDs)\n\n"
            "Response:\n"
            "  {\n"
            "    'scores': {\n"
            "      '1': 84,\n"
            "      '2': 67,\n"
            "      '3': null\n"
            "    }\n"
            "  }\n"
            "Where null = never analyzed with WriteGuard.\n\n"
            "Implementation:\n"
            "  1. Parse ids param: ids_list = [int(x) for x in request.args.get('ids','').split(',') if x.strip()]\n"
            "  2. If empty list, return {'scores': {}}\n"
            "  3. Use get_connection() with g.security_context for RLS compliance\n"
            "  4. Query: SELECT opp_id, overall_quality_score FROM wg_analysis_results\n"
            "            WHERE opp_id IN (...) AND opp_id IS NOT NULL\n"
            "            GROUP BY opp_id HAVING created_at = MAX(created_at)\n"
            "     OR use a subquery: SELECT w.opp_id, w.overall_quality_score\n"
            "     FROM wg_analysis_results w\n"
            "     INNER JOIN (SELECT opp_id, MAX(created_at) as latest FROM wg_analysis_results\n"
            "                  WHERE opp_id IN (...) GROUP BY opp_id) m\n"
            "     ON w.opp_id = m.opp_id AND w.created_at = m.latest\n"
            "  5. Build dict: {str(opp_id): score for each row}, defaulting to null for "
            "     IDs not in results\n"
            "  6. Return jsonify({'scores': scores_dict})\n\n"
            "Important: opp_id in wg_analysis_results is stored when /api/writeguard/analyze "
            "is called with an opp_id param. This field already exists — do not add new columns.\n\n"
            "DO NOT touch tools/dashboard/app.py or tools/dashboard/api/__init__.py.\n"
            "NOTE: This task deps on wgint-hints-01 for serialization — hints-01 must be "
            "merged before this task modifies govcon.py to prevent any race condition "
            "on the app startup."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "wgint-hints-01",  # serialize: hints-01 merges first
    },
    {
        "id": "wgint-wg-07",
        "title": "WG: proposals/list.html — WG score badge column (replaces plain WG link)",
        "description": (
            "File: tools/dashboard/templates/proposals/list.html (MODIFY)\n\n"
            "PREREQUISITE: wgint-hints-01 (help panel) and wgint-wg-06 (API endpoint) "
            "must both be merged before this task runs.\n\n"
            "Goal: replace the plain 'WG' link per row with a color-coded badge showing "
            "the last WriteGuard score, and add batch-loading JS.\n\n"
            "CHANGES:\n\n"
            "1. In <thead>, rename/replace the 'WG' column header with:\n"
            "   <th title='Last WriteGuard quality score — click badge to open analysis'>WG</th>\n\n"
            "2. In each opportunity row, replace the existing WG link cell with:\n"
            "   <td>\n"
            "     <span class='wg-score-badge' data-opp-id='{{ opp.id }}'\n"
            "           style='cursor:pointer;display:inline-block;padding:2px 7px;\n"
            "                  border-radius:4px;font-size:0.75rem;font-weight:700;\n"
            "                  background:var(--bg-secondary);color:var(--text-muted);'\n"
            "           title='Last WriteGuard score — click to open analysis'\n"
            "           onclick=\"window.open('/writeguard?opp_id={{ opp.id }}','_blank')\">—</span>\n"
            "   </td>\n\n"
            "3. Add JS at the bottom of the template (before {% endblock %}) that:\n"
            "   a. On DOMContentLoaded, collects all data-opp-id values\n"
            "   b. Batch-fetches: /api/govcon/opportunities/wg-scores?ids=1,2,3,...\n"
            "   c. For each badge, sets innerHTML to the score (or '—' if null) and\n"
            "      updates background/color:\n"
            "      - score >= 80: background=rgba(40,167,69,0.2), color=#4caf50 (green)\n"
            "      - score >= 60: background=rgba(255,193,7,0.2), color=#ffc107 (yellow)\n"
            "      - score < 60:  background=rgba(220,53,69,0.2), color=#dc3545 (red)\n"
            "      - null: keep default (—, muted gray)\n"
            "   d. On fetch error, silently keep the — placeholders (non-blocking)\n\n"
            "CSS class .wg-score-badge can be added inline — no new stylesheet needed.\n\n"
            "DO NOT touch tools/dashboard/app.py.\n"
            "DO NOT mirror to icdev/ yet — wgint-vv-14 handles that."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "wgint-wg-06",
    },
    {
        "id": "wgint-wg-08",
        "title": "WG: govcon/pipeline.html — WG score badge column in Active Proposal table",
        "description": (
            "File: tools/dashboard/templates/govcon/pipeline.html (MODIFY)\n\n"
            "PREREQUISITE: wgint-hints-02 (help panel) must be merged before this task.\n\n"
            "Goal: add a WG score badge column to the Active Proposal Pipeline table "
            "on /govcon, using the same approach as wgint-wg-07 for proposals/list.html.\n\n"
            "CHANGES:\n\n"
            "1. In the gc-table <thead>, add a new <th> after 'Capture Mgr':\n"
            "   <th title='Last WriteGuard quality score'>WG</th>\n\n"
            "2. In each active_proposals row (the {% for opp in active_proposals %} loop),\n"
            "   add a new <td> after the Capture Mgr cell:\n"
            "   <td>\n"
            "     <span class='wg-score-badge' data-opp-id='{{ opp.id }}'\n"
            "           style='cursor:pointer;display:inline-block;padding:2px 7px;\n"
            "                  border-radius:4px;font-size:0.75rem;font-weight:700;\n"
            "                  background:var(--bg-secondary);color:var(--text-muted);'\n"
            "           title='Last WriteGuard score — click to open analysis'\n"
            "           onclick=\"window.open('/writeguard?opp_id={{ opp.id }}','_blank')\">—</span>\n"
            "   </td>\n\n"
            "3. Add the SAME JS batch-fetch logic as in wgint-wg-07 "
            "(collect data-opp-id, fetch /api/govcon/opportunities/wg-scores?ids=..., "
            "populate badges with green/yellow/red colors).\n\n"
            "NOTE: If wg-07 already shipped, you can consolidate the JS into one "
            "DOMContentLoaded handler for both badge sets. But since govcon/pipeline.html "
            "is a separate template, include a self-contained version here.\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "wgint-hints-02",
    },
    {
        "id": "wgint-wg-09",
        "title": "WG: govcon/pipeline.html — add WriteGuard Drafts button to action bar",
        "description": (
            "File: tools/dashboard/templates/govcon/pipeline.html (MODIFY)\n\n"
            "PREREQUISITE: wgint-wg-08 must be merged before this task.\n\n"
            "Goal: add a WriteGuard entry point to the GovCon pipeline action bar so "
            "users can open WriteGuard directly when reviewing AI-generated drafts.\n\n"
            "CHANGE: in the .gc-action-bar div (after the '✍️ Draft' button), add:\n\n"
            "<button onclick=\"window.open('/writeguard','_blank')\" \n"
            "        class='btn btn-secondary'\n"
            "        style='padding:0.3rem 0.9rem;\n"
            "               border-color:rgba(74,144,217,0.4);\n"
            "               color:#6db3f8;'\n"
            "        title='Open WriteGuard to validate AI-generated draft quality'>\n"
            "  🛡️ WriteGuard Drafts\n"
            "</button>\n\n"
            "That's the only change — a single button added to the existing action bar row.\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "wgint-wg-08",
    },
    {
        "id": "wgint-wg-10",
        "title": "WG: cpmp/detail.html — data-writeguard attrs on MOD textareas + script include",
        "description": (
            "File: tools/dashboard/templates/cpmp/detail.html (MODIFY)\n\n"
            "PREREQUISITE: wgint-hints-03 must be merged before this task.\n\n"
            "Goal: wire WriteGuard inline quality badges onto the two key text areas "
            "in the Contract Modifications workflow so users get live quality feedback "
            "while writing contract language.\n\n"
            "CHANGES:\n\n"
            "1. Find the #mod-desc textarea in the MOD Request modal. Add attributes:\n"
            "   data-writeguard='true'\n"
            "   data-writeguard-label='Modification Description'\n"
            "   data-writeguard-mode='policy_language'\n"
            "   data-writeguard-min-words='10'\n"
            "   data-writeguard-position='bottom-right'\n\n"
            "2. Find the #transition-reason textarea in the MOD Status Transition modal. Add:\n"
            "   data-writeguard='true'\n"
            "   data-writeguard-label='Transition Justification'\n"
            "   data-writeguard-mode='policy_language'\n"
            "   data-writeguard-min-words='5'\n"
            "   data-writeguard-position='bottom-right'\n\n"
            "3. At the very bottom of the template (before {% endblock %} / {% endblock scripts %}), "
            "add the writeguard-inline.js script include IF NOT ALREADY PRESENT:\n"
            "   <script src=\"{{ url_for('static', filename='js/writeguard-inline.js') }}\"></script>\n\n"
            "How the inline widget works (already implemented in writeguard-inline.js):\n"
            "  - Auto-scans for any textarea with data-writeguard='true'\n"
            "  - Debounces 500ms after typing stops (or data-writeguard-debounce ms)\n"
            "  - Only activates after min-words threshold is reached\n"
            "  - Shows a small floating badge with score + dim breakdown on hover\n"
            "  - Does NOT block form submission\n\n"
            "DO NOT touch any other part of cpmp/detail.html."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "wgint-hints-03",
    },
    {
        "id": "wgint-wg-11",
        "title": "WG: cpmp/detail.html — Validate Language button in Modifications tab toolbar",
        "description": (
            "File: tools/dashboard/templates/cpmp/detail.html (MODIFY)\n\n"
            "PREREQUISITE: wgint-wg-10 must be merged before this task.\n\n"
            "Goal: add a visible 'Validate Language' button in the Modifications tab "
            "so users can open WriteGuard for deep analysis of contract modification text.\n\n"
            "CHANGE: in the Modifications tab header/toolbar area (near the existing "
            "'Request Modification' or 'New MOD' button), add:\n\n"
            "<button class='btn btn-secondary btn-sm'\n"
            "        onclick=\"window.open('/writeguard?mode=policy_language','_blank')\"\n"
            "        title='Open WriteGuard to validate contract modification language'\n"
            "        style='font-size:0.8rem;'>\n"
            "  🛡️ Validate Language\n"
            "</button>\n\n"
            "That is the ONLY change for this task.\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "wgint-wg-10",
    },
    {
        "id": "wgint-wg-12",
        "title": "WG: cpmp/deliverable_center.html — WG quality column + Validate All button",
        "description": (
            "File: tools/dashboard/templates/cpmp/deliverable_center.html (MODIFY)\n\n"
            "PREREQUISITE: wgint-hints-04 must be merged before this task.\n\n"
            "Goal: surface WriteGuard quality status for auto-generated CDRLs and "
            "add a bulk validation entry point.\n\n"
            "CHANGES:\n\n"
            "1. Add 'WG' column to the deliverables table:\n"
            "   In <thead>, add: <th title='WriteGuard quality score (AI-generated CDRLs only)'>WG</th>\n"
            "   In each row ({% for d in deliverables %} loop), add:\n"
            "   <td>\n"
            "   {% if d.auto_generated %}\n"
            "     <a href=\"/writeguard\" target=\"_blank\"\n"
            "        style='font-size:0.72rem;padding:2px 6px;border-radius:4px;\n"
            "               background:rgba(74,144,217,0.15);color:#6db3f8;\n"
            "               text-decoration:none;border:1px solid rgba(74,144,217,0.3);'\n"
            "        title='Validate this AI-generated CDRL with WriteGuard'>🛡️ Check</a>\n"
            "   {% else %}\n"
            "     <span style='color:var(--text-muted);font-size:0.75rem;'>—</span>\n"
            "   {% endif %}\n"
            "   </td>\n\n"
            "   If d.auto_generated is not in the template context, check the view function "
            "in app.py for the deliverable_center_page route and see what fields are passed. "
            "If auto_generated is not available, use d.generated_by_tool or simply show "
            "the link for all rows (with '🛡️' icon, no 'Check' text).\n\n"
            "2. Add 'Validate All Generated' button next to the existing Bulk Generate button:\n"
            "<button class='btn btn-secondary btn-sm'\n"
            "        onclick=\"window.open('/writeguard','_blank')\"\n"
            "        title='Open WriteGuard to validate auto-generated CDRL language'\n"
            "        style='font-size:0.8rem;'>\n"
            "  🛡️ Validate All Generated\n"
            "</button>\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "wgint-hints-04",
    },
    {
        "id": "wgint-wg-13",
        "title": "WG: cpmp/deliverable_detail.html — Validate with WriteGuard link in CDRL section",
        "description": (
            "File: tools/dashboard/templates/cpmp/deliverable_detail.html (MODIFY)\n\n"
            "PREREQUISITE: wgint-hints-05 must be merged before this task.\n\n"
            "Goal: add a WriteGuard validation link in the CDRL Generation section so "
            "users can immediately open WriteGuard to review the generated content.\n\n"
            "CHANGE: in the CDRL Generation table/section header area (find the section "
            "that displays generated CDRL content — look for 'generation' or 'generated' "
            "in the template), add a link next to the section heading:\n\n"
            "<a href='/writeguard' target='_blank'\n"
            "   class='btn btn-secondary btn-sm'\n"
            "   style='font-size:0.78rem;margin-left:8px;'\n"
            "   title='Open WriteGuard to validate this generated CDRL'>\n"
            "  🛡️ Validate with WriteGuard\n"
            "</a>\n\n"
            "That is the ONLY change for this task — a simple anchor tag.\n"
            "No JS needed. No API calls.\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "wgint-hints-05",
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: vv — Mirror + Verification
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "wgint-vv-14",
        "title": "VV: Mirror proposals/list.html to icdev/ namespace + companion sync",
        "description": (
            "PREREQUISITE: wgint-wg-07 must be merged before this task.\n\n"
            "Goal: ensure the proposals/list.html changes (help panel + WG badge column) "
            "are mirrored to the icdev/ canonical namespace.\n\n"
            "STEPS:\n\n"
            "1. Copy the full content of tools/dashboard/templates/proposals/list.html\n"
            "   to icdev/tools/dashboard/templates/proposals/list.html\n"
            "   (they should be identical files)\n\n"
            "2. Run companion sync:\n"
            "   python tools/dx/companion.py --sync --write --json\n\n"
            "3. Run coherence check:\n"
            "   python tools/workflow/coherence_checker.py --all --fix --gate\n\n"
            "4. Verify the dashboard still starts without errors:\n"
            "   Check that importing tools.dashboard.app does not raise ImportError or\n"
            "   TemplateNotFound for proposals/list.html\n\n"
            "NOTE: Other changed files (govcon/pipeline.html, cpmp/detail.html, "
            "cpmp/deliverable_center.html, cpmp/deliverable_detail.html) do NOT have "
            "icdev/ mirrors that need updating — only proposals/list.html is mirrored.\n"
            "If you find any of the other templates ARE mirrored in icdev/, mirror those too."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "wgint-wg-07",
    },
    {
        "id": "wgint-vv-15",
        "title": "VV: End-to-end Playwright verification — all 4 pages, help panels + WG badges",
        "description": (
            "PREREQUISITE: wgint-vv-14 must be merged. Also verify that ALL of the "
            "following tasks are in 'done' state before proceeding:\n"
            "  wgint-hints-01 through wgint-hints-05 (all 5 help panels)\n"
            "  wgint-wg-06 through wgint-wg-13 (all 8 WG integration tasks)\n"
            "  wgint-vv-14 (mirror + sync)\n\n"
            "If any are not done, wait or move them to done manually before this task runs.\n\n"
            "VERIFICATION STEPS:\n\n"
            "1. Start the dashboard: python tools/dashboard/app.py\n\n"
            "2. /proposals:\n"
            "   - ? button next to page title → help panel slides in, shows 'Pipeline Stats' content\n"
            "   - ? button next to stat grid → shows stat card explanations\n"
            "   - ? in pWin column header → shows pWin scoring content\n"
            "   - WG badge column shows '—' initially, then populates with scores after JS runs\n"
            "   - Click a WG badge → opens /writeguard?opp_id=X in new tab\n"
            "   - Screenshot: playwright/screenshots/wgint-proposals.png\n\n"
            "3. /govcon:\n"
            "   - ? next to hero header → help panel shows 'GovCon Intelligence Pipeline'\n"
            "   - ? next to Pipeline Flow → shows stage-by-stage explanation\n"
            "   - ? next to Active Proposal Pipeline → shows column guide\n"
            "   - WG badge column in the Active Proposal Pipeline table\n"
            "   - '🛡️ WriteGuard Drafts' button present in the action bar\n"
            "   - Screenshot: playwright/screenshots/wgint-govcon.png\n\n"
            "4. /cpmp (open any contract):\n"
            "   - Existing 10-tab help panel still works (no regression) — click any ? icon\n"
            "   - AI Advisor bar has a ? that opens the ai_advisor help entry\n"
            "   - Open Modifications tab → type 10+ words in #mod-desc → floating WG badge appears\n"
            "   - '🛡️ Validate Language' button present in the Modifications tab toolbar\n"
            "   - Screenshot: playwright/screenshots/wgint-cpmp-mods.png\n\n"
            "5. /cpmp/deliverables:\n"
            "   - ? next to page title → help panel slides in with stat_cards content\n"
            "   - ? next to table header → shows deliverables_table content\n"
            "   - WG '🛡️ Check' badges visible in WG column for auto-generated rows\n"
            "   - '🛡️ Validate All Generated' button visible next to Bulk Generate\n"
            "   - Screenshot: playwright/screenshots/wgint-deliv-center.png\n\n"
            "6. /cpmp/deliverables/<any_id>:\n"
            "   - ? buttons next to each section header → help panel slides in with correct content\n"
            "   - '🛡️ Validate with WriteGuard' link present in CDRL Generation section\n"
            "   - Screenshot: playwright/screenshots/wgint-deliv-detail.png\n\n"
            "7. Run regression check:\n"
            "   pytest tests/ -v --tb=short -k 'not slow'\n"
            "   ruff check tools/dashboard/ --select=E,W,F\n\n"
            "8. Write feature doc:\n"
            "   docs/features/phase-3-wgint-integration.md\n"
            "   (brief summary: what was added, which files changed, verification screenshots)\n\n"
            "If any step fails, file a follow-up Kanban task and document the failure."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "wgint-vv-14",
    },
]


def seed(dry_run: bool = False) -> None:
    """Insert all 15 tasks. Idempotent — skips existing IDs."""
    conn = _conn()
    inserted = 0
    skipped = 0

    print("\n  WriteGuard Integration Seeder")
    print(f"  Project: {PROJECT_KEY} ({PROJECT_PREFIX})")
    print(f"  Tasks:   {len(TASKS)}\n")

    if dry_run:
        print("  DRY RUN - not writing to DB\n")
        for t in TASKS:
            print(
                f"  [DRY] {t['id']:30s} "
                f"-> deps on {t.get('depends_on_task_id') or '(root)':30s}  "
                f"{t['title'][:55]}"
            )
        return

    try:
        for t in TASKS:
            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = ?", (t["id"],)
            ).fetchone()
            if existing:
                print(f"  SKIP  {t['id']} (already exists)")
                skipped += 1
                continue

            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority,
                    status, scheduled_at, depends_on_task_id,
                    executor_type, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t["id"],
                    t["title"],
                    t.get("description", ""),
                    t.get("task_type", "build"),
                    t.get("priority", "high"),
                    t.get("status", "backlog"),
                    t.get("scheduled_at"),
                    t.get("depends_on_task_id"),
                    "claude_cli",
                    _NOW,
                    _NOW,
                ),
            )
            dep = t.get("depends_on_task_id") or "root"
            print(
                f"  ADD   {t['id']:30s} "
                f"[deps={dep:30s}]  "
                f"{t['title'][:55]}"
            )
            inserted += 1
            # Also write to junction table for the dep-graph visualization on the board.
            if t.get("depends_on_task_id"):
                conn.execute(
                    "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) "
                    "VALUES (?, ?, ?) ON CONFLICT (task_id, depends_on_id) DO NOTHING",
                    (t["id"], t["depends_on_task_id"], _NOW),
                )

        conn.commit()
        print(f"\n  Done: {inserted} added, {skipped} skipped.")

        print("\n  Dep chain visualization:")
        for t in TASKS:
            if t.get("depends_on_task_id"):
                print(f"    {t['depends_on_task_id']} -> {t['id']}")
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Seed WriteGuard integration kanban tasks")
    p.add_argument("--dry-run", action="store_true", help="Print tasks without writing to DB")
    args = p.parse_args()
    seed(dry_run=args.dry_run)
