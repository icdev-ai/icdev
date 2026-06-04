#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed PMO Demo Wow-Factor kanban tasks.

Decomposes the executive PMO demo plan (5 wow factors, 17 atomic tasks)
into kanban_tasks rows with a proper depends_on DAG. The Kanban scheduler
will pick these up in dep order, run each in its own worktree, and merge
sequentially to main.

Wow factors (from /c/Users/schuo/.claude/plans/i-m-giving-a-demo-vivid-pinwheel.md):
  1. AI Executive Brief banner on all 4 pages        (epic=brief, 4 tasks + V&V)
  2. pWin x Weighted $ bubble chart on /proposals     (epic=chart, 3 tasks)
  3. Bid / No-Bid recommendation card on /proposals   (epic=bid,   2 tasks)
  4. IQE query widget on all 4 pages                  (epic=iqe,   5 tasks)
  5. Weekly PMO Brief archive viewer at /cpmp/reports (epic=viewer, 5 tasks)

Naming: prefix=pmodemo-, epic=brief|chart|bid|iqe|viewer, num=01..05
Per args/projects.yaml rule: <prefix><epic_key>-<NN>
  Examples: pmodemo-brief-01, pmodemo-iqe-04

Cross-file serialization (so worktree merges resolve cleanly):
  proposals/list.html is touched by brief-03, chart-02, bid-02, iqe-04
     ->  chain brief-03  ->  chart-02  ->  bid-02  ->  iqe-04
  govcon/pipeline.html is touched by brief-03, iqe-04
     ->  chain brief-03  ->  iqe-04
  cpmp/portfolio.html is touched by brief-03, iqe-04, viewer-04
     ->  chain brief-03  ->  iqe-04  ->  viewer-04
  cpmp/deliverable_center.html is touched by brief-03, iqe-04
     ->  chain brief-03  ->  iqe-04
  tools/dashboard/api/govcon.py is touched by chart-01, bid-01
     ->  chain chart-01  ->  bid-01
  tools/dashboard/app.py is touched by iqe-03, viewer-03
     ->  chain iqe-03  ->  viewer-03

Run: python tools/kanban/seed_pmo_demo_wow.py [--dry-run]

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
PROJECT_PREFIX = "pmodemo-"
PROJECT_KEY = "pmodemo"
PROJECT_NAME = "PMO Demo Wow Factors"
PROJECT_DESCRIPTION = (
    "Five wow-factor improvements to /proposals, /govcon, /cpmp, and "
    "/cpmp/deliverables for the executive PMO demo to PMs, Portfolio "
    "Managers, Capture Managers, and Executives. Audience priority: "
    "automated PMO  -  AI-assisted decision making. Demo date TBD. "
    "All tasks reuse existing engines (pmo_ai_advisor, bayesian_bid_scorer, "
    "cpars_predictor, IQE dispatch); no new ML, no new schemas, no new "
    "reflexes. Reference plan: C:/Users/schuo/.claude/plans/"
    "i-m-giving-a-demo-vivid-pinwheel.md"
)


def _conn():
    return get_connection()


TASKS: list[dict] = [
    # ══════════════════════════════════════════════════════════════════
    # EPIC: brief  -  AI Executive Brief banner on all 4 pages
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "pmodemo-brief-01",
        "title": "Brief: Build ai_brief_banner.py component (partial renderer)",
        "description": (
            "File: tools/dashboard/components/__init__.py (NEW, empty)\n"
            "File: tools/dashboard/components/ai_brief_banner.py (NEW, ~150 LoC)\n\n"
            "Build a Flask/Jinja-friendly partial renderer that, given a canvas "
            "name and the live portfolio snapshot, returns the inner HTML for "
            "the AI brief banner.\n\n"
            "Reuse (do not rebuild):\n"
            "  - tools/govcon/pmo_ai_advisor.get_pmo_recommendations(contract_id)\n"
            "  - tools/govcon/pmo_ai_advisor.predict_award_fee_narrative(contract_id)\n"
            "  - tools/govcon/pmo_ai_advisor.auto_detect_issues(contract_id)\n"
            "  - tools/govcon/portfolio_manager.get_portfolio_summary()\n"
            "  - tools/govcon/option_period_tracker.get_portfolio_countdown()\n"
            "  - tools/llm/router.LLMRouter().route(LLMRequest(function=\"pmo_recommendations\"))\n\n"
            "Signature:\n"
            "  def render_ai_brief(canvas: str, snapshot: dict) -> str\n"
            "Returns rendered HTML string (the <aside id='ai-brief'>...</aside>).\n"
            "Must work WITHOUT an LLM: every LLM call goes through "
            "pmo_ai_advisor which already has deterministic fallbacks.\n\n"
            "Required:\n"
            "  1. 'tools/dashboard/components/__init__.py'  -  empty package init\n"
            "  2. 'tools/dashboard/components/ai_brief_banner.py'  -  render_ai_brief()\n"
            "     - canvas='cpmp'   ->  call get_pmo_recommendations() on worst-3 contracts\n"
            "     - canvas='cpmp_deliverables'  ->  call auto_detect_issues() across all contracts, top 3 by severity\n"
            "     - canvas='proposals'  ->  call pipeline_value_rollup() + get portfolio top-3 at-risk opps by pWin\n"
            "     - canvas='govcon'  ->  call get_govcon_status() + capability_mapper.get_gaps(top_n=3)\n"
            "  3. Wrap each LLM-touching call in try/except: if LLM fails, render the deterministic template (already in pmo_ai_advisor).\n"
            "  4. Hard timeout: 5 seconds per LLM call (signal.alarm or threading.Timer).\n\n"
            "Mirror to icdev/ via companion sync at the END of this task.\n"
            "DO NOT touch tools/dashboard/app.py or tools/dashboard/api/__init__.py  -  "
            "another session is editing those files right now."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "pmodemo-brief-02",
        "title": "Brief: Build ai_brief_banner.html partial template",
        "description": (
            "File: tools/dashboard/templates/components/ai_brief_banner.html (NEW, ~80 LoC)\n\n"
            "A self-contained Jinja2 partial that renders the AI brief banner.\n"
            "Required structure:\n"
            "  <aside id='ai-brief' class='ai-brief-banner' data-canvas='{{ canvas }}'>\n"
            "    <header><span class='ai-brief-icon'>🧠</span><strong>AI Executive Brief</strong></header>\n"
            "    <div class='ai-brief-body'>{{ brief_html|safe }}</div>\n"
            "    <footer><span class='ai-brief-ts' data-ts='{{ generated_at }}'></span></footer>\n"
            "  </aside>\n\n"
            "Style: dark gradient (purple -> indigo), 1px border, rounded, padding 16px.\n"
            "JS: data-ts renders a 'Generated Xs ago' timestamp updated every 30s.\n"
            "If brief_html is empty, render skeleton with 'Loading...' text + shimmer.\n\n"
            "Mirror to icdev/tools/dashboard/templates/components/ai_brief_banner.html "
            "via companion sync at the end of this task."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-brief-01",
    },
    {
        "id": "pmodemo-brief-03",
        "title": "Brief: Include partial in all 4 page templates (+ icdev/ mirrors)",
        "description": (
            "Add {% include 'components/ai_brief_banner.html' %} (with canvas context var) "
            "to the top of all 4 page templates, just below the hero/header section.\n\n"
            "Files to modify (each gets ONE include line, no other changes):\n"
            "  1. tools/dashboard/templates/proposals/list.html (after stat grid)\n"
            "  2. tools/dashboard/templates/govcon/pipeline.html (after hero header)\n"
            "  3. tools/dashboard/templates/cpmp/portfolio.html (above aggregate banner)\n"
            "  4. tools/dashboard/templates/cpmp/deliverable_center.html (after stat row)\n\n"
            "Each include needs a 'canvas' context variable:\n"
            "  - proposals/list.html   ->  canvas='proposals'\n"
            "  - govcon/pipeline.html  ->  canvas='govcon'\n"
            "  - cpmp/portfolio.html   ->  canvas='cpmp'\n"
            "  - cpmp/deliverable_center.html  ->  canvas='cpmp_deliverables'\n\n"
            "The brief is rendered ASYNC on the client side via a small JS shim that "
            "calls /api/<canvas>/ai-brief and inserts the HTML into the partial. This "
            "keeps page render non-blocking even if the LLM is slow.\n\n"
            "Mirror all 4 file edits to icdev/tools/dashboard/templates/...\n\n"
            "DO NOT touch tools/dashboard/app.py or tools/dashboard/api/__init__.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-brief-02",
    },
    {
        "id": "pmodemo-brief-04",
        "title": "Brief: V&V  -  all 4 pages render banner with content (or fallback)",
        "description": (
            "After pmodemo-brief-03 merges, verify end-to-end:\n\n"
            "1. Restart the dashboard: kill the python.exe serving 5050 (or just refresh)\n"
            "2. Visit /proposals, /govcon, /cpmp, /cpmp/deliverables in a fresh browser\n"
            "3. For each page, the AI brief banner appears within 5s of page load\n"
            "4. The banner shows actual content (not 'Loading...' stuck forever)\n"
            "5. If LLM is unavailable, the deterministic fallback text appears\n"
            "6. No 500 errors in browser dev tools network tab\n"
            "7. Playwright smoke: take screenshots playwright/screenshots/pmodemo-brief-{canvas}.png\n\n"
            "If the banner is missing, the failure is most likely:\n"
            "  (a) The include is below a Jinja for-loop and rendering inside a row  -  move it ABOVE the loop\n"
            "  (b) The async fetch 404s because the canvas name is wrong  -  verify URL pattern\n"
            "  (c) The pmo_ai_advisor function call raises  -  log full stack trace to /tmp/ai_brief_error.log\n\n"
            "Document the fix in the task completion comment. If you ship a fix, attach the diff."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "pmodemo-brief-03",
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: chart  -  pWin x Weighted $ bubble chart on /proposals
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "pmodemo-chart-01",
        "title": "Chart: /api/govcon/proposals/bubble-data endpoint",
        "description": (
            "File: tools/dashboard/api/govcon.py (MODIFY, +30 LoC)\n\n"
            "Add new GET endpoint that returns one row per active opportunity:\n"
            "  Route: GET /api/govcon/proposals/bubble-data\n"
            "  Auth: requires role admin|bd|capture_mgr (same as list)\n"
            "  Response:\n"
            "    {\n"
            "      'opportunities': [\n"
            "        {\n"
            "          'opp_id': 'prop-001',\n"
            "          'title': '...',\n"
            "          'stage': 'discover|extract|map|draft|submit',\n"
            "          'pwin': 0.62,                # 0..1\n"
            "          'weighted_value': 1240000,    # dollars\n"
            "          'ceiling': 2000000,           # dollars (bubble size)\n"
            "          'days_to_deadline': 14\n"
            "        },\n"
            "        ...\n"
            "      ],\n"
            "      'count': N\n"
            "    }\n\n"
            "Reuse:\n"
            "  - tools/govcon/bayesian_bid_scorer.score_opportunity(opp_id)   ->  pwin, weighted_value\n"
            "  - tools/govcon/bayesian_bid_scorer.pipeline_value_rollup()  ->  per-opp breakdown if score_opportunity is slow\n\n"
            "If score_opportunity raises (no history yet), default pwin=0.5, weighted_value=ceiling*0.5.\n"
            "If ceiling is unknown, default to 1_000_000.\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-brief-03",  # serialize on proposals/list.html
    },
    {
        "id": "pmodemo-chart-02",
        "title": "Chart: Inline SVG bubble chart in proposals/list.html",
        "description": (
            "File: tools/dashboard/templates/proposals/list.html (MODIFY, +80 LoC)\n\n"
            "Add an inline-SVG bubble chart BELOW the stat grid, ABOVE the table.\n\n"
            "Layout (1200x500 SVG, viewBox 0 0 1200 500):\n"
            "  - X-axis: 0%  ->  100% pWin, labeled 'pWin'\n"
            "  - Y-axis: $0  ->  max weighted value (log scale), labeled 'Weighted $'\n"
            "  - Bubbles: one per opp, color by stage (Discover=#aaa, Extract=#7af, Map=#fa7, Draft=#a7f, Submit=#7fa)\n"
            "  - Bubble radius: sqrt(ceiling) / 50 (clamped 4..40)\n"
            "  - Hover: tooltip with title + pWin + weighted value\n"
            "  - Click: navigate to /proposals/<opp_id>\n"
            "  - Title above: 'Pipeline Risk x Reward  -  pWin x Weighted $'\n\n"
            "Data: fetched async from /api/govcon/proposals/bubble-data on page load.\n"
            "Loading: show skeleton placeholder while data loads.\n"
            "Empty state: hide the chart section if opportunities.length === 0.\n"
            "Toggle: 'View as chart' / 'View as table' link to hide either.\n\n"
            "Use hand-rolled SVG math (no Chart.js)  -  same pattern as cpmp/detail.html S-curve.\n\n"
            "Mirror to icdev/tools/dashboard/templates/proposals/list.html.\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-chart-01",
    },
    {
        "id": "pmodemo-chart-03",
        "title": "Chart: V&V  -  bubbles render, click navigates to opp detail",
        "description": (
            "After pmodemo-chart-02 merges, verify:\n\n"
            "1. Visit /proposals  -  chart section appears between stat grid and table\n"
            "2. Bubbles render with correct colors per stage\n"
            "3. At least 1 bubble exists (or chart is hidden if no active opps)\n"
            "4. Click a bubble  ->  navigates to /proposals/<opp_id>\n"
            "5. Toggle to 'View as table'  ->  table visible, chart hidden\n"
            "6. Toggle back to 'View as chart'  ->  chart visible again\n"
            "7. No layout shift on page load (chart reserves space)\n"
            "8. Playwright: screenshot playwright/screenshots/pmodemo-chart-proposals.png\n\n"
            "If bubbles are not visible:\n"
            "  (a) Check console  -  likely a CORS or auth issue on the API call\n"
            "  (b) Verify the API returns 200 with non-empty opportunities array\n"
            "  (c) Verify the SVG scales match the data range"
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "pmodemo-chart-02",
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: bid  -  Bid / No-Bid recommendation card on /proposals list
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "pmodemo-bid-01",
        "title": "Bid: /api/govcon/proposals/<id>/recommendation endpoint",
        "description": (
            "File: tools/dashboard/api/govcon.py (MODIFY, +25 LoC)\n\n"
            "Add new GET endpoint:\n"
            "  Route: GET /api/govcon/proposals/<opp_id>/recommendation\n"
            "  Auth: requires role admin|bd|capture_mgr\n"
            "  Response:\n"
            "    {\n"
            "      'opp_id': 'prop-001',\n"
            "      'recommendation': 'BID'|'NO_BID'|'MAYBE',\n"
            "      'pwin': 0.62,\n"
            "      'rationale': 'pWin 62% > 50% threshold; compliance coverage 78%',  # one-line\n"
            "      'factors': {\n"
            "        'incumbency': 0.30,\n"
            "        'past_performance_fit': 0.25,\n"
            "        'competitive_position': 0.20,\n"
            "        'compliance_coverage': 0.15,\n"
            "        'crm_engagement': 0.10\n"
            "      },\n"
            "      'computed_at': '2026-06-03T...'\n"
            "    }\n\n"
            "Reuse:\n"
            "  - tools/govcon/bayesian_bid_scorer.score_opportunity(opp_id)\n"
            "    Returns a dict with 'recommendation', 'pwin', 'factor_breakdown', 'rationale'.\n"
            "    If this function already returns a 'recommendation' field, just expose it.\n\n"
            "Map score_opportunity() output to the response shape above. 5-line wrapper.\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-chart-02",  # serialize on proposals/list.html
    },
    {
        "id": "pmodemo-bid-02",
        "title": "Bid: Add Bid column + BID/NO-BID/MAYBE badge to proposals list",
        "description": (
            "File: tools/dashboard/templates/proposals/list.html (MODIFY, +30 LoC)\n\n"
            "Add a new column 'Bid' to the proposals table, positioned right after the pWin column.\n\n"
            "Cell content: a colored badge per row:\n"
            "  - BID     ->  green badge (#2e7d32 background, white text)\n"
            "  - NO_BID  ->  red badge (#c62828 background, white text)\n"
            "  - MAYBE   ->  amber badge (#f9a825 background, black text)\n\n"
            "Data: fetched async per-row from /api/govcon/proposals/<id>/recommendation.\n"
            "Cache: store result in a JS Map<opp_id, recommendation> for the page session.\n"
            "Loading: show '...' placeholder while fetching.\n"
            "Tooltip on hover: full rationale text.\n\n"
            "Add a filter at the top of the table: 'Show: [All|Bid|Maybe|No-Bid]'.\n"
            "When filtered, hide rows that don't match (client-side).\n\n"
            "Mirror to icdev/tools/dashboard/templates/proposals/list.html.\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-bid-01",
    },
    {
        "id": "pmodemo-bid-03",
        "title": "Bid: V&V  -  badges render with correct colors per recommendation",
        "description": (
            "After pmodemo-bid-02 merges, verify:\n\n"
            "1. Visit /proposals  -  every row has a Bid column\n"
            "2. Each row shows BID, NO_BID, or MAYBE in the right color\n"
            "3. Hover over a badge shows the rationale\n"
            "4. Filter 'Bid' shows only BID rows; 'Maybe' shows only MAYBE; 'No-Bid' shows only NO_BID\n"
            "5. Filter 'All' shows all rows\n"
            "6. No badge stuck on '...' (i.e., the API call succeeded for all rows)\n"
            "7. Playwright: screenshot playwright/screenshots/pmodemo-bid-proposals.png\n\n"
            "If badges don't render:\n"
            "  (a) Check that /api/govcon/proposals/<id>/recommendation returns 200 for at least one opp\n"
            "  (b) Verify the JS fetch is firing (check browser dev tools Network tab)\n"
            "  (c) The badge colors are defined as CSS classes  -  verify the <style> block in the template"
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "pmodemo-bid-02",
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: iqe  -  IQE query widget on all 4 pages
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "pmodemo-iqe-01",
        "title": "IQE: Build tools/iqe/adapters/cpmp.py (registers cpmp collections)",
        "description": (
            "File: tools/iqe/adapters/cpmp.py (NEW, ~120 LoC)\n\n"
            "Mirror the pattern in tools/iqe/adapters/govcon.py:\n"
            "  - COLLECTIONS dict: 3 entries  -  cpmp.contracts, cpmp.deliverables, cpmp.options\n"
            "  - Each entry has: schema, sql (or virtual_view), description, examples\n"
            "  - register() function imported by tools/iqe/__init__.py\n\n"
            "Collections:\n"
            "  cpmp.contracts:\n"
            "    - description: 'CPMP portfolio contracts with health, EVM, CPARS'\n"
            "    - source table: cpmp_contracts (or equivalent; check init_db.py)\n"
            "    - columns: id, contract_number, title, agency, status, health_score, value, cpi, spi\n"
            "    - examples:\n"
            "      - 'Show red-health contracts'\n"
            "      - 'Show contracts with CPI < 0.9'\n"
            "      - 'Show top 5 contracts by value'\n"
            "\n"
            "  cpmp.deliverables:\n"
            "    - description: 'CDRLs and deliverables across all contracts'\n"
            "    - source table: cpmp_deliverables (or equivalent)\n"
            "    - columns: id, contract_id, cdrl_number, title, type, due_date, status\n"
            "    - examples:\n"
            "      - 'Show overdue deliverables'\n"
            "      - 'Show deliverables due in 7 days'\n"
            "      - 'Show CDRLs auto-generated this week'\n"
            "\n"
            "  cpmp.options:\n"
            "    - description: 'Contract option periods with AI go/no-go recommendations'\n"
            "    - source table: cpmp_options (or equivalent)\n"
            "    - columns: id, contract_id, period_number, start_date, end_date, exercised, ai_recommendation\n"
            "    - examples:\n"
            "      - 'Show options expiring in 90 days'\n"
            "      - 'Show options with go recommendation'\n"
            "      - 'Show options with no-go recommendation'\n\n"
            "If the actual CPMP table names differ (some may be prefixed differently), check "
            "tools/dashboard/api/cpmp.py for the SQL the existing endpoints use and mirror those "
            "table names.\n\n"
            "DO NOT touch tools/dashboard/app.py or tools/dashboard/api/__init__.py  -  register "
            "the adapter via tools/iqe/__init__.py's auto-import (if it exists) or via the "
            "IQE dispatch mechanism."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-bid-02",  # serialize on proposals/list.html
    },
    {
        "id": "pmodemo-iqe-02",
        "title": "IQE: Create 3+ seed query .md files in context/iqe/queries/cpmp/",
        "description": (
            "Files (NEW):\n"
            "  1. context/iqe/queries/cpmp/contracts.md\n"
            "  2. context/iqe/queries/cpmp/deliverables.md\n"
            "  3. context/iqe/queries/cpmp/options.md\n\n"
            "Each file format follows the IQE query markdown schema. Check the existing "
            "context/iqe/queries/govcon/*.md files for the exact schema. Each file should "
            "have 3+ seed questions that the IQE engine can route to the right collection.\n\n"
            "Example seed questions (you choose the final set based on the schema):\n"
            "  contracts.md:\n"
            "    - 'Show red-health contracts'\n"
            "    - 'Show contracts with CPI < 0.9'\n"
            "    - 'Show top 5 contracts by value'\n"
            "    - 'Show contracts awarded in the last 90 days'\n"
            "  deliverables.md:\n"
            "    - 'Show overdue deliverables'\n"
            "    - 'Show deliverables due in 7 days'\n"
            "    - 'Show CDRLs auto-generated this week'\n"
            "  options.md:\n"
            "    - 'Show options expiring in 90 days'\n"
            "    - 'Show options with go recommendation'\n"
            "    - 'Show options with no-go recommendation'\n"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-iqe-01",
    },
    {
        "id": "pmodemo-iqe-03",
        "title": "IQE: Register cpmp in _CANVAS_MAP (tools/dashboard/app.py:3325)",
        "description": (
            "File: tools/dashboard/app.py (MODIFY, +1 line in _CANVAS_MAP)\n\n"
            "Add 'cpmp' to the _CANVAS_MAP dict so the IQE dispatch knows which adapter to use "
            "when the canvas is 'cpmp'.\n\n"
            "If the dict is keyed by short name  ->  adapter path, add:\n"
            "  'cpmp': 'tools.iqe.adapters.cpmp',\n\n"
            "If the dict is keyed by something else, mirror the existing 'govcon' entry's shape "
            "and add an equivalent 'cpmp' entry.\n\n"
            "VERIFY the existing 'govcon' entry exists at the location noted in the plan "
            "(app.py:3325). If the line has moved, find it via grep.\n\n"
            "WARNING: another session is currently editing app.py. Use Edit (not Write) to "
            "make a surgical 1-line addition. If you cannot make a clean edit, post a comment "
            "on the task and abort."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-iqe-02",
    },
    {
        "id": "pmodemo-iqe-04",
        "title": "IQE: Include iqe_query_widget in all 4 page templates (+ icdev/ mirrors)",
        "description": (
            "Add {% include 'includes/iqe_query_widget.html' %} to the BOTTOM of all 4 page "
            "templates, with context variables per page:\n\n"
            "Files (MODIFY):\n"
            "  1. tools/dashboard/templates/proposals/list.html\n"
            "  2. tools/dashboard/templates/govcon/pipeline.html\n"
            "  3. tools/dashboard/templates/cpmp/portfolio.html\n"
            "  4. tools/dashboard/templates/cpmp/deliverable_center.html\n\n"
            "Context per page:\n"
            "  - proposals/list.html:\n"
            "      iqe_canvas='govcon', iqe_api_route='/api/iqe/dispatch',\n"
            "      iqe_examples=['Show top 5 opportunities by weighted value',\n"
            "                    'Show opportunities with pWin < 30%',\n"
            "                    'Show no-bid recommendations this quarter']\n"
            "  - govcon/pipeline.html:\n"
            "      iqe_canvas='govcon', iqe_api_route='/api/iqe/dispatch',\n"
            "      iqe_examples=['Show top 10 SAM.gov opportunities in NAICS 541512',\n"
            "                    'Show capability coverage gaps',\n"
            "                    'Show top competitor NAICS']\n"
            "  - cpmp/portfolio.html:\n"
            "      iqe_canvas='cpmp', iqe_api_route='/api/iqe/dispatch',\n"
            "      iqe_examples=['Show red-health contracts',\n"
            "                    'Show contracts with CPI < 0.9',\n"
            "                    'Show options expiring in 90 days']\n"
            "  - cpmp/deliverable_center.html:\n"
            "      iqe_canvas='cpmp', iqe_api_route='/api/iqe/dispatch',\n"
            "      iqe_examples=['Show overdue deliverables',\n"
            "                    'Show deliverables due in 7 days',\n"
            "                    'Show CDRLs auto-generated this week']\n\n"
            "Mirror all 4 file edits to icdev/tools/dashboard/templates/...\n\n"
            "DO NOT touch tools/dashboard/app.py or tools/dashboard/api/__init__.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-iqe-03",
    },
    {
        "id": "pmodemo-iqe-05",
        "title": "IQE: V&V  -  widget renders on all 4 pages, seed queries return results",
        "description": (
            "After pmodemo-iqe-04 merges, verify:\n\n"
            "1. Visit each of the 4 pages  -  IQE widget appears at the bottom\n"
            "2. Widget shows the 3 seed queries as clickable chips\n"
            "3. Click each seed query  -  widget returns a result (or 'no results found')\n"
            "4. Type a custom question in the input  -  widget returns a result\n"
            "5. The result table is rendered with rows (or empty state)\n"
            "6. No 500 errors in browser dev tools\n"
            "7. Playwright: screenshot playwright/screenshots/pmodemo-iqe-{canvas}.png\n\n"
            "If the widget doesn't render or seed queries fail:\n"
            "  (a) Verify /api/iqe/dispatch is wired and accepts canvas='cpmp' and 'govcon'\n"
            "  (b) Verify the seed query .md files match the schema the IQE engine expects\n"
            "  (c) Verify the iqe_query_widget.html partial exists at the expected path"
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "pmodemo-iqe-04",
    },

    # ══════════════════════════════════════════════════════════════════
    # EPIC: viewer  -  Weekly PMO Brief archive viewer at /cpmp/reports
    # ══════════════════════════════════════════════════════════════════
    {
        "id": "pmodemo-viewer-01",
        "title": "Viewer: /api/cpmp/reports/list + /api/cpmp/reports/<filename>",
        "description": (
            "File: tools/dashboard/api/cpmp.py (MODIFY, +40 LoC)\n\n"
            "Add 2 new GET endpoints:\n\n"
            "1. GET /api/cpmp/reports/list\n"
            "   Returns:\n"
            "     {\n"
            "       'reports': [\n"
            "         {'filename': 'pmo_weekly_2026-06-03.html', 'date': '2026-06-03', 'size_bytes': 12345},\n"
            "         ...\n"
            "       ],\n"
            "       'count': N\n"
            "     }\n"
            "   Globs data/reports/pmo_weekly_*.html. Sorted by filename desc (newest first).\n\n"
            "2. GET /api/cpmp/reports/<filename>\n"
            "   Returns the raw HTML content of data/reports/<filename>.\n"
            "   SECURITY: validate filename matches ^pmo_weekly_\\d{4}-\\d{2}-\\d{2}\\.html$ "
            "   to prevent path traversal. Return 403 if filename doesn't match.\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-brief-03",  # serialize on cpmp/portfolio.html
    },
    {
        "id": "pmodemo-viewer-02",
        "title": "Viewer: Build tools/dashboard/templates/cpmp/reports.html",
        "description": (
            "File: tools/dashboard/templates/cpmp/reports.html (NEW, ~80 LoC)\n\n"
            "Page structure:\n"
            "  - Header: 'PMO Weekly Brief Archive' (h1)\n"
            "  - Subheader: 'AI-generated portfolio briefs from the pmo_weekly_report reflex (Mon 07:00)'\n"
            "  - Left pane (1/3): list of reports as a vertical menu, each row = date + filename\n"
            "  - Right pane (2/3): iframe rendering the selected report's HTML\n\n"
            "On page load:\n"
            "  1. Fetch /api/cpmp/reports/list\n"
            "  2. Render the list\n"
            "  3. Auto-select the newest report and load it into the iframe\n\n"
            "Style: extend base.html layout. No new CSS framework.\n\n"
            "Mirror to icdev/tools/dashboard/templates/cpmp/reports.html.\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-viewer-01",
    },
    {
        "id": "pmodemo-viewer-03",
        "title": "Viewer: Register /cpmp/reports route in app.py",
        "description": (
            "File: tools/dashboard/app.py (MODIFY, +15 LoC in _register_govcon_pages)\n\n"
            "Add a new Flask route:\n"
            "  @bp.route('/cpmp/reports')\n"
            "  @require_role('admin', 'pm', 'capture_mgr', 'bd')\n"
            "  def cpmp_reports_page():\n"
            "      return render_template('cpmp/reports.html', ...)\n\n"
            "Mirror the existing cpmp_portfolio_page route's signature and any context vars it sets.\n\n"
            "WARNING: another session is currently editing app.py. Use Edit (not Write) to "
            "add the route surgically. If you cannot make a clean edit, post a comment and abort."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-viewer-02",
    },
    {
        "id": "pmodemo-viewer-04",
        "title": "Viewer: Add 'View PMO Briefs' button to cpmp/portfolio.html",
        "description": (
            "File: tools/dashboard/templates/cpmp/portfolio.html (MODIFY, +10 LoC)\n\n"
            "Add a button in the portfolio header that links to /cpmp/reports:\n"
            "  <a href='/cpmp/reports' class='btn btn-primary'>\n"
            "    📄 View PMO Briefs\n"
            "  </a>\n\n"
            "Position: in the top-right of the portfolio header, next to the page title.\n\n"
            "Mirror to icdev/tools/dashboard/templates/cpmp/portfolio.html.\n\n"
            "DO NOT touch tools/dashboard/app.py."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": "pmodemo-iqe-04",  # serialize on cpmp/portfolio.html
    },
    {
        "id": "pmodemo-viewer-05",
        "title": "Viewer: V&V  -  /cpmp/reports lists reports; iframe renders content",
        "description": (
            "After pmodemo-viewer-04 merges, verify:\n\n"
            "1. Visit /cpmp  -  'View PMO Briefs' button visible in header\n"
            "2. Click button  ->  navigates to /cpmp/reports\n"
            "3. /cpmp/reports lists at least 1 report (data/reports/pmo_weekly_2026-06-03.html)\n"
            "4. The newest report is auto-loaded into the iframe\n"
            "5. The iframe content is the actual weekly brief (LLM-narrated portfolio summary)\n"
            "6. Click another report in the list  ->  iframe content updates\n"
            "7. No 500 errors\n"
            "8. Playwright: screenshot playwright/screenshots/pmodemo-viewer-cpmp-reports.png\n\n"
            "If the page is empty:\n"
            "  (a) Verify data/reports/pmo_weekly_*.html exists (run pmo_weekly_report reflex if needed)\n"
            "  (b) Verify /api/cpmp/reports/list returns 200 with non-empty list\n"
            "  (c) Verify filename validation regex matches actual filenames"
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "pmodemo-viewer-04",
    },
]


# ══════════════════════════════════════════════════════════════════
# Final V&V  -  full demo rehearsal gate
# ══════════════════════════════════════════════════════════════════
TASKS.append({  # noqa: F821  -  appended after literal definition above
    "id": "pmodemo-vv-01",
    "title": "PMO Demo: Final V&V  -  full demo rehearsal + coherence gate",
    "description": (
        "After all 5 epics ship, run the final V&V gate:\n\n"
        "1. pytest tests/ -v --tb=short  -  must remain green\n"
        "2. ruff check .  -  must remain clean\n"
        "3. python tools/workflow/coherence_checker.py --all --fix --gate  -  must pass\n"
        "4. python tools/dx/companion.py --sync --write --json  -  must complete\n"
        "5. Restart the dashboard, refresh all 4 pages  -  no 500s\n"
        "6. Run through the 7-step demo flow:\n"
        "   (a) /cpmp  ->  AI brief  ->  click into red contract  ->  AI Recommendations\n"
        "   (b) /cpmp/reports  ->  view Monday's brief\n"
        "   (c) /cpmp/deliverables  ->  IQE widget  ->  'show overdue deliverables'\n"
        "   (d) /proposals  ->  bubble chart + BID badges  ->  drill into opp\n"
        "   (e) IQE widget  ->  'show opportunities with pWin < 30%'\n"
        "   (f) /govcon  ->  IQE widget  ->  'show capability gaps in NAICS 541512'\n"
        "   (g) /cpmp  ->  'Three things ran overnight without you...'\n"
        "7. Take screenshots of all 4 pages + /cpmp/reports for the deck\n"
        "8. Document any LLM calls that fell back to deterministic text (expected, not a failure)\n"
        "9. Write docs/features/pmo-demo-wow-factors.md with the demo flow as the body\n\n"
        "If any step fails, file a follow-up Kanban task and abort the gate."
    ),
    "task_type": "test",
    "priority": "critical",
    "status": "backlog",
    "scheduled_at": None,
    "depends_on_task_id": "pmodemo-brief-04",  # last task in dep chain
})


def seed(dry_run: bool = False) -> None:
    """Insert all 18 tasks. Idempotent  -  skips existing IDs."""
    conn = _conn()
    inserted = 0
    skipped = 0

    print(f"\n  PMO Demo Wow-Factor Seeder")
    print(f"  Project: {PROJECT_KEY} ({PROJECT_PREFIX})")
    print(f"  Tasks:   {len(TASKS)}\n")

    if dry_run:
        print("  DRY RUN - not writing to DB\n")
        for t in TASKS:
            print(f"  [DRY] {t['id']:25s} -> deps on {t.get('depends_on_task_id') or '(root)'}  {t['title'][:60]}")
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
            print(f"  ADD   {t['id']:25s} [deps={t.get('depends_on_task_id') or 'root':25s}]  {t['title'][:60]}")
            inserted += 1
            # Also write to junction table (kanban_task_deps) for the dep-graph
            # visualization on the kanban board. The scalar depends_on_task_id
            # column is read by the scheduler for cycle detection; the junction
            # table is read by the UI for multi-parent dep graphs.
            if t.get("depends_on_task_id"):
                conn.execute(
                    "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) "
                    "VALUES (?, ?, ?) ON CONFLICT (task_id, depends_on_id) DO NOTHING",
                    (t["id"], t["depends_on_task_id"], _NOW),
                )

        conn.commit()
        print(f"\n  Done: {inserted} added, {skipped} skipped.")

        # Print dep graph summary
        print(f"\n  Dep chain visualization:")
        for t in TASKS:
            if t.get("depends_on_task_id"):
                arrow = f"  {t['depends_on_task_id']} -> {t['id']}"
                print(arrow)
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Seed PMO demo wow-factor kanban tasks")
    p.add_argument("--dry-run", action="store_true", help="Print tasks without writing to DB")
    args = p.parse_args()
    seed(dry_run=args.dry_run)
